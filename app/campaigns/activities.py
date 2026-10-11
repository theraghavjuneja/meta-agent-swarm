"""Temporal Activity wrappers around app.campaigns.service.

Small and dumb by design: each activity delegates to service.py and returns
a plain-data (dict) result — never an ORM instance — since Temporal serializes
activity return values. This is the same pattern already used by
research/creative/assets; the orchestrating workflow (Module 8) is what
actually calls these.
"""

from __future__ import annotations

from temporalio import activity

from app.campaigns import service
from app.campaigns.dto import (
    CampaignIdInput,
    RecordStageEventInput,
    RecordUsageInput,
    SetCampaignStatusInput,
    SetWorkflowIdsInput,
)
from app.campaigns.models import Campaign
from app.common.logging import get_logger

logger = get_logger(__name__)


def _campaign_to_dict(campaign: Campaign) -> dict:
    return {
        "id": str(campaign.id),
        "status": campaign.status.value,
        "temporal_workflow_id": campaign.temporal_workflow_id,
        "temporal_run_id": campaign.temporal_run_id,
        "updated_at": campaign.updated_at.isoformat(),
    }


@activity.defn(name="campaigns.set_campaign_status")
async def set_campaign_status(input: SetCampaignStatusInput) -> dict:
    """Direct setter for workflow-driven intermediate stages
    (researching, awaiting_angle_selection, generating_spec,
    generating_assets)."""
    campaign = await service.set_status(input.campaign_id, input.status)
    return _campaign_to_dict(campaign)


@activity.defn(name="campaigns.recompute_campaign_status")
async def recompute_campaign_status(input: CampaignIdInput) -> dict:
    """Assets-driven aggregator for the terminal state (completed /
    completed_with_errors)."""
    campaign = await service.recompute_status_from_assets(input.campaign_id)
    return _campaign_to_dict(campaign)


@activity.defn(name="campaigns.record_stage_event")
async def record_stage_event(input: RecordStageEventInput) -> None:
    await service.record_stage_event(
        input.campaign_id, input.stage_name, input.event_type, input.detail
    )


@activity.defn(name="campaigns.record_provider_usage")
async def record_provider_usage(input: RecordUsageInput) -> None:
    await service.record_provider_usage(
        input.campaign_id,
        input.provider,
        input.operation,
        input.units,
        input.unit_type,
        input.estimated_cost_usd,
        input.is_estimated,
    )


@activity.defn(name="campaigns.set_workflow_ids")
async def set_workflow_ids(input: SetWorkflowIdsInput) -> None:
    await service.set_workflow_ids(
        input.campaign_id, input.workflow_id, input.run_id
    )