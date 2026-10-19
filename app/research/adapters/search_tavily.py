"""Real ``WebSearchPort`` adapter over Tavily.

Search only. Tavily can also extract page content, but page reading is a separate port with
a separate adapter here, so either half can be swapped without touching the other.

The provider call is made directly and any failure is normalised to ``InfrastructureError``.
No per-call retry is performed here -- Temporal's own ``RetryPolicy`` (configured on the
activity that ultimately calls this adapter) governs retries for the whole activity.
"""

from __future__ import annotations

from typing import Any

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.research.ports import SearchResult

__all__ = ["TavilySearchAdapter"]

logger = get_logger(__name__)

MAX_SNIPPET_CHARS = 600


class TavilySearchAdapter:
    """``WebSearchPort`` backed by the Tavily async client."""

    def __init__(self, *, api_key: str, search_depth: str = "basic") -> None:
        if not api_key:
            raise InfrastructureError("TAVILY_API_KEY is not configured.")
        from tavily import AsyncTavilyClient  # imported here: adapters own the SDK

        self._client = AsyncTavilyClient(api_key=api_key)
        self._search_depth = search_depth

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = await self._search(query=query, max_results=max_results)
        raw_results = payload.get("results", []) if isinstance(payload, dict) else []

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            content = str(item.get("content") or "")
            results.append(
                SearchResult(
                    url=url,
                    title=str(item.get("title") or url),
                    snippet=content[:MAX_SNIPPET_CHARS],
                    score=_as_float(item.get("score")),
                )
            )
        logger.info(
            "research.tavily.search",
            results=len(results),
            max_results=max_results,
        )
        return results[:max_results]

    async def _search(self, **kwargs: Any) -> Any:
        try:
            return await self._client.search(search_depth=self._search_depth, **kwargs)
        except InfrastructureError:
            raise
        except Exception as exc:
            logger.error("research.tavily.call_failed", error=repr(exc))
            raise InfrastructureError(f"Tavily search failed: {exc}") from exc


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None