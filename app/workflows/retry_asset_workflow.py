"""RetryAssetWorkflow — handles targeted regeneration of a single asset.

Workflow ID convention: ``retry-asset-{asset_id}-{retry_count}`` (set by the
caller, not by this file).

This workflow allows humans to explicitly trigger a retry of a single asset
stage (hero_image, ad_1x1, ad_9x16, or video) that failed or needs
regeneration.

Important Structural Guarantee
------------------------------
Retrying a ``hero_image`` only regenerates the hero image itself.  If the caller
wants the downstream compositing and video stages regenerated from the *new*
hero image, they must explicitly trigger those as separate retries once this
hero-image retry completes.  This workflow does not automatically fan out
to dependent stages.  This is by design, keeping the retry workflow atomic
and predictable.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.assets.activities import compose_ad, generate_hero_image, render_video
    from app.assets.dto import (
        ComposeAdInput,
        ComposeAdOutput,
        GenerateHeroImageInput,
        GenerateHeroImageOutput,
        RenderVideoInput,
        RenderVideoOutput,
        UsageSummary as AssetUsageSummary,
    )
    from app.campaigns.activities import (
        record_provider_usage,
        record_stage_event,
        recompute_campaign_status,
    )
    from app.campaigns.dto import (
        CampaignIdInput,
        RecordStageEventInput,
        RecordUsageInput,
    )
    from app.campaigns.models import StageEventType, StageName
    from app.workflows.retry_policies import (
        CAMPAIGN_ACTIVITY_RETRY_POLICY,
        CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        COMPOSE_RETRY_POLICY,
        COMPOSE_START_TO_CLOSE,
        IMAGE_RETRY_POLICY,
        IMAGE_START_TO_CLOSE,
        VIDEO_RETRY_POLICY,
        VIDEO_START_TO_CLOSE,
    )


@dataclass
class RetryAssetWorkflowInput:
    """Serialisable input for RetryAssetWorkflow."""

    campaign_id: UUID
    asset_id: UUID
    asset_type: str  # "hero_image" | "ad_1x1" | "ad_9x16" | "video"
    creative_spec_id: UUID
    hero_asset_id: UUID | None = None  # required for compose_ad and render_video retries


@workflow.defn(name="RetryAssetWorkflow")
class RetryAssetWorkflow:
    """Orchestrates the targeted regeneration of a single campaign asset."""

    @workflow.run
    async def run(self, input: RetryAssetWorkflowInput) -> None:  # noqa: A002
        campaign_id = input.campaign_id
        asset_type = input.asset_type

        # 1. Map asset_type to the appropriate StageName
        stage_name: StageName
        if asset_type == "hero_image":
            stage_name = StageName.HERO_IMAGE
        elif asset_type == "ad_1x1":
            stage_name = StageName.COMPOSE_1X1
        elif asset_type == "ad_9x16":
            stage_name = StageName.COMPOSE_9X16
        elif asset_type == "video":
            stage_name = StageName.RENDER_VIDEO
        else:
            raise ApplicationError(
                f"Unknown asset_type for retry: {asset_type}", non_retryable=True
            )

        # 2. Record that a retry is starting
        await self._record_event(campaign_id, stage_name, StageEventType.RETRIED)

        usage: AssetUsageSummary | None = None

        try:
            # 3. Dispatch to the correct activity based on asset_type
            if asset_type == "hero_image":
                hero_output: GenerateHeroImageOutput = await workflow.execute_activity(
                    generate_hero_image,
                    GenerateHeroImageInput(
                        campaign_id=campaign_id,
                        creative_spec_id=input.creative_spec_id,
                    ),
                    retry_policy=IMAGE_RETRY_POLICY,
                    start_to_close_timeout=IMAGE_START_TO_CLOSE,
                )
                usage = hero_output.usage

            elif asset_type in ("ad_1x1", "ad_9x16"):
                if not input.hero_asset_id:
                    raise ApplicationError(
                        "hero_asset_id is required to retry compose_ad",
                        non_retryable=True,
                    )
                compose_output: ComposeAdOutput = await workflow.execute_activity(
                    compose_ad,
                    ComposeAdInput(
                        campaign_id=campaign_id,
                        creative_spec_id=input.creative_spec_id,
                        hero_asset_id=input.hero_asset_id,
                        asset_type=asset_type,
                    ),
                    retry_policy=COMPOSE_RETRY_POLICY,
                    start_to_close_timeout=COMPOSE_START_TO_CLOSE,
                )
                usage = compose_output.usage

            elif asset_type == "video":
                if not input.hero_asset_id:
                    raise ApplicationError(
                        "hero_asset_id is required to retry render_video",
                        non_retryable=True,
                    )
                video_output: RenderVideoOutput = await workflow.execute_activity(
                    render_video,
                    RenderVideoInput(
                        campaign_id=campaign_id,
                        creative_spec_id=input.creative_spec_id,
                        hero_asset_id=input.hero_asset_id,
                    ),
                    retry_policy=VIDEO_RETRY_POLICY,
                    start_to_close_timeout=VIDEO_START_TO_CLOSE,
                )
                usage = video_output.usage

        except ActivityError as exc:
            # 4a. On failure, record FAILED event and recompute terminal status
            await self._record_event(
                campaign_id,
                stage_name,
                StageEventType.FAILED,
                detail={"error": str(exc), "context": "retry"},
            )
            await self._recompute_status(campaign_id)
            return

        # 4b. On success, record COMPLETED event and usage
        await self._record_event(campaign_id, stage_name, StageEventType.COMPLETED)
        if usage:
            await self._record_asset_usage(campaign_id, usage)

        # 5. Recompute campaign status (it might transition to completed now)
        await self._recompute_status(campaign_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _record_event(
        self,
        campaign_id: UUID,
        stage_name: StageName,
        event_type: StageEventType,
        detail: dict | None = None,
    ) -> None:
        await workflow.execute_activity(
            record_stage_event,
            RecordStageEventInput(
                campaign_id=campaign_id,
                stage_name=stage_name,
                event_type=event_type,
                detail=detail,
            ),
            retry_policy=CAMPAIGN_ACTIVITY_RETRY_POLICY,
            start_to_close_timeout=CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        )

    async def _recompute_status(self, campaign_id: UUID) -> None:
        await workflow.execute_activity(
            recompute_campaign_status,
            CampaignIdInput(campaign_id=campaign_id),
            retry_policy=CAMPAIGN_ACTIVITY_RETRY_POLICY,
            start_to_close_timeout=CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        )

    async def _record_asset_usage(
        self, campaign_id: UUID, usage: AssetUsageSummary
    ) -> None:
        """Persist provider usage for any asset activity."""
        cost = (
            Decimal(str(usage.estimated_cost_usd))
            if usage.estimated_cost_usd is not None
            else None
        )
        await workflow.execute_activity(
            record_provider_usage,
            RecordUsageInput(
                campaign_id=campaign_id,
                provider=usage.provider,
                operation=usage.operation,
                units=Decimal(str(usage.units)),
                unit_type=usage.unit_type,
                estimated_cost_usd=cost,
                is_estimated=usage.is_estimated,
            ),
            retry_policy=CAMPAIGN_ACTIVITY_RETRY_POLICY,
            start_to_close_timeout=CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        )
