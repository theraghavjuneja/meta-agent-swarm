"""The campaign HTTP surface.

One file, because the surface is eight endpoints that all hang off
``/campaigns`` and splitting them would spread a single resource across
several modules for no benefit.

Every handler here is thin in the specific sense the engineering principles
mean: it validates input (or hands validation to a domain DTO), calls one
service / repository / workflow helper, and shapes the result. There is no
branching on campaign status, no deciding what should happen next, no
retry logic. The two places that look like business rules - the angle
already-selected check and the asset still-generating check - are guards
against acting on a bad state, and the first of them is explicitly flagged
in ``repository_gaps`` as something that should move into the research
package.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import repository_gaps
from app.api.schemas import (
    AngleRead,
    AssetRead,
    AssetsResponse,
    CampaignBrief,
    CampaignCreatedResponse,
    CampaignDetail,
    CampaignListResponse,
    CampaignSummary,
    CreativeSpecResponse,
    ResearchResponse,
    ResearchRunRead,
    ResearchSourceRead,
    ResearchStepRead,
    RetryAssetResponse,
    SelectAngleRequest,
    SelectAngleResponse,
    SpecSnapshotRef,
    UsageResponse,
    UsageRowRead,
)
from app.api.temporal_client import (
    signal_select_angle,
    start_campaign_workflow,
    start_retry_asset_workflow,
)
from app.assets import repository as assets_repository
from app.assets.adapters import get_storage_adapter
from app.campaigns import service as campaigns_service
from app.campaigns.models import CampaignStatus
from app.common.exceptions import DomainError, InfrastructureError, ValidationError
from app.common.logging import get_logger
from app.config import get_settings
from app.creative import repository as creative_repository
from app.db import get_db_session, session_scope
from app.workflows import CampaignWorkflowInput, RetryAssetWorkflowInput

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

logger = get_logger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# Asset types that are produced *from* the hero image rather than from the
# spec directly, and therefore need a completed hero to retry.
_HERO_DERIVED_ASSET_TYPES = {"ad_1x1", "ad_9x16", "video"}

_MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


# ---------------------------------------------------------------------------
# POST /campaigns
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CampaignCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign and start its workflow",
    description=(
        "Accepts either `application/json` (the brief alone) or "
        "`multipart/form-data` with a `brief` part containing the same JSON "
        "and an optional `reference_image` file part."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": CampaignBrief.model_json_schema()
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "brief": {
                                "type": "string",
                                "description": "JSON string of CampaignBrief",
                            },
                            "reference_image": {
                                "type": "string",
                                "format": "binary",
                                "description": "Optional reference image",
                            },
                        },
                        "required": ["brief"],
                    }
                },
            },
            "required": True,
        }
    },
)
async def create_campaign(request: Request) -> CampaignCreatedResponse:
    """Validate the brief, store any reference image, create the row, start the workflow.

    Ordering is deliberate and worth stating, because three of these steps
    can fail independently:

    1. **Validate the brief first.** ``CampaignBrief``'s field validators
       raise this project's ``ValidationError``, so bad input is rejected
       before a single byte is written and before Temporal is touched.
    2. **Then the image.** Type and size are checked at this boundary,
       before any storage write is attempted, per the module's own
       requirement. A rejected upload must not leave an orphaned object.
    3. **Then the row, then the workflow, then the ids.** If starting the
       workflow fails, the campaign row already exists, so it is marked
       ``failed`` before the error propagates - otherwise a client would be
       left with a campaign stuck in ``draft`` forever with no workflow
       behind it and no indication why.
    """
    payload, image_bytes, image_content_type = await _parse_create_request(request)

    brief = CampaignBrief(**payload)

    if image_bytes is not None:
        reference_image_url = await _store_reference_image(
            image_bytes, image_content_type
        )
        brief = brief.model_copy(update={"reference_image_url": reference_image_url})

    campaign = await campaigns_service.create_campaign(brief)

    workflow_input = CampaignWorkflowInput(
        campaign_id=campaign.id,
        product_name=brief.product_name,
        product_description=brief.product_description,
        target_audience=brief.target_audience,
        objective=brief.objective,
        tone=brief.tone,
        cta=brief.cta,
        extra_context=_verified_claims_context(brief.verified_claims),
    )

    try:
        workflow_id, run_id = await start_campaign_workflow(campaign.id, workflow_input)
    except (DomainError, InfrastructureError):
        # The row exists but nothing is orchestrating it. Mark it failed so
        # the list view tells the truth, then let the error surface.
        await campaigns_service.set_status(campaign.id, CampaignStatus.FAILED)
        logger.error(
            "campaign_workflow_start_failed",
            campaign_id=str(campaign.id),
            exc_info=True,
        )
        raise

    await campaigns_service.set_workflow_ids(campaign.id, workflow_id, run_id)

    detail = await campaigns_service.get_campaign_detail(campaign.id)
    if detail is None:  # pragma: no cover - the row was just written
        raise InfrastructureError(
            f"Campaign {campaign.id} disappeared immediately after creation",
            code="campaign_read_after_write_failed",
        )

    return CampaignCreatedResponse(
        campaign=detail, workflow_id=workflow_id, run_id=run_id
    )


# ---------------------------------------------------------------------------
# GET /campaigns
# ---------------------------------------------------------------------------


@router.get("", response_model=CampaignListResponse, summary="List campaigns")
async def list_campaigns(
    campaign_status: Annotated[
        CampaignStatus | None,
        Query(alias="status", description="Filter by campaign status."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CampaignListResponse:
    """History view. Newest first, paginated.

    ``count`` is the size of this page, not a total - counting all rows on
    every list call is a cost the history view does not need to pay.
    """
    campaigns = await campaigns_service.list_campaigns(
        status=campaign_status, limit=limit, offset=offset
    )
    items = [
        CampaignSummary(
            id=c.id,
            product_name=c.product_name,
            status=c.status,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in campaigns
    ]
    return CampaignListResponse(
        items=items, limit=limit, offset=offset, count=len(items)
    )


# ---------------------------------------------------------------------------
# GET /campaigns/{campaign_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{campaign_id}",
    response_model=CampaignDetail,
    summary="Campaign detail, including current stage statuses",
)
async def get_campaign(campaign_id: UUID) -> CampaignDetail:
    """Detail view, used both for the history list and for reopening after a refresh.

    ``stage_statuses`` on the returned DTO is the per-stage last-known event
    derived from ``stage_events`` by the campaigns service - which is what
    lets a client rebuild the pipeline view without replaying anything.
    """
    detail = await campaigns_service.get_campaign_detail(campaign_id)
    if detail is None:
        raise _not_found(campaign_id)
    return detail


# ---------------------------------------------------------------------------
# GET /campaigns/{campaign_id}/research
# ---------------------------------------------------------------------------


@router.get(
    "/{campaign_id}/research",
    response_model=ResearchResponse,
    summary="Full research trace: steps, sources and angles",
)
async def get_research(campaign_id: UUID, session: SessionDep) -> ResearchResponse:
    """The inspectable trace the user reviews before selecting an angle.

    Returns ``200`` with a null ``run`` when research has not started yet,
    rather than ``404``: the campaign exists and "no research run yet" is a
    legitimate, pollable state, not a missing resource. A 404 here is
    reserved for a campaign id that genuinely does not exist.
    """
    await _require_campaign(campaign_id)

    run = await repository_gaps.get_latest_research_run(session, campaign_id)
    if run is None:
        return ResearchResponse(campaign_id=campaign_id, run=None)

    steps = await repository_gaps.list_steps(session, run.id)
    sources = await repository_gaps.list_sources(session, run.id)
    angles = await repository_gaps.list_angles(session, run.id)

    selected = next((a.id for a in angles if a.is_selected), None)

    return ResearchResponse(
        campaign_id=campaign_id,
        run=ResearchRunRead.from_model(run),
        steps=[ResearchStepRead.from_model(s) for s in steps],
        sources=[ResearchSourceRead.from_model(s) for s in sources],
        angles=[AngleRead.from_model(a) for a in angles],
        source_count=len(sources),
        selected_angle_id=selected,
    )


# ---------------------------------------------------------------------------
# POST /campaigns/{campaign_id}/select-angle
# ---------------------------------------------------------------------------


@router.post(
    "/{campaign_id}/select-angle",
    response_model=SelectAngleResponse,
    summary="Select one research angle and unblock the workflow",
)
async def select_angle(
    campaign_id: UUID, body: SelectAngleRequest
) -> SelectAngleResponse:
    """Persist the selection, then signal the workflow. In that order.

    These two writes are one logical act, and neither is independently
    trustworthy. The database write goes first and is committed before the
    signal is sent, so the workflow can never be told about a selection the
    database does not record.

    The residual failure mode is the signal failing after the commit: the
    row says selected, the workflow was never unblocked. That surfaces as a
    502 rather than a silent success, which is the point - but note that a
    straight client retry will then hit the 409 "already selected" guard.
    Recovering from that case means signalling the workflow directly (its id
    is on the campaign row and in the 201 response). Making the endpoint
    re-signal on repeat selection would fix the recovery path at the cost of
    the module's explicit "selecting an angle twice returns a clear 4xx"
    requirement, so the requirement wins and the hazard is documented here.

    This handler manages its own session rather than taking the request
    dependency, precisely so the commit happens *before* the signal instead
    of after the response.
    """
    await _require_campaign(campaign_id)

    async with session_scope() as session:
        angle = await repository_gaps.select_angle(
            session, campaign_id=campaign_id, angle_id=body.angle_id
        )
        angle_id = angle.id
    # session committed here

    await signal_select_angle(campaign_id, angle_id)

    return SelectAngleResponse(
        campaign_id=campaign_id, angle_id=angle_id, signalled=True
    )


# ---------------------------------------------------------------------------
# GET /campaigns/{campaign_id}/spec
# ---------------------------------------------------------------------------


@router.get(
    "/{campaign_id}/spec",
    response_model=CreativeSpecResponse,
    summary="Current creative specification",
)
async def get_spec(campaign_id: UUID, session: SessionDep) -> CreativeSpecResponse:
    """The highest-version spec for the campaign.

    404 when no spec has been generated yet. Unlike the research trace, an
    absent spec is not a meaningful intermediate shape to render - there is
    no partial spec - so a null body would only invite clients to guess.
    """
    await _require_campaign(campaign_id)

    spec = await creative_repository.get_current(session, campaign_id)
    if spec is None:
        raise DomainError(
            f"No creative spec has been generated for campaign {campaign_id} yet",
            code="spec_not_found",
            http_status=404,
        )
    return CreativeSpecResponse.from_model(spec)


# ---------------------------------------------------------------------------
# GET /campaigns/{campaign_id}/assets
# ---------------------------------------------------------------------------


@router.get(
    "/{campaign_id}/assets",
    response_model=AssetsResponse,
    summary="All asset rows with status, preview URL, prompt and spec snapshot",
)
async def list_assets(campaign_id: UUID, session: SessionDep) -> AssetsResponse:
    """Every asset row for the campaign.

    This is the one response that genuinely composes two packages: the
    ``assets`` rows, plus the ``creative_specs`` row each one points at. The
    spec is resolved per distinct ``creative_spec_id`` rather than per asset
    so four assets sharing one spec cost one read, and each asset is marked
    against whether its snapshot is still the campaign's current version -
    which is how a reviewer sees that a retried asset was regenerated from a
    newer spec than its siblings.
    """
    await _require_campaign(campaign_id)

    assets = await repository_gaps.list_assets_by_campaign(session, campaign_id)
    current_spec = await creative_repository.get_current(session, campaign_id)
    current_spec_id = current_spec.id if current_spec else None

    snapshots: dict[UUID, SpecSnapshotRef] = {}
    for asset in assets:
        spec_id = asset.creative_spec_id
        if spec_id in snapshots:
            continue
        if current_spec is not None and spec_id == current_spec_id:
            spec = current_spec
        else:
            spec = await assets_repository.get_creative_spec(session, spec_id)
        snapshots[spec_id] = SpecSnapshotRef(
            creative_spec_id=spec_id,
            version=spec.version,
            hook=spec.hook,
            cta=spec.cta,
            is_current=spec_id == current_spec_id,
        )

    return AssetsResponse(
        campaign_id=campaign_id,
        current_spec_version=current_spec.version if current_spec else None,
        items=[
            AssetRead.from_model(a, spec_snapshot=snapshots[a.creative_spec_id])
            for a in assets
        ],
    )


# ---------------------------------------------------------------------------
# POST /campaigns/{campaign_id}/assets/{asset_id}/retry
# ---------------------------------------------------------------------------


@router.post(
    "/{campaign_id}/assets/{asset_id}/retry",
    response_model=RetryAssetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry generation of a single asset",
)
async def retry_asset(
    campaign_id: UUID, asset_id: UUID, session: SessionDep
) -> RetryAssetResponse:
    """Start a ``RetryAssetWorkflow`` for one asset.

    Three guards, all returning 4xx rather than starting a doomed workflow:

    * the asset must belong to this campaign (a mismatched pair is a 404,
      not a silent retry of someone else's asset);
    * it must not already be ``pending`` or ``generating`` - retrying an
      in-flight asset would race two writers against one row;
    * for the three hero-derived asset types, a *completed* hero image must
      exist, since compose and video both read its bytes.

    Note the retry regenerates only this asset. Retrying the hero image does
    not re-derive the 1:1, 9:16 and video from the new hero; that is
    ``RetryAssetWorkflow``'s documented design, and the response's 202 means
    "this one asset is being regenerated", nothing more.
    """
    await _require_campaign(campaign_id)

    asset = await assets_repository.get_asset(session, asset_id)
    if asset.campaign_id != campaign_id:
        raise DomainError(
            f"Asset {asset_id} does not belong to campaign {campaign_id}",
            code="asset_campaign_mismatch",
            http_status=404,
        )

    asset_type = getattr(asset.asset_type, "value", asset.asset_type)
    asset_status = getattr(asset.status, "value", asset.status)

    if asset_status in {"pending", "generating"}:
        raise DomainError(
            f"Asset {asset_id} is currently {asset_status}; "
            "wait for it to finish before retrying",
            code="asset_not_retryable",
        )

    hero_asset_id: UUID | None = None
    if asset_type in _HERO_DERIVED_ASSET_TYPES:
        hero = await repository_gaps.get_hero_asset(session, campaign_id)
        hero_status = getattr(hero.status, "value", hero.status) if hero else None
        if hero is None or hero_status != "completed":
            raise DomainError(
                f"Cannot retry {asset_type} for campaign {campaign_id}: "
                "it is derived from the hero image, which has not completed",
                code="hero_image_unavailable",
            )
        hero_asset_id = hero.id

    retry_input = RetryAssetWorkflowInput(
        campaign_id=campaign_id,
        asset_id=asset.id,
        asset_type=asset_type,
        creative_spec_id=asset.creative_spec_id,
        hero_asset_id=hero_asset_id,
    )

    workflow_id, run_id = await start_retry_asset_workflow(
        retry_input, attempt=asset.attempt_count
    )

    return RetryAssetResponse(
        campaign_id=campaign_id,
        asset_id=asset.id,
        asset_type=asset_type,
        workflow_id=workflow_id,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# GET /campaigns/{campaign_id}/usage
# ---------------------------------------------------------------------------


@router.get(
    "/{campaign_id}/usage",
    response_model=UsageResponse,
    summary="Recorded provider usage and cost, estimates labelled",
)
async def get_usage(campaign_id: UUID, session: SessionDep) -> UsageResponse:
    """Provider usage grouped by provider and operation.

    Estimated and metered rows are kept apart and subtotalled separately.
    ``total_cost_usd`` is the sum of both and is only meaningful read
    alongside ``includes_estimates`` - the brief requires estimates to be
    clearly labelled, and a single blended figure with a footnote is not
    that.
    """
    await _require_campaign(campaign_id)

    rows = await repository_gaps.aggregate_usage(session, campaign_id)

    estimated = sum(
        (r.estimated_cost_usd or 0) for r in rows if r.is_estimated
    )
    metered = sum(
        (r.estimated_cost_usd or 0) for r in rows if not r.is_estimated
    )

    return UsageResponse(
        campaign_id=campaign_id,
        rows=[
            UsageRowRead(
                provider=r.provider,
                operation=r.operation,
                units=r.units,
                unit_type=r.unit_type,
                estimated_cost_usd=r.estimated_cost_usd,
                is_estimated=r.is_estimated,
                call_count=r.call_count,
            )
            for r in rows
        ],
        total_cost_usd=estimated + metered,
        estimated_cost_usd=estimated,
        metered_cost_usd=metered,
        includes_estimates=any(r.is_estimated for r in rows),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _not_found(campaign_id: UUID) -> DomainError:
    """One consistent not-found path.

    ``DomainError`` already supports a per-instance status override, so
    404s ride the same exception type and the same handler as every other
    domain failure instead of introducing a parallel ``HTTPException`` route
    out of the router.
    """
    return DomainError(
        f"Campaign {campaign_id} not found",
        code="campaign_not_found",
        http_status=404,
    )


async def _require_campaign(campaign_id: UUID) -> None:
    """404 early if the campaign does not exist.

    Called by every sub-resource handler so that ``/campaigns/{bad-id}/spec``
    reports the campaign as missing rather than reporting an absent spec for
    a campaign that was never there.
    """
    campaign = await campaigns_service.get_campaign(campaign_id)
    if campaign is None:
        raise _not_found(campaign_id)


def _verified_claims_context(claims: list[str]) -> str | None:
    """Fold verified claims into the workflow's free-text ``extra_context``.

    ``CampaignWorkflowInput`` has no ``verified_claims`` field, and adding
    one would mean editing ``app/workflows`` - out of scope for this pass.
    They are passed as labelled context instead, which keeps them in front
    of the research agent. The claims are also persisted on the campaign row
    regardless, so nothing is lost either way.

    TODO: add a first-class ``verified_claims`` field to
    ``CampaignWorkflowInput`` and ``ResearchRequest`` so the agent receives
    them as structured data rather than prose.
    """
    if not claims:
        return None
    joined = "\n".join(f"- {claim}" for claim in claims)
    return f"Verified product claims (these are supported facts):\n{joined}"


async def _parse_create_request(
    request: Request,
) -> tuple[dict[str, Any], bytes | None, str | None]:
    """Accept the brief as JSON, or as multipart alongside a reference image.

    One URL handles both because they create the same resource; branching on
    content type here is less surprising to a client than two endpoints that
    differ only by whether a file came with the request.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_brief = form.get("brief")
        if raw_brief is None:
            raise ValidationError(
                "multipart request must include a 'brief' part containing the "
                "campaign brief as JSON",
                fields={"brief": ["required"]},
            )
        if not isinstance(raw_brief, str):
            raise ValidationError(
                "'brief' must be a JSON text part, not a file",
                fields={"brief": ["must be a JSON string"]},
            )
        try:
            payload = json.loads(raw_brief)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"'brief' is not valid JSON: {exc.msg}",
                fields={"brief": ["invalid JSON"]},
            ) from exc

        upload = form.get("reference_image")
        if upload is None or isinstance(upload, str):
            return _as_payload_dict(payload), None, None

        image_bytes, image_content_type = await _read_reference_image(upload)
        return _as_payload_dict(payload), image_bytes, image_content_type

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - any parse failure is a 422
        raise ValidationError(
            "Request body is not valid JSON",
            fields={"__root__": ["invalid JSON"]},
        ) from exc

    return _as_payload_dict(payload), None, None


