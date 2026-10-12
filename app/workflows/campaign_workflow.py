"""CampaignWorkflow — the top-level Temporal workflow for the campaign pipeline.

Workflow ID convention: ``campaign-{campaign_id}``  (set by the caller — the
api module's POST /campaigns handler — not by this file).

Sequence
--------
1. Research        run_research_agent              → up to 3 sourced angles
2. Angle select    wait on ``select_angle`` signal → human picks one angle
3. Spec            generate_creative_spec          → validated creative spec
4. Hero image      generate_hero_image             → base image all assets derive from
5. Concurrent      compose_ad (1×1 and 9×16) and render_video in parallel;
                   per-task error isolation ensures one failure never cancels siblings
6. Terminal        recompute_campaign_status       → derives true status from asset rows

Design constraints (enforced structurally, not by convention)
-------------------------------------------------------------
* No direct DB/HTTP/SDK calls anywhere in this file.  Every side-effect goes
  through ``workflow.execute_activity`` so Temporal can replay safely.
* ``select_angle`` is a ``@workflow.signal``; the main coroutine is blocked on
  ``workflow.wait_condition`` until the signal arrives.  Generation cannot begin
  without an explicit human selection — this is a structural guarantee, not a
  comment.
* ``asyncio.gather(..., return_exceptions=True)`` provides per-task isolation
  for the three concurrent asset stages.  Each helper method catches
  ``ActivityError`` internally and records failure without re-raising, so one
  failing stage cannot cancel or fail the other two.
* ``recompute_campaign_status`` is the *only* path to a terminal campaign
  status.  This workflow never calls set_campaign_status("completed") directly.
  The true state is always derived from the asset rows themselves.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from temporalio import workflow
from temporalio.exceptions import ActivityError


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
        set_campaign_status,
    )
    from app.campaigns.dto import (
        CampaignIdInput,
        RecordStageEventInput,
        RecordUsageInput,
        SetCampaignStatusInput,
    )
    from app.campaigns.models import CampaignStatus, StageEventType, StageName
    from app.config import get_settings
    from app.creative.activities import generate_creative_spec
    from app.creative.dto import GenerateSpecInput, GenerateSpecOutput
    from app.research.activities import run_research_agent
    from app.research.dto import ResearchOutcome, ResearchRequest
    from app.workflows.retry_policies import (
        CAMPAIGN_ACTIVITY_RETRY_POLICY,
        CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        COMPOSE_RETRY_POLICY,
        COMPOSE_START_TO_CLOSE,
        IMAGE_RETRY_POLICY,
        IMAGE_START_TO_CLOSE,
        RESEARCH_RETRY_POLICY,
        SPEC_RETRY_POLICY,
        SPEC_START_TO_CLOSE,
        VIDEO_RETRY_POLICY,
        VIDEO_START_TO_CLOSE,
        research_start_to_close,
    )


# ---------------------------------------------------------------------------
# Workflow input
# ---------------------------------------------------------------------------


@dataclass
class CampaignWorkflowInput:
    """Serialisable input for CampaignWorkflow.

    The field set mirrors ``app.research.dto.ResearchRequest`` so the api
    module can construct both from the same campaign row without an extra
    translation layer.  ``cta`` is required here (not ``str | None`` as in
    ``ResearchRequest``) because ``GenerateSpecInput.cta`` demands a non-empty
    string — the api validates this at campaign-creation time.
    """

    campaign_id: UUID
    product_name: str
    product_description: str
    target_audience: str
    objective: str
    tone: str
    cta: str
    extra_context: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------


@workflow.defn(name="CampaignWorkflow")
class CampaignWorkflow:
    """Orchestrates the full campaign generation pipeline for one campaign."""

    def __init__(self) -> None:
        # Signal state — mutated by select_angle(), read by wait_condition().
        self._angle_selected: bool = False
        self._selected_angle_id: str | None = None

    # ------------------------------------------------------------------
    # Signal handler
    # ------------------------------------------------------------------

    @workflow.signal
    def select_angle(self, angle_id: str) -> None:
        """Receive the user's angle selection and unblock the main coroutine.

        Called by the api layer once the user has reviewed the research
        output and chosen one of the returned creative angles.  Generation
        cannot begin until this signal has been received — the main coroutine
        is blocked on ``workflow.wait_condition`` until the flag is set here.
        """
        self._selected_angle_id = angle_id
        self._angle_selected = True
        workflow.logger.info(
            "select_angle signal received", extra={"angle_id": angle_id}
        )

    # ------------------------------------------------------------------
    # Main coroutine
    # ------------------------------------------------------------------

    @workflow.run
    async def run(self, input: CampaignWorkflowInput) -> None:  # noqa: A002
        campaign_id = input.campaign_id

        # Read once at startup so replay is deterministic (settings are
        # constant across a worker process lifetime).
        settings = get_settings()
        _research_timeout = research_start_to_close(settings.research_timeout_seconds)

        # ----------------------------------------------------------------
        # Step 1 — Research
        # set_campaign_status before the activity so the DB reflects
        # "researching" while the activity is in flight, not just after.
        # ----------------------------------------------------------------
        await self._set_status(campaign_id, CampaignStatus.RESEARCHING)
        await self._record_event(
            campaign_id, StageName.RESEARCH, StageEventType.STARTED
        )

        try:
            research_outcome: ResearchOutcome = await workflow.execute_activity(
                run_research_agent,
                ResearchRequest(
                    campaign_id=campaign_id,
                    product_name=input.product_name,
                    product_description=input.product_description,
                    target_audience=input.target_audience,
                    objective=input.objective,
                    tone=input.tone,
                    cta=input.cta,
                    extra_context=input.extra_context,
                ),
                retry_policy=RESEARCH_RETRY_POLICY,
                start_to_close_timeout=_research_timeout,
            )
        except ActivityError as exc:
            # Research permanently failed — no angles means nothing downstream
            # can run; terminate the workflow with a "failed" status.
            await self._record_event(
                campaign_id,
                StageName.RESEARCH,
                StageEventType.FAILED,
                detail={"error": str(exc)},
            )
            await self._set_status(campaign_id, CampaignStatus.FAILED)
            return

        await self._record_event(
            campaign_id, StageName.RESEARCH, StageEventType.COMPLETED
        )
        await self._record_research_usage(campaign_id, research_outcome)

        # ----------------------------------------------------------------
        # Step 2 — Angle selection
        # Structural block: generation cannot begin until the signal fires.
        # ----------------------------------------------------------------
        await self._set_status(campaign_id, CampaignStatus.AWAITING_ANGLE_SELECTION)
        await workflow.wait_condition(lambda: self._angle_selected)
        await self._record_event(
            campaign_id, StageName.ANGLE_SELECTION, StageEventType.COMPLETED
        )

        # _selected_angle_id is guaranteed non-None after wait_condition exits
        # because select_angle() sets it before flipping the flag.
        angle_id = UUID(self._selected_angle_id)  # type: ignore[arg-type]

        # ----------------------------------------------------------------
        # Step 3 — Creative spec generation
        # ----------------------------------------------------------------
        await self._set_status(campaign_id, CampaignStatus.GENERATING_SPEC)
        await self._record_event(
            campaign_id, StageName.SPEC_GENERATION, StageEventType.STARTED
        )

        try:
            spec_output: GenerateSpecOutput = await workflow.execute_activity(
                generate_creative_spec,
                GenerateSpecInput(
                    campaign_id=campaign_id,
                    angle_id=angle_id,
                    product_name=input.product_name,
                    product_description=input.product_description,
                    target_audience=input.target_audience,
                    objective=input.objective,
                    tone=input.tone,
                    cta=input.cta,
                ),
                retry_policy=SPEC_RETRY_POLICY,
                start_to_close_timeout=SPEC_START_TO_CLOSE,
            )
        except ActivityError as exc:
            # No spec means no assets — nothing downstream can run.
            await self._record_event(
                campaign_id,
                StageName.SPEC_GENERATION,
                StageEventType.FAILED,
                detail={"error": str(exc)},
            )
            await self._set_status(campaign_id, CampaignStatus.FAILED)
            return

        await self._record_event(
            campaign_id, StageName.SPEC_GENERATION, StageEventType.COMPLETED
        )
        await self._record_spec_usage(campaign_id, spec_output)

        creative_spec_id: UUID = spec_output.id

        # ----------------------------------------------------------------
        # Step 4 — Hero image
        # All downstream assets derive from this one; a permanent failure
        # here short-circuits steps 5 and skips straight to step 6.
        # ----------------------------------------------------------------
        await self._set_status(campaign_id, CampaignStatus.GENERATING_ASSETS)
        await self._record_event(
            campaign_id, StageName.HERO_IMAGE, StageEventType.STARTED
        )

        try:
            hero_output: GenerateHeroImageOutput = await workflow.execute_activity(
                generate_hero_image,
                GenerateHeroImageInput(
                    campaign_id=campaign_id,
                    creative_spec_id=creative_spec_id,
                ),
                retry_policy=IMAGE_RETRY_POLICY,
                start_to_close_timeout=IMAGE_START_TO_CLOSE,
            )
        except ActivityError as exc:
            await self._record_event(
                campaign_id,
                StageName.HERO_IMAGE,
                StageEventType.FAILED,
                detail={"error": str(exc)},
            )
            # Record each dependent stage as "not attempted" — these are not
            # failures in their own right but they cannot proceed without a hero.
            _not_attempted = {"reason": "hero_image_failed"}
            for _stage in (
                StageName.COMPOSE_1X1,
                StageName.COMPOSE_9X16,
                StageName.RENDER_VIDEO,
            ):
                await self._record_event(
                    campaign_id,
                    _stage,
                    StageEventType.FAILED,
                    detail=_not_attempted,
                )
            # Let recompute derive the terminal status from the asset rows.
            await self._recompute_status(campaign_id)
            return

        await self._record_event(
            campaign_id, StageName.HERO_IMAGE, StageEventType.COMPLETED
        )
        await self._record_asset_usage(campaign_id, hero_output.usage)

        hero_asset_id: UUID = hero_output.asset_id

        # ----------------------------------------------------------------
        # Step 5 — Concurrent asset generation
        #
        # All three stages start simultaneously; none depends on another.
        # Each helper records its own start/complete/fail events and catches
        # ActivityError internally — a permanent failure in one stage must
        # not cancel or prevent the other two from completing.
        #
        # return_exceptions=True is a belt-and-suspenders safety net: the
        # helpers are designed not to raise, but if an unexpected exception
        # does escape a helper it becomes a list value rather than cancelling
        # the gather and aborting the siblings.
        # ----------------------------------------------------------------
        await asyncio.gather(
            self._run_compose_stage(
                campaign_id=campaign_id,
                creative_spec_id=creative_spec_id,
                hero_asset_id=hero_asset_id,
                asset_type="ad_1x1",
                stage_name=StageName.COMPOSE_1X1,
            ),
            self._run_compose_stage(
                campaign_id=campaign_id,
                creative_spec_id=creative_spec_id,
                hero_asset_id=hero_asset_id,
                asset_type="ad_9x16",
                stage_name=StageName.COMPOSE_9X16,
            ),
            self._run_video_stage(
                campaign_id=campaign_id,
                creative_spec_id=creative_spec_id,
                hero_asset_id=hero_asset_id,
            ),
            return_exceptions=True,
        )

        # ----------------------------------------------------------------
        # Step 6 — Terminal status
        #
        # recompute_campaign_status is the sole path to a terminal campaign
        # status.  It derives the true state from the asset rows themselves
        # (completed vs completed_with_errors) rather than the workflow
        # guessing based on which gather results were exceptions.
        # ----------------------------------------------------------------
        await self._recompute_status(campaign_id)

    # ------------------------------------------------------------------
    # Private helpers — concurrent stage runners
    # ------------------------------------------------------------------

    async def _run_compose_stage(
        self,
        *,
        campaign_id: UUID,
        creative_spec_id: UUID,
        hero_asset_id: UUID,
        asset_type: str,
        stage_name: StageName,
    ) -> None:
        """Run one compose_ad activity with full lifecycle event tracking.

        ``ActivityError`` is caught and recorded here — this method never
        re-raises, ensuring sibling stages in the asyncio.gather are not
        cancelled when this stage fails.
        """
        await self._record_event(campaign_id, stage_name, StageEventType.STARTED)
        try:
            result: ComposeAdOutput = await workflow.execute_activity(
                compose_ad,
                ComposeAdInput(
                    campaign_id=campaign_id,
                    creative_spec_id=creative_spec_id,
                    hero_asset_id=hero_asset_id,
                    asset_type=asset_type,
                ),
                retry_policy=COMPOSE_RETRY_POLICY,
                start_to_close_timeout=COMPOSE_START_TO_CLOSE,
            )
        except ActivityError as exc:
            await self._record_event(
                campaign_id,
                stage_name,
                StageEventType.FAILED,
                detail={"error": str(exc)},
            )
            return  # do not re-raise; sibling stages must proceed

        await self._record_event(campaign_id, stage_name, StageEventType.COMPLETED)
        await self._record_asset_usage(campaign_id, result.usage)

    async def _run_video_stage(
        self,
        *,
        campaign_id: UUID,
        creative_spec_id: UUID,
        hero_asset_id: UUID,
    ) -> None:
        """Run the render_video activity with full lifecycle event tracking.

        ``ActivityError`` is caught and recorded here — this method never
        re-raises, ensuring sibling compose stages in the asyncio.gather are
        not cancelled when video rendering fails.
        """
        await self._record_event(
            campaign_id, StageName.RENDER_VIDEO, StageEventType.STARTED
        )
        try:
            result: RenderVideoOutput = await workflow.execute_activity(
                render_video,
                RenderVideoInput(
                    campaign_id=campaign_id,
                    creative_spec_id=creative_spec_id,
                    hero_asset_id=hero_asset_id,
                ),
                retry_policy=VIDEO_RETRY_POLICY,
                start_to_close_timeout=VIDEO_START_TO_CLOSE,
            )
        except ActivityError as exc:
            await self._record_event(
                campaign_id,
                StageName.RENDER_VIDEO,
                StageEventType.FAILED,
                detail={"error": str(exc)},
            )
            return  # do not re-raise; sibling compose stages must proceed

        await self._record_event(
            campaign_id, StageName.RENDER_VIDEO, StageEventType.COMPLETED
        )
        await self._record_asset_usage(campaign_id, result.usage)

    # ------------------------------------------------------------------
    # Private helpers — campaign-activity wrappers
    # ------------------------------------------------------------------

    async def _set_status(self, campaign_id: UUID, status: CampaignStatus) -> None:
        await workflow.execute_activity(
            set_campaign_status,
            SetCampaignStatusInput(campaign_id=campaign_id, status=status),
            retry_policy=CAMPAIGN_ACTIVITY_RETRY_POLICY,
            start_to_close_timeout=CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        )

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

    # ------------------------------------------------------------------
    # Private helpers — usage recording
    # Each domain module's UsageSummary has a slightly different shape;
    # these helpers normalise to the RecordUsageInput contract.
    # ------------------------------------------------------------------

    async def _record_research_usage(
        self, campaign_id: UUID, outcome: ResearchOutcome
    ) -> None:
        """Persist provider usage for the research activity.

        ``ResearchOutcome`` carries a ``ResearchSummary``, not a provider-usage
        DTO.  We synthesise the usage record from the token counts that the
        summary does carry.  An estimated_cost_usd is omitted here because the
        research activity already records its own cost estimate against the
        ``research_runs`` row; recording a second estimate from token counts
        alone would be redundant and potentially misleading.
        """
        total_tokens = (
            outcome.summary.input_tokens + outcome.summary.output_tokens
        )
        await workflow.execute_activity(
            record_provider_usage,
            RecordUsageInput(
                campaign_id=campaign_id,
                provider=outcome.summary.model_used or "anthropic",
                operation="research_agent",
                units=Decimal(str(total_tokens)),
                unit_type="tokens",
                estimated_cost_usd=None,
                is_estimated=True,
            ),
            retry_policy=CAMPAIGN_ACTIVITY_RETRY_POLICY,
            start_to_close_timeout=CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        )

    async def _record_spec_usage(
        self, campaign_id: UUID, output: GenerateSpecOutput
    ) -> None:
        """Persist provider usage for the generate_creative_spec activity.

        ``creative.dto.UsageSummary`` reports token counts (prompt, completion,
        total).  We use total_tokens when available, falling back to the sum of
        prompt + completion tokens.
        """
        total_tokens = output.usage.total_tokens or (
            (output.usage.prompt_tokens or 0) + (output.usage.completion_tokens or 0)
        )
        await workflow.execute_activity(
            record_provider_usage,
            RecordUsageInput(
                campaign_id=campaign_id,
                provider=output.usage.provider,
                operation="generate_creative_spec",
                units=Decimal(str(total_tokens)),
                unit_type="tokens",
                estimated_cost_usd=output.usage.estimated_cost_usd,
                is_estimated=output.usage.is_estimated,
            ),
            retry_policy=CAMPAIGN_ACTIVITY_RETRY_POLICY,
            start_to_close_timeout=CAMPAIGN_ACTIVITY_START_TO_CLOSE,
        )

    async def _record_asset_usage(
        self, campaign_id: UUID, usage: AssetUsageSummary
    ) -> None:
        """Persist provider usage for any asset activity.

        ``assets.dto.UsageSummary`` uses ``float`` for ``units`` and
        ``estimated_cost_usd``; we convert to ``Decimal`` (via ``str`` to
        avoid float-rounding surprises) for ``RecordUsageInput``.
        """
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
