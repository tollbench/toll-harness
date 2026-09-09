from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from toll_harness.core.types import (
    JsonObject,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from toll_harness.models.base import ModelAdapter, ModelInvocationError

# Anthropic's most capable model is the neutral default; operators may override
# model.model_id in agent.yaml.
_DEFAULT_MODEL = "claude-opus-4-8"

# PROMPT CACHING. The draft loop sends the same stable prefix -- the front door,
# the tools index, the block index -- in front of every round on a want, and the
# runtime sends the same system instruction on every call of a run. Marking that
# block cacheable is what turns ~25,000 input tokens a plan into a fraction of
# it. The provider caches from 1,024 tokens up (2,048 on the small models) and
# IGNORES a marker on anything shorter, so a floor here only keeps the request
# honest; it is never an error. Four characters to a token, deliberately
# pessimistic.
CACHE_MIN_CHARS = 4_000


def _tool_alias(name: str) -> str:
    # Tool names carry dots (e.g. "state.save"); Anthropic requires ^[a-zA-Z0-9_-]{1,64}$.
    return name.replace(".", "__")


def _canonical_tool_name(name: str) -> str:
    return name.replace("__", ".")


class AnthropicModelAdapter(ModelAdapter):
    """Anthropic Messages API adapter with no model-specific runtime behavior.

    Thinking is intentionally left unconfigured: the harness normalized message
    format carries only text / tool_call / tool_result blocks, so it cannot round
    -trip Anthropic thinking blocks (which must be replayed unchanged alongside
    tool use). Omitting the ``thinking`` parameter runs a plain tool-use loop,
    which is what the provider-neutral runtime expects.
    """

    def __init__(
        self,
        model_id: str = _DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 2048,
        client: Any | None = None,
        prompt_caching: bool = True,
    ):
        self._model_id = model_id
        self.max_tokens = max_tokens
        self.prompt_caching = prompt_caching
        if client is None:
            try:
                import anthropic
            except ImportError as error:
                raise RuntimeError("Install Toll Harness with the 'anthropic' extra") from error
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.client = client

    @property
    def model_id(self) -> str:
        return self._model_id

    def _message_to_anthropic(self, message: ModelMessage) -> JsonObject:
        content: list[JsonObject] = []
        for block in message.content:
            block_type = block.get("type")
            if block_type == "text":
                content.append({"type": "text", "text": str(block.get("text", ""))})
            elif block_type == "tool_call":
                content.append(
                    {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": _tool_alias(block["name"]),
                        "input": block.get("arguments", {}),
                    }
                )
            elif block_type == "tool_result":
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["call_id"],
                        "content": json.dumps(block.get("output", {})),
                        "is_error": bool(block.get("is_error", False)),
                    }
                )
            else:
                raise ValueError(f"Unsupported normalized content type: {block_type}")
        return {"role": message.role, "content": content}

    def caches_a_stable_prefix(self) -> bool:
        return bool(self.prompt_caching)

    def _system_field(self, system: str) -> Any:
        """The system as a cacheable block, or exactly the string it was.

        A short system goes over the wire unchanged, so a caller that never
        needed caching sees the request it always sent.
        """
        text = str(system or "")
        if not (self.prompt_caching and len(text) >= CACHE_MIN_CHARS):
            return text
        return [
            {
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        request: JsonObject = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "system": self._system_field(system),
            "messages": [self._message_to_anthropic(message) for message in messages],
        }
        if tools:
            request["tools"] = [
                {
                    "name": _tool_alias(tool.name),
                    "description": f"{tool.name} v{tool.version}: {tool.description}",
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]
        try:
            response = self.client.messages.create(**request)
        except Exception as error:
            code = getattr(error, "type", None) or type(error).__name__
            message = getattr(error, "message", None) or str(error)
            raise ModelInvocationError("anthropic", code, message) from error

        normalized: list[JsonObject] = []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
                normalized.append({"type": "text", "text": block.text})
            elif block_type == "tool_use":
                call = ToolCall(
                    id=block.id,
                    name=_canonical_tool_name(block.name),
                    arguments=dict(block.input or {}),
                )
                calls.append(call)
                normalized.append(
                    {
                        "type": "tool_call",
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                )
            # thinking / other block types are intentionally ignored
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        # What the cache did, in the provider's own words, so the caller can log
        # it: read tokens are the ones that cost a tenth, creation tokens are
        # the ones that paid to put the prefix there.
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return ModelResponse(
            message=ModelMessage(role="assistant", content=normalized),
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                raw={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                },
            ),
            stop_reason=getattr(response, "stop_reason", None),
            raw_metadata={"model": getattr(response, "model", self.model_id)},
        )
