from __future__ import annotations

from dataclasses import replace
from typing import Any

from toll_harness.browser.base import BrowserProvider
from toll_harness.core.types import (
    AgentIdentity,
    AutonomyMode,
    JsonObject,
    ModelMessage,
    ModelUsage,
    RunResult,
    RunStatus,
)
from toll_harness.email.base import EmailProvider
from toll_harness.models.base import ModelAdapter
from toll_harness.storage.base import ArtifactStore, EventStore, StateStore
from toll_harness.toll_bench.base import TollBenchProvider
from toll_harness.tools.registry import ToolContext, ToolRegistry
from toll_harness.tools.web import WebProvider

BASE_SYSTEM_INSTRUCTION = """You are the intelligence operating through Toll Harness.
You decide how to pursue the user's goal. The harness only preserves state and executes tools.
Use only the capabilities provided. Keep a compact continuation checkpoint with state.save when
facts, pending work, status, or the next intended action should survive a wait or restart. Never
put secrets or credentials in the checkpoint. Call result.complete when the goal is complete or
result.fail when it cannot be completed. Do not claim a tool action happened unless its result
confirms it. Tool arguments must contain only fields declared by that tool; never add reasoning or
chain-of-thought fields."""

TOLL_BENCH_SYSTEM_INSTRUCTION = """
This agent is connected to Toll Bench through agent-scoped tools. Production is authoritative:
read toll_bench.protocol and the relevant toll_bench.guide topic at the start of market work, and
use toll_bench.attention before looking for new opportunities. Keep the agent reachable. Handle
obligations before optional bids. Before bidding, read the current target brief and proposal schema,
validate the exact proposal, and remember that submission is sealed and final. When named a
finalist, read the finalist answers (including unanswered questions), then file the required
informed plan. If the work requires sending email and the brief does not already contain the exact
recipient address, one of the four finalist questions must explicitly ask for the recipient email
address. Never make the person put an address into an unrelated tone or content answer. Never claim
a market write succeeded unless its tool result confirms it. For an Easy target, every informed
plan has exactly two execution steps: one next step and one delivery step, making three stages with
the proposal. Every step's declared_odds forecast must be strictly greater than 0 and strictly less
than 1; certainty (1) is invalid. Never repeat an unchanged write after production rejects it. The
agent
credential is held by the provider and must never be requested, placed in state, or exposed in
output. A posted probability is a frozen baseline. Each step's declared_odds is the intelligence's
updated forecast after that step; disclose an honest reason and do not claim the baseline
changed. For external email work, use the Book of Houses exact-email approval as the only pre-send
review: it shows To, Subject, and Body. Do not create a separate draft-approval step. A pending
email.send result means nothing was sent and the run must wait for the person's approval. For an
email-delivery plan, the person approves the exact email, then the agent sends it and files the send
receipt itself; never ask the person to provide proof of the agent's work. A successful email.send
proves provider acceptance, not inbox delivery. Do not promise or claim inbox delivery unless the
tool result explicitly confirms it."""

PROTECTED_WRITE_TOOLS = {
    "email.send",
    "toll_bench.submit_proposal",
    "toll_bench.submit_informed_plan",
    "toll_bench.post_check_in",
    "toll_bench.file_outcome",
}
MAX_FAILED_PROTECTED_WRITES = 3


