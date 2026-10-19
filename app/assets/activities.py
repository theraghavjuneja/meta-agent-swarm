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

from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import UUID

import httpx
from temporalio import activity

from app.assets.adapters import (
    get_image_adapter,
    get_storage_adapter,
    get_video_adapter,
)
from app.assets.compositing import compose_1x1, compose_9x16
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
from app.db import session_scope

logger = get_logger(__name__)

_STORAGE_CONTENT_TYPES = {
    AssetType.HERO_IMAGE: "image/jpeg",
    AssetType.AD_1X1: "image/jpeg",
    AssetType.AD_9X16: "image/jpeg",
    AssetType.VIDEO: "video/mp4",
}

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
    two URL shapes the adapters in this module produce: `file://` (local
    and fixture storage adapters) and `http(s)://` (S3-compatible adapter,
    whether presigned or served from a public base URL).
    """
    if storage_url.startswith("file://"):
        parsed = urlparse(storage_url)
        path = url2pathname(parsed.path)
        with open(path, "rb") as f:
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


@activity.defn
async def generate_hero_image(input: GenerateHeroImageInput) -> GenerateHeroImageOutput:
    provider = "replicate-flux"

    async with session_scope() as session:
        spec = await get_creative_spec(session, input.creative_spec_id)
        asset = await ensure_pending(
            session,
            campaign_id=input.campaign_id,
            creative_spec_id=input.creative_spec_id,
            asset_type=AssetType.HERO_IMAGE,
            generation_prompt=spec.scene_description,
            provider=provider,
        )
        idempotency_key = derive_idempotency_key(input.campaign_id, AssetType.HERO_IMAGE)
        asset = await mark_generating(session, asset.id)
        attempt_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            image_adapter = get_image_adapter()
            generated = await image_adapter.generate(spec.scene_description, idempotency_key)
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
        except InfrastructureError as exc:
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
            compose_fn = compose_1x1 if asset_type == AssetType.AD_1X1 else compose_9x16
            composed_bytes = compose_fn(hero_bytes, spec)

            storage_adapter = get_storage_adapter()
            key = _storage_key(input.campaign_id, asset_type, "jpg")
            stored = await storage_adapter.save(
                composed_bytes, key, _STORAGE_CONTENT_TYPES[asset_type]
            )

            width, height = _AD_DIMENSIONS[asset_type]
            asset = await mark_completed(session, asset.id, storage_url=stored.storage_url, width=width, height=height)
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

        outline = spec.video_outline or {}
        render_spec = VideoRenderSpec(
            headline_text=spec.hook,
            cta_text=spec.cta,
            target_width=int(outline.get("width", 1080)),
            target_height=int(outline.get("height", 1920)),
            target_duration_seconds=float(outline.get("duration_seconds", 8.0)),
        )

        try:
            hero_bytes = await _fetch_bytes(hero_asset.storage_url)

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