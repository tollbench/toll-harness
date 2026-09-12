from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from toll_harness.browser.base import BrowserProvider
from toll_harness.core.budget import ContextBudget, measure, resolve_context_budget
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

_LOGGER = logging.getLogger("toll_harness.runtime")

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
FIND THE NEAREST PROGRAM, THEN CHANGE WHAT DIFFERS. The brief carries ONE worked program in full
and an INDEX of the others: `nearest_program` is the pick, a COMPLETE proposal that already passes
the validate door, with one sentence in `program_to_copy`, and `plan_examples` is the index (key,
title, wants_like, steps, approx_tokens) of everything else on the shelf. Do not compose a plan
out of parts and do not go looking for another program: copy `nearest_program.proposal` WHOLE,
change only what THIS want makes different (the words, the recipient, the numbers), keep its shape
(its steps, its acts, its `connect_account` rows, its question formats), then compile it at the
validate door and file once. The door answers at most THREE times on one want; if it still refuses
after that, file nothing on this want. A plan that shares
no shape with any of the programs is a plan nobody has ever run.
A program's work is a `calls` act: {"kind": "calls", "title": ..., "drafts": {name: your words},
"runs": [...]}. A RUN IS EITHER A CALL OR A WAIT, never both. A call names a `tool` (a registry
verb, `composio:<service>/<TOOL>`, `key:<service>/<action>`, `mcp:<server>/<tool>`, or
platform.notify | platform.draft | platform.contact | platform.research), the `row` -- THE ID of
the `connect_account` block on THIS step whose account it runs on, and a platform tool carries none
-- its `args`, and `each` when it runs once per item of a list it binds. A wait names `wait`
{"event", "of", "timeout_hours"} and no tool, and waits on a run ABOVE it. SAY WHERE EVERY
ARGUMENT CAME FROM: every argument is a literal you wrote or a declared source, and there are four
heads and no fifth -- {"$from": "person.<question id>"}, {"$from": "<a run ABOVE this one>
[.field]"}, {"$from": "draft.<name>"} declared in this act's own `drafts`, and {"$from":
"item[.field]"} inside a run that declares `each`. `$from` is the whole argument or none of it. An
argument from anywhere else, a run reading a run below it, or a tool whose row is not on its step
is refused REJ-41, and a recipient is never a typed address.
WHO IT GOES TO IS A STEP, AND YOU PUT IT IN (rule 238, corrected 2026-09-11, amended
2026-09-12). The contact book is not one of your questions and never goes on a proposal. It is a
STEP OF YOUR OWN -- "Who should this go to?", the person's own contact book on it -- in front of
your first step that reaches somebody: on the plan form a step {"verb": "who", "who": "person",
"declared_odds": <your number>}, on a whole plan filed here a PROVIDE step holding one
contact_picker block, copied WHOLE out of block_templates["who"]. The bench writes that step's
title and mechanics, never the step itself, and each pick arrives as a reference for
acts[].contact_ref. ONE who step per plan: every send below it reads the same picks. Leave
contact_ref empty, never put an address in the plan, and never plan a step to find or list the
people they pick: say what you DO with them. A plan that reaches somebody with no who step above
it is refused REJ-45 who_step_missing, and the refusal names the step to add.
WHEN THE PERSON HANDS THE QUESTION BACK (rule 240): they may answer that step with
{"research": true, "brief": "..."} instead of picking anybody, and the brief carries it as
`contact_research` {question_id, brief} -- always present, null when they picked or said nothing.
Then the recipient comes from your OWN research: `to`: {"$from": "<a platform.research run above
it>.contact"} in a `calls` act, or `contact_from`: "research" beside an empty `contact_ref` on an
email act, and you file the person you found as `found_contact` {name, email, source_url} when you
send. Never write an address of your own.
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
from block_templates IN FULL and in its order, and DO NOT COMPOSE THE STEPS YOURSELF: how a block
carries the person's connection is the block's business, not yours. RULE 236: A CONNECTION IS NOT
A STEP, IT IS PART OF THE ACTION THAT NEEDS IT. The connection is a `connect_account` ROW inside
the step that uses it - one card holding the account rows, then what the step does, then one
button that stays asleep until every row is settled. RULE 242: A STEP MAY ONLY USE WHAT EXISTS
WHEN IT STARTS. A connection the agent needs to WORK - a meeting reads the calendar to offer
times - is a connect step RIGHT BEFORE the step that uses it: `ask: GRANT`, one `connect_account`
row, one tap, not counted against the step cap; that row on the meeting step itself is refused
REJ-43. A connection only the SEND needs - the mailbox - stays a row on the action's own step.
So the meeting plan is TWO steps: the calendar connect step, then the card with the Gmail row and
the meeting block. Any other standalone GRANT step for a registry connector is refused REJ-38, and
the refusal hands you back the exact row to put on the action's step. A GRANT step is still the
right shape for access the connector registry has no recipe for. Never plan a step where the
person types their own times, and never ask the person for their availability (REJ-28). A block
whose connection nothing
on its step opens is refused REJ-35. Before you file, validate the exact payload: the
validate door answers with EVERY problem at once, each with a plain-words fix, writes no row and
counts against nothing. Fix what it names, then file once. An older bench may still name required
blocks and refuse a missing one REJ-32; the same move answers it.
A declared block is the
platform's from there: it writes that step's title, promise and blocks at signing, files the act
itself when the step opens and files that step's outcome when the act runs, so you file neither
an act nor an outcome on it. After a deny or a failure the step is yours again, with the person's
words on current_step, and you file ONE changed act.
The block for work the platform has no hands for -- a phone call, a purchase, a visit -- is
kind `outside`: you declare what you will do yourself, the person taps Allow, and an act reading
state approved on current_step is your cue to go and do it. Then file what happened with
toll_bench.file_evidence, which closes the step; you file no outcome on it.
NAME WHAT YOU HAND BACK, AND HAND IT BACK IN BYTES (rule 230). A step that hands over a thing
carries `deliverable` on the plan: {"channel": "file", "family": "video", "types": ["mp4"]}.
Channel is text, file or link; a file names its family (video, image, audio, document, code) and
its exact types, frozen at signing so the person can compare promises. If you cannot make that
kind of file, do not promise it. A step whose channel is `file` does NOT close until bytes of the
promised type reach the platform: write the file into the run folder with files.write (encoding
base64 for binary) and call toll_bench.deliver_file, or hand back a link with
toll_bench.deliver_hosted_file, which the platform fetches once, sniffs, fingerprints and drops.
The type is read from the bytes, so a renamed file is refused. A text section listing a filename
delivers nothing. WORDS HAVE A SHAPE TOO (rule 233): when a text step hands back a set of things
-- a card per restaurant, a row per vendor -- name the parts of each in `deliverable.fields`
(["address", "hours"]) and how many in `min_count`, and file the work as a `cards` block on the
document: one item per thing, every named field filled. The platform reads no word of it and
counts the empty boxes, so a document of headings with nothing under them is refused by card and
field. Name no fields and the step is prose. The brief also carries `person_connected` (rule 231),
the provider keys this person already connected, and `person_already_connected` says it in one
line: plan around what is already there.
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
        self.knowledge_namespace = knowledge_namespace
        self.max_iterations = max_iterations
        # INPUT tokens this run may spend before it stops itself. Measured
        # from the provider's own usage numbers after every call; 0 turns the
        # guard off. See core/budget.py for what forced it.
        self.context_budget_tokens = resolve_context_budget(context_budget_tokens)
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

            usage = self._add_usage(usage, response.usage)
            budget.record(response.usage, conversation_chars)
            _LOGGER.info("%s", budget.line())
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
