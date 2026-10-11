"""Pydantic v2 DTOs for the research package.

Every boundary of this module speaks in these types: the Temporal activity's input and
output, the step recorder's payload, and — importantly — the schema the LLM's final
structured output is validated against. No ORM object crosses a boundary.

``AngleSet`` is the contract for untrusted model output: if a hostile string inside a
fetched page persuades the model to emit a different shape, validation fails here rather
than that shape reaching the database.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AngleSet",
    "AngleSourceRef",
    "CreativeAngleDTO",
    "ResearchOutcome",
    "ResearchRequest",
    "ResearchStatus",
    "ResearchStepRecord",
    "ResearchSummary",
    "SourceRecord",
    "StepType",
    "StopReason",
]


class ResearchStatus(StrEnum):
    """Outcome status of a research run, as reported by the activity.

    Note this is *not* the DB enum: ``research_runs.status`` only distinguishes
    running/completed/failed. ``COMPLETED_WITH_GAPS`` still persists as ``completed``;
    the gap itself lives in ``ResearchOutcome.gap_note``.
    """

    COMPLETED = "completed"
    COMPLETED_WITH_GAPS = "completed_with_gaps"
    FAILED = "failed"


class StepType(StrEnum):
    """Mirrors the ``research_step_type`` Postgres enum."""

    SEARCH = "search"
    READ_PAGE = "read_page"
    DECIDE = "decide"


class StopReason(StrEnum):
    MODEL_FINISHED = "model_finished"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_ITERATIONS = "max_iterations"
    TIMEOUT = "timeout"
    LLM_ERROR = "llm_error"


# --------------------------------------------------------------------------------------
# Activity input
# --------------------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    """Input to ``run_research_agent``.

    Carries the campaign brief by value rather than re-reading a ``campaigns`` row,
    because the ``campaigns`` package does not exist yet and because an activity input
    that is self-contained is replayable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: UUID
    product_name: str = Field(min_length=1, max_length=255)
    product_description: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    objective: str = Field(min_length=1, max_length=255)
    tone: str = Field(min_length=1, max_length=100)
    cta: str | None = Field(default=None, max_length=255)
    extra_context: str | None = None


# --------------------------------------------------------------------------------------
# The angle schema — validated against untrusted model output
# --------------------------------------------------------------------------------------


class AngleSourceRef(BaseModel):
    """A citation on an angle. ``url`` is matched back to a ``research_sources`` row."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=512)


class CreativeAngleDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience_insight: str = Field(
        min_length=1,
        description="A specific, evidence-backed insight about the target audience.",
    )
    hook: str = Field(
        min_length=1,
        description="The one-line creative hook this angle leads with.",
    )
    visual_direction: str = Field(
        min_length=1,
        description="How the angle should look: scene, subject, mood, treatment.",
    )
    rationale: str = Field(
        min_length=1,
        description="Why this angle follows from the research gathered.",
    )
    sources: list[AngleSourceRef] = Field(
        min_length=1,
        description="The sources that support this angle. At least one is required.",
    )


class AngleSet(BaseModel):
    """The LLM's final structured output: up to three angles plus an optional gap note."""

    model_config = ConfigDict(extra="forbid")

    angles: list[CreativeAngleDTO] = Field(
        default_factory=list,
        max_length=3,
        description="Between zero and three creative angles, best first.",
    )
    gap_note: str | None = Field(
        default=None,
        description=(
            "Set when fewer than three well-sourced angles could be produced, "
            "explaining plainly what evidence was missing."
        ),
    )


# --------------------------------------------------------------------------------------
# Trace records
# --------------------------------------------------------------------------------------


class SourceRecord(BaseModel):
    """A successfully read page, destined for a ``research_sources`` row."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    excerpt: str
    accessed_at: datetime


class ResearchStepRecord(BaseModel):
    """One row of the observable trace, handed to the step recorder as it happens."""

    step_number: int = Field(ge=1)
    step_type: StepType
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    decision_summary: str = ""
    sources: list[SourceRecord] = Field(default_factory=list)
    tool_calls_used: int = Field(default=0, ge=0)


# --------------------------------------------------------------------------------------
# Activity output
# --------------------------------------------------------------------------------------


class ResearchSummary(BaseModel):
    """Flat, log-friendly summary of how the run actually went."""

    stop_reason: StopReason
    iterations: int = 0
    tool_calls_used: int = 0
    tool_call_budget: int = 0
    sources_found: int = 0
    angles_produced: int = 0
    model_used: str = ""
    mock_mode: bool = False
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class ResearchOutcome(BaseModel):
    """Output of ``run_research_agent``."""

    status: ResearchStatus
    research_run_id: UUID
    campaign_id: UUID
    angles: list[CreativeAngleDTO] = Field(default_factory=list)
    angle_ids: list[UUID] = Field(default_factory=list)
    gap_note: str | None = None
    summary: ResearchSummary
    error: str | None = None


def angle_set_json_schema() -> dict[str, Any]:
    """JSON Schema handed to ``LLMPort.structured_output`` for the final answer."""
    return AngleSet.model_json_schema()


StepTypeLiteral = Literal["search", "read_page", "decide"]