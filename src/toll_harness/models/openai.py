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


def _tool_alias(name: str) -> str:
    # Tool names carry dots (e.g. "state.save"); OpenAI requires ^[a-zA-Z0-9_-]{1,64}$.
    return name.replace(".", "__")


def _canonical_tool_name(name: str) -> str:
    return name.replace("__", ".")


class OpenAIModelAdapter(ModelAdapter):
    """OpenAI Chat Completions adapter with no model-specific runtime behavior.

    model.model_id is required (no default) so the adapter never assumes a
    particular OpenAI model on the operator's behalf.
    """

    def __init__(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        max_tokens: int = 2048,
        client: Any | None = None,
    ):
        self._model_id = model_id
        self.max_tokens = max_tokens
        if client is None:
            try:
                import openai
            except ImportError as error:
                raise RuntimeError("Install Toll Harness with the 'openai' extra") from error
            client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.client = client

    @property
    def model_id(self) -> str:
        return self._model_id

    def _messages_to_openai(
        self, system: str, messages: Sequence[ModelMessage]
    ) -> list[JsonObject]:
        out: list[JsonObject] = [{"role": "system", "content": system}]
        for message in messages:
            if message.role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[JsonObject] = []
                for block in message.content:
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif block_type == "tool_call":
                        tool_calls.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": _tool_alias(block["name"]),
                                    "arguments": json.dumps(block.get("arguments", {})),
                                },
                            }
                        )
                    else:
                        raise ValueError(f"Unsupported assistant content type: {block_type}")
                entry: JsonObject = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                out.append(entry)
            else:  # user turns carry text and/or tool results
                for block in message.content:
                    block_type = block.get("type")
                    if block_type == "text":
                        out.append({"role": "user", "content": str(block.get("text", ""))})
                    elif block_type == "tool_result":
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": block["call_id"],
                                "content": json.dumps(block.get("output", {})),
                            }
                        )
                    else:
                        raise ValueError(f"Unsupported user content type: {block_type}")
        return out

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
            "messages": self._messages_to_openai(system, messages),
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": _tool_alias(tool.name),
                        "description": f"{tool.name} v{tool.version}: {tool.description}",
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            request["tool_choice"] = "auto"
        try:
            response = self.client.chat.completions.create(**request)
        except Exception as error:
            code = getattr(error, "type", None) or type(error).__name__
            message = getattr(error, "message", None) or str(error)
            raise ModelInvocationError("openai", code, message) from error

        choice = response.choices[0]
        model_message = choice.message
        normalized: list[JsonObject] = []
        calls: list[ToolCall] = []
        text = getattr(model_message, "content", None) or ""
        if text:
            normalized.append({"type": "text", "text": text})
        for tool_call in getattr(model_message, "tool_calls", None) or []:
            arguments = json.loads(tool_call.function.arguments or "{}")
            call = ToolCall(
                id=tool_call.id,
                name=_canonical_tool_name(tool_call.function.name),
                arguments=arguments,
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
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens)
        return ModelResponse(
            message=ModelMessage(role="assistant", content=normalized),
            text=text,
            tool_calls=calls,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                raw={"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            ),
            stop_reason=getattr(choice, "finish_reason", None),
            raw_metadata={"model": getattr(response, "model", self.model_id)},
        )
