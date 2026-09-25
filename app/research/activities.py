"""The ``run_research_agent`` Temporal activity.

Composition root for a research run: resolve adapters, create the run row, drive the loop
with a DB-backed recorder, persist the angles, and only then mark the run complete.

Ordering matters and is deliberate:

1. ``research_runs`` row first, so every step written afterwards has a parent.
2. Steps are written by the recorder as the loop executes, not here.
3. Angles are persisted **before** the run is marked ``completed``. The status is never set
   optimistically — a crash between the loop finishing and the angles landing leaves the
   run ``running`` with a full trace, which is recoverable and honest, rather than
   ``completed`` with nothing to show.

Budget exhaustion is not a failure: the activity returns ``completed_with_gaps`` with a
gap note. Only genuine infrastructure failure marks the run ``failed`` and re-raises so
Temporal's own retry policy can take over.

No workflow calls this yet — it is directly awaitable, and the ``workflows`` module will
wire it up later.
"""

from __future__ import annotations

import time
from decimal import Decimal

from temporalio import activity

from app.common.logging import get_logger
from app.config import get_settings
from app.research.adapters import build_research_adapters
from app.research.dto import (
    ResearchOutcome,
    ResearchRequest,
    ResearchStatus,
    ResearchSummary,
)
from app.research.loop import LoopResult, ResearchLoop
from app.research.ports import TokenUsage
from app.research.repository import (
    DbStepRecorder,
    complete_research_run,
    create_research_run,
    fail_research_run,
    persist_angles,
)

__all__ = ["run_research_agent"]

logger = get_logger(__name__)

#: Nominal per-token estimates, deliberately rough — usage tracking proper lands with the
#: ``provider_usage`` table in a later module.
_INPUT_COST_PER_1K = Decimal("0.003")
_OUTPUT_COST_PER_1K = Decimal("0.015")
_FIXTURE_NOMINAL_COST = Decimal("0.0000")



@activity.defn(name="run_research_agent")
async def run_research_agent(request: ResearchRequest) -> ResearchOutcome:
    settings = get_settings()

    adapters = build_research_adapters(settings)
    max_tool_calls = int(getattr(settings, "research_max_tool_calls", 8))
    max_iterations = int(getattr(settings, "research_max_iterations", 10))
    timeout_seconds = float(getattr(settings, "research_timeout_seconds", 180))

    research_run_id = await create_research_run(
        campaign_id=request.campaign_id,
        tool_call_budget=max_tool_calls,
        model_used=adapters.llm.model_name,
        mock_mode=adapters.mock_mode,
    )

    logger.info(
        "research.activity.started",
        campaign_id=str(request.campaign_id),
        research_run_id=str(research_run_id),
        provider_mode=getattr(settings, "provider_mode", "fixture"),
        tool_call_budget=max_tool_calls,
    )

    loop = ResearchLoop(
        llm=adapters.llm,
        search=adapters.search,
        page_reader=adapters.page_reader,
        recorder=DbStepRecorder(research_run_id),
        max_tool_calls=max_tool_calls,
        max_iterations=max_iterations,
        timeout_seconds=timeout_seconds,
        min_sources=int(getattr(settings, "research_min_sources", 3)),
        max_reads_per_domain=int(getattr(settings, "research_max_reads_per_domain", 2)),
        extra_blocked_domains=list(getattr(settings, "research_excluded_domains", []) or []),
    )

    started = time.monotonic()
    try:
        result = await loop.run(request)
        angle_ids = await persist_angles(research_run_id, result.angles)
    except Exception as exc:
        logger.error(
            "research.activity.failed",
            research_run_id=str(research_run_id),
            error=repr(exc),
        )
        await fail_research_run(research_run_id)
        raise

    cost = _estimate_cost(result.usage, mock_mode=adapters.mock_mode)
    await complete_research_run(
        research_run_id,
        tool_calls_used=result.tool_calls_used,
        estimated_cost_usd=cost,
    )

    outcome = ResearchOutcome(
        status=(
            ResearchStatus.COMPLETED
            if len(result.angles) == 3 and not result.gap_note
            else ResearchStatus.COMPLETED_WITH_GAPS
        ),
        research_run_id=research_run_id,
        campaign_id=request.campaign_id,
        angles=result.angles,
        angle_ids=angle_ids,
        gap_note=result.gap_note,
        summary=_summarise(
            result=result,
            model_used=adapters.llm.model_name,
            mock_mode=adapters.mock_mode,
            tool_call_budget=max_tool_calls,
            duration_seconds=time.monotonic() - started,
        ),
    )
    logger.info(
        "research.activity.completed",
        research_run_id=str(research_run_id),
        status=outcome.status.value,
        angles=len(outcome.angles),
        steps_recorded=result.steps_recorded,
    )
    return outcome


def _summarise(
    *,
    result: LoopResult,
    model_used: str,
    mock_mode: bool,
    tool_call_budget: int,
    duration_seconds: float,
) -> ResearchSummary:
    return ResearchSummary(
        stop_reason=result.stop_reason,
        iterations=result.iterations,
        tool_calls_used=result.tool_calls_used,
        tool_call_budget=tool_call_budget,
        sources_found=len(result.sources),
        angles_produced=len(result.angles),
        model_used=model_used,
        mock_mode=mock_mode,
        duration_seconds=round(duration_seconds, 3),
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


def _estimate_cost(usage: TokenUsage, *, mock_mode: bool) -> Decimal:
    if mock_mode:
        return _FIXTURE_NOMINAL_COST
    estimate = (
        Decimal(usage.input_tokens) / 1000 * _INPUT_COST_PER_1K
        + Decimal(usage.output_tokens) / 1000 * _OUTPUT_COST_PER_1K
    )
    return estimate.quantize(Decimal("0.0001"))