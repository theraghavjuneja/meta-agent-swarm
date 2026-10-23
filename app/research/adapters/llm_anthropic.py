from __future__ import annotations

import json
from collections.abc import Sequence
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

__all__ = ["AnthropicLLMAdapter"]

logger = get_logger(__name__)



DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT_SECONDS = 200.0


class AnthropicLLMAdapter:
    """``LLMPort`` backed by the OpenAI Python SDK (direct OpenAI endpoint).

    The class name is kept as-is so the rest of the codebase doesn't need
    changing — only the implementation underneath was swapped.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        resolved_key = api_key
        if not resolved_key:
            raise InfrastructureError("OPENAI_API_KEY is not configured.")

        from openai import AsyncOpenAI  # imported here: adapters own the SDK

        self._client = AsyncOpenAI(api_key=resolved_key, timeout=timeout_seconds)
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    # -- public ------------------------------------------------------------------------

    async def next_turn(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        tools: Sequence[ToolDefinition],
    ) -> LLMTurn:
        response = await self._create(
            model=self._model,
            max_completion_tokens=self._max_tokens,
            messages=_serialise_messages(system, messages),
            tools=_serialise_tools(tools) if tools else None,
        )
        message = response.choices[0].message
        tool_uses: list[ToolUseBlock] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_uses.append(
                ToolUseBlock(
                    id=getattr(tool_call, "id", ""),
                    name=getattr(tool_call.function, "name", ""),
                    input=_parse_arguments(getattr(tool_call.function, "arguments", "")),
                )
            )
        return LLMTurn(
            text=(getattr(message, "content", None) or "").strip(),
            tool_uses=tool_uses,
            stop_reason=response.choices[0].finish_reason,
            usage=_usage(response),
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
        tool = {
            "type": "function",
            "function": {
                "name": schema_name,
                "description": schema_description
                or f"Return the final {schema_name} payload conforming to this schema.",
                "parameters": schema,
            },
        }

        # Dummy tools are included so the provider doesn't reject historical
        # tool calls in the message history that reference these tool names.
        dummy_tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Dummy tool to satisfy validation.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_page",
                    "description": "Dummy tool to satisfy validation.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

        response = await self._create(
            model=self._model,
            max_completion_tokens=self._max_tokens,
            messages=_serialise_messages(system, messages),
            tools=[tool] + dummy_tools,
            tool_choice={"type": "function", "function": {"name": schema_name}},
        )
        message = response.choices[0].message
        for tool_call in getattr(message, "tool_calls", None) or []:
            if getattr(tool_call.function, "name", None) == schema_name:
                data = _parse_arguments(getattr(tool_call.function, "arguments", ""))
                return StructuredOutput(data=data, usage=_usage(response))
        raise InfrastructureError(
            f"OpenAI returned no {schema_name} tool_call despite forced tool use."
        )

    # -- provider call -----------------------------------------------------------------

    async def _create(self, **kwargs: Any) -> Any:
        # gpt-5.6-luna (and other reasoning models) do not support function
        # tools unless reasoning_effort is set to 'none'.
        kwargs.setdefault("reasoning_effort", "none")
        try:
            return await self._client.chat.completions.create(**kwargs)
        except InfrastructureError:
            raise
        except Exception as exc:
            logger.error("research.openai.call_failed", error=repr(exc))
            raise InfrastructureError(f"OpenAI request failed: {exc}") from exc


# ==================================================================================
# Helpers
# ==================================================================================


def _serialise_messages(system: str, messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
    """Convert internal message format to OpenAI's chat format.

    Prepends the system prompt as a system message, then translates each
    message's content blocks (text, tool_use, tool_result) into the
    OpenAI-compatible shape.
    """
    serialised: list[dict[str, Any]] = []
    if system:
        serialised.append({"role": "system", "content": system})
    for message in messages:
        dumped = message.model_dump(mode="json")
        serialised.extend(_serialise_one_message(dumped.get("role", "user"), dumped.get("content")))
    return serialised


def _serialise_one_message(role: str, content: Any) -> list[dict[str, Any]]:
    if content is None or isinstance(content, str):
        return [{"role": role, "content": content or ""}]

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else None
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}) or {}),
                    },
                }
            )
        elif block_type == "tool_result":
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": _stringify_tool_result(block.get("content")),
                }
            )
        elif isinstance(block, str):
            text_parts.append(block)

    out: list[dict[str, Any]] = []
    if tool_results:
        out.extend(tool_results)
    else:
        turn: dict[str, Any] = {"role": role, "content": "\n".join(p for p in text_parts if p)}
        if tool_calls:
            turn["tool_calls"] = tool_calls
        out.append(turn)
    return out


def _stringify_tool_result(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _serialise_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        dumped = tool.model_dump()
        result.append(
            {
                "type": "function",
                "function": {
                    "name": dumped.get("name", ""),
                    "description": dumped.get("description", ""),
                    "parameters": dumped.get("input_schema", {}),
                },
            }
        )
    return result


def _parse_arguments(raw_arguments: str) -> dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except (TypeError, ValueError) as exc:
        raise InfrastructureError(f"OpenAI returned malformed tool arguments: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}


def _usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    return TokenUsage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


# ==================================================================================
# !! OLD GROQ IMPLEMENTATION (commented out) !!
# ==================================================================================

# from __future__ import annotations
#
# import json
# from collections.abc import Sequence
# from typing import Any
#
# from app.common.exceptions import InfrastructureError
# from app.common.logging import get_logger
# from app.research.ports import (
#     LLMMessage,
#     LLMTurn,
#     StructuredOutput,
#     TokenUsage,
#     ToolDefinition,
#     ToolUseBlock,
# )
#
# __all__ = ["AnthropicLLMAdapter"]
#
# logger = get_logger(__name__)
#
# DEFAULT_MODEL = "openai/gpt-oss-120b"
# DEFAULT_MAX_TOKENS = 4096
# DEFAULT_TIMEOUT_SECONDS = 90.0
#
#
# class AnthropicLLMAdapter:
#     """``LLMPort`` backed by ``groq.AsyncGroq``."""
#
#     def __init__(self, *, api_key: str, model: str = DEFAULT_MODEL, ...) -> None:
#         if not api_key:
#             raise InfrastructureError("GROQ_API_KEY is not configured.")
#         from groq import AsyncGroq
#         self._client = AsyncGroq(api_key=api_key, timeout=timeout_seconds)
#         self._model = model
#         self._max_tokens = max_tokens
#
#     async def next_turn(self, *, system, messages, tools) -> LLMTurn:
#         response = await self._create(
#             model=self._model,
#             max_completion_tokens=self._max_tokens,
#             messages=_serialise_messages(system, messages),
#             tools=_serialise_tools(tools) if tools else None,
#         )
#         # ... (parse tool_calls from response.choices[0].message) ...
#
#     async def structured_output(self, *, system, messages, schema, schema_name, ...) -> StructuredOutput:
#         # ... forced tool_choice with dummy tools for web_search / read_page ...
#
#     async def _create(self, **kwargs) -> Any:
#         try:
#             return await self._client.chat.completions.create(**kwargs)
#         except Exception as exc:
#             logger.error("research.groq.call_failed", error=repr(exc))
#             raise InfrastructureError(f"Groq request failed: {exc}") from exc