from __future__ import annotations

from typing import Any

from toll_harness.core.types import AutonomyMode, RunStatus
from toll_harness.storage.base import EventStore, StateStore


class OperatorChannel:
    def __init__(self, state_store: StateStore, event_store: EventStore):
        self.state_store = state_store
        self.event_store = event_store

    def observe(self, run_id: str) -> dict[str, Any]:
        run = self.state_store.get_run(run_id)
        agent = self.state_store.get_agent(run.agent_id) if run.agent_id else None
        checkpoint = self.state_store.load_checkpoint(run_id)
        events = self.event_store.list_events(run_id)
        return {
            "run": {
                "id": run.id,
                "goal": run.goal,
                "status": run.status.value,
                "model": run.model,
                "requested_mode": run.requested_mode.value,
                "observed_mode": run.observed_mode.value,
                "agent_id": run.agent_id,
            },
            "agent": (
                {
                    "id": agent.id,
                    "name": agent.name,
                    "intelligence": agent.intelligence,
                    "company": agent.company,
                    "harness": agent.harness,
                    "autonomy": agent.autonomy_mode.value,
                    "email_status": agent.email_status.value,
                    "email_verification_recipient": agent.email_verification_recipient,
                    "email_address": agent.email_address,
                }
                if agent
                else None
            ),
            "checkpoint": checkpoint.data,
            "events": [event.__dict__ for event in events],
        }

    def message(self, run_id: str, message: str) -> None:
        run = self.state_store.get_run(run_id)
        if run.requested_mode is AutonomyMode.AUTONOMOUS:
            raise PermissionError("Operator messages are disabled for an Autonomous run")
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise ValueError("Operator messages cannot be added to a terminal run")
        if not message.strip():
            raise ValueError("Operator message must not be empty")
        self.event_store.append_event(run_id, "operator.message", "operator", {"message": message})
        self.state_store.increment_operator_messages(run_id)
