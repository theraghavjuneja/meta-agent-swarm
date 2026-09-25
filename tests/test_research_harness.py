"""Task 1: the research harness stays on task, verifies evidence, and stays observable.

A scripted LLM plays the agent so each gate can be exercised with the exact off-track
behaviour seen in real runs (background queries, news sources, invented URLs, fabricated
quotes), for more than one vertical.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from app.common.exceptions import InfrastructureError
from app.research.adapters import FixtureLLMAdapter, FixturePageReaderAdapter, FixtureSearchAdapter
from app.research.dto import Lens, ResearchRequest, SourceType, StepType, StopReason
from app.research.loop import ResearchLoop
from app.research.ports import (
    LLMMessage,
    LLMTurn,
    PageContent,
    SearchResult,
    StructuredOutput,
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
)
from app.research.source_policy import check_query, classify_source, screen_results

# --------------------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------------------

PROTEIN_PLAN = {
    "category": "protein powder",
    "anchor_terms": ["protein powder", "whey", "protein shake", "gym"],
    "questions": [
        {"lens": "audience_voice", "question": "How do busy gym-goers talk about getting enough protein?", "why_it_matters": "hook language"},
        {"lens": "objections", "question": "What puts them off protein powders?", "why_it_matters": "pre-empt doubts"},
        {"lens": "usage_moments", "question": "When do they actually drink a shake?", "why_it_matters": "the scene"},
    ],
}

REDDIT = "https://www.reddit.com/r/fitness/comments/abc/protein_shake_taste"
REVIEWS = "https://www.trustpilot.com/review/some-protein-brand"
FORUM = "https://forum.bodybuilding-community.org/threads/post-workout-shake"
TOI = "https://timesofindia.indiatimes.com/life-style/health/protein-powder-news"
WIKI = "https://en.wikipedia.org/wiki/Whey_protein"

PAGES = {
    REDDIT: "Honestly the chalky taste of most protein powder is why I quit. I drink a shake in the car after the gym because I have 10 minutes.",
    REVIEWS: "Mixes clean, no clumps. I stopped buying whey that upset my stomach.",
    FORUM: "Post workout shake is the only protein I reliably get on weekdays at the gym.",
    TOI: "Company X announced quarterly results for its protein powder division.",
}


class ScriptedLLM:
    """next_turn follows a script; structured_output dispatches on schema_name."""

    def __init__(self, turns: Sequence[tuple[str, list[tuple[str, dict[str, Any]]]]], *, plan=PROTEIN_PLAN,
                 findings: Callable[[str], dict[str, Any]] | None = None,
                 angles: dict[str, Any] | None = None, plan_fails: bool = False) -> None:
        self._turns = list(turns)
        self._plan = plan
        self._findings = findings or _default_findings
        self._angles = angles
        self._plan_fails = plan_fails
        self.synthesis_prompt: str | None = None

    @property
    def model_name(self) -> str:
        return "scripted"

    async def next_turn(self, *, system: str, messages: Sequence[LLMMessage], tools: Sequence[ToolDefinition]) -> LLMTurn:
        if not self._turns:
            return LLMTurn(text="done", stop_reason="end_turn")
        text, calls = self._turns.pop(0)
        uses = [ToolUseBlock(id=f"t{uuid.uuid4().hex[:6]}", name=n, input=i) for n, i in calls]
        return LLMTurn(text=text, tool_uses=uses, stop_reason="tool_use" if uses else "end_turn")

    async def structured_output(self, *, system: str, messages: Sequence[LLMMessage], schema: dict[str, Any],
                                schema_name: str, schema_description: str = "") -> StructuredOutput:
        prompt = messages[-1].content[0].text
        if schema_name == "research_plan":
            if self._plan_fails:
                raise InfrastructureError("planner down")
            return StructuredOutput(data=self._plan, usage=TokenUsage())
        if schema_name == "page_findings":
            url = prompt.split('<source_content type="page" url="', 1)[1].split('"', 1)[0]
            return StructuredOutput(data=self._findings(url), usage=TokenUsage())
        if schema_name == "creative_angles":
            self.synthesis_prompt = prompt
            return StructuredOutput(data=self._angles or _angles_citing("F1", "F2", "F3"), usage=TokenUsage())
        raise AssertionError(schema_name)


def _default_findings(url: str) -> dict[str, Any]:
    text = PAGES.get(url, "")
    if url == TOI:
        return {"relevant": False, "relevance_note": "Company financial news.", "findings": []}
    quote = text.split(".")[0]
    return {
        "relevant": True,
        "relevance_note": "Audience describing the category.",
        "findings": [{"lens": "audience_voice" if url == REDDIT else "objections" if url == REVIEWS else "usage_moments",
                      "observation": "What the page says.", "quote": quote}],
    }


def _angles_citing(*ids: str) -> dict[str, Any]:
    return {
        "angles": [
            {"audience_insight": f"insight {i}", "hook": f"Hook {i}", "visual_direction": "One shaker on a car seat.",
             "rationale": "because", "finding_ids": [i]}
            for i in ids
        ],
        "gap_note": None,
    }


class FakeSearch:
    def __init__(self, results: Sequence[SearchResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def search(self, query: str, max_results: int = 5, *, exclude_domains: Sequence[str] = ()) -> list[SearchResult]:
        self.calls.append((query, tuple(exclude_domains)))
        return self.results[:max_results]


class FakeReader:
    async def read(self, url: str) -> PageContent:
        return PageContent(url=url, title=f"Title of {url}", extracted_text=PAGES.get(url, "nothing here"))


class Recorder:
    def __init__(self) -> None:
        self.steps = []

    async def record_step(self, step) -> None:
        self.steps.append(step)


def _results() -> list[SearchResult]:
    return [
        SearchResult(url=TOI, title="Protein powder maker posts results", snippet="protein powder business news", score=0.99),
        SearchResult(url=WIKI, title="Whey protein - Wikipedia", snippet="Whey protein is a mixture", score=0.98),
        SearchResult(url=REDDIT, title="Protein powder that doesn't taste chalky?", snippet="protein shake taste thread", score=0.9),
        SearchResult(url=REVIEWS, title="Reviews of a whey brand", snippet="whey reviews", score=0.8),
        SearchResult(url=FORUM, title="Post workout protein shake routine", snippet="gym shake", score=0.7),
        SearchResult(url="https://example.com/knitting", title="Knitting patterns", snippet="yarn", score=0.95),
    ]


def _request(**overrides) -> ResearchRequest:
    fields = dict(campaign_id=uuid.uuid4(), product_name="Beast Whey", product_description="Whey protein powder, 25 g per scoop",
                  target_audience="busy gym-goers", objective="launch", tone="energetic", cta="Explore the range")
    fields.update(overrides)
    return ResearchRequest(**fields)


def _run(llm, search=None, **loop_kwargs):
    recorder = Recorder()
    loop = ResearchLoop(llm=llm, search=search or FakeSearch(_results()), page_reader=FakeReader(), recorder=recorder,
                        max_tool_calls=loop_kwargs.pop("max_tool_calls", 20), max_iterations=loop_kwargs.pop("max_iterations", 8),
                        timeout_seconds=30, **loop_kwargs)
    return asyncio.run(loop.run(_request())), recorder.steps


def _search(lens: str, query: str) -> tuple[str, dict[str, Any]]:
    return ("web_search", {"lens": lens, "query": query})


def _read(lens: str, url: str) -> tuple[str, dict[str, Any]]:
    return ("read_page", {"lens": lens, "url": url})


GOOD_TURNS = [
    ("Start with how the audience talks.", [_search("audience_voice", "protein powder taste complaints reddit")]),
    ("Read the three candid sources.", [_read("audience_voice", REDDIT), _read("objections", REVIEWS), _read("usage_moments", FORUM)]),
    ("Plan covered.", []),
]

# --------------------------------------------------------------------------------------
# Source policy (pure, vertical-agnostic)
# --------------------------------------------------------------------------------------


def test_background_queries_rejected_in_any_vertical():
    cases = [
        (["perfume", "fragrance"], "how are perfumes made"),
        (["perfume", "fragrance"], "perfume ingredients list"),
        (["protein powder", "whey"], "how is whey protein manufactured"),
        (["protein powder", "whey"], "history of protein powder"),
        (["running shoes"], "what is a running shoe"),
        (["project management software"], "project management software ceo acquisition"),
    ]
    for anchors, query in cases:
        assert check_query(query, anchors) is not None, query


def test_audience_queries_allowed_in_any_vertical():
    cases = [
        (["perfume", "fragrance"], "perfume longevity complaints reddit"),
        (["protein powder", "whey"], "what are gym goers biggest complaints about protein powder taste"),
        (["running shoes"], "running shoes blisters beginners forum"),
    ]
    for anchors, query in cases:
        assert check_query(query, anchors) is None, query


def test_off_anchor_query_rejected():
    assert "anchor" in check_query("celebrity gym outfits", ["protein powder", "whey"])


def test_classify_sources():
    assert classify_source(TOI) is SourceType.NEWS
    assert classify_source(WIKI) is SourceType.REFERENCE
    assert classify_source(REDDIT) is SourceType.COMMUNITY
    assert classify_source(REVIEWS) is SourceType.REVIEWS
    assert classify_source(FORUM) is SourceType.COMMUNITY
    assert classify_source("https://www.youtube.com/watch?v=1") is SourceType.SOCIAL_VIDEO


def test_screening_drops_news_reference_and_off_topic():
    screened = screen_results(_results(), anchors=["protein powder", "whey", "protein shake", "gym"],
                              blocked_types={SourceType.NEWS, SourceType.REFERENCE, SourceType.SOCIAL_VIDEO})
    kept = [s.result.url for s in screened if s.kept]
    assert kept == [REDDIT, REVIEWS, FORUM]
    reasons = {s.result.url: s.reason for s in screened if not s.kept}
    assert "news" in reasons[TOI] and "reference" in reasons[WIKI] and "off-topic" in reasons["https://example.com/knitting"]


# --------------------------------------------------------------------------------------
# Harness behaviour
# --------------------------------------------------------------------------------------


def test_happy_path_is_planned_scoped_verified_and_traced():
    llm = ScriptedLLM(GOOD_TURNS)
    search = FakeSearch(_results())
    result, steps = _run(llm, search)

    assert steps[0].step_type is StepType.DECIDE and steps[0].input["phase"] == "plan"
    assert steps[-1].input["phase"] == "synthesise"
    assert [s.step_number for s in steps] == list(range(1, len(steps) + 1))

    search_step = next(s for s in steps if s.step_type is StepType.SEARCH)
    assert search_step.input["lens"] == "audience_voice"
    assert {r["url"] for r in search_step.output["kept"]} == {REDDIT, REVIEWS, FORUM}
    assert TOI in {r["url"] for r in search_step.output["screened_out"]}
    assert "timesofindia.indiatimes.com" in search.calls[0][1]  # provider-side exclusion too

    assert len(result.sources) == 3
    assert all(s.excerpt.startswith('"') for s in result.sources)  # excerpt = verified quotes
    assert len(result.angles) == 3
    for angle in result.angles:
        assert angle.observations and angle.sources
        obs = angle.observations[0]
        assert obs.quote.lower() in PAGES[obs.source_url].lower()
    assert result.gap_note is None
    # The synthesiser saw findings, not raw page text.
    assert "<verified_findings>" in llm.synthesis_prompt


def test_background_query_is_rejected_and_recorded():
    turns = [("Learn how whey is made first.", [_search("audience_voice", "how is whey protein made")])] + GOOD_TURNS
    result, steps = _run(ScriptedLLM(turns))
    rejected = next(s for s in steps if s.step_type is StepType.SEARCH)
    assert rejected.output["error_kind"] == "query_out_of_scope"
    assert rejected.decision_summary == "Learn how whey is made first."
    assert len(result.sources) == 3  # recovered afterwards


def test_off_plan_lens_is_rejected():
    turns = [("Check trends.", [_search("creative_landscape", "protein powder ads")])] + GOOD_TURNS
    _, steps = _run(ScriptedLLM(turns))
    assert next(s for s in steps if s.step_type is StepType.SEARCH).output["error_kind"] == "off_plan_lens"


def test_cannot_read_urls_not_surfaced_by_search_or_screened_out():
    turns = [
        ("Search.", [_search("audience_voice", "protein powder taste reddit")]),
        ("Read news and an invented URL.", [_read("audience_voice", TOI), _read("audience_voice", "https://evil.example/whey")]),
    ] + GOOD_TURNS[1:]
    _, steps = _run(ScriptedLLM(turns))
    kinds = [s.output.get("error_kind") for s in steps if s.step_type is StepType.READ_PAGE]
    assert kinds[:2] == ["url_not_from_search", "url_not_from_search"]


def test_fabricated_quotes_are_discarded_and_page_not_counted():
    def findings(url: str) -> dict[str, Any]:
        if url == REVIEWS:
            return {"relevant": True, "relevance_note": "x", "findings": [
                {"lens": "objections", "observation": "made up", "quote": "this sentence is not on the page at all"}]}
        return _default_findings(url)

    result, steps = _run(ScriptedLLM(GOOD_TURNS, findings=findings, angles=_angles_citing("F1", "F2")))
    review_step = next(s for s in steps if s.step_type is StepType.READ_PAGE and s.input["url"] == REVIEWS)
    assert review_step.output["relevant"] is False
    assert review_step.output["discarded_findings"][0]["reason"] == "quote not found verbatim on page"
    assert REVIEWS not in {s.url for s in result.sources}


def test_angles_citing_unknown_findings_are_dropped_with_a_gap_note():
    result, _ = _run(ScriptedLLM(GOOD_TURNS, angles=_angles_citing("F1", "F99")))
    assert len(result.angles) == 1
    assert "cited no verified finding" in result.gap_note


def test_early_stop_is_sent_back_when_sources_are_short():
    turns = [
        ("Search.", [_search("audience_voice", "protein powder taste reddit")]),
        ("Read one.", [_read("audience_voice", REDDIT)]),
        ("I think that's enough.", []),
        ("OK, more.", [_read("objections", REVIEWS), _read("usage_moments", FORUM)]),
        ("Covered.", []),
    ]
    result, steps = _run(ScriptedLLM(turns))
    nudge = next(s for s in steps if s.input.get("phase") == "continue_check")
    assert "1 of 3" in nudge.output["reason"]
    assert len(result.sources) == 3


def test_domain_cap_forces_source_diversity():
    other = [SearchResult(url=f"https://www.reddit.com/r/fitness/{i}/protein_powder", title="protein powder thread", snippet="whey", score=0.5) for i in range(3)]
    turns = [
        ("Search.", [_search("audience_voice", "protein powder reddit")]),
        ("Read three reddit threads.", [_read("audience_voice", r.url) for r in other]),
    ]
    _, steps = _run(ScriptedLLM(turns), search=FakeSearch(other))
    reads = [s for s in steps if s.step_type is StepType.READ_PAGE]
    assert [s.output.get("error_kind") for s in reads] == [None, None, "domain_cap"]


def test_parallel_calls_respect_tool_budget():
    turns = [("Search a lot.", [_search("audience_voice", f"protein powder taste {i}") for i in range(5)])]
    result, steps = _run(ScriptedLLM(turns), max_tool_calls=3)
    assert result.tool_calls_used == 3
    assert result.stop_reason is StopReason.MAX_TOOL_CALLS
    assert len([s for s in steps if s.step_type is StepType.SEARCH]) == 3


def test_planner_failure_falls_back_to_generic_plan():
    result, steps = _run(ScriptedLLM(GOOD_TURNS, plan_fails=True))
    assert steps[0].output["fallback"] is True
    assert {q.lens for q in result.plan.questions} == {Lens.AUDIENCE_VOICE, Lens.PURCHASE_DRIVERS, Lens.OBJECTIONS}


def test_fixture_mode_runs_the_full_harness_offline():
    recorder = Recorder()
    loop = ResearchLoop(llm=FixtureLLMAdapter(), search=FixtureSearchAdapter(), page_reader=FixturePageReaderAdapter(),
                        recorder=recorder, max_tool_calls=20, max_iterations=8, timeout_seconds=30)
    result = asyncio.run(loop.run(_request()))
    assert len(result.sources) == 3
    assert len(result.angles) == 3 and all(a.observations for a in result.angles)
    assert result.gap_note is None
    phases = [s.input.get("phase") for s in recorder.steps if s.step_type is StepType.DECIDE]
    assert phases == ["plan", "synthesise"]
