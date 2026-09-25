"""Temporal activities for app/assets.

Each activity: resolves the asset row via `ensure_pending`, computes the
idempotency key, calls `mark_generating`, calls the relevant port directly,
and on success writes the result via `StoragePort` then calls `mark_completed`.
An `asset_generation_attempts` row is recorded either way. A row is only ever
marked completed by the attempt that actually produced the verified result
and finished writing it to storage -- never optimistically.

Ports raise `InfrastructureError` on provider failure, which these activities
let propagate uncaught (after recording the failed attempt) so that Temporal's
own RetryPolicy governs whole-activity retries. No app-level retry wrapper is
used here -- retrying is Temporal's job.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import UUID

import httpx
from PIL import Image
from temporalio import activity

from app.assets.adapters import (
    get_image_adapter,
    get_storage_adapter,
    get_video_adapter,
)
from app.assets.compositing import compose_with_report, render_video_layers
from app.assets.dto import (
    ComposeAdInput,
    ComposeAdOutput,
    GenerateHeroImageInput,
    GenerateHeroImageOutput,
    RenderVideoInput,
    RenderVideoOutput,
    UsageSummary,
)
from app.assets.models import AssetType, AttemptStatus
from app.assets.ports import VideoRenderSpec
from app.assets.prompts import build_hero_image_prompt
from app.assets.repository import (
    derive_idempotency_key,
    ensure_pending,
    get_asset,
    get_creative_spec,
    mark_completed,
    mark_failed,
    mark_generating,
    record_attempt,
)
from app.common.exceptions import InfrastructureError, ValidationError
from app.common.logging import get_logger
from app.config import get_settings
from app.db import session_scope

logger = get_logger(__name__)

_STORAGE_CONTENT_TYPES = {
    AssetType.HERO_IMAGE: "image/jpeg",
    AssetType.AD_1X1: "image/jpeg",
    AssetType.AD_9X16: "image/jpeg",
    AssetType.VIDEO: "video/mp4",
}

# Clamped into [VIDEO_MIN_DURATION_SECONDS, VIDEO_MAX_DURATION_SECONDS]:
# long enough for hook -> product -> CTA with a >= 2 s CTA hold, short
# enough to be watched through (Meta recommends <= 15 s for Stories/Reels).
_VIDEO_TARGET_DURATION_SECONDS = 8.0

_AD_DIMENSIONS = {
    AssetType.AD_1X1: (1080, 1080),
    AssetType.AD_9X16: (1080, 1920),
}


def _storage_key(campaign_id: UUID, asset_type: AssetType, extension: str) -> str:
    return f"campaigns/{campaign_id}/{asset_type.value}.{extension}"


async def _fetch_bytes(storage_url: str) -> bytes:
    """Reads back a previously-stored asset's bytes from its `storage_url`.

    Not covered by `StoragePort` (the port only defines `save`, per this
    module's spec), so this is a small local helper that understands the
    URL shapes the adapters in this module produce: `file://` (used by
    older/alternate storage adapters), the api's own `{api_base}/assets/...`
    and `{api_base}/fixtures/...` static paths (local-storage and
    fixture-storage adapters respectively -- read straight off the shared
    bind-mounted disk rather than over HTTP, since the worker container has
    no uvicorn listening on that host:port), and generic `http(s)://` for
    everything else (S3-compatible adapter, whether presigned or served
    from a public base URL).
    """
    if storage_url.startswith("file://"):
        parsed = urlparse(storage_url)
        path = url2pathname(parsed.path)
        with open(path, "rb") as f:
            return f.read()

    from pathlib import Path
    from app.assets.adapters.storage_fixture import _FIXTURE_STORAGE_DIR

    settings = get_settings()
    api_base = getattr(settings, "api_base_url", "http://localhost:8000").rstrip("/")

    # Both of these are URLs meant for a *browser* to load a static file from
    # the api container's uvicorn process. Resolving them the same way here
    # -- an httpx GET against api_base_url -- would work fine from a browser
    # on the host, but not from the worker container: nothing listens on
    # localhost:8000 *inside* the worker container (only `api` runs
    # uvicorn). Since both directories are on the same bind mount
    # (`..:/code`) shared by api and worker, we read the bytes straight off
    # disk instead of round-tripping over HTTP -- mirroring exactly what
    # StaticFiles would have served, for both the local-storage adapter
    # (storage_local.py) and the fixture-storage adapter (storage_fixture.py).
    assets_prefix = f"{api_base}/assets/"
    if storage_url.startswith(assets_prefix):
        key = storage_url[len(assets_prefix):]
        local_path = Path(settings.local_storage_path) / key
        with open(local_path, "rb") as f:
            return f.read()

    fixtures_prefix = f"{api_base}/fixtures/"
    if storage_url.startswith(fixtures_prefix):
        key = storage_url[len(fixtures_prefix):]
        local_path = Path(_FIXTURE_STORAGE_DIR) / key
        with open(local_path, "rb") as f:
            return f.read()

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(storage_url)
        response.raise_for_status()
        return response.content


async def _record_success(session, *, asset_id: UUID, attempt_started_at: datetime, provider_request_id: str | None) -> None:
    asset = await get_asset(session, asset_id)
    await record_attempt(
        session,
        asset_id=asset_id,
        attempt_number=asset.attempt_count,
        status=AttemptStatus.SUCCEEDED,
        provider_request_id=provider_request_id,
        error=None,
        started_at=attempt_started_at,
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


async def _record_failure(session, *, asset_id: UUID, attempt_started_at: datetime, error: str) -> None:
    asset = await get_asset(session, asset_id)
    await record_attempt(
        session,
        asset_id=asset_id,
        attempt_number=asset.attempt_count,
        status=AttemptStatus.FAILED,
        provider_request_id=None,
        error=error,
        started_at=attempt_started_at,
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    await mark_failed(session, asset_id, error)


# Longest edge sent to the image model. Plenty for label fidelity, and keeps
# an upload of a 10 MB phone photo from becoming a slow, oversized request.
_REFERENCE_MAX_EDGE_PX = 1536


async def _load_reference_image(reference_image_url: str) -> bytes:
    """Fetch the brief's reference packshot and normalise it to PNG.

    The URL was produced by our own storage adapter at upload time (type and
    size already validated there), but it is re-verified here as an actual
    decodable image: the brief's JSON form also accepts a caller-supplied
    URL, and a non-image must fail clearly rather than inside the provider.
    Transparency is kept (PNG), since a cut-out packshot is the ideal input.
    """
    try:
        raw = await _fetch_bytes(reference_image_url)
    except Exception as exc:  # noqa: BLE001 - any fetch failure is transient infra
        raise InfrastructureError(
            f"Could not fetch reference image {reference_image_url!r}: {exc}"
        ) from exc

    try:
        with Image.open(io.BytesIO(raw)) as src:
            image = src.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - Pillow raises several types
        raise ValidationError(
            f"Reference image {reference_image_url!r} is not a readable image"
        ) from exc

    image.thumbnail((_REFERENCE_MAX_EDGE_PX, _REFERENCE_MAX_EDGE_PX), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@activity.defn
async def generate_hero_image(input: GenerateHeroImageInput) -> GenerateHeroImageOutput:
    provider = "openai"
    has_reference = bool(input.reference_image_url)

    async with session_scope() as session:
        spec = await get_creative_spec(session, input.creative_spec_id)
        prompt = build_hero_image_prompt(spec, has_reference_image=has_reference)
        asset = await ensure_pending(
            session,
            campaign_id=input.campaign_id,
            creative_spec_id=input.creative_spec_id,
            asset_type=AssetType.HERO_IMAGE,
            generation_prompt=prompt,
            provider=provider,
        )
        idempotency_key = derive_idempotency_key(input.campaign_id, AssetType.HERO_IMAGE)
        asset = await mark_generating(session, asset.id)
        attempt_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            reference_image = (
                await _load_reference_image(input.reference_image_url)
                if input.reference_image_url
                else None
            )
            image_adapter = get_image_adapter()
            generated = await image_adapter.generate(
                prompt, idempotency_key, reference_image=reference_image
            )
            image_bytes = generated.read_bytes()

            storage_adapter = get_storage_adapter()
            key = _storage_key(input.campaign_id, AssetType.HERO_IMAGE, "jpg")
            stored = await storage_adapter.save(
                image_bytes, key, _STORAGE_CONTENT_TYPES[AssetType.HERO_IMAGE]
            )

            asset = await mark_completed(
                session,
                asset.id,
                storage_url=stored.storage_url,
                width=generated.width,
                height=generated.height,
            )
            await _record_success(
                session,
                asset_id=asset.id,
                attempt_started_at=attempt_started_at,
                provider_request_id=generated.provider_request_id,
            )
        except (InfrastructureError, ValidationError) as exc:
            # ValidationError: an unreadable reference image. Recorded like a
            # provider failure so the asset row never sticks in "generating".
            await _record_failure(session, asset_id=asset.id, attempt_started_at=attempt_started_at, error=str(exc))
            raise

        return GenerateHeroImageOutput(
            asset_id=asset.id,
            storage_url=asset.storage_url,
            width=asset.width,
            height=asset.height,
            usage=UsageSummary(
                provider=provider,
                operation="generate_hero_image",
                units=1,
                unit_type="image",
                estimated_cost_usd=0.0,
                is_estimated=True,
            ),
        )


@activity.defn
async def compose_ad(input: ComposeAdInput) -> ComposeAdOutput:
    provider = "pillow-compositing"

    try:
        asset_type = AssetType(input.asset_type)
    except ValueError as exc:
        raise ValidationError(f"compose_ad: unsupported asset_type {input.asset_type!r}") from exc
    if asset_type not in _AD_DIMENSIONS:
        raise ValidationError(f"compose_ad: asset_type must be ad_1x1 or ad_9x16, got {asset_type!r}")

    async with session_scope() as session:
        spec = await get_creative_spec(session, input.creative_spec_id)
        hero_asset = await get_asset(session, input.hero_asset_id)
        if not hero_asset.storage_url:
            raise ValidationError(f"compose_ad: hero asset {input.hero_asset_id} has no storage_url yet")

        asset = await ensure_pending(
            session,
            campaign_id=input.campaign_id,
            creative_spec_id=input.creative_spec_id,
            asset_type=asset_type,
            generation_prompt=f"compose:{asset_type.value}:{spec.hook}",
            provider=provider,
        )
        asset = await mark_generating(session, asset.id)
        attempt_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            hero_bytes = await _fetch_bytes(hero_asset.storage_url)
            # LEGACY (pre layout report):
            # compose_fn = compose_1x1 if asset_type == AssetType.AD_1X1 else compose_9x16
            # composed_bytes = compose_fn(hero_bytes, spec)
            width, height = _AD_DIMENSIONS[asset_type]
            composed_bytes, layout_report = compose_with_report(hero_bytes, spec, (width, height))

            storage_adapter = get_storage_adapter()
            key = _storage_key(input.campaign_id, asset_type, "jpg")
            stored = await storage_adapter.save(
                composed_bytes, key, _STORAGE_CONTENT_TYPES[asset_type]
            )

            # Retained with the asset: the exact copy overlaid and why it went where
            # it did (placement, product overlap, measured contrast, collision flag).
            generation_record = json.dumps(
                {
                    "compositor": "deterministic-overlay",
                    "headline": spec.hook,
                    "cta": spec.cta,
                    "typography_style": getattr(spec, "typography_style", None),
                    "layout": layout_report,
                }
            )
            asset = await mark_completed(
                session,
                asset.id,
                storage_url=stored.storage_url,
                width=width,
                height=height,
                generation_prompt=generation_record,
            )
            await _record_success(session, asset_id=asset.id, attempt_started_at=attempt_started_at, provider_request_id=None)
        except InfrastructureError as exc:
            await _record_failure(session, asset_id=asset.id, attempt_started_at=attempt_started_at, error=str(exc))
            raise

        return ComposeAdOutput(
            asset_id=asset.id,
            storage_url=asset.storage_url,
            width=asset.width,
            height=asset.height,
            usage=UsageSummary(
                provider=provider,
                operation=f"compose_{asset_type.value}",
                units=1,
                unit_type="image",
                estimated_cost_usd=0.0,
                is_estimated=False,
            ),
        )


@activity.defn
async def render_video(input: RenderVideoInput) -> RenderVideoOutput:
    provider = "ffmpeg"

    async with session_scope() as session:
        spec = await get_creative_spec(session, input.creative_spec_id)
        hero_asset = await get_asset(session, input.hero_asset_id)
        if not hero_asset.storage_url:
            raise ValidationError(f"render_video: hero asset {input.hero_asset_id} has no storage_url yet")

        asset = await ensure_pending(
            session,
            campaign_id=input.campaign_id,
            creative_spec_id=input.creative_spec_id,
            asset_type=AssetType.VIDEO,
            generation_prompt=f"render_video:{spec.hook}",
            provider=provider,
        )
        idempotency_key = derive_idempotency_key(input.campaign_id, AssetType.VIDEO)
        asset = await mark_generating(session, asset.id)
        attempt_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Format and duration come from settings. They used to be read out of
        # `video_outline`, which never contains them (it only holds `beats`),
        # so the configured VIDEO_* values were silently ignored.
        settings = get_settings()
        width, height = settings.video_width_px, settings.video_height_px
        duration = min(
            max(_VIDEO_TARGET_DURATION_SECONDS, settings.video_min_duration_seconds),
            settings.video_max_duration_seconds,
        )

        try:
            hero_bytes = await _fetch_bytes(hero_asset.storage_url)

            # The same layout engine as the 9:16 still, so the video's type,
            # colours and CTA are identical to the approved image ads.
            layers = render_video_layers(hero_bytes, spec, (width, height))
            render_spec = VideoRenderSpec(
                headline_text=spec.hook,
                cta_text=spec.cta,
                target_width=width,
                target_height=height,
                target_duration_seconds=float(duration),
                background_frame=layers.background_frame,
                headline_layer=layers.headline_layer,
                end_card_frame=layers.end_card_frame,
                cta_layer=layers.cta_layer,
                extra={"beats": (spec.video_outline or {}).get("beats", [])},
            )

            video_adapter = get_video_adapter()
            generated = await video_adapter.render(hero_bytes, render_spec, idempotency_key)
            video_bytes = generated.read_bytes()

            storage_adapter = get_storage_adapter()
            key = _storage_key(input.campaign_id, AssetType.VIDEO, "mp4")
            stored = await storage_adapter.save(
                video_bytes, key, _STORAGE_CONTENT_TYPES[AssetType.VIDEO]
            )

            asset = await mark_completed(
                session,
                asset.id,
                storage_url=stored.storage_url,
                width=generated.width,
                height=generated.height,
                duration_seconds=generated.duration_seconds,
            )
            await _record_success(
                session,
                asset_id=asset.id,
                attempt_started_at=attempt_started_at,
                provider_request_id=generated.provider_request_id,
            )
        except InfrastructureError as exc:
            await _record_failure(session, asset_id=asset.id, attempt_started_at=attempt_started_at, error=str(exc))
            raise

        return RenderVideoOutput(
            asset_id=asset.id,
            storage_url=asset.storage_url,
            width=asset.width,
            height=asset.height,
            duration_seconds=float(asset.duration_seconds or 0),
            usage=UsageSummary(
                provider=provider,
                operation="render_video",
                units=float(asset.duration_seconds or 0),
                unit_type="seconds",
                estimated_cost_usd=0.0,
                is_estimated=True,
            ),
        )