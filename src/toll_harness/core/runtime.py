from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from toll_harness.browser.base import BrowserProvider
from toll_harness.core.budget import ContextBudget, measure, resolve_context_budget
from toll_harness.core.own_means import OwnMeans, own_means_line
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
from toll_harness.models.base import ModelAdapter, what_came_back
from toll_harness.storage.base import ArtifactStore, EventStore, SecretStore, StateStore
from toll_harness.toll_bench.base import TollBenchProvider
from toll_harness.tools.registry import ToolContext, ToolRegistry
from toll_harness.tools.web import WebProvider

_LOGGER = logging.getLogger("toll_harness.runtime")

BASE_SYSTEM_INSTRUCTION = """You are the intelligence operating through Toll Harness.
You decide how to pursue the user's goal. The harness only preserves state and executes tools.
Use your available capabilities, including your own runtime tools. Keep a compact continuation
checkpoint with state.save when facts, pending work, status, or the next intended action should
survive a wait or restart. Never
put secrets or credentials in the checkpoint. Call result.complete when the goal is complete or
result.fail when it cannot be completed. Do not claim a tool action happened unless its result
confirms it. Tool arguments must contain only fields declared by that tool; never add reasoning or
chain-of-thought fields."""

TOLL_BENCH_SYSTEM_INSTRUCTION = """
This agent is connected to Toll Bench through agent-scoped tools. The bench is the authority and
says its own rules once: toll_bench.guide is the whole skill page, the brief, the plan door and
the step carry their forms and notes, and toll_bench.doors lists every door it publishes. Read
them rather than guess. Handle obligations (toll_bench.attention) before optional bids. A refused
write comes back in the bench's own words: fix exactly what it names and send again, never an
unchanged payload. Never claim a write succeeded unless its result confirms it. Every
declared_odds you file is your chance the PERSON ends up with the thing, strictly between 0 and
1. Anything that reaches a person or an account (an email, a calendar event, a post) is an act
the person approves word for word (toll_bench.propose_act), never something you ask them to do,
and evidence of your own work is yours to file. A promised file goes back in bytes
(toll_bench.deliver_file or toll_bench.deliver_hosted_file). The agent credential is held by the
provider and must never be requested, placed in state, or exposed in output. Your own accounts
are yours to create with the responsible party's authority (http.request, the browser,
secret.generate, browser.type_secret); never accept a person's password, OTP, session or cookie.
Waiting on a reply does not count against you; set wake.set_timer to follow up later."""

