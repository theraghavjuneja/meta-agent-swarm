"""Persistence for ``creative_specs``.

Two operations, matching the append-only nature of the table:

* ``create_version`` — insert the next version for a campaign. Rows are never
  updated, so "next version" is computed inside the same transaction as the
  insert to avoid a race between two concurrent generations for the same
  campaign (e.g. a retried workflow / two activity attempts overlapping).
* ``get_current`` — the highest version for a campaign, or ``None``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.creative.models import CreativeSpec

__all__ = ["create_version", "get_current"]


async def create_version(
    session: AsyncSession,
    *,
    campaign_id: UUID,
    angle_id: UUID,
    spec_data: dict[str, Any],
) -> CreativeSpec:
    """Insert ``spec_data`` as the next version of ``creative_specs`` for ``campaign_id``.

    ``spec_data`` is the already-validated field set (``hook``, ``approved_copy``,
    ``cta``, ``product_identity``, ``scene_description``, ``palette``,
    ``composition_guidance``, ``video_outline``, ``is_valid``) — validation is the
    caller's (service.py's) job, this function only persists.

    Concurrency: a Postgres transaction-scoped advisory lock keyed on
    ``campaign_id`` serializes concurrent "compute next version, then insert"
    sequences for the *same* campaign, without taking a table- or row-level lock
    that would block unrelated campaigns. The lock is released automatically at
    transaction end (commit or rollback), so no explicit unlock is needed.
    """

    await session.execute(
        sa.select(
            sa.func.pg_advisory_xact_lock(
                sa.cast(sa.func.hashtext(str(campaign_id)), sa.BigInteger)
            )
        )
    )

    current_max = await session.scalar(
        sa.select(sa.func.max(CreativeSpec.version)).where(
            CreativeSpec.campaign_id == campaign_id
        )
    )
    next_version = (current_max or 0) + 1

    spec = CreativeSpec(
        campaign_id=campaign_id,
        angle_id=angle_id,
        version=next_version,
        **spec_data,
    )
    session.add(spec)
    await session.flush()
    return spec


async def get_current(session: AsyncSession, campaign_id: UUID) -> CreativeSpec | None:
    """Return the highest-version ``creative_specs`` row for ``campaign_id``, if any."""

    result = await session.execute(
        sa.select(CreativeSpec)
        .where(CreativeSpec.campaign_id == campaign_id)
        .order_by(CreativeSpec.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
