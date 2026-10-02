from __future__ import  annotations

import  uuid

from datetime import  datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func



# Naming convention for constraints/indexes so Alembic autogenerate produces
# stable, predictable names (e.g. "ix_campaigns_status") instead of
# driver-generated ones that vary between runs/dialects.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}



class Base(DeclarativeBase):
    """

    Shared declarative base. Every model inherits from this
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

class IdMixin:
    """
    UUID primary key generated on Python side.
        Using `default=uuid.uuid4` (rather than a Postgres-side
    `gen_random_uuid()` server default) avoids a hard dependency on the
    `pgcrypto` extension being enabled on every environment this runs
    against. The id is still known before `INSERT`, which is convenient for
    building response DTOs without a round-trip.

    """
    id:Mapped[uuid.UUID]=mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

class TimeStampMixin:
    created_at:Mapped[datetime]=mapped_column(
        server_default=func.now(),
        nullable=False
    )

    updated_at:Mapped[datetime]=mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False

    )


class CreatedAtMixin:
    """`created_at` only, for append-only tables (e.g. stage_events,
    provider_usage) that are never updated after insert."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
