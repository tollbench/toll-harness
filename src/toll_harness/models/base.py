from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from toll_harness.core.types import ModelMessage, ModelResponse, ToolDefinition


class ModelAdapter(ABC):
    """Provider-neutral intelligence boundary."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def invoke(
        self,
        *,
        system: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        raise NotImplementedError
