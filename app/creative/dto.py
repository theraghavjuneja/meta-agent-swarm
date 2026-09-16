"""Pydantic DTOs for Module 5 (``app/creative``).

Two kinds of models live here, and it matters which is which:

* ``GenerateSpecInput`` / ``GenerateSpecOutput`` are the activity's boundary DTOs.
* ``CreativeSpecSchema`` (and its nested pieces) is the exact structured shape we
  request from the LLM and validate the model's JSON output against, *before*
  anything is persisted. ``service.py`` is the only place that turns a validated
  ``CreativeSpecSchema`` into a ``CreativeSpec`` row.

``campaigns`` doesn't exist yet, so ``GenerateSpecInput`` carries the brief fields
the spec prompt needs directly, rather than loading a ``Campaign`` row.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "GenerateSpecInput",
    "GenerateSpecOutput",
    "UsageSummary",
    "ProductIdentity",
    "VideoBeat",
    "VideoOutline",
    "CreativeSpecSchema",
]

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")


# --------------------------------------------------------------------------- #
# Activity boundary DTOs
# --------------------------------------------------------------------------- #


class GenerateSpecInput(BaseModel):
    """Input to the ``generate_creative_spec`` activity.

    Brief fields are passed explicitly (rather than reached for on a ``Campaign``
    row) because ``app.campaigns`` does not exist in this pass.
    """

    model_config = ConfigDict(frozen=True)

    campaign_id: UUID
    angle_id: UUID

    product_name: str = Field(..., min_length=1)
    product_description: str = Field(..., min_length=1)
    target_audience: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    tone: str = Field(..., min_length=1)
    cta: str = Field(..., min_length=1)


class UsageSummary(BaseModel):
    """Cost/usage info for the LLM call that produced the spec.

    Not persisted anywhere yet — ``provider_usage`` doesn't exist until Module 7.
    This rides along on ``GenerateSpecOutput`` so the workflows module (Module 8)
    can persist it later without this activity's signature needing to change.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    operation: Literal["llm_completion"] = "llm_completion"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None
    is_estimated: bool = False


class GenerateSpecOutput(BaseModel):
    """Output of the ``generate_creative_spec`` activity."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    version: int
    is_valid: bool
    usage: UsageSummary


# --------------------------------------------------------------------------- #
# Structured-output schema requested from / validated against the LLM
# --------------------------------------------------------------------------- #


class ProductIdentity(BaseModel):
    """Small structured description of the product as it should read across assets."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    key_visual_traits: list[str] = Field(..., min_length=1, max_length=8)


class VideoBeat(BaseModel):
    """One beat in the video outline, e.g. hook frame / product frame / CTA frame."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


class VideoOutline(BaseModel):
    """A short ordered list of beats. Kept simple — this feeds Module 6's video pipeline."""

    model_config = ConfigDict(extra="forbid")

    beats: list[VideoBeat] = Field(..., min_length=2, max_length=6)


class CreativeSpecSchema(BaseModel):
    """The exact structured shape requested from and validated against the LLM's output.

    ``extra="forbid"`` is deliberate: it makes schema drift from the model surface
    as a validation failure (and therefore a corrective retry) instead of silently
    dropping fields.
    """

    model_config = ConfigDict(extra="forbid")

    hook: str = Field(..., min_length=1)
    approved_copy: str = Field(..., min_length=1)
    cta: str = Field(..., min_length=1)
    product_identity: ProductIdentity
    scene_description: str = Field(..., min_length=1)
    palette: list[str] = Field(..., min_length=1, max_length=8)
    composition_guidance: str = Field(..., min_length=1)
    video_outline: VideoOutline

    @field_validator("palette")
    @classmethod
    def _validate_hex_colors(cls, value: list[str]) -> list[str]:
        invalid = [v for v in value if not _HEX_COLOR_RE.match(v)]
        if invalid:
            raise ValueError(f"palette contains invalid hex color string(s): {invalid!r}")
        return value
