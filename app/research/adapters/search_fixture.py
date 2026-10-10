"""Fixture web-search adapter: a small canned result set, clearly labelled.

Results are keyed loosely by query keyword with a generic fallback, so any query the
scripted fixture LLM issues returns something plausible. ``fail_times`` forces N failures
before the adapter starts succeeding.
"""

from __future__ import annotations

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.research.ports import SearchResult

__all__ = ["FixtureSearchAdapter", "FIXTURE_RESULTS"]

logger = get_logger(__name__)

FIXTURE_RESULTS: list[SearchResult] = [
    SearchResult(
        url="https://example.com/fixtures/sleep-research-overview",
        title="[FIXTURE] What people mean when they say they can't sleep",
        snippet=(
            "[FIXTURE] Survey write-up: respondents consistently describe an inability to "
            "stop thinking rather than an absence of tiredness."
        ),
        score=0.94,
    ),
    SearchResult(
        url="https://example.com/fixtures/consumer-wellness-trends",
        title="[FIXTURE] Consumer wellness category trends",
        snippet=(
            "[FIXTURE] Category analysis: shoppers report scepticism toward clinical-"
            "sounding claims and reward brands that state limits plainly."
        ),
        score=0.88,
    ),
    SearchResult(
        url="https://example.com/fixtures/evening-routine-forum",
        title="[FIXTURE] Community thread: what my evening routine actually looks like",
        snippet=(
            "[FIXTURE] Forum thread in which participants describe the evening as the only "
            "self-directed part of the day."
        ),
        score=0.71,
    ),
]


class FixtureSearchAdapter:
    """Deterministic ``WebSearchPort`` implementation. No network, no credentials."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self._fail_budget = max(0, fail_times)
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        self._calls += 1
        if self._fail_budget > 0:
            self._fail_budget -= 1
            logger.warning(
                "research.fixture.forced_failure",
                adapter="search",
                query=query,
            )
            raise InfrastructureError(
                "[FIXTURE] Forced search failure (reproducible-failure mode)."
            )

        lowered = query.lower()
        ranked = sorted(
            FIXTURE_RESULTS,
            key=lambda r: (
                -sum(word in r.title.lower() or word in r.snippet.lower() for word in lowered.split()),
                -(r.score or 0.0),
            ),
        )
        return ranked[: max(1, max_results)]