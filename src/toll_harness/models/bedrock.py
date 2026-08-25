from __future__ import annotations

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


class BedrockModelAdapter(ModelAdapter):
    """Amazon Bedrock Converse adapter with no model-specific runtime behavior."""

    def __init__(
        self,
        model_id: str,
        *,
        region: str = "us-west-2",
        profile_name: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0,
        client: Any | None = None,
    ):
        self._model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.temperature = temperature
        if client is None:
            try:
                import boto3
            except ImportError as error:
                raise RuntimeError("Install Toll Harness with the 'aws' extra") from error
            client = boto3.Session(profile_name=profile_name, region_name=region).client(
                "bedrock-runtime"
            )
        self.client = client

    @property
    def model_id(self) -> str:
        return self._model_id

    @staticmethod
    def _tool_alias(name: str) -> str:
        return name.replace(".", "__")

    @staticmethod
    def _canonical_tool_name(name: str) -> str:
        return name.replace("__", ".")

    def _message_to_bedrock(self, message: ModelMessage) -> JsonObject:
        content: list[JsonObject] = []
        for block in message.content:
            block_type = block.get("type")
            if block_type == "text":
                content.append({"text": str(block.get("text", ""))})
            elif block_type == "tool_call":
                content.append(
                    {
                        "toolUse": {
                            "toolUseId": block["id"],
                            "name": self._tool_alias(block["name"]),
                            "input": block.get("arguments", {}),
                        }
                    }
                )
            elif block_type == "tool_result":
                tool_result: JsonObject = {
                    "toolUseId": block["call_id"],
                    "content": [{"json": block.get("output", {})}],
                }
                if block.get("is_error"):
                    tool_result["status"] = "error"
                content.append({"toolResult": tool_result})
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
            "modelId": self.model_id,
            "system": [{"text": system}],
            "messages": [self._message_to_bedrock(message) for message in messages],
            "inferenceConfig": {
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        if tools:
            request["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": self._tool_alias(tool.name),
                            "description": f"{tool.name} v{tool.version}: {tool.description}",
                            "inputSchema": {"json": tool.input_schema},
                        }
                    }
                    for tool in tools
                ]
            }
        try:
            response = self.client.converse(**request)
        except Exception as error:
            details = getattr(error, "response", {}).get("Error", {})
            raise ModelInvocationError(
                "bedrock",
                details.get("Code", type(error).__name__),
                details.get("Message", str(error)),
            ) from error

        output = response.get("output", {}).get("message", {})
        normalized: list[JsonObject] = []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in output.get("content", []):
            if "text" in block:
                text = block["text"]
                text_parts.append(text)
                normalized.append({"type": "text", "text": text})
            elif "toolUse" in block:
                tool_use = block["toolUse"]
                call = ToolCall(
                    id=tool_use["toolUseId"],
                    name=self._canonical_tool_name(tool_use["name"]),
                    arguments=tool_use.get("input", {}),
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
        usage = response.get("usage", {})
        return ModelResponse(
            message=ModelMessage(role="assistant", content=normalized),
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=ModelUsage(
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                total_tokens=usage.get(
                    "totalTokens", usage.get("inputTokens", 0) + usage.get("outputTokens", 0)
                ),
                raw=usage,
            ),
            stop_reason=response.get("stopReason"),
            raw_metadata={"metrics": response.get("metrics", {})},
        )