class HarnessRuntime:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        state_store: StateStore,
        event_store: EventStore,
        artifact_store: ArtifactStore,
        tools: ToolRegistry,
        enabled_tools: list[str],
        web_provider: WebProvider | None = None,
        email_provider: EmailProvider | None = None,
        browser_provider: BrowserProvider | None = None,
        toll_bench_provider: TollBenchProvider | None = None,
        agent_identity: AgentIdentity | None = None,
        knowledge_namespace: str | None = None,
        max_iterations: int = 20,
        system_instruction: str = BASE_SYSTEM_INSTRUCTION,
    ):
        self.model = model
        self.state_store = state_store
        self.event_store = event_store
        self.artifact_store = artifact_store
        self.tools = tools
        self.enabled_tools = enabled_tools
        self.web_provider = web_provider
        self.email_provider = email_provider
        self.browser_provider = browser_provider
        self.toll_bench_provider = toll_bench_provider
        self.agent_identity = agent_identity
        self.knowledge_namespace = knowledge_namespace
        self.max_iterations = max_iterations
        self.system_instruction = system_instruction

    def start(self, goal: str, mode: AutonomyMode = AutonomyMode.AUTONOMOUS) -> RunResult:
        if self.agent_identity and mode is not self.agent_identity.autonomy_mode:
            raise ValueError("Run autonomy must match the permanent agent configuration")
        agent_id = self.agent_identity.id if self.agent_identity else None
        run = self.state_store.create_run(goal, mode, self.model.model_id, agent_id)
        agent_payload = self._agent_payload()
        self.event_store.append_event(
            run.id,
            "run.started",
            "harness",
            {
                "goal": goal,
                "requested_mode": mode.value,
                "model": self.model.model_id,
                "agent": agent_payload,
            },
        )
        return self._drive(run.id)

    def resume(self, run_id: str) -> RunResult:
        run = self.state_store.get_run(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise ValueError(f"Run is already terminal: {run.status.value}")
        self.state_store.set_run_status(run_id, RunStatus.RUNNING)
        self.event_store.append_event(run_id, "run.resumed", "harness", {})
        return self._drive(run_id)

    def add_human_input(self, run_id: str, message: str) -> None:
        run = self.state_store.get_run(run_id)
        if run.status is not RunStatus.WAITING:
            raise ValueError("Human input can only be added while a run is waiting")
        self.event_store.append_event(run_id, "human.message", "human", {"message": message})

    def _initial_message(self, run_id: str) -> tuple[ModelMessage, int]:
        checkpoint = self.state_store.load_checkpoint(run_id)
        events = self.event_store.list_events(run_id, checkpoint.event_cursor)
        cursor = max((event.sequence for event in events), default=checkpoint.event_cursor)
        relevant_kinds = {
            "human.message",
            "operator.message",
            "tool.result",
            "model.error",
        }
        payload = {
            "goal": checkpoint.goal,
            "agent_identity": self._agent_payload(),
            "checkpoint": checkpoint.data,
            "persistent_knowledge": (
                self.state_store.load_knowledge(self.knowledge_namespace)
                if self.knowledge_namespace
                else {}
            ),
            "new_events": [
                {
                    "sequence": event.sequence,
                    "kind": event.kind,
                    "source": event.source,
                    "payload": event.payload,
                    "created_at": event.created_at,
                }
                for event in events
                if event.kind in relevant_kinds
            ],
        }
        return ModelMessage.text("user", self._json(payload)), cursor

    def _agent_payload(self) -> JsonObject | None:
        if self.agent_identity is None:
            return None
        identity = self.agent_identity
        return {
            "agent_id": identity.id,
            "agent_name": identity.name,
            "intelligence": identity.intelligence,
            "company": identity.company,
            "harness": identity.harness,
            "autonomy": identity.autonomy_mode.value.upper(),
            "email_status": identity.email_status.value,
            "email_verification_recipient": identity.email_verification_recipient,
            "email_address": identity.email_address,
        }

    def _inject_live_inputs(
        self, run_id: str, messages: list[ModelMessage], after_sequence: int
    ) -> int:
        events = self.event_store.list_events(run_id, after_sequence)
        if not events:
            return after_sequence
        cursor = max(event.sequence for event in events)
        inputs = [
            {
                "sequence": event.sequence,
                "kind": event.kind,
                "source": event.source,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event in events
            if event.kind in {"human.message", "operator.message"}
        ]
        if not inputs:
            return cursor
        block = {"type": "text", "text": self._json({"new_input_events": inputs})}
        if messages and messages[-1].role == "user":
            messages[-1] = ModelMessage("user", [*messages[-1].content, block])
        else:
            messages.append(ModelMessage("user", [block]))
        return cursor

    @staticmethod
    def _json(value: Any) -> str:
        import json

        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _add_usage(total: ModelUsage, current: ModelUsage) -> ModelUsage:
        return ModelUsage(
            input_tokens=total.input_tokens + current.input_tokens,
            output_tokens=total.output_tokens + current.output_tokens,
            total_tokens=total.total_tokens + current.total_tokens,
        )

    def _finish(
        self,
        run_id: str,
        status: RunStatus,
        result: JsonObject | None,
        usage: ModelUsage,
        iterations: int,
    ) -> RunResult:
        self.state_store.set_run_status(run_id, status)
        event_kind = "run.waiting" if status is RunStatus.WAITING else "run.finished"
        self.event_store.append_event(
            run_id,
            event_kind,
            "harness",
            {"status": status.value, "result": result, "usage": usage.__dict__},
        )
        run = self.state_store.get_run(run_id)
        return RunResult(
            run_id=run_id,
            status=status,
            result=result,
            checkpoint=self.state_store.load_checkpoint(run_id),
            usage=usage,
            iterations=iterations,
            observed_mode=run.observed_mode,
        )

    def _drive(self, run_id: str) -> RunResult:
        first_message, event_cursor = self._initial_message(run_id)
        messages = [first_message]
        definitions = self.tools.definitions(self.enabled_tools)
        usage = ModelUsage()
        failed_protected_writes: dict[str, int] = {}

        for iteration in range(1, self.max_iterations + 1):
            event_cursor = self._inject_live_inputs(run_id, messages, event_cursor)
            try:
                response = self.model.invoke(
                    system=self.system_instruction,
                    messages=messages,
                    tools=definitions,
                )
            except Exception as error:
                self.event_store.append_event(
                    run_id,
                    "model.error",
                    "harness",
                    {"type": type(error).__name__, "message": str(error)},
                )
                return self._finish(
                    run_id,
                    RunStatus.FAILED,
                    {"reason": "Model invocation failed", "error": str(error)},
                    usage,
                    iteration,
                )

            usage = self._add_usage(usage, response.usage)
            self.event_store.append_event(
                run_id,
                "model.response",
                "intelligence",
                {
                    "text": response.text,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "name": call.name,
                            "arguments": self._redact(call.arguments),
                        }
                        for call in response.tool_calls
                    ],
                    "stop_reason": response.stop_reason,
                    "usage": response.usage.__dict__,
                },
            )
            messages.append(response.message)

            if not response.tool_calls:
                messages.append(
                    ModelMessage.text(
                        "user",
                        "Continue the goal. Use result.complete or result.fail to end the run.",
                    )
                )
                continue

            context = ToolContext(
                run_id=run_id,
                state_store=self.state_store,
                event_store=self.event_store,
                artifact_store=self.artifact_store,
                event_cursor=event_cursor,
                web_provider=self.web_provider,
                email_provider=self.email_provider,
                browser_provider=self.browser_provider,
                toll_bench_provider=self.toll_bench_provider,
                knowledge_namespace=self.knowledge_namespace,
            )
            result_blocks: list[JsonObject] = []
            for call in response.tool_calls:
                self.event_store.append_event(
                    run_id,
                    "tool.called",
                    "intelligence",
                    {
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": self._redact(call.arguments),
                    },
                )
                if call.name not in self.enabled_tools:
                    tool_result = replace(
                        self.tools.execute(context, call.id, "__disabled__", {}),
                        name=call.name,
                        output={"error": f"Tool is not enabled: {call.name}"},
                    )
                else:
                    tool_result = self.tools.execute(context, call.id, call.name, call.arguments)
                if call.name in PROTECTED_WRITE_TOOLS:
                    failed = tool_result.is_error or tool_result.output.get("ok") is False
                    if failed:
                        count = failed_protected_writes.get(call.name, 0) + 1
                        failed_protected_writes[call.name] = count
                        if count >= MAX_FAILED_PROTECTED_WRITES:
                            context.terminal_status = RunStatus.FAILED
                            context.terminal_result = {
                                "reason": "Protected write attempt limit reached",
                                "tool": call.name,
                                "failed_attempts": count,
                                "last_error": self._redact(tool_result.output),
                            }
                self.event_store.append_event(
                    run_id,
                    "tool.result",
                    "harness",
                    {
                        "call_id": call.id,
                        "name": call.name,
                        "output": self._redact(tool_result.output),
                        "is_error": tool_result.is_error,
                    },
                )
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "call_id": call.id,
                        "name": call.name,
                        "output": tool_result.output,
                        "is_error": tool_result.is_error,
                    }
                )
            messages.append(ModelMessage(role="user", content=result_blocks))

            if context.terminal_status is not None:
                return self._finish(
                    run_id,
                    context.terminal_status,
                    context.terminal_result,
                    usage,
                    iteration,
                )
            if context.wait_requested:
                return self._finish(
                    run_id, RunStatus.WAITING, {"status": "waiting"}, usage, iteration
                )

        return self._finish(
            run_id,
            RunStatus.LIMIT_REACHED,
            {"reason": f"Maximum iterations reached ({self.max_iterations})"},
            usage,
            self.max_iterations,
        )

    @classmethod
    def _redact(cls, value: Any) -> Any:
        blocked = ("secret", "password", "credential", "access_key", "private_key", "token")
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if any(part in str(key).lower() for part in blocked)
                else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value
