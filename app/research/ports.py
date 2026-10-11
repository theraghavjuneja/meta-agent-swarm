"""Ports for the research package.

These are the only interfaces the research domain logic (``loop.py``, ``activities.py``)
knows about. Concrete providers live in ``app.research.adapters`` and are selected by
``app.research.adapters.build_research_adapters`` based on ``settings.provider_mode``.

Nothing in this module imports a provider SDK, ``httpx``, or a search client. The result
types below are small Pydantic models rather than raw SDK response objects so that the
loop never has to know which vendor produced a turn.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ContentBlock",
    "LLMMessage",
    "LLMPort",
    "LLMTurn",
    "PageContent",
    "PageReaderPort",
    "SearchResult",
    "StructuredOutput",
    "TextBlock",
    "TokenUsage",
    "ToolDefinition",
    "ToolResultBlock",
    "ToolUseBlock",
    "WebSearchPort",
]


# --------------------------------------------------------------------------------------
# Search / page-reading result types
# --------------------------------------------------------------------------------------


class SearchResult(BaseModel):
    """A single web-search hit. ``snippet`` is untrusted, provider-supplied text."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    snippet: str
    score: float | None = None


class PageContent(BaseModel):
    """Main-content extraction for a single fetched page.

    ``extracted_text`` is untrusted content: it must only ever reach the model wrapped in
    ``<source_content>`` delimiters (see ``app.research.loop``).
    """

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    extracted_text: str
    truncated: bool = False
    original_char_count: int | None = None


# --------------------------------------------------------------------------------------
# LLM message / turn types
# --------------------------------------------------------------------------------------


class ToolDefinition(BaseModel):
    """A tool advertised to the model. ``input_schema`` is a JSON Schema object."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]


class LLMMessage(BaseModel):
    """One message in the running history handed back to the model each turn."""

    role: Literal["user", "assistant"]
    content: list[ContentBlock]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class LLMTurn(BaseModel):
    """One model turn: free text, zero or more tool-use requests, or both."""

    text: str = ""
    tool_uses: list[ToolUseBlock] = Field(default_factory=list)
    stop_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @property
    def is_final(self) -> bool:
        """True when the model asked for no tools, i.e. it believes it is done."""
        return not self.tool_uses


class StructuredOutput(BaseModel):
    """Raw (schema-shaped but not yet domain-validated) structured model output.

    The caller is responsible for validating ``data`` against its own Pydantic model —
    the adapter asks the provider to conform to the schema, it does not guarantee it.
    """

    data: dict[str, Any]
    usage: TokenUsage = Field(default_factory=TokenUsage)


# --------------------------------------------------------------------------------------
# Protocols
# --------------------------------------------------------------------------------------


@runtime_checkable
class LLMPort(Protocol):
    """Everything the research loop needs from a language model.

    Two capabilities, deliberately separate:

    * ``next_turn`` drives the tool-calling (ReAct) loop;
    * ``structured_output`` produces a JSON object conforming to an arbitrary JSON
      Schema. It is intentionally schema-agnostic — ``research`` uses it for the final
      three angles, ``creative`` will reuse it for the creative spec.
    """

    @property
    def model_name(self) -> str:
        """Identifier persisted as ``research_runs.model_used``."""
        ...

    async def next_turn(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMTurn: ...

    async def structured_output(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        schema: dict[str, Any],
        schema_name: str,
        schema_description: str = "",
    ) -> StructuredOutput: ...


@runtime_checkable
class WebSearchPort(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


@runtime_checkable
class PageReaderPort(Protocol):
    async def read(self, url: str) -> PageContent: ...