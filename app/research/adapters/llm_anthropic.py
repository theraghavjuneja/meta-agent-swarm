# """Real ``LLMPort`` adapter over the Anthropic Messages API.

# Two capabilities, one client:

# * ``next_turn`` sends the running history plus the tool definitions and translates the
#   response's content blocks into this package's own block types. No SDK object escapes
#   this module.
# * ``structured_output`` uses forced tool use: the requested JSON Schema is advertised as a
#   single tool and ``tool_choice`` pins the model to it, so the model must answer with an
#   object shaped like the schema. The caller still validates — this constrains the model, it
#   does not trust it.

# Every provider call is made directly; any failure is normalised to ``InfrastructureError``
# before leaving this module. No per-call retry is performed here -- Temporal's own
# ``RetryPolicy`` (configured on the activity that ultimately calls this adapter) governs
# retries for the whole activity.
# """

# from __future__ import annotations

# from collections.abc import Sequence
# from typing import Any

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

# __all__ = ["AnthropicLLMAdapter"]

# logger = get_logger(__name__)

# DEFAULT_MAX_TOKENS = 4096
# DEFAULT_TIMEOUT_SECONDS = 90.0


# class AnthropicLLMAdapter:
#     """``LLMPort`` backed by ``anthropic.AsyncAnthropic``."""

#     def __init__(
#         self,
#         *,
#         api_key: str,
#         model: str,
#         max_tokens: int = DEFAULT_MAX_TOKENS,
#         timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
#     ) -> None:
#         if not api_key:
#             raise InfrastructureError("ANTHROPIC_API_KEY is not configured.")
#         from anthropic import AsyncAnthropic  # imported here: adapters own the SDK

#         self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)
#         self._model = model
#         self._max_tokens = max_tokens

#     @property
#     def model_name(self) -> str:
#         return self._model

#     # -- public ------------------------------------------------------------------------

#     async def next_turn(
#         self,
#         *,
#         system: str,
#         messages: Sequence[LLMMessage],
#         tools: Sequence[ToolDefinition],
#     ) -> LLMTurn:
#         response = await self._create(
#             model=self._model,
#             max_tokens=self._max_tokens,
#             system=system,
#             messages=_serialise_messages(messages),
#             tools=[t.model_dump() for t in tools],
#         )
#         text_parts: list[str] = []
#         tool_uses: list[ToolUseBlock] = []
#         for block in getattr(response, "content", []) or []:
#             block_type = getattr(block, "type", None)
#             if block_type == "text":
#                 text_parts.append(getattr(block, "text", ""))
#             elif block_type == "tool_use":
#                 raw_input = getattr(block, "input", {}) or {}
#                 tool_uses.append(
#                     ToolUseBlock(
#                         id=getattr(block, "id", ""),
#                         name=getattr(block, "name", ""),
#                         input=dict(raw_input) if isinstance(raw_input, dict) else {},
#                     )
#                 )
#         return LLMTurn(
#             text="\n".join(p for p in text_parts if p).strip(),
#             tool_uses=tool_uses,
#             stop_reason=getattr(response, "stop_reason", None),
#             usage=_usage(response),
#         )

#     async def structured_output(
#         self,
#         *,
#         system: str,
#         messages: Sequence[LLMMessage],
#         schema: dict[str, Any],
#         schema_name: str,
#         schema_description: str = "",
#     ) -> StructuredOutput:
#         tool = {
#             "name": schema_name,
#             "description": schema_description
#             or f"Return the final {schema_name} payload conforming to this schema.",
#             "input_schema": schema,
#         }
#         response = await self._create(
#             model=self._model,
#             max_tokens=self._max_tokens,
#             system=system,
#             messages=_serialise_messages(messages),
#             tools=[tool],
#             tool_choice={"type": "tool", "name": schema_name},
#         )
#         for block in getattr(response, "content", []) or []:
#             if getattr(block, "type", None) == "tool_use":
#                 raw_input = getattr(block, "input", {}) or {}
#                 if isinstance(raw_input, dict):
#                     return StructuredOutput(data=dict(raw_input), usage=_usage(response))
#         raise InfrastructureError(
#             f"Anthropic returned no {schema_name} tool_use block despite forced tool use."
#         )

#     # -- provider call -----------------------------------------------------------------

#     async def _create(self, **kwargs: Any) -> Any:
#         try:
#             return await self._client.messages.create(**kwargs)
#         except InfrastructureError:
#             raise
#         except Exception as exc:
#             logger.error("research.anthropic.call_failed", error=repr(exc))
#             raise InfrastructureError(f"Anthropic request failed: {exc}") from exc


# def _serialise_messages(messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
#     return [m.model_dump(mode="json") for m in messages]


# def _usage(response: Any) -> TokenUsage:
#     usage = getattr(response, "usage", None)
#     return TokenUsage(
#         input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
#         output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
#     )


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
 
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 90.0
 
 
class AnthropicLLMAdapter:
    """``LLMPort`` backed by ``groq.AsyncGroq``.
 
    Kept under its original name/interface on purpose -- only the implementation
    underneath was swapped from Anthropic to Groq.
    """
 
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise InfrastructureError("GROQ_API_KEY is not configured.")
        from groq import AsyncGroq  # imported here: adapters own the SDK
 
        self._client = AsyncGroq(api_key=api_key, timeout=timeout_seconds)
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
        response = await self._create(
            model=self._model,
            max_completion_tokens=self._max_tokens,
            messages=_serialise_messages(system, messages),
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": schema_name}},
        )
        message = response.choices[0].message
        for tool_call in getattr(message, "tool_calls", None) or []:
            if getattr(tool_call.function, "name", None) == schema_name:
                data = _parse_arguments(getattr(tool_call.function, "arguments", ""))
                return StructuredOutput(data=data, usage=_usage(response))
        raise InfrastructureError(
            f"Groq returned no {schema_name} tool_call despite forced tool use."
        )
 
    # -- provider call -----------------------------------------------------------------
 
    async def _create(self, **kwargs: Any) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except InfrastructureError:
            raise
        except Exception as exc:
            logger.error("research.groq.call_failed", error=repr(exc))
            raise InfrastructureError(f"Groq request failed: {exc}") from exc
 
 
def _serialise_messages(system: str, messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
    """Groq has no top-level ``system`` field -- it's just the first message.
 
    Also translates this package's Anthropic-block-shaped ``content`` (plain string, or
    a list of ``{"type": "text"/"tool_use"/"tool_result", ...}`` blocks) into Groq's
    OpenAI-style shape: a ``tool_use`` block becomes an entry in the assistant message's
    ``tool_calls`` list (with JSON-stringified arguments), and each ``tool_result`` block
    becomes its own ``role: "tool"`` message carrying the matching ``tool_call_id``.
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
        # tool_result blocks are their own messages, not part of the assistant turn.
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
        raise InfrastructureError(f"Groq returned malformed tool arguments: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}
 
 
def _usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    return TokenUsage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )