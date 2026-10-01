
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.campaigns.dto import CampaignBrief, CampaignDetail, CampaignSummary

__all__ = [
    "AngleRead",
    "AssetRead",
    "AssetsResponse",
    "CampaignBrief",
    "CampaignCreatedResponse",
    "CampaignDetail",
    "CampaignListResponse",
    "CampaignSummary",
    "CreativeSpecResponse",
    "ErrorResponse",
    "ResearchResponse",
    "ResearchRunRead",
    "ResearchSourceRead",
    "ResearchStepRead",
    "RetryAssetResponse",
    "SelectAngleRequest",
    "SelectAngleResponse",
    "UsageResponse",
    "UsageRowRead",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """The single error envelope every handler returns.

    Mirrors ``AppError.to_dict()`` so the body shape is defined by the
    exception hierarchy rather than reinvented per endpoint. ``fields`` is
    populated only for validation failures.
    """

    code: str
    message: str
    fields: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# POST /campaigns, GET /campaigns, GET /campaigns/{id}
# ---------------------------------------------------------------------------


class CampaignCreatedResponse(BaseModel):
    """201 body: the created row plus where its workflow lives.

    Returning the workflow ids is deliberate - it gives an operator a direct
    handle into the Temporal UI for a campaign that later misbehaves.
    """

    campaign: CampaignDetail
    workflow_id: str
    run_id: str


class CampaignListResponse(BaseModel):
    items: list[CampaignSummary]
    limit: int
    offset: int
    count: int = Field(description="Number of items in this page, not the total.")


# ---------------------------------------------------------------------------
# GET /campaigns/{id}/research
# ---------------------------------------------------------------------------


class ResearchSourceRead(BaseModel):
    id: UUID
    url: str
    title: str
    accessed_at: datetime
    excerpt: str

    @classmethod
    def from_model(cls, source: Any) -> ResearchSourceRead:
        return cls(
            id=source.id,
            url=source.url,
            title=source.title,
            accessed_at=source.accessed_at,
            excerpt=source.excerpt,
        )


class ResearchStepRead(BaseModel):
    """One row of the agent's trace: what it did and why."""

    step_number: int
    step_type: str
    input: dict[str, Any]
    output: dict[str, Any]
    decision_summary: str
    created_at: datetime

    @classmethod
    def from_model(cls, step: Any) -> ResearchStepRead:
        return cls(
            step_number=step.step_number,
            step_type=_enum_value(step.step_type),
            input=step.input or {},
            output=step.output or {},
            decision_summary=step.decision_summary,
            created_at=step.created_at,
        )


class AngleRead(BaseModel):
    """A creative angle, with the sources that actually back it.

    ``sources`` here are ``research_sources`` rows reached through the
    ``angle_sources`` join - that is, pages that were genuinely fetched. An
    angle that cited a URL the agent never successfully read has no link row
    and so contributes nothing here, which is the point: the UI cannot render
    a citation for a page that was never read.
    """

    id: UUID
    angle_number: int
    audience_insight: str
    hook: str
    visual_direction: str
    rationale: str
    is_selected: bool
    sources: list[ResearchSourceRead]
    # Sourced observations (verified quotes) -- as opposed to the interpretive
    # audience_insight / hook / visual_direction / rationale above.
    observations: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_model(cls, angle: Any) -> AngleRead:
        return cls(
            id=angle.id,
            angle_number=angle.angle_number,
            audience_insight=angle.audience_insight,
            hook=angle.hook,
            visual_direction=angle.visual_direction,
            rationale=angle.rationale,
            is_selected=angle.is_selected,
            sources=[ResearchSourceRead.from_model(s) for s in angle.sources],
            observations=list(getattr(angle, "observations", None) or []),
        )


class ResearchRunRead(BaseModel):
    id: UUID
    status: str
    tool_call_budget: int
    tool_calls_used: int
    model_used: str
    mock_mode: bool
    estimated_cost_usd: Decimal | None
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_model(cls, run: Any) -> ResearchRunRead:
        return cls(
            id=run.id,
            status=_enum_value(run.status),
            tool_call_budget=run.tool_call_budget,
            tool_calls_used=run.tool_calls_used,
            model_used=run.model_used,
            mock_mode=run.mock_mode,
            estimated_cost_usd=run.estimated_cost_usd,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )


class ResearchResponse(BaseModel):
    """The full research trace for a campaign.

    Composed across ``research_runs`` / ``research_steps`` /
    ``research_sources`` / ``creative_angles``, which is why it is an
    API-only shape rather than a reused domain DTO. ``source_count`` is
    surfaced separately because the brief requires at least three source
    pages per run and a shortfall must be visible, not inferred by counting
    an array client-side.
    """

    campaign_id: UUID
    run: ResearchRunRead | None
    steps: list[ResearchStepRead] = Field(default_factory=list)
    sources: list[ResearchSourceRead] = Field(default_factory=list)
    angles: list[AngleRead] = Field(default_factory=list)
    source_count: int = 0
    selected_angle_id: UUID | None = None


# ---------------------------------------------------------------------------
# POST /campaigns/{id}/select-angle
# ---------------------------------------------------------------------------


class SelectAngleRequest(BaseModel):
    angle_id: UUID


class SelectAngleResponse(BaseModel):
    campaign_id: UUID
    angle_id: UUID
    signalled: bool = Field(
        description=(
            "True once the running workflow has acknowledged the signal. The "
            "response is only returned when both the database write and the "
            "signal succeeded."
        )
    )


# ---------------------------------------------------------------------------
# GET /campaigns/{id}/spec
# ---------------------------------------------------------------------------


class CreativeSpecResponse(BaseModel):
    """The current (highest-version) creative spec.

    Flattened from the ORM row rather than reusing ``CreativeSpecSchema``
    from ``app.creative.dto``: that model is the *LLM output contract*
    (``extra="forbid"``, no id, no version) and it deliberately has no place
    for the persistence fields a client needs, above all ``version``, which
    is what makes an asset's spec snapshot reference meaningful.
    """

    id: UUID
    campaign_id: UUID
    angle_id: UUID
    version: int
    is_valid: bool
    hook: str
    approved_copy: str
    cta: str
    product_identity: dict[str, Any]
    scene_description: str
    palette: list[str]
    composition_guidance: str
    video_outline: dict[str, Any]
    typography_style: str = "modern_clean"
    created_at: datetime

    @classmethod
    def from_model(cls, spec: Any) -> CreativeSpecResponse:
        return cls(
            id=spec.id,
            campaign_id=spec.campaign_id,
            angle_id=spec.angle_id,
            version=spec.version,
            is_valid=spec.is_valid,
            hook=spec.hook,
            approved_copy=spec.approved_copy,
            cta=spec.cta,
            product_identity=spec.product_identity or {},
            scene_description=spec.scene_description,
            palette=list(spec.palette or []),
            composition_guidance=spec.composition_guidance,
            video_outline=spec.video_outline or {},
            typography_style=getattr(spec, "typography_style", None) or "modern_clean",
            created_at=spec.created_at,
        )


# ---------------------------------------------------------------------------
# GET /campaigns/{id}/assets
# ---------------------------------------------------------------------------


class SpecSnapshotRef(BaseModel):
    """Which spec version an asset was actually generated from.

    This is the cross-package composition the brief calls for. An asset row
    stores ``creative_spec_id``; on its own that id tells a reviewer nothing.
    Resolving it to ``version`` (and the hook/CTA that were current at the
    time) is what makes "the exact spec snapshot used" a real, inspectable
    claim - including when a newer spec version has since superseded it.
    """

    creative_spec_id: UUID
    version: int | None = None
    hook: str | None = None
    cta: str | None = None
    is_current: bool = Field(
        default=False,
        description="False means this asset predates the campaign's current spec version.",
    )


class AssetRead(BaseModel):
    id: UUID
    campaign_id: UUID
    asset_type: str
    status: str
    preview_url: str | None = Field(
        default=None,
        description="Populated once the asset reaches 'completed'; null otherwise.",
    )
    width: int | None
    height: int | None
    duration_seconds: Decimal | None
    generation_prompt: str
    provider: str
    attempt_count: int
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    spec_snapshot: SpecSnapshotRef

    @classmethod
    def from_model(cls, asset: Any, *, spec_snapshot: SpecSnapshotRef) -> AssetRead:
        status = _enum_value(asset.status)
        
        preview_url = asset.storage_url if status == "completed" else None
        # Backwards compatibility: rewrite old file:/// URLs from the DB to HTTP
        if preview_url and preview_url.startswith("file://"):
            from app.config import get_settings
            settings = get_settings()
            api_base = getattr(settings, "api_base_url", "http://localhost:8000").rstrip("/")
            if "/fixtures/storage/" in preview_url:
                path_part = preview_url.split("/fixtures/storage/")[-1]
                preview_url = f"{api_base}/fixtures/{path_part}"
            elif "/assets/" in preview_url:
                path_part = preview_url.split("/assets/")[-1]
                preview_url = f"{api_base}/assets/{path_part}"

        return cls(
            id=asset.id,
            campaign_id=asset.campaign_id,
            asset_type=_enum_value(asset.asset_type),
            status=status,
            # Only a completed asset has a URL worth handing out. A row can
            # carry a stale storage_url from a previous successful attempt
            # while a retry is mid-flight; exposing that would show a client
            # an old asset labelled as the current one.
            preview_url=preview_url,
            width=asset.width,
            height=asset.height,
            duration_seconds=asset.duration_seconds,
            generation_prompt=asset.generation_prompt,
            provider=asset.provider,
            attempt_count=asset.attempt_count,
            last_error=asset.last_error,
            started_at=asset.started_at,
            completed_at=asset.completed_at,
            spec_snapshot=spec_snapshot,
        )


class AssetsResponse(BaseModel):
    campaign_id: UUID
    current_spec_version: int | None
    items: list[AssetRead]


class RetryAssetResponse(BaseModel):
    campaign_id: UUID
    asset_id: UUID
    asset_type: str
    workflow_id: str
    run_id: str


# ---------------------------------------------------------------------------
# GET /campaigns/{id}/usage
# ---------------------------------------------------------------------------


class UsageRowRead(BaseModel):
    provider: str
    operation: str
    units: Decimal
    unit_type: str
    estimated_cost_usd: Decimal | None
    is_estimated: bool
    call_count: int


class UsageResponse(BaseModel):
    """Recorded provider usage, with estimates labelled rather than blended.

    ``total_cost_usd`` sums every row including estimated ones, so
    ``includes_estimates`` must be read alongside it. The two subtotals are
    given separately so a caller never has to trust a single blended number.
    """

    campaign_id: UUID
    rows: list[UsageRowRead]
    total_cost_usd: Decimal
    estimated_cost_usd: Decimal
    metered_cost_usd: Decimal
    includes_estimates: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum_value(value: Any) -> str:
    """Return the wire value for a column that may be an Enum or a plain str.

    The domain packages are not consistent about whether a status column
    comes back as its Python enum member or as a raw string, so this
    normalises both rather than guessing.
    """
    return getattr(value, "value", value)