"""Real ``WebSearchPort`` adapter over Tavily.

Search only. Tavily can also extract page content, but page reading is a separate port with
a separate adapter here, so either half can be swapped without touching the other.

The provider call goes through ``app.common.retry.with_retry``; exhausted retries surface
as ``InfrastructureError``.
"""

from __future__ import annotations

from typing import Any

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.common.retry import with_retry
from app.research.ports import SearchResult

__all__ = ["TavilySearchAdapter"]

logger = get_logger(__name__)

MAX_SNIPPET_CHARS = 600


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {"ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException"}:
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    return "timeout" in str(exc).lower() or "rate limit" in str(exc).lower()


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
            return await self._search_with_retry(**kwargs)
        except InfrastructureError:
            raise
        except Exception as exc:
            logger.error("research.tavily.call_failed", error=repr(exc))
            raise InfrastructureError(f"Tavily search failed: {exc}") from exc

    @with_retry(max_attempts=3, is_retryable=_is_retryable)
    async def _search_with_retry(self, **kwargs: Any) -> Any:
        return await self._client.search(search_depth=self._search_depth, **kwargs)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None