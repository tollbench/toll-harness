from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from toll_harness.core.types import ModelMessage, ModelResponse, ToolDefinition
from toll_harness.models.base import ModelAdapter


class ScriptedModelAdapter(ModelAdapter):
    """Deterministic adapter for conformance tests and offline runtime demonstrations."""

    def __init__(self, responses: Sequence[ModelResponse], model_id: str = "scripted.test-v1"):
        self._model_id = model_id
        self.responses = deque(responses)
        self.invocations: list[dict] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        self.invocations.append(
            {"system": system, "messages": list(messages), "tools": list(tools)}
        )
        if not self.responses:
            raise RuntimeError("Scripted model has no response remaining")
        return self.responses.popleft()
