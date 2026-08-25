from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from toll_harness.core.types import (
    AgentIdentity,
    AutonomyMode,
    Checkpoint,
    Event,
    JsonObject,
    RunRecord,
    RunStatus,
)


class StateStore(ABC):
    @abstractmethod
    def register_agent(self, identity: AgentIdentity) -> AgentIdentity:
        raise NotImplementedError

    @abstractmethod
    def get_agent(self, agent_id: str) -> AgentIdentity:
        raise NotImplementedError

    @abstractmethod
    def create_run(
        self,
        goal: str,
        mode: AutonomyMode,
        model: str,
        agent_id: str | None = None,
    ) -> RunRecord:
        raise NotImplementedError

    @abstractmethod
    def get_run(self, run_id: str) -> RunRecord:
        raise NotImplementedError

    @abstractmethod
    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        raise NotImplementedError

    @abstractmethod
    def increment_operator_messages(self, run_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def load_checkpoint(self, run_id: str) -> Checkpoint:
        raise NotImplementedError

    @abstractmethod
    def save_checkpoint(self, run_id: str, data: JsonObject, event_cursor: int) -> Checkpoint:
        raise NotImplementedError

    @abstractmethod
    def load_knowledge(self, namespace: str) -> JsonObject:
        raise NotImplementedError

    @abstractmethod
    def save_knowledge(self, namespace: str, data: JsonObject) -> None:
        raise NotImplementedError


class EventStore(ABC):
    @abstractmethod
    def append_event(self, run_id: str, kind: str, source: str, payload: JsonObject) -> Event:
        raise NotImplementedError

    @abstractmethod
    def list_events(self, run_id: str, after_sequence: int = 0) -> list[Event]:
        raise NotImplementedError


class ArtifactStore(ABC):
    @abstractmethod
    def list(self, run_id: str, prefix: str = "") -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def read(self, run_id: str, path: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def write(self, run_id: str, path: str, content: bytes) -> dict[str, Any]:
        raise NotImplementedError


class SecretStore(ABC):
    @abstractmethod
    def set(self, name: str, value: str) -> None:
        """Store one explicitly named secret without making it enumerable."""
        raise NotImplementedError

    @abstractmethod
    def get(self, name: str) -> str | None:
        """Return one explicitly named secret. Secrets must not be enumerable."""
        raise NotImplementedError
