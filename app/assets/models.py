"""SQLAlchemy models for `assets` and `asset_generation_attempts`, matching
the authoritative schema exactly.

Assumes app.db exposes:
    Base            - declarative base
    IdMixin         - adds `id: UUID` primary key (server_default gen_random_uuid())
    TimestampMixin  - adds `created_at` / `updated_at` TIMESTAMPTZ columns

`Asset` uses Base + IdMixin + TimestampMixin since it's updated in place.
`AssetGenerationAttempt` uses Base + IdMixin only -- it's append-only, and
its created_at-equivalent is just started_at/completed_at as specified.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin


class AssetType(str, enum.Enum):
    HERO_IMAGE = "hero_image"
    AD_1X1 = "ad_1x1"
    AD_9X16 = "ad_9x16"
    VIDEO = "video"


class AssetStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Reused by both the ORM columns below and the alembic migration so the
# Postgres ENUM value lists can never drift from the Python enums.
asset_type_enum = SAEnum(
    AssetType, name="asset_type", values_callable=lambda enum_cls: [member.value for member in enum_cls]
)
asset_status_enum = SAEnum(
    AssetStatus, name="asset_status", values_callable=lambda enum_cls: [member.value for member in enum_cls]
)
attempt_status_enum = SAEnum(
    AttemptStatus, name="attempt_status", values_callable=lambda enum_cls: [member.value for member in enum_cls]
)


class Asset(Base, IdMixin, TimestampMixin):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("campaign_id", "asset_type", name="uq_assets_campaign_id_asset_type"),
    )

    # FK to campaigns(id) is deferred to Module 7's migration -- campaigns
    # doesn't exist yet, so this column is intentionally left un-FK'd here.
    campaign_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    creative_spec_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("creative_specs.id"), nullable=False
    )
    asset_type: Mapped[AssetType] = mapped_column(asset_type_enum, nullable=False)
    status: Mapped[AssetStatus] = mapped_column(asset_status_enum, nullable=False, default=AssetStatus.PENDING)
    storage_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 1), nullable=True)
    generation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    attempts: Mapped[list["AssetGenerationAttempt"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetGenerationAttempt.attempt_number",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Asset id={self.id} campaign_id={self.campaign_id} asset_type={self.asset_type} status={self.status}>"


class AssetGenerationAttempt(Base, IdMixin):
    __tablename__ = "asset_generation_attempts"
    __table_args__ = (
        UniqueConstraint("asset_id", "attempt_number", name="uq_attempts_asset_id_attempt_number"),
        Index("idx_asset_attempts_asset_id", "asset_id"),
    )

    asset_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(attempt_status_enum, nullable=False)
    provider_request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    asset: Mapped["Asset"] = relationship(back_populates="attempts")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<AssetGenerationAttempt asset_id={self.asset_id} attempt_number={self.attempt_number} status={self.status}>"
