"""Persistence layer for app.campaigns.

All query logic lives here per SRP - service.py is thin pass-throughs plus
validation/business rules, not query construction.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import DomainError
from app.common.logging import get_logger
from app.db import session_scope

from app.campaigns.dto import CampaignBrief
from app.campaigns.models import (
    Campaign,
    CampaignStatus,
    ProviderUsage,
    StageEvent,
    StageEventType,
    StageName,
)

logger = get_logger(__name__)

# The four asset-producing stages that must all reach a terminal state
# before a campaign's status can be aggregated to completed /
# completed_with_errors. Kept as StageName so it stays in lockstep with the
# stage_name enum rather than duplicating literal strings.
EXPECTED_ASSET_STAGES: tuple[StageName, ...] = (
    StageName.HERO_IMAGE,
    StageName.COMPOSE_1X1,
    StageName.COMPOSE_9X16,
    StageName.RENDER_VIDEO,
)

# Terminal asset statuses this aggregator understands. Assumed to match
# app.assets' own status vocabulary (Module 6); adjust here if that
# vocabulary differs.
_ASSET_STATUS_COMPLETED = "completed"
_ASSET_STATUS_FAILED = "failed"


async def create(brief: CampaignBrief) -> Campaign:
    async with session_scope() as session:
        campaign = Campaign(
            product_name=brief.product_name,
            product_description=brief.product_description,
            target_audience=brief.target_audience,
            objective=brief.objective,
            tone=brief.tone,
            cta=brief.cta,
            reference_image_url=brief.reference_image_url,
            verified_claims=list(brief.verified_claims),
            status=CampaignStatus.DRAFT,
        )
        session.add(campaign)
        await session.flush()
        await session.refresh(campaign)
        logger.info("campaign_created", campaign_id=str(campaign.id))
        return campaign


async def get(campaign_id: UUID) -> Campaign | None:
    async with session_scope() as session:
        return await session.get(Campaign, campaign_id)


async def list_campaigns(
    status: CampaignStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Campaign]:
    async with session_scope() as session:
        stmt = (
            select(Campaign)
            .order_by(Campaign.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _get_or_raise(session: AsyncSession, campaign_id: UUID) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise DomainError(f"Campaign {campaign_id} not found")
    return campaign


async def set_workflow_ids(
    campaign_id: UUID, workflow_id: str, run_id: str | None
) -> Campaign:
    async with session_scope() as session:
        campaign = await _get_or_raise(session, campaign_id)
        campaign.temporal_workflow_id = workflow_id
        campaign.temporal_run_id = run_id
        await session.flush()
        await session.refresh(campaign)
        logger.info(
            "campaign_workflow_ids_set",
            campaign_id=str(campaign_id),
            workflow_id=workflow_id,
        )
        return campaign


async def set_status(campaign_id: UUID, status: CampaignStatus) -> Campaign:
    """Direct setter for workflow-driven intermediate stages.

    Never called from a request handler with 'completed' /
    'completed_with_errors' - those are only reachable via
    recompute_status_from_assets.
    """
    async with session_scope() as session:
        campaign = await _get_or_raise(session, campaign_id)
        campaign.status = status
        await session.flush()
        await session.refresh(campaign)
        logger.info(
            "campaign_status_set",
            campaign_id=str(campaign_id),
            status=status.value,
        )
        return campaign


async def recompute_status_from_assets(campaign_id: UUID) -> Campaign:
    """Assets-driven aggregator for the terminal state.

    Reads the four expected asset rows via app.assets.repository and
    applies the aggregation rule:

    * completed              -> all four asset rows completed
    * completed_with_errors  -> at least one permanently failed, the rest
                                 completed
    * otherwise               -> leave status untouched (either fewer than
                                 four asset rows exist yet, or some are
                                 still in flight - both are workflow-
                                 sequencing questions, not this function's
                                 job to guess at)
    """
    # Imported lazily (function-local) rather than at module load time:
    # app.campaigns and app.assets are independent sibling packages, and
    # only this one function needs to reach across into assets - importing
    # it lazily avoids giving the whole package a hard import-time
    # dependency on assets.
    from app.assets.repository import list_assets_by_campaign

    async with session_scope() as session:
        campaign = await _get_or_raise(session, campaign_id)

        assets = await list_assets_by_campaign(campaign_id)
        expected_stage_values = {stage.value for stage in EXPECTED_ASSET_STAGES}
        relevant = [a for a in assets if a.stage_name in expected_stage_values]

        if len(relevant) < len(EXPECTED_ASSET_STAGES):
            logger.info(
                "campaign_status_recompute_skipped_incomplete_assets",
                campaign_id=str(campaign_id),
                asset_count=len(relevant),
            )
            return campaign

        statuses = {a.status for a in relevant}

        if statuses == {_ASSET_STATUS_COMPLETED}:
            new_status = CampaignStatus.COMPLETED
        elif _ASSET_STATUS_FAILED in statuses and statuses <= {
            _ASSET_STATUS_COMPLETED,
            _ASSET_STATUS_FAILED,
        }:
            new_status = CampaignStatus.COMPLETED_WITH_ERRORS
        else:
            # Some assets are still pending/processing - not our call yet.
            logger.info(
                "campaign_status_recompute_skipped_in_flight",
                campaign_id=str(campaign_id),
            )
            return campaign

        campaign.status = new_status
        await session.flush()
        await session.refresh(campaign)
        logger.info(
            "campaign_status_recomputed",
            campaign_id=str(campaign_id),
            status=new_status.value,
        )
        return campaign


async def record_stage_event(
    campaign_id: UUID,
    stage_name: StageName,
    event_type: StageEventType,
    detail: dict | None = None,
) -> StageEvent:
    async with session_scope() as session:
        await _get_or_raise(session, campaign_id)
        event = StageEvent(
            campaign_id=campaign_id,
            stage_name=stage_name,
            event_type=event_type,
            detail=detail,
        )
        session.add(event)
        await session.flush()
        await session.refresh(event)
        logger.info(
            "stage_event_recorded",
            campaign_id=str(campaign_id),
            stage_name=stage_name.value,
            event_type=event_type.value,
        )
        return event


async def list_stage_events(campaign_id: UUID) -> list[StageEvent]:
    async with session_scope() as session:
        stmt = (
            select(StageEvent)
            .where(StageEvent.campaign_id == campaign_id)
            .order_by(StageEvent.occurred_at.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def record_provider_usage(
    campaign_id: UUID,
    provider: str,
    operation: str,
    units: Decimal,
    unit_type: str,
    estimated_cost_usd: Decimal | None = None,
    is_estimated: bool = True,
) -> ProviderUsage:
    async with session_scope() as session:
        await _get_or_raise(session, campaign_id)
        usage = ProviderUsage(
            campaign_id=campaign_id,
            provider=provider,
            operation=operation,
            units=units,
            unit_type=unit_type,
            estimated_cost_usd=estimated_cost_usd,
            is_estimated=is_estimated,
        )
        session.add(usage)
        await session.flush()
        await session.refresh(usage)
        logger.info(
            "provider_usage_recorded",
            campaign_id=str(campaign_id),
            provider=provider,
            operation=operation,
            is_estimated=is_estimated,
        )
        return usage