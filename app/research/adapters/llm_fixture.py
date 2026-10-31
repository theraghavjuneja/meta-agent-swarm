"""Fixture LLM adapter: a canned, deterministic script that exercises the whole loop.

The script follows the harness in ``ResearchLoop``: a canned research plan, then
search → read → read → read → "I'm done", with canned per-page findings (whose quotes
really occur in the fixture pages, so they pass verification) and angles that cite those
findings by id. That drives every phase end-to-end with no network and no credentials.

Everything it returns is explicitly labelled ``[FIXTURE]`` so sample data can never be
mistaken for live research in the UI, the database, or a demo.

``fail_times`` implements the reproducible-failure-on-demand requirement: the first N calls
raise ``InfrastructureError`` before the script proceeds normally. It is a real capability
of the adapter, callable from the running system, not a test double.
"""

from __future__ import annotations

import copy
import re
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

__all__ = ["FIXTURE_ANGLES", "FIXTURE_PAGE_FINDINGS", "FIXTURE_PLAN", "FixtureLLMAdapter"]

logger = get_logger(__name__)

FIXTURE_SOURCE_A = "https://example.com/fixtures/sleep-research-overview"
FIXTURE_SOURCE_B = "https://example.org/fixtures/consumer-wellness-trends"
FIXTURE_SOURCE_C = "https://example.net/fixtures/evening-routine-forum"


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
        tool_input={
            "lens": "audience_voice",
            "query": "sleep quality complaints young professionals",
            "max_results": 3,
        },
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] The overview result looks like the most substantive of the three, "
            "so I'll read it in full for a concrete audience insight."
        ),
        tool_name="read_page",
        tool_input={"lens": "audience_voice", "url": FIXTURE_SOURCE_A},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] That gave me the anxiety-about-sleep framing. Now I want the "
            "market-side view so at least one angle is grounded in category trends."
        ),
        tool_name="read_page",
        tool_input={"lens": "objections", "url": FIXTURE_SOURCE_B},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] I have the emotional and the category view. One more read on how "
            "this audience talks about its own evenings, in its own words."
        ),
        tool_name="read_page",
        tool_input={"lens": "usage_moments", "url": FIXTURE_SOURCE_C},
    ),
    _ScriptedTurn(
        text=(
            "[FIXTURE] Three solid sources covering the emotional, category and "
            "behavioural angles. That is enough to write three distinct angles."
        ),
    ),
)


FIXTURE_PLAN: dict[str, Any] = {
    "category": "[FIXTURE] sleep ritual product",
    "anchor_terms": ["sleep", "evening routine", "wellness", "switch off"],
    "questions": [
        {
            "lens": "audience_voice",
            "question": "[FIXTURE] How do young professionals describe bad nights in their own words?",
            "why_it_matters": "[FIXTURE] The hook should use their phrasing, not ours.",
        },
        {
            "lens": "objections",
            "question": "[FIXTURE] What makes them distrust products in this category?",
            "why_it_matters": "[FIXTURE] One angle can pre-empt the credibility objection.",
        },
        {
            "lens": "usage_moments",
            "question": "[FIXTURE] What does the hour before bed actually look like for them?",
            "why_it_matters": "[FIXTURE] Gives the ad a concrete scene to show.",
        },
    ],
}