# THE PAST IS A SUMMARY (0.36.0). A tool result the model has already read
# once is not re-sent whole on every later call. What forced it: on the old
# road every result stayed in the conversation for the rest of the run, so a
# 48,529-character brief and a 213,097-character proposals list rode every
# one of nine calls on one fleet unit (2026-09-09, 10:39-10:43) -- 79,500 input tokens
# a call, 532,529 for the run. Before each call after the first, every tool
# result older than the last call is cut to its first 400 characters and a
# note saying so; the model asks again if it needs the rest. The one payload
# kept whole is the step it is walking (toll_bench.current_step).
FOLD_KEEP_CHARS = 400
FOLD_NOTE = "(older result, ask again if needed)"
KEEP_WHOLE_TOOLS = frozenset({"toll_bench.current_step"})

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
        secret_store: SecretStore | None = None,
        agent_identity: AgentIdentity | None = None,
        operator_instructions: str | None = None,
        own_means: tuple[OwnMeans, ...] | list[OwnMeans] | None = None,
        knowledge_namespace: str | None = None,
        max_iterations: int = 20,
        context_budget_tokens: int | None = None,
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
        self.secret_store = secret_store
        self.agent_identity = agent_identity
        # Free-text, operator-authored instructions attached to their agent. No
        # length cap by design: it is the operator's own agent. Delivered to the
        # model on every run/resume via the agent payload, kept distinct from the
        # model's own saved scratchpad (state.save knowledge).
        self.operator_instructions = operator_instructions
        # What the agent does by its own means, outside the tool list
        # (agent.yaml `agent.own_means`, 0.58.0). A declaration, never a tool:
        # it shapes the prompts that weigh "do I have a tool for this".
        self.own_means: tuple[OwnMeans, ...] = tuple(own_means or ())
        self.knowledge_namespace = knowledge_namespace
        self.max_iterations = max_iterations
        # INPUT tokens this run may spend before it stops itself. Measured
        # from the provider's own usage numbers after every call; 0 turns the
        # guard off. See core/budget.py for what forced it.
        self.context_budget_tokens = resolve_context_budget(
            context_budget_tokens, getattr(model, "context_window_tokens", None)
        )
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

    def resume(
        self, run_id: str, *, cause: str | None = None, note: str | None = None
    ) -> RunResult:
        run = self.state_store.get_run(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise ValueError(f"Run is already terminal: {run.status.value}")
        self.state_store.set_run_status(run_id, RunStatus.RUNNING)
        payload: JsonObject = {}
        if cause:
            payload["cause"] = cause
            if note:
                payload["note"] = note
        self.event_store.append_event(run_id, "run.resumed", "harness", payload)
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
            "run.resumed",
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
        payload: JsonObject = {
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
        # Operator instructions ride the agent payload so the model sees them on
        # every run and resume. Omitted entirely when unset so agents without the
        # field produce a byte-identical payload to before (no empty-string noise).
        if self.operator_instructions:
            payload["operator_instructions"] = self.operator_instructions
        # Same law: omitted when the agent declared none.
        if self.own_means:
            payload["own_means"] = own_means_line(self.own_means)
        return payload

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
        native_artifact_aliases = {
            event.payload["source_path"]: event.payload["path"]
            for event in self.event_store.list_events(run_id)
            if event.kind == "model.artifact" and event.source == "harness"
        }
        budget = ContextBudget(limit=self.context_budget_tokens)

        for iteration in range(1, self.max_iterations + 1):
            event_cursor = self._inject_live_inputs(run_id, messages, event_cursor)
            if iteration > 1:
                messages, folded, before, after = self._fold_older_tool_results(messages)
                if folded:
                    _LOGGER.info(
                        "folded %d older tool result(s) before model call %d: "
                        "%d -> %d chars (~%d tokens saved)",
                        folded,
                        iteration,
                        before,
                        after,
                        max(0, before - after) // 4,
                    )
            # THE RUN STOPS BEFORE THE PROVIDER DOES. The last call's input
            # token count is this conversation's size; the next call is that
            # plus everything appended since. Crossing the budget ends the run
            # here, with the step it was on and the last tool it called, rather
            # than in a provider 400 that says only that the prompt was long.
            conversation_chars = self._conversation_chars(messages)
            if budget.would_exceed(conversation_chars):
                report = budget.report(conversation_chars)
                _LOGGER.warning(
                    "Run %s stopped on the context budget: next prompt ~%d input "
                    "tokens over a budget of %d, on %s, last tool %s",
                    run_id,
                    report["estimated_next_input_tokens"],
                    budget.limit,
                    report.get("step_title") or report.get("step_id") or "no named step",
                    report.get("last_tool") or "none",
                )
                self.event_store.append_event(run_id, "run.context_budget", "harness", report)
                return self._finish(run_id, RunStatus.FAILED, report, usage, iteration)
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

            for artifact in response.artifacts:
                # The adapter supplies bytes; the runtime never opens a model path.
                from pathlib import PurePath

                if PurePath(artifact.filename).name != artifact.filename:
                    raise ValueError("Invalid native artifact filename")
                imported = self.artifact_store.write(
                    run_id, "generated/" + artifact.filename, artifact.content
                )
                native_artifact_aliases[artifact.source_path] = imported["path"]
                self.event_store.append_event(
                    run_id, "model.artifact", "harness",
                    {**imported, "source_path": artifact.source_path},
                )

            usage = self._add_usage(usage, response.usage)
            budget.record(response.usage, conversation_chars)
            # ONE LINE PER MODEL CALL (0.68.0): the budget's own line, then
            # the run and the work it holds, the prompt's size, the answer's
            # size and what stopped it.
            held = " ".join(
                f"{name}={budget.place[name]}"
                for name in ("target_id", "deal_id", "step_id")
                if budget.place.get(name)
            )
            _LOGGER.info(
                "%s; run %s%s, prompt %d chars; %s",
                budget.line(), run_id, f" {held}" if held else "",
                conversation_chars, what_came_back(response),
            )
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
                            "arguments": self._audit_arguments(call.name, call.arguments),
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
                native_artifact_aliases=native_artifact_aliases,
                web_provider=self.web_provider,
                email_provider=self.email_provider,
                browser_provider=self.browser_provider,
                toll_bench_provider=self.toll_bench_provider,
                secret_store=self.secret_store,
                knowledge_namespace=self.knowledge_namespace,
            )
            result_blocks: list[JsonObject] = []
            for call in response.tool_calls:
                self._note_place(budget.place, call.name, call.arguments)
                self.event_store.append_event(
                    run_id,
                    "tool.called",
                    "intelligence",
                    {
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": self._audit_arguments(call.name, call.arguments),
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
                self._note_place(budget.place, call.name, call.arguments, tool_result.output)
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

    @staticmethod
    def _fold_older_tool_results(
        messages: list[ModelMessage],
    ) -> tuple[list[ModelMessage], int, int, int]:
        """Every tool result the model already read, cut to its first 400
        characters plus a note. The LAST message is never touched: it holds
        the results the model has not seen yet. Returns the new list, how
        many were folded, and the characters before and after."""
        import json

        folded = 0
        before = 0
        after = 0
        out: list[ModelMessage] = []
        for message in messages[:-1]:
            if message.role != "user":
                out.append(message)
                continue
            changed = False
            blocks: list[JsonObject] = []
            for block in message.content:
                output = block.get("output") if block.get("type") == "tool_result" else None
                if (
                    output is None
                    or block.get("name") in KEEP_WHOLE_TOOLS
                    or (isinstance(output, dict) and output.get("note") == FOLD_NOTE)
                ):
                    blocks.append(block)
                    continue
                text = json.dumps(output, separators=(",", ":"), default=str)
                if len(text) <= FOLD_KEEP_CHARS:
                    blocks.append(block)
                    continue
                summary = {"older_result": text[:FOLD_KEEP_CHARS], "note": FOLD_NOTE}
                before += len(text)
                after += len(json.dumps(summary, separators=(",", ":")))
                blocks.append({**block, "output": summary})
                folded += 1
                changed = True
            out.append(ModelMessage(message.role, blocks) if changed else message)
        if messages:
            out.append(messages[-1])
        return out, folded, before, after

    @staticmethod
    def _conversation_chars(messages: list[ModelMessage]) -> int:
        """How big the prompt for the next call would be, in characters."""
        return sum(measure(message.content) for message in messages)

    @staticmethod
    def _note_place(
        place: JsonObject, name: str, arguments: Any, output: Any = None
    ) -> None:
        """Where the run was when it stopped.

        A budget failure that says only "too many tokens" tells the foreman
        nothing. These are the handles that name the work: the last tool
        called, and whatever target, deal or step the run was holding. Read
        off the calls the run already makes, never a call of its own.
        """
        place["last_tool"] = name
        if isinstance(arguments, dict):
            for key in ("target_id", "deal_id", "step_id", "proposal_id"):
                value = arguments.get(key)
                if value:
                    place[key] = str(value)
        if isinstance(output, dict):
            step = output.get("current_step")
            if isinstance(step, dict):
                if step.get("id"):
                    place["step_id"] = str(step["id"])
                if step.get("number") is not None:
                    place["step_number"] = step["number"]
                if step.get("title"):
                    place["step_title"] = str(step["title"])

    @classmethod
    def _audit_arguments(cls, name: str, arguments: Any) -> Any:
        """Event-safe view of a tool call's arguments.

        http.request arguments can carry credentials (resolved or as
        {{secret:...}} placeholders, which are sensitive too) in the URL,
        header values, and body; the audit trail records only the method, the
        target domain, the header names, and the body size. If the host itself
        cannot be stated without exposing a placeholder, it is omitted.
        """
        if name == "http.request" and isinstance(arguments, dict):
            headers = arguments.get("headers")
            body = arguments.get("body")
            try:
                host = urlparse(str(arguments.get("url") or "")).hostname or None
            except ValueError:
                host = None
            if host and ("{{" in host or "}}" in host):
                host = None
            return {
                "method": arguments.get("method"),
                "domain": host,
                "header_names": sorted(headers) if isinstance(headers, dict) else [],
                "body_bytes": len(str(body).encode("utf-8")) if body is not None else 0,
            }
        return cls._redact(arguments)

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
