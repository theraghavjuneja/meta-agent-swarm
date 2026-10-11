"""Persistence for the research package.

The important design decision here is that ``DbStepRecorder.record_step`` opens and commits
its **own** session per step. That is what makes the partial-trace property real: if the
process dies at step 4, steps 1–3 are already committed rows, not pending objects in a
transaction that gets rolled back. Batching them into one transaction would give a tidier
session lifecycle and a useless trace.

Writes use ``INSERT ... ON CONFLICT DO NOTHING`` against the natural keys
(``research_run_id, step_number`` and ``research_run_id, url``) so that a retried activity
or a repeated page read cannot duplicate or corrupt the trace.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.db.session import session_scope
from app.research.dto import CreativeAngleDTO, ResearchStepRecord, SourceRecord
from app.research.models import (
    AngleSource,
    CreativeAngle,
    ResearchRun,
    ResearchRunStatus,
    ResearchSource,
    ResearchStep,
)

__all__ = [
    "DbStepRecorder",
    "complete_research_run",
    "create_research_run",
    "fail_research_run",
    "persist_angles",
]

logger = get_logger(__name__)


def _normalise_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


# --------------------------------------------------------------------------------------
# Run lifecycle
# --------------------------------------------------------------------------------------


async def create_research_run(
    *,
    campaign_id: UUID,
    tool_call_budget: int,
    model_used: str,
    mock_mode: bool,
) -> UUID:
    """Insert the ``research_runs`` row in ``running`` status and return its id."""
    async with session_scope() as session:
        run = ResearchRun(
            campaign_id=campaign_id,
            status=ResearchRunStatus.RUNNING,
            tool_call_budget=tool_call_budget,
            tool_calls_used=0,
            model_used=model_used,
            mock_mode=mock_mode,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    logger.info(
        "research.run.created",
        research_run_id=str(run_id),
        campaign_id=str(campaign_id),
    )
    return run_id


async def complete_research_run(
    research_run_id: UUID,
    *,
    tool_calls_used: int,
    estimated_cost_usd: Decimal | None = None,
) -> None:
    """Mark the run completed. Called only once the work has genuinely finished."""
    async with session_scope() as session:
        await session.execute(
            sa.update(ResearchRun)
            .where(ResearchRun.id == research_run_id)
            .values(
                status=ResearchRunStatus.COMPLETED,
                tool_calls_used=tool_calls_used,
                estimated_cost_usd=estimated_cost_usd,
                completed_at=datetime.now(timezone.utc),
            )
        )
    logger.info("research.run.completed", research_run_id=str(research_run_id))


async def fail_research_run(
    research_run_id: UUID,
    *,
    tool_calls_used: int | None = None,
    estimated_cost_usd: Decimal | None = None,
) -> None:
    """Mark the run failed. The steps already written stay, and stay readable."""
    values: dict[str, object] = {
        "status": ResearchRunStatus.FAILED,
        "completed_at": datetime.now(timezone.utc),
    }
    if tool_calls_used is not None:
        values["tool_calls_used"] = tool_calls_used
    if estimated_cost_usd is not None:
        values["estimated_cost_usd"] = estimated_cost_usd
    async with session_scope() as session:
        await session.execute(
            sa.update(ResearchRun).where(ResearchRun.id == research_run_id).values(**values)
        )
    logger.warning("research.run.failed", research_run_id=str(research_run_id))


# --------------------------------------------------------------------------------------
# Step recorder
# --------------------------------------------------------------------------------------


class DbStepRecorder:
    """Concrete ``StepRecorder``: writes one ``research_steps`` row per call, committed.

    Also upserts any ``research_sources`` rows the step produced and keeps
    ``research_runs.tool_calls_used`` current, so the live trace screen can render
    "n of m tool calls used" from the database alone while the loop is still running.
    """

    def __init__(self, research_run_id: UUID) -> None:
        self._research_run_id = research_run_id

    @property
    def research_run_id(self) -> UUID:
        return self._research_run_id

    async def record_step(self, step: ResearchStepRecord) -> None:
        async with session_scope() as session:
            await session.execute(
                pg_insert(ResearchStep)
                .values(
                    research_run_id=self._research_run_id,
                    step_number=step.step_number,
                    step_type=step.step_type.value,
                    input=step.input,
                    output=step.output,
                    decision_summary=step.decision_summary,
                )
                .on_conflict_do_nothing(constraint="uq_research_steps_run_step")
            )
            await self._upsert_sources(session, step.sources)
            await session.execute(
                sa.update(ResearchRun)
                .where(ResearchRun.id == self._research_run_id)
                .values(tool_calls_used=step.tool_calls_used)
            )
        logger.debug(
            "research.step.recorded",
            research_run_id=str(self._research_run_id),
            step_number=step.step_number,
            step_type=step.step_type.value,
        )

    async def _upsert_sources(
        self, session: AsyncSession, sources: Iterable[SourceRecord]
    ) -> None:
        for source in sources:
            await session.execute(
                pg_insert(ResearchSource)
                .values(
                    research_run_id=self._research_run_id,
                    url=source.url,
                    title=source.title or source.url,
                    accessed_at=source.accessed_at,
                    excerpt=source.excerpt,
                )
                .on_conflict_do_nothing(constraint="uq_research_sources_run_url")
            )


# --------------------------------------------------------------------------------------
# Angles
# --------------------------------------------------------------------------------------


async def persist_angles(
    research_run_id: UUID, angles: Sequence[CreativeAngleDTO]
) -> list[UUID]:
    """Insert the angles and link each to the sources it cites.

    Citations are matched to ``research_sources`` rows by normalised URL. An angle citing
    a URL that was never successfully read is still stored — the loop has already flagged
    it in the outcome's gap note — it simply gets no link row, so the UI cannot render a
    citation that was never actually fetched.
    """
    if not angles:
        return []

    angle_ids: list[UUID] = []
    async with session_scope() as session:
        rows = await session.execute(
            sa.select(ResearchSource.id, ResearchSource.url).where(
                ResearchSource.research_run_id == research_run_id
            )
        )
        by_url: dict[str, UUID] = {
            _normalise_url(url): source_id for source_id, url in rows.all()
        }

        for number, angle in enumerate(angles[:3], start=1):
            row = CreativeAngle(
                research_run_id=research_run_id,
                angle_number=number,
                audience_insight=angle.audience_insight,
                hook=angle.hook,
                visual_direction=angle.visual_direction,
                rationale=angle.rationale,
                is_selected=False,
            )
            session.add(row)
            await session.flush()
            angle_ids.append(row.id)

            linked: set[UUID] = set()
            for ref in angle.sources:
                source_id = by_url.get(_normalise_url(ref.url))
                if source_id is None or source_id in linked:
                    continue
                linked.add(source_id)
                await session.execute(
                    pg_insert(AngleSource)
                    .values(angle_id=row.id, source_id=source_id)
                    .on_conflict_do_nothing()
                )

    logger.info(
        "research.angles.persisted",
        research_run_id=str(research_run_id),
        count=len(angle_ids),
    )
    return angle_ids