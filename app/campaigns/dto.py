"""DTOs for app.campaigns.

Two families live here:

* Read/write shapes for the future API (``CampaignBrief``,
  ``CampaignSummary``, ``CampaignDetail``).
* Thin input DTOs used by this module's own activities
  (``RecordUsageInput``, ``RecordStageEventInput``, plus the small
  single-field inputs the other activities take).

Validation note: pydantic v2 only intercepts ``ValueError``/``TypeError``/
``AssertionError`` raised inside a validator and folds them into its own
``pydantic.ValidationError``. Because ``app.common.exceptions.ValidationError``
is none of those, raising it directly from a field validator lets it escape
the pydantic model constructor unchanged - so bad brief input fails fast
with *this project's* ``ValidationError``, per the layered-exception
convention, rather than pydantic's.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.common.exceptions import ValidationError
from app.campaigns.models import CampaignStatus, StageEventType, StageName

def _require_nonempty(value: str, field_name: str, max_length: int | None = None) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValidationError(f"{field_name} is required")
    if max_length is not None and len(stripped) > max_length:
        raise ValidationError(f"{field_name} must be at most {max_length} characters")
    return stripped


class CampaignBrief(BaseModel):
    """Create-input shape for POST /campaigns (the future api route)."""

    product_name: str
    product_description: str
    target_audience: str
    objective: str
    tone: str
    cta: str
    reference_image_url: str | None = None
    verified_claims: list[str] = Field(default_factory=list)

    @field_validator("product_name")
    @classmethod
    def _validate_product_name(cls, v: str) -> str:
        return _require_nonempty(v, "product_name", 255)

    @field_validator("product_description")
    @classmethod
    def _validate_product_description(cls, v: str) -> str:
        return _require_nonempty(v, "product_description")

    @field_validator("target_audience")
    @classmethod
    def _validate_target_audience(cls, v: str) -> str:
        return _require_nonempty(v, "target_audience")

    @field_validator("objective")
    @classmethod
    def _validate_objective(cls, v: str) -> str:
        return _require_nonempty(v, "objective", 255)

    @field_validator("tone")
    @classmethod
    def _validate_tone(cls, v: str) -> str:
        return _require_nonempty(v, "tone", 100)

    @field_validator("cta")
    @classmethod
    def _validate_cta(cls, v: str) -> str:
        return _require_nonempty(v, "cta", 255)

    @field_validator("reference_image_url")
    @classmethod
    def _validate_reference_image_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        return v or None

    @field_validator("verified_claims")
    @classmethod
    def _validate_verified_claims(cls, v: list[str]) -> list[str]:
        cleaned = [claim.strip() for claim in v if claim and claim.strip()]
        return cleaned


class CampaignSummary(BaseModel):
    """List-view shape."""

    id: UUID
    product_name: str
    status: CampaignStatus
    created_at: datetime
    updated_at: datetime


class StageStatusInfo(BaseModel):
    """Last known event for a single stage, derived from stage_events."""

    stage_name: StageName
    last_event_type: StageEventType
    occurred_at: datetime
    detail: dict | None = None


class CampaignDetail(CampaignSummary):
    """Full detail shape.

    ``stage_statuses`` is this module's best-effort "current stage
    statuses" hook: a dict of stage -> last known stage_events row for
    that stage. It reflects only what stage_events records; assembling a
    full cross-package detail view (e.g. pulling in the actual creative
    spec or asset URLs) is api's job, not this module's - this module has
    no visibility into research/assets detail beyond what it needs for
    status aggregation.
    """

    product_description: str
    target_audience: str
    objective: str
    tone: str
    cta: str
    reference_image_url: str | None
    verified_claims: list[str]
    temporal_workflow_id: str | None
    stage_statuses: dict[StageName, StageStatusInfo] = Field(default_factory=dict)


class RecordStageEventInput(BaseModel):
    """Matches stage_events' writable columns."""

    campaign_id: UUID
    stage_name: StageName
    event_type: StageEventType
    detail: dict | None = None


class RecordUsageInput(BaseModel):
    """Matches provider_usage's writable columns."""

    campaign_id: UUID
    provider: str
    operation: str
    units: Decimal
    unit_type: str
    estimated_cost_usd: Decimal | None = None
    is_estimated: bool = True


class SetWorkflowIdsInput(BaseModel):
    campaign_id: UUID
    workflow_id: str
    run_id: str | None = None


class SetCampaignStatusInput(BaseModel):
    campaign_id: UUID
    status: CampaignStatus


class CampaignIdInput(BaseModel):
    """Single-field input for activities that only need the campaign id
    (e.g. recompute_campaign_status)."""

    campaign_id: UUID