def _as_payload_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError(
            "Campaign brief must be a JSON object",
            fields={"__root__": ["expected an object"]},
        )
    return payload


async def _read_reference_image(upload: Any) -> tuple[bytes, str]:
    """Enforce type and size limits *before* anything is read into storage.

    Size is checked twice on purpose. Starlette usually populates ``size``
    from the multipart headers, which lets an oversized upload be rejected
    without materialising it; when it does not, the bytes are read and
    checked. The second check is the one that actually guarantees the limit,
    the first is what stops a large body from being pointlessly buffered.
    """
    settings = get_settings()
    allowed = list(settings.allowed_reference_image_types)
    max_bytes = settings.max_reference_image_mb * 1024 * 1024

    content_type = (getattr(upload, "content_type", None) or "").split(";")[0].strip()
    if content_type not in allowed:
        raise ValidationError(
            f"Reference image type '{content_type or 'unknown'}' is not allowed. "
            f"Allowed types: {', '.join(allowed)}",
            fields={"reference_image": ["unsupported content type"]},
        )

    declared_size = getattr(upload, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        raise ValidationError(
            f"Reference image is larger than the "
            f"{settings.max_reference_image_mb} MB limit",
            fields={"reference_image": ["file too large"]},
        )

    data = await upload.read()
    if len(data) > max_bytes:
        raise ValidationError(
            f"Reference image is larger than the "
            f"{settings.max_reference_image_mb} MB limit",
            fields={"reference_image": ["file too large"]},
        )
    if not data:
        raise ValidationError(
            "Reference image is empty",
            fields={"reference_image": ["empty file"]},
        )

    return data, content_type


async def _store_reference_image(data: bytes, content_type: str | None) -> str:
    """Write the validated image through the assets storage port.

    Resolved through ``app.assets.adapters``' factory - the same one the
    activities use - so a reference image lands in whichever backend the
    environment is configured for, and the router never learns whether that
    is a local directory, S3, or a fixture.

    The key uses a fresh UUID rather than the campaign id because the
    campaign row does not exist yet: the URL has to be on the brief before
    ``create_campaign`` is called.
    """
    extension = _MIME_EXTENSIONS.get(content_type or "", "bin")
    key = f"reference-images/{uuid.uuid4()}.{extension}"

    storage = get_storage_adapter()
    try:
        stored = await storage.save(data, key, content_type or "application/octet-stream")
    except InfrastructureError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InfrastructureError.wrap(
            exc, provider="storage", operation="save_reference_image"
        ) from exc

    logger.info("reference_image_stored", key=key, bytes=len(data))
    return stored.storage_url