# Keyed by page URL. Every quote occurs verbatim in the matching fixture page, so the
# harness's quote verification passes; the trends page's embedded injection is ignored.
FIXTURE_PAGE_FINDINGS: dict[str, dict[str, Any]] = {
    FIXTURE_SOURCE_A: {
        "relevant": True,
        "relevance_note": "[FIXTURE] First-hand survey language about sleeplessness.",
        "findings": [
            {
                "lens": "audience_voice",
                "observation": "[FIXTURE] Respondents frame the problem as not switching off, not tiredness.",
                "quote": 'the recurring phrase is not "I\'m tired" but "I can\'t switch off"',
            },
            {
                "lens": "usage_moments",
                "observation": "[FIXTURE] Evening routines are the first thing dropped on a long day.",
                "quote": "evening routines are abandoned first when the day overruns",
            },
        ],
    },
    FIXTURE_SOURCE_B: {
        "relevant": True,
        "relevance_note": (
            "[FIXTURE] Category buyer scepticism; the page also contains an embedded "
            "instruction, which was ignored."
        ),
        "findings": [
            {
                "lens": "objections",
                "observation": "[FIXTURE] Shoppers look for what a product does not promise.",
                "quote": "Shoppers report checking what a product explicitly does not promise",
            },
        ],
    },
    FIXTURE_SOURCE_C: {
        "relevant": True,
        "relevance_note": "[FIXTURE] Audience's own description of the pre-bed hour.",
        "findings": [
            {
                "lens": "usage_moments",
                "observation": "[FIXTURE] The hour before bed is felt as the only unclaimed time.",
                "quote": "the hour before bed as the only part of the day nobody else has a claim on",
            },
        ],
    },
}

_PAGE_URL_RE = re.compile(r'<source_content type="page" url="([^"]+)"')


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
            "finding_ids": ["F1", "F2"],
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
            "finding_ids": ["F3"],
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
            "finding_ids": ["F4", "F2"],
        },
    ],
    "gap_note": None,
}


FIXTURE_CREATIVE_SPEC: dict[str, Any] = {
    "hook": "[FIXTURE] Your body clocked out hours ago. Your head didn't.",
    "approved_copy": (
        "[FIXTURE] The twenty minutes before sleep, finally handled. No miracle claims — "
        "just a short ritual that gives your mind somewhere to land."
    ),
    "cta": "[FIXTURE] Shop the ritual",
    "product_identity": {
        "name": "[FIXTURE] Product",
        "key_visual_traits": ["[FIXTURE] matte finish", "[FIXTURE] warm amber accent"],
    },
    "scene_description": (
        "[FIXTURE] A dark bedroom lit only by a phone screen, the product resting unlit "
        "on the nightstand; cool blues resolving into warm amber."
    ),
    "palette": ["#0B1E3D", "#F5A623"],
    "composition_guidance": (
        "[FIXTURE] Close, low-angle shot; product in the lower third; negative space above "
        "for hook copy."
    ),
    "video_outline": {
        "beats": [
            {"label": "[FIXTURE] Hook", "description": "[FIXTURE] Dark room, phone glow, restless energy."},
            {"label": "[FIXTURE] Product", "description": "[FIXTURE] Product resting on nightstand, warm light rises."},
            {"label": "[FIXTURE] CTA", "description": "[FIXTURE] Logo and CTA card on warm amber background."},
        ]
    },
    "typography_style": "modern_clean",
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
        if schema_name == "research_plan":
            return StructuredOutput(
                data=copy.deepcopy(FIXTURE_PLAN),
                usage=TokenUsage(input_tokens=400, output_tokens=220),
            )
        if schema_name == "page_findings":
            url = _last_page_url(messages)
            data = FIXTURE_PAGE_FINDINGS.get(url or "")
            return StructuredOutput(
                data=copy.deepcopy(data)
                if data is not None
                else {
                    "relevant": False,
                    "relevance_note": "[FIXTURE] No canned findings for this page.",
                    "findings": [],
                },
                usage=TokenUsage(input_tokens=1200, output_tokens=160),
            )
        if schema_name == "creative_angles":
            return StructuredOutput(
                data=copy.deepcopy(FIXTURE_ANGLES),
                usage=TokenUsage(input_tokens=900, output_tokens=420),
            )
        if schema_name == "CreativeSpecSchema":
            return StructuredOutput(
                data=dict(FIXTURE_CREATIVE_SPEC),
                usage=TokenUsage(input_tokens=700, output_tokens=260),
            )
        raise NotImplementedError(
            f"[FIXTURE] No fixture data registered for schema_name={schema_name!r}."
        )


def _last_page_url(messages: Sequence[LLMMessage]) -> str | None:
    for message in reversed(messages):
        for block in message.content if isinstance(message.content, list) else []:
            match = _PAGE_URL_RE.search(getattr(block, "text", "") or "")
            if match:
                return match.group(1).replace("&quot;", '"')
    return None
