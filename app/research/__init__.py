"""Module 4 — the research agent.

Public surface, in the order a caller meets it:

    from app.research import ResearchRequest
    from app.research.activities import run_research_agent

    outcome = await run_research_agent(
        ResearchRequest(campaign_id=..., product_name=..., ...)
    )

Everything else (ports, adapters, the loop, the repository) is wiring that the activity
composes. Imports here stay lazy-free and side-effect-free: importing the package must not
construct a provider client or touch the database.
"""

from app.research.dto import (
    AngleSet,
    CreativeAngleDTO,
    ResearchOutcome,
    ResearchRequest,
    ResearchStatus,
    ResearchStepRecord,
    ResearchSummary,
    SourceRecord,
    StepType,
    StopReason,
)
from app.research.loop import LoopResult, NullStepRecorder, ResearchLoop, StepRecorder
from app.research.ports import (
    LLMPort,
    PageContent,
    PageReaderPort,
    SearchResult,
    WebSearchPort,
)

__all__ = [
    "AngleSet",
    "CreativeAngleDTO",
    "LLMPort",
    "LoopResult",
    "NullStepRecorder",
    "PageContent",
    "PageReaderPort",
    "ResearchLoop",
    "ResearchOutcome",
    "ResearchRequest",
    "ResearchStatus",
    "ResearchStepRecord",
    "ResearchSummary",
    "SearchResult",
    "SourceRecord",
    "StepRecorder",
    "StepType",
    "StopReason",
    "WebSearchPort",
]