from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EmailProvider(ABC):
    @abstractmethod
    def list(self, *, limit: int = 20, unread_only: bool = False) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def read(self, message_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def send(
        self,
        *,
        to: list[str],
        subject: str,
        text: str,
        idempotency_key: str,
        attachment_file_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def reply(self, *, message_id: str, text: str, idempotency_key: str) -> dict[str, Any]:
        raise NotImplementedError

    def wait(self, *, after: str | None = None, timeout_seconds: int = 30) -> dict[str, Any]:
        raise NotImplementedError("This email provider does not support event waiting")
