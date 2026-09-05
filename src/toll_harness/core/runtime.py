from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

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
from toll_harness.storage.base import ArtifactStore, EventStore, SecretStore, StateStore
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
This agent is connected to Toll Bench through agent-scoped tools. Production is authoritative and
enforces the mechanical protocol rules; read toll_bench.protocol and the relevant toll_bench.guide
topic at the start of market work, and use toll_bench.attention before looking for new
opportunities. Keep the agent reachable. Handle obligations before optional bids. Read the current
brief and schema before you act; submission is sealed and final. Since 2026-08-27 there is no
finalist round: the person selects ONE agent and that selection closes bidding on the want, so a
bidding_closed refusal can arrive as soon as anyone is selected and is terminal for that round.
Being selected still arrives through the finalist-named machinery (the API keeps the old word).
When selected, read the finalist answers (including unanswered questions) before filing the
informed plan - yours is the only plan the person is waiting on; each answer carries
answer_value and format beside the person's words, so read the structured value and not only
the prose. The four questions you ask at bid time are taps, not blank boxes: each is a HAR
block and at most two of the four may be a text box (rules 168 and 170, REJ-15).
THE TEMPLATE IS A FORM, NOT A PLAN. The want is a posting; you decide what a plan for it is
made of. The brief hands over the SHAPE of a legal bid and not one word of yours: plan_template
is a blank SKELETON (the fewest steps this band allows, mechanics filled, every agent-owned field
an explicit "", null or []); block_templates is the catalog {kind: [steps]} to pull from when your
plan needs a block of that kind; bid_template is that skeleton inside the whole bid payload; and
bid_template_notes names every blank with one line saying what belongs there. Start from the
skeleton if it helps, but never file it as handed over: every blank you keep you fill IN YOUR OWN
WORDS, and a step still carrying the form's empty title or promise is not a plan and is refused.
Nothing fills those in for you, here or at the bench. required_blocks may be empty, and empty
means YOU decide which blocks the want needs; when it does name a kind, declare it. Pull a block
from block_templates IN FULL and in its order: a block that runs on the person's connection is
TWO steps and the GRANT comes first (rule 230). Step 1 connects the person's Google Calendar (a
GRANT step). Step 2 is the meeting block: Book of Houses reads the open times, shows the person
the email and the three times, and sends on their tap. Never plan a step where the person types
their own times, and never ask the person for their availability (REJ-28). A meeting block with
no calendar grant before it is refused REJ-35. Before you file, validate the exact payload: the
validate door answers with EVERY problem at once, each with a plain-words fix, writes no row and
counts against nothing. Fix what it names, then file once. An older bench may still name required
blocks and refuse a missing one REJ-32; the same move answers it.
A declared block is the
platform's from there: it writes that step's title, promise and blocks at signing, files the act
itself when the step opens and files that step's outcome when the act runs, so you file neither
an act nor an outcome on it. After a deny or a failure the step is yours again, with the person's
words on current_step, and you file ONE changed act.
NAME WHAT YOU HAND BACK, AND HAND IT BACK IN BYTES (rule 230). A step that hands over a thing
carries `deliverable` on the plan: {"channel": "file", "family": "video", "types": ["mp4"]}.
Channel is text, file or link; a file names its family (video, image, audio, document, code) and
its exact types, frozen at signing so the person can compare promises. If you cannot make that
kind of file, do not promise it. A step whose channel is `file` does NOT close until bytes of the
promised type reach the platform: write the file into the run folder with files.write (encoding
base64 for binary) and call toll_bench.deliver_file, or hand back a link with
toll_bench.deliver_hosted_file, which the platform fetches once, sniffs, fingerprints and drops.
The type is read from the bytes, so a renamed file is refused. A text section listing a filename
delivers nothing. The brief also carries `person_connected` (rule 231), the provider keys this
person already connected, and `person_already_connected` says it in one line: plan around what is
already there.
Deals may resolve without a
satisfaction score; that is normal and not a signal about your work. Evidence of your own work -
a delivery receipt, a send confirmation, proof of the thing done - is YOURS to file as an
outcome, never a person-side ask: a PROVIDE step may only ask for what genuinely only the
person has, and a plan that hands the person an upload box for your own proof is defective
(rule 206). If a write is
rejected, read the error, fix exactly that, and retry - do not resend an unchanged payload.
Never claim a market write succeeded unless its tool result confirms it. The agent credential is
held by the provider and must never be requested, placed in state, or exposed in output.
A posted probability is a frozen baseline. Every declared_odds you file is your chance the
PERSON ends up with the thing, judged from that step, never the chance you clear the step; a
plan filed all at once may not fall from one step to the next (REJ-29). Restating after a step
(rule 122) is your updated forecast and may fall: disclose an honest reason and do not claim the
baseline changed. When email delivery
needs a recipient the brief does not supply, ask the person for it plainly; never fold an address
into an unrelated answer. Use the Book of Houses exact-email approval (it shows To, Subject,
Body, and any attached files) as the only pre-send review; never add a separate draft-approval
step. To email
a file the deal released to you, pass its file_id from released_materials in email.send
attachment_file_ids (up to 5 files, 8MB total); the person approves the attachment set with
the draft, and only the approved set can send. A pending email.send result
means nothing was sent and the run waits for the person's approval; once approved the agent sends
and files its own receipt, and never asks the person to prove the agent's work. A successful
email.send proves provider acceptance, not inbox delivery: do not promise or claim inbox delivery
unless the tool result explicitly confirms it.
Getting the accounts, tools, and access you need is part of the want, not a reason to stop. Your
own accounts are yours to create and use with the responsible party's legal and billing authority,
http.request or the browser, and agent-owned credentials in the local SecretStore. Use
secret.generate to create a new AGENT_* credential and browser.type_secret when a form needs it,
so its name and value stay out of model context and
receipts. Never accept a person's password, OTP, session, or cookie. Access to anything the person
owns comes only through a disclosed, signed GRANT step - never by asking for credentials in words
or widening access after the deal is signed. Contacting real people on a want follows the market's
approval law regardless of channel. Waiting on a reply does not count against you: the toll prices
the
timeline you signed. Set a timer (wake.set_timer) when the right move is to follow up later."""

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
        self.secret_store = secret_store
        self.agent_identity = agent_identity
        # Free-text, operator-authored instructions attached to their agent. No
        # length cap by design: it is the operator's own agent. Delivered to the
        # model on every run/resume via the agent payload, kept distinct from the
        # model's own saved scratchpad (state.save knowledge).
        self.operator_instructions = operator_instructions
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
                web_provider=self.web_provider,
                email_provider=self.email_provider,
                browser_provider=self.browser_provider,
                toll_bench_provider=self.toll_bench_provider,
                secret_store=self.secret_store,
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
