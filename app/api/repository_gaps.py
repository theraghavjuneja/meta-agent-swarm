

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.assets.models import Asset
from app.campaigns.models import ProviderUsage
from app.common.exceptions import DomainError
from app.research.models import CreativeAngle, ResearchRun, ResearchSource, ResearchStep

__all__ = [
    "UsageRow",
    "aggregate_usage",
    "get_hero_asset",
    "get_latest_research_run",
    "list_angles",
    "list_assets_by_campaign",
    "list_sources",
    "list_steps",
    "select_angle",
]


# ---------------------------------------------------------------------------
# Research reads
# MOVE TO: app.research.repository
# ---------------------------------------------------------------------------


async def get_latest_research_run(
    session: AsyncSession, campaign_id: UUID
) -> ResearchRun | None:
    """Most recent research run for a campaign, or None.

    "Latest" rather than "the" run because ``research_runs`` is append-only
    by design - re-running research inserts a new row - so a campaign can
    legitimately have several.
    """
    result = await session.execute(
        sa.select(ResearchRun)
        .where(ResearchRun.campaign_id == campaign_id)
        .order_by(ResearchRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_steps(session: AsyncSession, research_run_id: UUID) -> list[ResearchStep]:
    """The observable trace, oldest first. Partial traces are expected and fine."""
    result = await session.execute(
        sa.select(ResearchStep)
        .where(ResearchStep.research_run_id == research_run_id)
        .order_by(ResearchStep.step_number.asc())
    )
    return list(result.scalars().all())


async def list_sources(
    session: AsyncSession, research_run_id: UUID
) -> list[ResearchSource]:
    result = await session.execute(
        sa.select(ResearchSource)
        .where(ResearchSource.research_run_id == research_run_id)
        .order_by(ResearchSource.accessed_at.asc())
    )
    return list(result.scalars().all())


async def list_angles(
    session: AsyncSession, research_run_id: UUID
) -> list[CreativeAngle]:
    """Angles with their linked sources eagerly loaded.

    ``selectinload`` is explicit here even though ``CreativeAngle.sources``
    is already ``lazy="selectin"``, so that this read does not depend on a
    model-level default that someone could reasonably change later.
    """
    result = await session.execute(
        sa.select(CreativeAngle)
        .where(CreativeAngle.research_run_id == research_run_id)
        .options(selectinload(CreativeAngle.sources))
        .order_by(CreativeAngle.angle_number.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Angle selection
# MOVE TO: app.research.repository (the query) + a research service (the rule)
#
# This is the one genuinely domain-level thing in this file. The
# "an angle can be selected only once" rule is a state-machine invariant and
# should not live behind an HTTP handler.
# ---------------------------------------------------------------------------


async def select_angle(
    session: AsyncSession, *, campaign_id: UUID, angle_id: UUID
) -> CreativeAngle:
    """Mark one angle selected, rejecting a second selection.

    Three things are checked, all of them 409-worthy rather than 500-worthy:

    * the angle exists;
    * it belongs to a research run for *this* campaign (so an angle id
      lifted from another campaign cannot be selected here);
    * no angle on that run is already selected.

    The database also has a partial unique index
    (``idx_creative_angles_one_selected``) enforcing one selected angle per
    run, so the check here is the friendly error message and the index is
    the actual guarantee. That ordering matters: under two concurrent
    requests the index wins and the loser gets an IntegrityError, which
    ``session_scope`` surfaces as ``InfrastructureError``. Slightly blunt,
    but never a double-selected run.
    """
    angle = await session.get(CreativeAngle, angle_id)
    if angle is None:
        raise DomainError(
            f"Angle {angle_id} not found",
            code="angle_not_found",
            http_status=404,
        )

    run = await session.get(ResearchRun, angle.research_run_id)
    if run is None or run.campaign_id != campaign_id:
        raise DomainError(
            f"Angle {angle_id} does not belong to campaign {campaign_id}",
            code="angle_campaign_mismatch",
            http_status=404,
        )

    already = await session.execute(
        sa.select(CreativeAngle.id).where(
            CreativeAngle.research_run_id == angle.research_run_id,
            CreativeAngle.is_selected.is_(True),
        )
    )
    selected_id = already.scalars().first()
    if selected_id is not None:
        if selected_id == angle_id:
            raise DomainError(
                f"Angle {angle_id} has already been selected for this campaign",
                code="angle_already_selected",
            )
        raise DomainError(
            f"Angle {selected_id} has already been selected for this campaign; "
            "an angle can only be chosen once",
            code="angle_already_selected",
        )

    angle.is_selected = True
    await session.flush()
    return angle


# ---------------------------------------------------------------------------
# Asset reads
# MOVE TO: app.assets.repository
#
# ``list_assets_by_campaign`` is additionally referenced by
# ``app.campaigns.repository.recompute_status_from_assets`` via
# ``from app.assets.repository import list_assets_by_campaign`` - an import
# that currently fails. Moving this function there fixes that too.
# ---------------------------------------------------------------------------


async def list_assets_by_campaign(
    session: AsyncSession, campaign_id: UUID
) -> list[Asset]:
    result = await session.execute(
        sa.select(Asset)
        .where(Asset.campaign_id == campaign_id)
        .order_by(Asset.asset_type.asc())
    )
    return list(result.scalars().all())


async def get_hero_asset(session: AsyncSession, campaign_id: UUID) -> Asset | None:
    """The campaign's hero image row, which compose/video retries derive from."""
    result = await session.execute(
        sa.select(Asset).where(
            Asset.campaign_id == campaign_id,
            Asset.asset_type == "hero_image",
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Usage aggregation
# MOVE TO: app.campaigns.repository
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRow:
    provider: str
    operation: str
    units: Decimal
    unit_type: str
    estimated_cost_usd: Decimal | None
    is_estimated: bool
    call_count: int


async def aggregate_usage(session: AsyncSession, campaign_id: UUID) -> list[UsageRow]:
    """Group ``provider_usage`` by provider/operation/unit_type/is_estimated.

    ``is_estimated`` is part of the grouping key on purpose. Collapsing an
    estimated row and a metered row into one total would produce a number
    that is neither, and the brief is explicit that estimates must be
    clearly labelled. Keeping them in separate rows means the response can
    label each one honestly and the caller can choose whether to add them.
    """
    stmt = (
        sa.select(
            ProviderUsage.provider,
            ProviderUsage.operation,
            ProviderUsage.unit_type,
            ProviderUsage.is_estimated,
            sa.func.sum(ProviderUsage.units).label("units"),
            sa.func.sum(ProviderUsage.estimated_cost_usd).label("cost"),
            sa.func.count().label("call_count"),
        )
        .where(ProviderUsage.campaign_id == campaign_id)
        .group_by(
            ProviderUsage.provider,
            ProviderUsage.operation,
            ProviderUsage.unit_type,
            ProviderUsage.is_estimated,
        )
        .order_by(ProviderUsage.provider.asc(), ProviderUsage.operation.asc())
    )
    result = await session.execute(stmt)
    return [
        UsageRow(
            provider=row.provider,
            operation=row.operation,
            units=row.units or Decimal(0),
            unit_type=row.unit_type,
            estimated_cost_usd=row.cost,
            is_estimated=row.is_estimated,
            call_count=row.call_count,
        )
        for row in result.all()
    ]