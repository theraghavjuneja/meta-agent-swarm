"""Fixture LLM adapter: a canned, deterministic script that exercises the whole loop.

The script is: search → read page → read page → "I'm done" → structured three angles. That
is enough to drive every branch of ``ResearchLoop`` end-to-end with no network access and
no credentials.

Everything it returns is explicitly labelled ``[FIXTURE]`` so sample data can never be
mistaken for live research in the UI, the database, or a demo.

``fail_times`` implements the reproducible-failure-on-demand requirement: the first N calls
raise ``InfrastructureError`` before the script proceeds normally. It is a real capability
of the adapter, callable from the running system, not a test double.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.research.ports import (
    LLMMessage,
    LLMTurn,
    StructuredOutput,
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
)

__all__ = ["FIXTURE_ANGLES", "FixtureLLMAdapter"]

logger = get_logger(__name__)

FIXTURE_SOURCE_A = "https://example.com/fixtures/sleep-research-overview"
FIXTURE_SOURCE_B = "https://example.com/fixtures/consumer-wellness-trends"
FIXTURE_SOURCE_C = "https://example.com/fixtures/evening-routine-forum"


@dataclass(frozen=True)
class _ScriptedTurn:
    text: str
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)


_SCRIPT: tuple[_ScriptedTurn, ...] = (
    _ScriptedTurn(
        text=(
            "[FIXTURE] Starting broad: I need to know what this audience actually "
            "complains about before I can pick an angle."
        ),
        tool_name="web_search",
        tool_input={"query": "sleep quality complaints young professionals", "max_results": 3},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] The overview result looks like the most substantive of the three, "
            "so I'll read it in full for a concrete audience insight."
        ),
        tool_name="read_page",
        tool_input={"url": FIXTURE_SOURCE_A},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] That gave me the anxiety-about-sleep framing. Now I want the "
            "market-side view so at least one angle is grounded in category trends."
        ),
        tool_name="read_page",
        tool_input={"url": FIXTURE_SOURCE_B},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] I have the emotional and the category view. One more read on how "
            "this audience talks about its own evenings, in its own words."
        ),
        tool_name="read_page",
        tool_input={"url": FIXTURE_SOURCE_C},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] Three solid sources covering the emotional, category and "
            "behavioural angles. That is enough to write three distinct angles."
        ),
    ),
)


FIXTURE_ANGLES: dict[str, Any] = {
    "angles": [
        {
            "audience_insight": (
                "[FIXTURE] This audience does not describe itself as tired; it describes "
                "itself as unable to switch off. The problem is framed as mental, not "
                "physical."
            ),
            "hook": "[FIXTURE] Your body clocked out hours ago. Your head didn't.",
            "visual_direction": (
                "[FIXTURE] A dark bedroom lit only by a phone screen, the product resting "
                "unlit on the nightstand; cool blues resolving into warm amber."
            ),
            "rationale": (
                "[FIXTURE] Leads with the audience's own language about racing thoughts "
                "rather than generic tiredness messaging."
            ),
            "sources": [{"url": FIXTURE_SOURCE_A, "title": "[FIXTURE] Sleep research overview"}],
        },
        {
            "audience_insight": (
                "[FIXTURE] Buyers in this category distrust claims that sound clinical "
                "but carry no evidence, and actively look for what a product will not do."
            ),
            "hook": "[FIXTURE] No miracle. Just the twenty minutes before sleep, handled.",
            "visual_direction": (
                "[FIXTURE] Flat daylight, plain surface, product shot with a single honest "
                "line of copy; deliberately unglamorous."
            ),
            "rationale": (
                "[FIXTURE] Turns the category's credibility problem into the campaign's "
                "differentiator by underclaiming on purpose."
            ),
            "sources": [
                {"url": FIXTURE_SOURCE_B, "title": "[FIXTURE] Consumer wellness trends"}
            ],
        },
        {
            "audience_insight": (
                "[FIXTURE] Evening routine is treated as the one controllable part of a "
                "day that otherwise belongs to other people."
            ),
            "hook": "[FIXTURE] The first thing today that was actually your idea.",
            "visual_direction": (
                "[FIXTURE] Close, tactile shots of a short ritual — kettle, lamp, product "
                "— in warm low light, no faces until the final frame."
            ),
            "rationale": (
                "[FIXTURE] Positions the product as autonomy rather than sleep aid, which "
                "both sources support."
            ),
            "sources": [
                {"url": FIXTURE_SOURCE_C, "title": "[FIXTURE] Community thread: evening routines"},
                {"url": FIXTURE_SOURCE_A, "title": "[FIXTURE] Sleep research overview"},
            ],
        },
    ],
    "gap_note": None,
}


class FixtureLLMAdapter:
    """Deterministic ``LLMPort`` implementation. No network, no credentials."""

    def __init__(self, *, model_name: str = "fixture-model", fail_times: int = 0) -> None:
        self._model_name = model_name
        self._fail_budget = max(0, fail_times)
        self._turn_index = 0
        self._turn_calls = 0
        self._structured_calls = 0

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def turn_calls(self) -> int:
        return self._turn_calls

    def _maybe_fail(self, operation: str) -> None:
        if self._fail_budget > 0:
            self._fail_budget -= 1
            logger.warning(
                "research.fixture.forced_failure",
                adapter="llm",
                operation=operation,
            )
            raise InfrastructureError(
                f"[FIXTURE] Forced failure on {operation} (reproducible-failure mode)."
            )

    async def next_turn(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMTurn:
        self._turn_calls += 1
        self._maybe_fail("next_turn")

        if self._turn_index >= len(_SCRIPT):
            return LLMTurn(
                text="[FIXTURE] Script exhausted; concluding.",
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=120, output_tokens=40),
            )

        scripted = _SCRIPT[self._turn_index]
        self._turn_index += 1
        tool_uses: list[ToolUseBlock] = []
        if scripted.tool_name is not None:
            tool_uses.append(
                ToolUseBlock(
                    id=f"fixture_tool_{self._turn_index}",
                    name=scripted.tool_name,
                    input=dict(scripted.tool_input),
                )
            )
        return LLMTurn(
            text=scripted.text,
            tool_uses=tool_uses,
            stop_reason="tool_use" if tool_uses else "end_turn",
            usage=TokenUsage(input_tokens=600, output_tokens=90),
        )

    async def structured_output(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        schema: dict[str, Any],
        schema_name: str,
        schema_description: str = "",
    ) -> StructuredOutput:
        self._structured_calls += 1
        self._maybe_fail("structured_output")
        return StructuredOutput(
            data={
                "angles": [dict(a) for a in FIXTURE_ANGLES["angles"]],
                "gap_note": FIXTURE_ANGLES["gap_note"],
            },
            usage=TokenUsage(input_tokens=900, output_tokens=420),
        )