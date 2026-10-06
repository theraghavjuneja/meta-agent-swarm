"""SQLAlchemy 2.0 models for the research tables.

Five tables, matching the project schema: ``research_runs``, ``research_steps``,
``research_sources``, ``creative_angles`` and the ``angle_sources`` join table.

``research_runs.campaign_id`` is a plain indexed UUID column for now. The ``campaigns``
table does not exist yet, so neither a ``ForeignKey`` nor a cross-package relationship can
be declared here — both get added in the ``campaigns`` module's migration, at which point
this column becomes ``REFERENCES campaigns(id) ON DELETE CASCADE``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

__all__ = [
    "ResearchRunStatus",
    "ResearchStepTypeEnum",
    "ResearchRun",
    "ResearchStep",
    "ResearchSource",
    "CreativeAngle",
    "AngleSource",
    "research_run_status_enum",
    "research_step_type_enum",
]


class ResearchRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchStepTypeEnum(StrEnum):
    SEARCH = "search"
    READ_PAGE = "read_page"
    DECIDE = "decide"


# Native Postgres enums, created once by the migration and reused by both models below.
research_run_status_enum = sa.Enum(
    ResearchRunStatus,
    name="research_run_status",
    values_callable=lambda enum: [member.value for member in enum],
    create_type=False,
)
research_step_type_enum = sa.Enum(
    ResearchStepTypeEnum,
    name="research_step_type",
    values_callable=lambda enum: [member.value for member in enum],
    create_type=False,
)


class ResearchRun(Base, IdMixin, TimestampMixin):
    """One research attempt. Re-running research inserts a new row, never overwrites."""

    __tablename__ = "research_runs"
    __table_args__ = (sa.Index("idx_research_runs_campaign_id", "campaign_id"),)

    campaign_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[ResearchRunStatus] = mapped_column(
        research_run_status_enum,
        nullable=False,
        server_default=ResearchRunStatus.RUNNING.value,
    )
    tool_call_budget: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tool_calls_used: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    model_used: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(10, 4), nullable=True
    )
    mock_mode: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    steps: Mapped[list["ResearchStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    sources: Mapped[list["ResearchSource"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    angles: Mapped[list["CreativeAngle"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchStep(Base, IdMixin):
    """Append-only trace row, written as the loop executes."""

    __tablename__ = "research_steps"
    __table_args__ = (
        sa.UniqueConstraint(
            "research_run_id", "step_number", name="uq_research_steps_run_step"
        ),
        sa.Index("idx_research_steps_run_id", "research_run_id"),
    )

    research_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    step_type: Mapped[ResearchStepTypeEnum] = mapped_column(
        research_step_type_enum, nullable=False
    )
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision_summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    run: Mapped[ResearchRun] = relationship(back_populates="steps")


class ResearchSource(Base, IdMixin):
    """A page successfully read during a run. Unique per (run, url)."""

    __tablename__ = "research_sources"
    __table_args__ = (
        sa.UniqueConstraint("research_run_id", "url", name="uq_research_sources_run_url"),
        sa.Index("idx_research_sources_run_id", "research_run_id"),
    )

    research_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    excerpt: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    run: Mapped[ResearchRun] = relationship(back_populates="sources")


class CreativeAngle(Base, IdMixin, TimestampMixin):
    """One of the (up to) three candidate angles a run produced."""

    __tablename__ = "creative_angles"
    __table_args__ = (
        sa.UniqueConstraint(
            "research_run_id", "angle_number", name="uq_creative_angles_run_number"
        ),
        sa.CheckConstraint(
            "angle_number BETWEEN 1 AND 3", name="ck_creative_angles_angle_number"
        ),
        sa.Index(
            "idx_creative_angles_one_selected",
            "research_run_id",
            unique=True,
            postgresql_where=sa.text("is_selected"),
        ),
    )

    research_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    angle_number: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    audience_insight: Mapped[str] = mapped_column(sa.Text, nullable=False)
    hook: Mapped[str] = mapped_column(sa.Text, nullable=False)
    visual_direction: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rationale: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_selected: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )

    run: Mapped[ResearchRun] = relationship(back_populates="angles")
    sources: Mapped[list[ResearchSource]] = relationship(
        secondary="angle_sources", lazy="selectin", viewonly=True
    )


class AngleSource(Base):
    """``creative_angles`` ↔ ``research_sources``; one source can back several angles."""

    __tablename__ = "angle_sources"

    angle_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("creative_angles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
        primary_key=True,
    )