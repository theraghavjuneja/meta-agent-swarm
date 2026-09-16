"""Adapter factory for the research package.

This is the **only** place in ``app.research`` that looks at ``settings.provider_mode``.
The loop, the activity and the repository all receive ports and never ask which
implementation they got.

Fixture mode also honours optional force-failure settings, which is how the
"reproducible failure on demand" requirement is triggered against a running system:

    RESEARCH_FIXTURE_FAIL_LLM=1        # first LLM call fails, then it succeeds
    RESEARCH_FIXTURE_FAIL_SEARCH=1
    RESEARCH_FIXTURE_FAIL_PAGE_READER=1

They are read with ``getattr`` defaults so that this module works whether or not
``app.config.Settings`` has grown those fields yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.common.logging import get_logger
from app.config import get_settings
from app.research.adapters.llm_anthropic import AnthropicLLMAdapter
from app.research.adapters.llm_fixture import FixtureLLMAdapter
from app.research.adapters.page_reader_fixture import FixturePageReaderAdapter
from app.research.adapters.page_reader_http import HttpPageReaderAdapter
from app.research.adapters.search_fixture import FixtureSearchAdapter
from app.research.adapters.search_tavily import TavilySearchAdapter
from app.research.ports import LLMPort, PageReaderPort, WebSearchPort

__all__ = [
    "AnthropicLLMAdapter",
    "FixtureLLMAdapter",
    "FixturePageReaderAdapter",
    "FixtureSearchAdapter",
    "HttpPageReaderAdapter",
    "ResearchAdapters",
    "TavilySearchAdapter",
    "build_research_adapters",
]

logger = get_logger(__name__)

FIXTURE_MODE = "fixture"


@dataclass(frozen=True, slots=True)
class ResearchAdapters:
    """The three ports a research run needs, plus whether they are fixtures."""

    llm: LLMPort
    search: WebSearchPort
    page_reader: PageReaderPort
    mock_mode: bool


def build_research_adapters(settings: Any | None = None) -> ResearchAdapters:
    """Return the adapter bundle for the configured provider mode."""
    settings = settings or get_settings()
    mode = str(getattr(settings, "provider_mode", FIXTURE_MODE)).lower()

    if mode == FIXTURE_MODE:
        adapters = ResearchAdapters(
            llm=FixtureLLMAdapter(
                model_name=str(getattr(settings, "anthropic_model", "fixture-model")),
                fail_times=int(getattr(settings, "research_fixture_fail_llm", 0) or 0),
            ),
            search=FixtureSearchAdapter(
                fail_times=int(getattr(settings, "research_fixture_fail_search", 0) or 0)
            ),
            page_reader=FixturePageReaderAdapter(
                fail_times=int(
                    getattr(settings, "research_fixture_fail_page_reader", 0) or 0
                )
            ),
            mock_mode=True,
        )
        logger.info("research.adapters.built", provider_mode=FIXTURE_MODE)
        return adapters

    adapters = ResearchAdapters(
        llm=AnthropicLLMAdapter(
            api_key=str(getattr(settings, "anthropic_api_key", "") or ""),
            model=str(getattr(settings, "anthropic_model", "")),
        ),
        search=TavilySearchAdapter(
            api_key=str(getattr(settings, "tavily_api_key", "") or "")
        ),
        page_reader=HttpPageReaderAdapter(),
        mock_mode=False,
    )
    logger.info("research.adapters.built", provider_mode=mode)
    return adapters