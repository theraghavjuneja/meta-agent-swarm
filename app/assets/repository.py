"""Repository functions for the assets module.

All persistence goes through here; models are never mutated directly from
activities. `assets` rows are updated in place -- inserted exactly once via
`ensure_pending`, then moved forward through pending -> generating ->
completed/failed. `asset_generation_attempts` is append-only.

Assumes app.db exposes an async `session_scope()` async-context-manager
yielding an `AsyncSession` for use outside any request context (Temporal
activities run outside the request lifecycle, so `get_db_session()`'s
FastAPI-dependency form doesn't apply here) -- consistent with how Modules
3-5's activities are described as using it.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.assets.models import Asset, AssetGenerationAttempt, AssetStatus, AssetType, AttemptStatus
from app.common.exceptions import DomainError
from app.creative.models import CreativeSpec


def derive_idempotency_key(campaign_id: UUID, asset_type: AssetType | str) -> str:
    """One deterministic derivation of the idempotency key, reused by every
    activity in this package (the DRY idempotency-key logic called for in
    the project's engineering principles). Stable across retries and across
    re-runs of the same logical (campaign_id, asset_type) unit of work."""
    type_value = asset_type.value if isinstance(asset_type, AssetType) else str(asset_type)
    raw = f"assets:{campaign_id}:{type_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def ensure_pending(
    session: AsyncSession,
    *,
    campaign_id: UUID,
    creative_spec_id: UUID,
    asset_type: AssetType,
    generation_prompt: str,
    provider: str,
) -> Asset:
    """Get-or-create the one row for (campaign_id, asset_type).

    Relies on the DB unique constraint (campaign_id, asset_type) rather than
    a check-then-insert race: attempts an insert and, on conflict, falls
    back to selecting the existing row. Safe under concurrent/retried
    activity invocations.
    """
    idempotency_key = derive_idempotency_key(campaign_id, asset_type)

    insert_stmt = (
        pg_insert(Asset)
        .values(
            campaign_id=campaign_id,
            creative_spec_id=creative_spec_id,
            asset_type=asset_type,
            status=AssetStatus.PENDING,
            generation_prompt=generation_prompt,
            provider=provider,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing(constraint="uq_assets_campaign_id_asset_type")
        .returning(Asset)
    )
    result = await session.execute(insert_stmt)
    inserted = result.scalar_one_or_none()
    if inserted is not None:
        await session.flush()
        return inserted

    existing = await session.execute(
        select(Asset).where(Asset.campaign_id == campaign_id, Asset.asset_type == asset_type)
    )
    asset = existing.scalar_one_or_none()
    if asset is None:
        # Should be unreachable: the insert only no-ops on conflict, and a
        # conflict implies a row already exists for this key.
        raise DomainError(
            f"ensure_pending: no row found or inserted for campaign_id={campaign_id}, asset_type={asset_type}"
        )
    return asset


async def get_asset(session: AsyncSession, asset_id: UUID) -> Asset:
    result = await session.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        raise DomainError(f"Asset not found: {asset_id}")
    return asset


async def get_creative_spec(session: AsyncSession, creative_spec_id: UUID) -> CreativeSpec:
    result = await session.execute(select(CreativeSpec).where(CreativeSpec.id == creative_spec_id))
    spec = result.scalar_one_or_none()
    if spec is None:
        raise DomainError(f"CreativeSpec not found: {creative_spec_id}")
    return spec


async def mark_generating(session: AsyncSession, asset_id: UUID) -> Asset:
    """Transitions the row to `generating` and increments attempt_count.
    Safe to call again after a failure -- failed -> generating is a valid
    transition, representing a fresh attempt at the same logical asset."""
    asset = await get_asset(session, asset_id)
    asset.status = AssetStatus.GENERATING
    asset.started_at = datetime.utcnow()
    asset.attempt_count += 1
    await session.flush()
    return asset


async def mark_completed(
    session: AsyncSession,
    asset_id: UUID,
    *,
    storage_url: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration_seconds: Optional[float] = None,
) -> Asset:
    """Marks the row completed. Only ever called by the attempt that
    actually produced the verified result and finished writing it to
    storage -- never optimistically."""
    asset = await get_asset(session, asset_id)
    asset.status = AssetStatus.COMPLETED
    asset.storage_url = storage_url
    asset.width = width
    asset.height = height
    asset.duration_seconds = duration_seconds
    asset.last_error = None
    asset.completed_at = datetime.utcnow()
    await session.flush()
    return asset


async def mark_failed(session: AsyncSession, asset_id: UUID, error: str) -> Asset:
    asset = await get_asset(session, asset_id)
    asset.status = AssetStatus.FAILED
    asset.last_error = error
    asset.completed_at = datetime.utcnow()
    await session.flush()
    return asset


async def record_attempt(
    session: AsyncSession,
    *,
    asset_id: UUID,
    attempt_number: int,
    status: AttemptStatus,
    provider_request_id: Optional[str],
    error: Optional[str],
    started_at: datetime,
    completed_at: Optional[datetime],
) -> AssetGenerationAttempt:
    """One row per attempt, success or failure -- including attempts
    Temporal itself retries once Module 8 wires up the workflow's
    RetryPolicy."""
    attempt = AssetGenerationAttempt(
        asset_id=asset_id,
        attempt_number=attempt_number,
        status=status,
        provider_request_id=provider_request_id,
        error=error,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(attempt)
    await session.flush()
    return attempt
