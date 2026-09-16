"""SQLAlchemy models for the campaigns package.

Matches the authoritative schema exactly:

* ``Campaign``       -> Base + IdMixin + TimestampMixin (has updated_at)
* ``StageEvent``      -> Base + IdMixin only (append-only, no updated_at)
* ``ProviderUsage``   -> Base + IdMixin only (append-only, no updated_at)

``StageEvent`` and ``ProviderUsage`` still need a creation timestamp
(``occurred_at`` / ``created_at`` respectively) even though they don't get
``updated_at``, so those columns are declared directly on the models rather
than pulled in via TimestampMixin.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin


# --------------------------------------------------------------------------
# Enums - mirror the Postgres enum types byte-for-byte (values, not just
# names, since these are used as the ``values_callable`` source for the
# native Postgres ENUM columns below).
# --------------------------------------------------------------------------


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    RESEARCHING = "researching"
    AWAITING_ANGLE_SELECTION = "awaiting_angle_selection"
    GENERATING_SPEC = "generating_spec"
    GENERATING_ASSETS = "generating_assets"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class StageName(str, enum.Enum):
    RESEARCH = "research"
    ANGLE_SELECTION = "angle_selection"
    SPEC_GENERATION = "spec_generation"
    HERO_IMAGE = "hero_image"
    COMPOSE_1X1 = "compose_1x1"
    COMPOSE_9X16 = "compose_9x16"
    RENDER_VIDEO = "render_video"


class StageEventType(str, enum.Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRIED = "retried"


def _pg_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class Campaign(Base, IdMixin, TimestampMixin):
    __tablename__ = "campaigns"

    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_description: Mapped[str] = mapped_column(Text, nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(String(255), nullable=False)
    tone: Mapped[str] = mapped_column(String(100), nullable=False)
    cta: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_claims: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    status: Mapped[CampaignStatus] = mapped_column(
        _pg_enum(CampaignStatus, "campaign_status"),
        nullable=False,
        default=CampaignStatus.DRAFT,
        server_default=CampaignStatus.DRAFT.value,
    )
    temporal_workflow_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    temporal_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    stage_events: Mapped[list["StageEvent"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    provider_usage: Mapped[list["ProviderUsage"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Campaign id={self.id} status={self.status}>"


class StageEvent(Base, IdMixin):
    __tablename__ = "stage_events"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_name: Mapped[StageName] = mapped_column(
        _pg_enum(StageName, "stage_name"), nullable=False
    )
    event_type: Mapped[StageEventType] = mapped_column(
        _pg_enum(StageEventType, "stage_event_type"), nullable=False
    )
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="stage_events")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<StageEvent campaign_id={self.campaign_id} "
            f"stage={self.stage_name} event={self.event_type}>"
        )


class ProviderUsage(Base, IdMixin):
    __tablename__ = "provider_usage"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    units: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    is_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="provider_usage")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<ProviderUsage campaign_id={self.campaign_id} "
            f"provider={self.provider} operation={self.operation}>"
        )