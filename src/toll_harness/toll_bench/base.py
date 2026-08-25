from __future__ import annotations

from typing import Any, Protocol


class TollBenchProvider(Protocol):
    """Agent-scoped Toll Bench operations exposed to the intelligence."""

    def protocol(self) -> dict[str, Any]: ...

    def guide(self, topic: str) -> dict[str, Any]: ...

    def proposal_schema(self) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...

    def ensure_reachable(self) -> dict[str, Any]: ...

    def attention(self, *, wait: int = 0) -> dict[str, Any]: ...

    def events(self, *, after: str | None = None, wait: int = 0) -> dict[str, Any]: ...

    def list_targets(self) -> dict[str, Any]: ...

    def read_brief(self, target_id: str) -> dict[str, Any]: ...

    def list_proposals(self) -> dict[str, Any]: ...

    def validate_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]: ...

    def submit_proposal(
        self, target_id: str, proposal: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]: ...

    def read_finalist_answers(self, target_id: str, proposal_id: str) -> dict[str, Any]: ...

    def submit_informed_plan(
        self,
        target_id: str,
        proposal_id: str,
        plan: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def current_step(self, deal_id: str) -> dict[str, Any]: ...

    def reply_step_message(
        self,
        deal_id: str,
        step_id: str,
        reply: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def post_check_in(
        self, deal_id: str, pulse: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]: ...

    def file_outcome(
        self, target_id: str, outcome: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]: ...
