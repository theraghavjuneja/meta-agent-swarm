"""SQLAlchemy 2.0 model for the ``creative_specs`` table.

One table: ``creative_specs``. Rows are append-only — a new spec generation
inserts the next version for a campaign, it never updates an existing row
(mirrors the ``research_runs`` "re-run inserts a new row" convention).

``campaign_id`` is a plain indexed UUID column for the same reason it is on
``research_runs``: the ``campaigns`` table does not exist yet, so no ``ForeignKey``
can be declared here. It becomes ``REFERENCES campaigns(id) ON DELETE CASCADE``
once the ``campaigns`` module's migration lands.

``angle_id`` *can* be a real ``ForeignKey`` today, since ``creative_angles``
already exists (Module 4 / ``app.research``).

Composed from ``Base + IdMixin`` only (no ``TimestampMixin``): these rows are
never updated, so there is no ``updated_at`` to track — ``created_at`` is added
directly, the same way ``ResearchStep``/``ResearchSource`` do it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin

__all__ = ["CreativeSpec"]


class CreativeSpec(Base, IdMixin):
    """One versioned creative specification for a campaign."""

    __tablename__ = "creative_specs"
    __table_args__ = (
        sa.UniqueConstraint(
            "campaign_id", "version", name="uq_creative_specs_campaign_version"
        ),
        sa.Index("idx_creative_specs_campaign_id", "campaign_id"),
    )

    # No ForeignKey yet — see module docstring. Indexed for the same reason
    # research_runs.campaign_id is indexed: it's the lookup key even without an FK.
    campaign_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    angle_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("creative_angles.id"),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_valid: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true()
    )

    hook: Mapped[str] = mapped_column(sa.Text, nullable=False)
    approved_copy: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cta: Mapped[str] = mapped_column(sa.Text, nullable=False)

    # {"name": str, "key_visual_traits": [str, ...]} — CreativeSpecSchema.product_identity
    product_identity: Mapped[dict] = mapped_column(JSONB, nullable=False)

    scene_description: Mapped[str] = mapped_column(sa.Text, nullable=False)

    # list[str] of "#rrggbb"/"#rgb" hex colors — CreativeSpecSchema.palette
    palette: Mapped[list] = mapped_column(JSONB, nullable=False)

    composition_guidance: Mapped[str] = mapped_column(sa.Text, nullable=False)

    # {"beats": [{"label": str, "description": str}, ...]} — CreativeSpecSchema.video_outline
    video_outline: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # "modern_clean" | "editorial_serif" | "bold_athletic" — picks the overlay type
    # system and the hero prompt's art direction (app/assets/compositing.py, prompts.py).
    typography_style: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default="modern_clean"
    )

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
