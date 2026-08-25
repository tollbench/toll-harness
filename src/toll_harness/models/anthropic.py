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
    ):
        self._model_id = model_id
        self.max_tokens = max_tokens
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
            "system": system,
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
        return ModelResponse(
            message=ModelMessage(role="assistant", content=normalized),
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                raw={"input_tokens": input_tokens, "output_tokens": output_tokens},
            ),
            stop_reason=getattr(response, "stop_reason", None),
            raw_metadata={"model": getattr(response, "model", self.model_id)},
        )
