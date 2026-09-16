"""Business-rule layer for app.campaigns.

Validation/business rules live here; query logic stays in repository.py
per SRP. No Temporal awareness here at all - starting the workflow, and
deciding what workflow_id/run_id to hand to set_workflow_ids, is api's job
(Module 9), not this module's.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.common.logging import get_logger

from app.campaigns import repository
from app.campaigns.dto import CampaignBrief, CampaignDetail, StageStatusInfo
from app.campaigns.models import Campaign, CampaignStatus, StageEventType, StageName

logger = get_logger(__name__)


async def create_campaign(brief: CampaignBrief) -> Campaign:
    """Validates via the DTO, then persists.

    CampaignBrief's field validators already raise
    app.common.exceptions.ValidationError on bad input (missing required
    fields, over-length fields) at construction time - before this
    function, and therefore before any row is written - so there is no
    additional validation to redo here. This is what lets a future API
    route reject bad input before a Temporal workflow is ever started.
    """
    campaign = await repository.create(brief)
    logger.info("campaign_created", campaign_id=str(campaign.id))
    return campaign


async def get_campaign(campaign_id: UUID) -> Campaign | None:
    return await repository.get(campaign_id)


async def list_campaigns(
    status: CampaignStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Campaign]:
    return await repository.list_campaigns(status=status, limit=limit, offset=offset)


async def get_campaign_detail(campaign_id: UUID) -> CampaignDetail | None:
    campaign = await repository.get(campaign_id)
    if campaign is None:
        return None
    return await _to_detail(campaign)


async def set_workflow_ids(
    campaign_id: UUID, workflow_id: str, run_id: str | None = None
) -> Campaign:
    return await repository.set_workflow_ids(campaign_id, workflow_id, run_id)


async def set_status(campaign_id: UUID, status: CampaignStatus) -> Campaign:
    return await repository.set_status(campaign_id, status)


async def recompute_status_from_assets(campaign_id: UUID) -> Campaign:
    return await repository.recompute_status_from_assets(campaign_id)


async def record_stage_event(
    campaign_id: UUID,
    stage_name: StageName,
    event_type: StageEventType,
    detail: dict | None = None,
):
    return await repository.record_stage_event(
        campaign_id, stage_name, event_type, detail
    )


async def record_provider_usage(
    campaign_id: UUID,
    provider: str,
    operation: str,
    units: Decimal,
    unit_type: str,
    estimated_cost_usd: Decimal | None = None,
    is_estimated: bool = True,
):
    return await repository.record_provider_usage(
        campaign_id,
        provider,
        operation,
        units,
        unit_type,
        estimated_cost_usd,
        is_estimated,
    )


async def _to_detail(campaign: Campaign) -> CampaignDetail:
    """Builds the stage -> last-known-event breakdown from stage_events.

    Events come back ordered oldest-first, so simply overwriting the dict
    entry per stage as we walk them leaves each stage pointing at its most
    recent event - a cheap way to get "current stage statuses" without a
    window function. This is this module's TODO hook for api: a fuller
    cross-package detail view (actual creative spec / asset URLs) is
    assembled by api, not here.
    """
    events = await repository.list_stage_events(campaign.id)
    stage_statuses: dict[StageName, StageStatusInfo] = {}
    for event in events:
        stage_statuses[event.stage_name] = StageStatusInfo(
            stage_name=event.stage_name,
            last_event_type=event.event_type,
            occurred_at=event.occurred_at,
            detail=event.detail,
        )

    return CampaignDetail(
        id=campaign.id,
        product_name=campaign.product_name,
        status=campaign.status,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        product_description=campaign.product_description,
        target_audience=campaign.target_audience,
        objective=campaign.objective,
        tone=campaign.tone,
        cta=campaign.cta,
        reference_image_url=campaign.reference_image_url,
        verified_claims=list(campaign.verified_claims or []),
        temporal_workflow_id=campaign.temporal_workflow_id,
        stage_statuses=stage_statuses,
    )