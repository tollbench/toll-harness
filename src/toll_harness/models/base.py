from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from toll_harness.core.types import ModelMessage, ModelResponse, ToolDefinition


class ModelInvocationError(RuntimeError):
    """Raised when a provider rejects or fails an invocation."""

    def __init__(self, provider: str, code: str, message: str):
        super().__init__(f"{provider} {code}: {message}")
        self.provider = provider
        self.code = code
        self.message = message


class ModelAdapter(ABC):
    """Provider-neutral intelligence boundary."""

    # 0.63.0. What the agent spent on a deal, for the optional `agent_cost`
    # hand-back slot. `cost_ledger` is set by whoever builds the runtime (None
    # counts nothing). `cost_basis` is how the adapter's dollars are to be read:
    # "metered" when the call is billed to an API key, "subscription_estimate"
    # when it runs on a plan and the dollars are a share priced from a table.
    cost_ledger: Any = None
    cost_basis: str = "metered"

    def count_spend(
        self,
        *,
        fresh_in: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        out: int = 0,
        model: object = None,
    ) -> None:
        """Put one billed call on the current deal's tab. No ledger, no deal, or
        no tokens counts nothing, and counting never fails a model call."""
        ledger = self.cost_ledger
        if ledger is None:
            return
        try:
            ledger.add(
                model or self.model_id,
                basis=self.cost_basis,
                fresh_in=fresh_in,
                cache_read=cache_read,
                cache_write=cache_write,
                out=out,
            )
        except Exception:  # noqa: BLE001 - the bill is never worth the call
            pass

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

    def caches_a_stable_prefix(self) -> bool:
        """Does repeating one block in front of every call cost less here?

        FALSE IS THE HONEST DEFAULT and it is what every adapter that does not
        answer gets: an unknown provider is charged full price for every byte,
        so a caller that would move something into a repeated prefix must keep
        sending it once instead. Adapters that know better say so.
        """
        return False
