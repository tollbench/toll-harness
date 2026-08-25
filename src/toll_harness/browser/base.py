from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrowserProvider(ABC):
    @abstractmethod
    def open(self, url: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def observe(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def click(self, ref: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def type(self, ref: str, text: str, submit: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def wait(self, seconds: float) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None
