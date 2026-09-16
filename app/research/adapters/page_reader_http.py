"""Real ``PageReaderPort`` adapter: fetch over HTTP, then extract the main content.

Extraction approach
-------------------
1. ``httpx.AsyncClient`` fetches the URL with redirects followed, a browser-ish user agent,
   a per-request timeout, and a response-size cap so one enormous page cannot exhaust
   memory. Non-HTML content types are rejected rather than run through an HTML extractor.
2. ``trafilatura.extract`` does the main-content extraction — it strips navigation,
   boilerplate and comments and is reliable across the kind of article and blog pages this
   agent reads. It is an optional dependency: if it is not installed, or returns nothing
   usable for a given page, the adapter falls back to a conservative tag-stripping pass
   (``script``/``style`` removed, tags dropped, entities unescaped, whitespace collapsed).
   A degraded extraction is more useful to the agent than a hard failure.
3. The result is truncated at ``MAX_EXTRACTED_CHARS`` with ``truncated=True`` set, so the
   loop and the persisted trace both know the text is partial.

Everything returned here is untrusted content. This adapter never interprets it; the loop
wraps it in ``<source_content>`` delimiters before it reaches the model.
"""

from __future__ import annotations

import html
import re
from typing import Any, Final

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.common.retry import with_retry
from app.research.ports import PageContent

__all__ = ["HttpPageReaderAdapter"]

logger = get_logger(__name__)

MAX_EXTRACTED_CHARS: Final[int] = 40_000
MAX_RESPONSE_BYTES: Final[int] = 5_000_000
DEFAULT_TIMEOUT_SECONDS: Final[float] = 20.0
USER_AGENT: Final[str] = "CampaignStudioResearchBot/1.0 (+https://example.com/bot)"

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"\n{3,}")


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
    }:
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status is not None and (status == 429 or status >= 500)


class HttpPageReaderAdapter:
    """``PageReaderPort`` backed by ``httpx`` plus ``trafilatura`` (with a plain fallback)."""

    def __init__(self, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        import httpx  # imported here: adapters own the HTTP client

        self._httpx = httpx
        self._timeout = timeout_seconds

    async def read(self, url: str) -> PageContent:
        if not url.lower().startswith(("http://", "https://")):
            raise InfrastructureError(f"Refusing to fetch non-HTTP URL: {url!r}")

        response = await self._fetch(url)
        content_type = str(response.headers.get("content-type", "")).lower()
        if content_type and "html" not in content_type and "text" not in content_type:
            raise InfrastructureError(
                f"Unsupported content type {content_type!r} at {url}"
            )

        raw = response.text
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise InfrastructureError(f"Response from {url} exceeded the size cap.")

        extracted = _extract_main_content(raw, url)
        truncated = len(extracted) > MAX_EXTRACTED_CHARS
        logger.info(
            "research.page_reader.read",
            url=url,
            chars=len(extracted),
            truncated=truncated,
        )
        return PageContent(
            url=str(response.url) or url,
            title=_extract_title(raw) or url,
            extracted_text=extracted[:MAX_EXTRACTED_CHARS],
            truncated=truncated,
            original_char_count=len(extracted),
        )

    async def _fetch(self, url: str) -> Any:
        try:
            return await self._fetch_with_retry(url)
        except InfrastructureError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "research.page_reader.fetch_failed",
                url=url,
                error=repr(exc),
            )
            raise InfrastructureError(f"Failed to fetch {url}: {exc}") from exc

    @with_retry(max_attempts=3, retryable=_is_retryable)
    async def _fetch_with_retry(self, url: str) -> Any:
        async with self._httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response


def _extract_main_content(raw_html: str, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            raw_html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()
        logger.warning("research.page_reader.extraction_empty", url=url)
    except ImportError:
        logger.warning("research.page_reader.trafilatura_missing", url=url)
    except Exception as exc:  # noqa: BLE001 - extraction must degrade, not fail
        logger.warning(
            "research.page_reader.extraction_failed",
            url=url,
            error=repr(exc),
        )
    return _strip_tags(raw_html)


def _strip_tags(raw_html: str) -> str:
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    text = _TAG_RE.sub("\n", without_scripts)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return _WHITESPACE_RE.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def _extract_title(raw_html: str) -> str | None:
    match = _TITLE_RE.search(raw_html)
    if not match:
        return None
    return html.unescape(_TAG_RE.sub("", match.group(1))).strip()[:512] or None