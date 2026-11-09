"""THE STEP ASK -- one step of a signed plan is one small question and one call.

WHAT FORCED THIS MODULE (Steven, 2026-09-09, 19:55): "It's just stepping
through the plan. why would that cost so much? ... fix that please." One deal
step had cost 261,749 input tokens over twenty calls on the old road.

0.74.0 (harness thinness audit, item 9): THE STEP IS THE BENCH'S FORMS. The
harness no longer decides whose move a step is or which of seven moves it is.
It reads three things off the current-step answer and relays them:

  1. WHOSE TURN. `turn.whose_turn` (contract 4.0.7) says it: `agent` is a
     move; anything else starts no model call. The person's own words come
     first: an unanswered message or an owed reply is always the agent's to
     answer. Only a bench that sends no `turn` at all falls back to the step's
     own `state`.
  2. ONE TOOL PER FORM. Every row on `submission.actions` is a tool, named by
     the bench, with the bench's own schema, posted to its own endpoint; the
     worker-status form is `report_parked`; a send-out's `moves` are tools too;
     a file step's `deliver_file` reads a file the model made in its own
     workdir. `need_tools` is always there: it hands the step to the old road.
  3. THE BENCH'S PAYLOAD, WHOLE. The tail is the current-step answer as the
     bench sent it -- `turn`, `instruction`, `recommended_action`,
     `prerequisites`, `earlier_steps`, `repeats`, `sent_out`, `rewound`, the
     person's words -- minus only the forms, which are the tools.

One call; post; a refusal comes back as `the_bench_refused` on the one more
ask. The reply machinery (0.56.1: a reply names what it answers) and the park
report (W29: an outside step never ends in silence) are kept.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from toll_harness.core import thinking
from toll_harness.core.budget import ContextBudget
from toll_harness.core.operator_block import with_operator_block
from toll_harness.core.own_means import OwnMeans, with_own_means
from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.base import what_came_back
from toll_harness.toll_bench.draft import (
    PERSON_SAID_INSTRUCTION,
    PERSON_SAID_KEY,
    cached_input_tokens,
    read_json_object,
    stable_prefix,
)

_LOGGER = logging.getLogger("toll_harness.step")

# The one-ball state that means the agent holds the step (the fallback when a
# bench sends no `turn`).
AGENT_STEP_STATE = "agent_working"

# The bench's names for the forms this module adds plumbing to.
OUTSIDE_EVIDENCE_ACTION = "file_outside_evidence"
STEP_MESSAGE_ACTION = "post_step_message"
OUTCOME_ACTION = "submit_step_outcome"
DELIVER_FILE_ACTION = "deliver_file"
# What the harness posts on the thread when it parks a step itself, because
# the model made no call at all. The model's own sentence is better and is
# used when there is one; this is the floor, and silence is not an option.
OUTSIDE_PARKED_SENTENCE = (
    "I cannot do this one myself and I have stopped rather than sit on it: "
    "the work has to happen on a service I have no tool for. Nothing is "
    "moving on this step until you decide what to do with it."
)

ROAD_STEP_ASK = "step_ask"
ROAD_AGENTIC = "agentic"
NEED_TOOLS = "need_tools"
REPORT_PARKED = "report_parked"

# Keys the provider and the old road still read by name.
EARLIER_STEPS_KEY = "earlier_steps"
LAST_FILING_KEY = "your_last_filing"
# Where `cli._permitted_step_state` lists the forms the agent's tool list
# turned off: [{"action": bench action, "tools": [the tool names missing]}].
FORMS_TURNED_OFF = "forms_turned_off"
_REPLY_IN_ENDPOINT = re.compile(r"/replies/([^/]+)/dismiss$")
# A file deliver_file reads stays under the platform's own per-file cap.
DELIVER_FILE_MAX_BYTES = 50 * 1024 * 1024

STEP_DOOR = (
    "THIS IS NOT A PLAN ROUND. The plan is signed and you are walking ONE step "
    "of it. Below is the bench's own answer for this step, whole: `turn` and "
    "`submission.instruction` say the one move that is yours, "
    "`recommended_action` names the form, and every form is a tool you are "
    "given. Make exactly ONE call. If the person wrote to you and it is "
    "unanswered, answer them first with post_step_message. If you cannot do "
    "this step at all, call report_parked and say why in one plain sentence. "
    "If it needs a live search, a browser, a file you have not made yet, or a "
    "tool you were not given, call need_tools. Never ask for a password, code, "
    "session or cookie."
)

REFUSED_INSTRUCTION = (
    "Your last answer was refused; the bench's words are in "
    "`the_bench_refused`. Fix exactly what they name and make the call once more."
)

# A REFUSAL MADE ON THIS MACHINE SAYS SO (2026-11-08, Sam on want 9b2fc180).
# The provider marks every refusal it makes before sending with
# `refused_by: "harness"`; the step labels it `harness_rejected`, not
# `server_rejected`, and the one more try says the bench never saw it. Sam's
# outcome named both a link and words, this harness refused it in 0 ms, and
# the label told him the bench had.
REFUSED_HERE_INSTRUCTION = (
    "Your last answer was refused on this machine before it was sent; the bench "
    "never saw it. The reason is in `the_bench_refused`. Fix exactly what it "
    "names and make the call once more."
)


def _refused_here(result: dict[str, Any] | None) -> bool:
    return isinstance(result, dict) and result.get("refused_by") == "harness"

PULSE_DUE_INSTRUCTION = (
    "A progress pulse is due (rule 100): if you are not filing the outcome now, "
    "post one honest pulse."
)

NEED_TOOLS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"why": {"type": "string", "minLength": 1}},
    "required": ["why"],
}
DELIVER_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1,
                 "description": "The file you made, under your own working directory."},
        "title": {"type": "string", "minLength": 1, "maxLength": 80,
                  "description": "The item's real name as the person's card wears it."},
        "filename": {"type": "string"},
    },
    "required": ["path", "title"],
}
_OPEN_SCHEMA: dict[str, Any] = {"type": "object"}


# ---------------------------------------------------------------------------
# 1. WHOSE TURN
# ---------------------------------------------------------------------------
_PARTY_WORDS = {"agent": "agent", "you": "human", "outside": "outside"}


def turn_of(payload: Any) -> dict[str, Any] | None:
    """The bench's `turn` on this answer (root, else `submission.turn`)."""
    if not isinstance(payload, dict):
        return None
    turn = payload.get("turn")
    if isinstance(turn, dict):
        return turn
    submission = payload.get("submission")
    turn = submission.get("turn") if isinstance(submission, dict) else None
    return turn if isinstance(turn, dict) else None


def whose_turn(payload: Any) -> str | None:
    """`agent`, `human`, `outside`, or None (no turn: not started or closed).

    Read off `turn.whose_turn`, else the retiring `turn.party`. Only a bench
    that sends no `turn` at all falls back to the step's own state, and a
    payload that says neither is the agent's to look at (as it always was)."""
    turn = turn_of(payload)
    if turn is not None:
        whose = str(turn.get("whose_turn") or "").strip().lower()
        if whose:
            return whose
        party = str(turn.get("party") or "").strip().lower()
        return _PARTY_WORDS.get(party)
    step = (payload or {}).get("current_step") if isinstance(payload, dict) else None
    state = str((step or {}).get("state") or "").strip().lower()
    return "agent" if state in ("", AGENT_STEP_STATE) else "human"


def not_the_agents_move(payload: Any) -> str | None:
    """Why this step starts no model call, in the bench's words; None when it
    is the agent's to move. The person's words always come first."""
    if not isinstance(payload, dict):
        return None
    if unanswered_on_step(payload) or payload.get("owed_replies"):
        return None
    thread = payload.get("step_thread") if isinstance(payload.get("step_thread"), dict) else {}
    if thread.get("unanswered_elsewhere"):
        return None
    whose = whose_turn(payload)
    if whose == "agent":
        return None
    turn = turn_of(payload) or {}
    words = turn.get("why") or turn.get("label") or turn.get("state")
    return f"turn {whose or 'none'}" + (f": {words}" if words else "")


# ---------------------------------------------------------------------------
# 2. ONE TOOL PER FORM
# ---------------------------------------------------------------------------
def _form_words(form: dict[str, Any]) -> str:
    words = [str(form.get("result") or "")]
    if form.get("template") is not None:
        words.append("Template: " + json.dumps(form["template"], default=str))
    if form.get("field_notes"):
        words.append("Fields: " + json.dumps(form["field_notes"], default=str))
    if form.get("prerequisites"):
        words.append("Prerequisites: " + json.dumps(form["prerequisites"], default=str))
    return " ".join(word for word in words if word)


def _tool_name(name: str, taken: dict[str, Any]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "form"
    candidate, index = base, 1
    while candidate in taken:
        index += 1
        candidate = f"{base}_{index}"
    return candidate


def step_tools(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Tool name -> {action, schema, form}: one tool per form the bench
    publishes on this step, generated at run time. Two forms of one name (two
    owed replies, two declared acts) are two tools, `propose_act`,
    `propose_act_2`. The schema is the bench's, whole."""
    submission = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
    tools: dict[str, dict[str, Any]] = {}
    for form in submission.get("actions") or []:
        if not isinstance(form, dict) or not form.get("action"):
            continue
        action = str(form["action"])
        schema = form.get("schema") if isinstance(form.get("schema"), dict) else None
        if action == DELIVER_FILE_ACTION:
            schema = DELIVER_FILE_SCHEMA
        if schema is None:
            continue
        tools[_tool_name(action, tools)] = {"action": action, "schema": schema, "form": form}
    status = submission.get("worker_status")
    if isinstance(status, dict) and isinstance(status.get("schema"), dict):
        tools[REPORT_PARKED] = {"action": REPORT_PARKED,
                                "schema": parked_schema(status["schema"]), "form": status}
    sent_out = payload.get("sent_out") if isinstance(payload.get("sent_out"), dict) else {}
    for move in sent_out.get("moves") or []:
        if not isinstance(move, dict) or not move.get("move") or not move.get("call"):
            continue
        name = _tool_name(f"sent_out_{move['move']}", tools)
        form = {"action": name, "endpoint": move.get("call"), "template": move.get("body"),
                "result": f"The send-out move `{move['move']}`, as the bench offers it."}
        tools[name] = {"action": name, "schema": _OPEN_SCHEMA, "form": form,
                       "body": move.get("body") if isinstance(move.get("body"), dict) else {}}
    tools[NEED_TOOLS] = {"action": NEED_TOOLS, "schema": NEED_TOOLS_SCHEMA, "form": {}}
    return tools


def parked_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The worker-status form, narrowed to the one report this move makes:
    stopped, and why. `running` is the runtime's own word, never the model's."""
    properties = dict(schema.get("properties") or {})
    properties["state"] = {"const": "parked"}
    properties["reason"] = {"type": "string", "minLength": 1, "maxLength": 280}
    return {"type": "object", "properties": properties,
            "required": ["state", "round", "reason"]}


def _definition(name: str, tool: dict[str, Any]) -> ToolDefinition:
    if tool["action"] == NEED_TOOLS:
        words = ("This step needs a live search, a browser, a file or a tool you "
                 "were not given, or the bench's whole record of an earlier step. "
                 "Say which in `why`; the step goes to the road that has them.")
    elif tool["action"] == REPORT_PARKED:
        words = ("Say out loud that you have stopped and why, in one plain sentence: "
                 "it stops the clock and posts your sentence on the step for the person.")
    elif tool["action"] == DELIVER_FILE_ACTION:
        words = ("Hand the person a file you made: `path` under your own working "
                 "directory, `title` the item's real name. " + _form_words(tool["form"]))
    else:
        words = _form_words(tool["form"])
    return ToolDefinition(name, words, tool["schema"])


_CALL_NAME_KEYS = ("name", "tool", "call", "function")
_CALL_ARGUMENT_KEYS = ("arguments", "args", "input", "parameters")


def tool_call_in_text(text: Any, tools: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """(name, arguments) when the text holds one JSON tool call naming a
    tool on offer: {"name": t, "arguments": {...}}, {"tool": t, ...the
    arguments...}, or {t: {...}}. None otherwise."""
    parsed = read_json_object(text)
    if not parsed:
        return None
    name = next((parsed[key] for key in _CALL_NAME_KEYS
                 if isinstance(parsed.get(key), str)), None)
    if name in tools:
        arguments = next((parsed[key] for key in _CALL_ARGUMENT_KEYS
                          if isinstance(parsed.get(key), dict)), None)
        if arguments is None:
            arguments = {k: v for k, v in parsed.items() if k not in _CALL_NAME_KEYS}
        return str(name), arguments
    if len(parsed) == 1:
        ((only, value),) = parsed.items()
        if only in tools and isinstance(value, dict):
            return only, value
    return None


def _schema_problem(arguments: Any, schema: dict[str, Any]) -> str | None:
    """Where the arguments break the bench's schema, never their values."""
    error = next(iter(sorted(
        Draft202012Validator(schema).iter_errors(arguments),
        key=lambda e: len(list(e.absolute_path)),
    )), None)
    if error is None:
        return None
    path = "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )
    return f"arguments{path} fails {error.validator}"


# ---------------------------------------------------------------------------
# 3. THE TAIL -- the bench's payload, whole, minus the forms
# ---------------------------------------------------------------------------
_FORM_KEYS = ("actions", "worker_status")


def step_tail(payload: dict[str, Any], *, pulse_due: bool = False,
              refused: dict[str, Any] | None = None) -> dict[str, Any]:
    """The current-step answer as the bench sent it, minus the forms (they are
    the tools; their names stay as `forms_as_tools`), plus the bench's own
    refusal of the last try when there was one."""
    tail = {key: value for key, value in payload.items() if key != "submission"}
    submission = payload.get("submission")
    if isinstance(submission, dict):
        kept = {key: value for key, value in submission.items() if key not in _FORM_KEYS}
        kept["forms_as_tools"] = [
            str(form.get("action")) for form in submission.get("actions") or []
            if isinstance(form, dict) and form.get("action")
        ]
        tail["submission"] = kept
    if pulse_due:
        tail["pulse_due"] = True
    if refused:
        tail["the_bench_refused"] = refused
    return tail


# ---------------------------------------------------------------------------
# 3b. WHAT A REPLY ANSWERS (0.56.1)
# ---------------------------------------------------------------------------
# WHAT FORCED IT: 2026-09-25 07:20 UTC, lab agent Rick, deal 40b6df58, step
# 17fbac0e. The step loop answered the person's message 871c5dbb six times in
# 70 seconds. The bench pays only the message a reply NAMES on `answering`;
# the harness never sent the key, so every 200 read `answered: []`,
# `answering_key_read: null`, `unread_from_person: 1`, and the next poll owed
# the same answer again. The harness now names it: the model's own pick when
# it is one of the owed ids, else the LATEST unanswered message on the step,
# which pays everything the person said before it. The model never keeps
# this book.
#
# And if a reply that named a message still comes back unpaid, the harness
# says so in the log and holds off answering that same message again for a
# while, so a bench that does not read the key costs one reply, not six.
UNPAID_REPLY_HOLD_SECONDS = 900.0
_UNPAID_REPLIES: dict[str, float] = {}


def unanswered_on_step(payload: dict[str, Any] | None) -> list[str]:
    """The ids of the person's unanswered messages on this step, oldest first.

    Read off `step_thread.unanswered_messages` (contract 4.0.4). A bench that
    predates that list: the person's messages on the thread, but only while
    `unread_from_person` says something is owed (since 3.13 the thread carries
    only the unanswered ones)."""
    thread = (payload or {}).get("step_thread") if isinstance(payload, dict) else None
    if not isinstance(thread, dict):
        return []
    rows = thread.get("unanswered_messages")
    if isinstance(rows, list) and rows:
        return [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]
    if not thread.get("unread_from_person"):
        return []
    return [
        str(item.get("id"))
        for item in thread.get("messages") or []
        if isinstance(item, dict) and item.get("id") and item.get("who") == "person"
    ]


def answering_for(owed: list[str], asked: Any = None) -> str | list[str] | None:
    """What a reply names on `answering`. None when nothing is owed: a reply
    that answers nobody claims nothing. The model's own pick when every id in
    it is owed; otherwise the latest owed message, which answers the rest."""
    if not owed:
        return None
    if isinstance(asked, str) and asked.strip() in owed:
        return asked.strip()
    if isinstance(asked, list) and asked:
        picked = [str(item).strip() for item in asked]
        if all(item in owed for item in picked):
            return picked if len(picked) > 1 else picked[0]
    return owed[-1]


def _named(answering: str | list[str] | None) -> list[str]:
    if not answering:
        return []
    return [answering] if isinstance(answering, str) else [str(item) for item in answering]


def reply_is_held(message_id: str, now: float | None = None) -> bool:
    """True while a reply that named this message came back unpaid recently."""
    now = time.monotonic() if now is None else now
    for key in [key for key, at in _UNPAID_REPLIES.items()
                if now - at > UNPAID_REPLY_HOLD_SECONDS]:
        _UNPAID_REPLIES.pop(key, None)
    return message_id in _UNPAID_REPLIES


def note_unpaid_reply(
    result: Any,
    answering: str | list[str] | None,
    step_id: str,
    log: logging.Logger | None = None,
) -> list[str]:
    """The named ids a 200 left unpaid, each logged once as a WARNING and held.

    Unpaid means the bench answered `answering_key_read: null` or `answered: []`
    while the message is still on `unanswered_messages`. A key the answer does
    not carry at all is an older bench and proves nothing either way."""
    named = _named(answering)
    if not named or not isinstance(result, dict) or not result.get("ok", True):
        return []
    key_unread = "answering_key_read" in result and result.get("answering_key_read") is None
    paid_nothing = "answered" in result and not result.get("answered")
    if not (key_unread or paid_nothing):
        return []
    rows = result.get("unanswered_messages")
    still = (
        {str(row.get("id")) for row in rows if isinstance(row, dict)}
        if isinstance(rows, list) else set(named)
    )
    unpaid = [mid for mid in named if mid in still]
    for mid in unpaid:
        (log or _LOGGER).warning(
            "reply on step %s named message %s and the bench paid nothing "
            "(answering_key_read=%s answered=%s); not answering %s again for %d seconds",
            step_id, mid, result.get("answering_key_read"), result.get("answered"),
            mid, int(UNPAID_REPLY_HOLD_SECONDS),
        )
        _UNPAID_REPLIES[mid] = time.monotonic()
    return unpaid


# ---------------------------------------------------------------------------
# 4. THE ASK
# ---------------------------------------------------------------------------
class StepAsk:
    """One deal step: whose turn, one small question, one call to the bench."""

    def __init__(self, model: Any, provider: Any, *, logger: logging.Logger | None = None):
        self.model = model
        self.provider = provider
        self.log = logger or _LOGGER
        self.prefix: str = ""
        self.calls = 0
        self.prompt_chars = 0
        self.cached_tokens = 0
        self.input_tokens = 0
        self.trail: list[dict[str, Any]] = []
        # The same line the runtime writes, so a step reads off the log the
        # same way a run does: "model call N: prompt X input tokens ...".
        self.budget = ContextBudget(limit=0)
        # The deal and step this ask is on, for its log lines (0.68.0).
        self.ids: dict[str, Any] = {}
        # This ask's own run id on the want's log (0.75.0): one step ask is
        # one run there, the way one runtime run is.
        self.run_id: str = uuid.uuid4().hex
        # What the agent does by its own means (0.58.0); empty changes nothing.
        self.own_means: tuple[OwnMeans, ...] = ()
        # The operator's standing instructions (0.59.0): one labeled block,
        # the whole text, beside the system text of every ask. Empty changes
        # nothing.
        self.operator_instructions: str = ""

    # -- the model ---------------------------------------------------------
    def _ask(
        self, instruction: str, tail: dict[str, Any], what: str, tools: list[ToolDefinition]
    ) -> Any:
        body = json.dumps(tail, separators=(",", ":"), sort_keys=True, default=str)
        text = STEP_DOOR + "\n\n" + instruction + "\n\n" + body
        self.calls += 1
        self.prompt_chars += len(text)
        deal = str(self.ids.get("deal_id") or "?")
        step = str(self.ids.get("step_id") or "?")
        try:
            # 0.75.0: the turn, live, on the want's log; with no stream this
            # is the plain call.
            response = thinking.invoke(
                self.model,
                system=self.prefix,
                messages=[ModelMessage.text("user", text)],
                tools=tools,
                place=self._place(),
            )
        except Exception as exc:
            # The exception CLASS only: provider messages can echo headers.
            self.log.warning(
                "step ask %s deal=%s step=%s: model call %d: prompt %d chars "
                "(prefix %d, tail %d), no answer: api_error %s",
                what, deal, step, self.calls, len(self.prefix) + len(text),
                len(self.prefix), len(text), type(exc).__name__,
            )
            raise
        usage = getattr(response, "usage", None)
        measured = f"model call {self.calls}: prompt input tokens unreported"
        if usage is not None:
            self.budget.record(usage, len(self.prefix) + len(text))
            self.input_tokens += max(0, int(getattr(usage, "input_tokens", 0) or 0))
            measured = self.budget.line()
        cached = cached_input_tokens(usage)
        if cached:
            self.cached_tokens += cached
        self.log.info(
            "step ask %s deal=%s step=%s: %s; prompt %d chars (prefix %d "
            "cacheable, tail %d, ~%d tokens; cached %s); %s",
            what, deal, step, measured,
            len(self.prefix) + len(text), len(self.prefix), len(text),
            (len(self.prefix) + len(text)) // 4,
            "unreported" if cached is None else cached,
            what_came_back(response),
        )
        return response

    def _place(self) -> dict[str, str]:
        """The want, deal and step this ask is on, for the want's log."""
        return thinking.here(
            target_id=self.ids.get("target_id"), deal_id=self.ids.get("deal_id"),
            step_id=self.ids.get("step_id"), run_id=self.run_id,
        )

    def _stream_the_answer(self, response: Any, picked: Any) -> None:
        """What the harness made of the turn, on the want's log (0.75.0): a
        call the model wrote as text gets its tool_call line, and a reply the
        harness could not use says why."""
        if isinstance(picked, tuple):
            if not getattr(response, "tool_calls", None):
                thinking.tool_call(self.model, picked[0], picked[1], place=self._place())
            return
        if isinstance(picked, dict) and picked.get("call"):
            thinking.tool_result(self.model, picked["call"], picked, is_error=True,
                                 duration_ms=0, place=self._place())
        elif isinstance(picked, dict):
            thinking.note(self.model, f"the reply made no usable tool call "
                                      f"({picked.get('failure')}): {picked.get('message')}",
                          place=self._place())

    @staticmethod
    def _one_call(
        response: Any, tools: dict[str, dict[str, Any]]
    ) -> tuple[str, dict[str, Any]] | dict[str, Any]:
        """(tool name, arguments) when the answer is exactly one permitted tool
        call whose arguments fit the bench's schema; otherwise the refusal,
        named for what was wrong. Nothing runs before this says yes."""
        allowed = sorted(tools)
        calls = list(getattr(response, "tool_calls", None) or [])
        if not calls:
            # A JSON TOOL CALL WRITTEN AS PLAIN TEXT is read before the reply
            # is called malformed (H3, 0.74.0): some models answer a tool
            # schema with {"name": ..., "arguments": {...}} in the text.
            found = tool_call_in_text(getattr(response, "text", ""), tools)
            if found is not None:
                name, arguments = found
                problem = _schema_problem(arguments, tools[name]["schema"])
                if problem:
                    return {"ok": False, "error": "invalid_arguments",
                            "failure": "invalid_arguments", "call": name, "message": problem}
                return name, dict(arguments)
        if len(calls) != 1:
            empty = not calls and not str(getattr(response, "text", "") or "").strip()
            failure = "empty_response" if empty else "malformed_response"
            return {"ok": False, "error": failure, "failure": failure,
                    "tool_calls": len(calls), "allowed": allowed,
                    "message": f"Make exactly one tool call, one of {', '.join(allowed)}."}
        name = str(calls[0].name or "")
        if name not in tools:
            return {"ok": False, "error": "disallowed_tool", "failure": "disallowed_tool",
                    "call": name, "allowed": allowed,
                    "message": f"The call must be one of {', '.join(allowed)}."}
        arguments = calls[0].arguments
        problem = _schema_problem(arguments, tools[name]["schema"])
        if problem:
            return {"ok": False, "error": "invalid_arguments", "failure": "invalid_arguments",
                    "call": name, "message": problem}
        return name, dict(arguments)

    # -- the call ----------------------------------------------------------
    @staticmethod
    def _key(step_id: str, call: str, answer: dict[str, Any]) -> str:
        digest = hashlib.sha256(
            json.dumps(answer, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:12]
        return f"step-{step_id}-{call}-{digest}"

    def _make_the_call(
        self,
        ids: dict[str, Any],
        tool: dict[str, Any],
        answer: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Post the one call. The forms that carry harness plumbing (a reply
        that names what it answers, the outcome's pulse and cost slot, the
        parked report, a file's bytes, the provider's own doors) go through
        their own methods; every other form posts to its own endpoint."""
        action = tool["action"]
        form = tool.get("form") or {}
        deal_id = str(ids.get("deal_id") or "")
        step_id = str(ids.get("step_id") or "")
        key = self._key(step_id, action, answer)
        provider = self.provider
        try:
            if action == STEP_MESSAGE_ACTION:
                result = self._reply(
                    deal_id, step_id, str(answer.get("reply") or ""), key,
                    answering_for(unanswered_on_step(payload), answer.get("answering")),
                )
            elif action == OUTCOME_ACTION:
                result = self._file_the_outcome(ids, answer, payload, key)
            elif action == REPORT_PARKED:
                result = self._park_the_step(
                    deal_id, step_id, answer.get("round"),
                    str(answer.get("reason") or ""), key,
                    answering=answering_for(unanswered_on_step(payload)),
                )
            elif action == DELIVER_FILE_ACTION:
                result = self._deliver_file(deal_id, step_id, answer)
            elif action == "propose_act":
                result = provider.propose_act(deal_id, step_id, answer, key)
            elif action == "dismiss_reply":
                found = _REPLY_IN_ENDPOINT.search(str(form.get("endpoint") or ""))
                result = provider.dismiss_reply(
                    deal_id, step_id, found.group(1) if found else "",
                    {"reason": str(answer.get("reason") or "")}, key,
                )
            elif action == "post_work_pulse":
                result = provider.post_check_in(deal_id, answer, key)
            elif action == "wait_outside":
                result = provider.wait_outside(deal_id, step_id, answer, key)
            elif action == OUTSIDE_EVIDENCE_ACTION:
                result = provider.file_evidence(
                    deal_id, step_id,
                    summary=str(answer.get("summary") or ""),
                    links=list(answer.get("links") or []),
                    receipt_ids=list(answer.get("receipt_ids") or []),
                )
            else:
                body = {**(tool.get("body") or {}), **answer}
                result = provider.submit_step_form(
                    form, body, key, deal_id=deal_id, step_id=step_id)
        except Exception as error:  # noqa: BLE001 - a refusal is an answer, not a crash
            result = {
                "ok": False,
                "error": getattr(error, "code", None) or type(error).__name__,
                "status": getattr(error, "status", None),
                "message": getattr(error, "message", None) or str(error),
            }
        if isinstance(result, dict) and not result.get("ok", True):
            result = dict(result, failure=(
                "harness_rejected" if _refused_here(result) else "server_rejected"))
        return result if isinstance(result, dict) else {"ok": True, "result": result}

    def _reply(
        self, deal_id: str, step_id: str, words: str, key: str,
        answering: str | list[str] | None,
    ) -> dict[str, Any]:
        """One post on the step thread, naming what it answers when anything is
        owed (0.56.1). A provider is only handed `answering` when there is one,
        so a reply that answers nobody claims nothing."""
        if answering:
            try:
                result = self.provider.reply_step_message(
                    deal_id, step_id, words, key, answering=answering)
            except TypeError as error:
                if "answering" not in str(error):
                    raise
                # A provider written before 0.56.1 takes no `answering`.
                self.log.warning(
                    "step %s: this provider's reply_step_message takes no "
                    "`answering`; message %s was not named", step_id, answering,
                )
                return self.provider.reply_step_message(deal_id, step_id, words, key)
        else:
            result = self.provider.reply_step_message(deal_id, step_id, words, key)
        note_unpaid_reply(result, answering, step_id, self.log)
        return result

    def _park_the_step(
        self, deal_id: str, step_id: str, round_number: Any, reason: str, key: str,
        answering: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """SAY IT TWICE, IN ONE ROUND (W29): stopped, and why, where the
        person can read it. The worker-status door is machine-facing, so the
        same sentence goes on the step thread; a refused status report does not
        stop that and does not fail the move."""
        words = (reason or "").strip() or OUTSIDE_PARKED_SENTENCE
        try:
            number = int(round_number)
        except (TypeError, ValueError):
            number = 0
        try:
            self.provider.report_worker_status(
                deal_id, step_id, "parked", number, reason=words)
        except Exception:  # noqa: BLE001 - visibility must never fail the move
            self.log.warning("could not report parked on step %s", step_id)
        return self._reply(deal_id, step_id, words, key, answering)

    def _file_the_outcome(
        self, ids: dict[str, Any], answer: dict[str, Any], payload: dict[str, Any], key: str
    ) -> dict[str, Any]:
        """The 100% pulse, then the outcome -- unless the bench records the
        completion on the outcome itself (`completion_recorded_on_outcome`)."""
        outcome = dict(answer)
        outcome.setdefault("step_ref", str(ids.get("step_id") or ""))
        latest = payload.get("latest_work_pulse") or {}
        try:
            at_full = int(latest.get("progress_percent") or 0) >= 100
        except (TypeError, ValueError, AttributeError):
            at_full = False
        submission = payload.get("submission")
        submission = submission if isinstance(submission, dict) else {}
        automatic = submission.get("completion_recorded_on_outcome") is True
        if not at_full and not automatic:
            note = str(outcome.get("note") or "")
            pulse = {
                "changed": note or "Finished the step's work",
                "now": "Filing the handover",
                "next": "Your review",
                "progress_percent": 100,
            }
            pulsed = self.provider.post_check_in(
                str(ids.get("deal_id") or ""), pulse, key + "-pulse"
            )
            if not (pulsed or {}).get("ok", True):
                return pulsed
        result = self.provider.file_outcome(str(ids.get("target_id") or ""), outcome, key)
        if isinstance(result, dict):
            for name in ("finds_refused", "finds_taken"):
                rows = result.get(name)
                if isinstance(rows, list) and rows:
                    self.log.warning(
                        "step %s: the outcome landed and the door took %d card(s) "
                        "off as %s; the next ask on this step names them",
                        ids.get("step_id"), len(rows), name,
                    )
        return result

    def _deliver_file(self, deal_id: str, step_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        """A file the model made, read out of its own working directory and
        handed to the bench's file door. Nothing outside that directory is
        read, and nothing over the platform's own per-file cap is sent."""
        root = getattr(self.model, "workdir", None)
        if not root:
            return {"ok": False, "error": "no_workdir",
                    "message": "This model has no working directory to read a file from; "
                               "use need_tools and deliver it on the old road."}
        base = Path(root).resolve()
        wanted = Path(str(answer.get("path") or ""))
        path = (wanted if wanted.is_absolute() else base / wanted).resolve()
        if base not in path.parents and path != base:
            return {"ok": False, "error": "path_outside_workdir",
                    "message": f"{answer.get('path')} is not under {base}."}
        if not path.is_file():
            return {"ok": False, "error": "file_not_found",
                    "message": f"There is no file at {answer.get('path')}."}
        if path.stat().st_size > DELIVER_FILE_MAX_BYTES:
            return {"ok": False, "error": "file_too_large",
                    "message": "The platform takes 50 MB per file."}
        return self.provider.deliver_file(
            deal_id,
            filename=str(answer.get("filename") or path.name),
            content=path.read_bytes(),
            title=str(answer.get("title") or ""),
            step_ref=step_id or None,
        )

    def _note(self, call: Any, result: dict[str, Any]) -> None:
        self.trail.append({
            "call": call,
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "failure": result.get("failure"),
        })

    # -- the run -----------------------------------------------------------
    def run(self, obligation: dict[str, Any], payload: dict[str, Any],
            **options: Any) -> dict[str, Any]:
        """One step (see `_run`). Whatever the step ends with, what the
        thinking stream holds for it goes now (0.75.0)."""
        try:
            return self._run(obligation, payload, **options)
        finally:
            thinking.flush(self.model, self.run_id)

    def _run(
        self,
        obligation: dict[str, Any],
        payload: dict[str, Any],
        *,
        brief: Any = None,
        act_kinds: Any = None,
        changed: list[str] | None = None,
        pulse_due: bool = False,
        refused: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One step, one call (asked twice at most: once, and once more with
        the bench's refusal). Says which road when it is not this one.

        `refused` is the bench's refusal from a PREVIOUS cycle on this same
        state: it rides the first ask. `changed` is accepted for older callers;
        the bench's own `what_changed` rides the payload."""
        step = payload.get("current_step") or {}
        deal = payload.get("deal") or {}
        number = step.get("number")
        ids = {
            "deal_id": deal.get("id") or obligation.get("deal_id"),
            "step_id": step.get("id") or obligation.get("step_id"),
            "target_id": deal.get("target_goal_id") or obligation.get("target_id"),
        }
        self.ids = ids
        thread = payload.get("step_thread") if isinstance(payload.get("step_thread"), dict) else {}
        if thread.get("unanswered_elsewhere") and not unanswered_on_step(payload):
            return {"ok": False, "road": ROAD_AGENTIC, "model_calls": 0,
                    "why": "the person wrote on another step and its words are not on "
                           "this payload"}
        why = not_the_agents_move(payload)
        if why:
            return {"ok": True, "road": ROAD_STEP_ASK, "move": None, "call": None,
                    "why": why, "result": {"ok": True, "not_yours": why},
                    "tool_count": 0, "model_calls": 0, "prompt_chars": 0,
                    "prefix_chars": 0, "input_tokens": 0, "cached_tokens": 0,
                    "trail": []}
        owed = unanswered_on_step(payload)
        latest = owed[-1] if owed else None
        if latest and reply_is_held(latest):
            # 0.56.1: the last reply that named this message came back unpaid.
            # Answering it again is how Rick sent six; hold, say so, and let a
            # new message (or the hold running out) move it.
            self.log.warning(
                "step %s: message %s was answered and the bench did not mark it "
                "paid; not answering it again this cycle", number, latest)
            return {"ok": True, "road": ROAD_STEP_ASK, "move": None, "call": None,
                    "held": latest, "why": f"a reply naming {latest} already landed unpaid",
                    "result": {"ok": True, "held": latest}, "tool_count": 0,
                    "model_calls": 0, "prompt_chars": 0, "prefix_chars": 0,
                    "input_tokens": 0, "cached_tokens": 0, "trail": []}
        submission = payload.get("submission")
        submission = submission if isinstance(submission, dict) else {}
        recommended = str(submission.get("recommended_action") or "")
        off = {
            str(row.get("action")): [str(tool) for tool in row.get("tools") or []]
            for row in submission.get(FORMS_TURNED_OFF) or [] if isinstance(row, dict)
        }
        if recommended and recommended in off:
            # 0.56.2: THE BENCH'S OWN NEXT DOOR IS OFF THIS AGENT'S TOOL LIST.
            # Asking anyway hands the model chat alone, and it spends the loop
            # guard's tries writing "the tool was not offered" to the person
            # (Rick, deal 40b6df58). Say what is missing, once, and ask nothing.
            why = (f"{recommended} is not on this agent's tool list; add "
                   f"{', '.join(off[recommended])} to runtime.tools in agent.yaml "
                   f"(the bench published the {recommended} form)")
            self.log.warning("step ask step %s: %s; not asking the model", number, why)
            return {"ok": True, "road": ROAD_STEP_ASK, "move": None, "call": None,
                    "missing_form": recommended, "why": why,
                    "result": {"ok": True, "missing_form": recommended}, "tool_count": 0,
                    "model_calls": 0, "prompt_chars": 0, "prefix_chars": 0,
                    "input_tokens": 0, "cached_tokens": 0, "trail": []}
        tools = step_tools(payload)
        if len(tools) == 1:  # need_tools alone: the bench published no form
            return {"ok": False, "road": ROAD_AGENTIC, "model_calls": 0,
                    "why": "the bench publishes no form on this step"}
        caches = bool(getattr(self.model, "caches_a_stable_prefix", lambda: False)())
        # 0.59.0: the operator's standing instructions ride beside the system
        # text on every ask, WHOLE, the same bytes on both tries.
        self.prefix = with_operator_block(
            stable_prefix(brief, act_kinds, with_tools=caches),
            self.operator_instructions,
        )
        instruction = with_own_means("", self.own_means).strip()
        if pulse_due:
            instruction = (instruction + " " + PULSE_DUE_INSTRUCTION).strip()
        if isinstance(payload.get(PERSON_SAID_KEY), list):
            instruction = (instruction + "\n" + PERSON_SAID_INSTRUCTION).strip()
        definitions = [_definition(name, tool) for name, tool in tools.items()]
        name: str | None = None
        result: dict[str, Any] = {}
        last_refusal = refused
        for attempt in (1, 2):
            if attempt == 2:
                self.log.warning(
                    "step ask step %s: %s refused (%s %s); asking once more with the refusal",
                    number, name, result.get("failure"), result.get("error"),
                )
                last_refusal = result
            tail = step_tail(payload, pulse_due=pulse_due, refused=last_refusal)
            words = instruction
            if last_refusal:
                words = ((REFUSED_HERE_INSTRUCTION if _refused_here(last_refusal)
                          else REFUSED_INSTRUCTION) + " " + instruction).strip()
            response = self._ask(
                words, tail, f"step {number}" + (" again" if attempt == 2 else ""), definitions
            )
            picked = self._one_call(response, tools)
            self._stream_the_answer(response, picked)
            if isinstance(picked, dict):
                name, result = picked.get("call"), picked
            else:
                name, answer = picked
                if tools[name]["action"] == NEED_TOOLS:
                    thinking.note(self.model, "the model asked for its tools: the agentic road "
                                              "takes this step", place=self._place())
                    return {
                        "ok": False,
                        "road": ROAD_AGENTIC,
                        "why": f"the model says it needs tools: {str(answer.get('why'))[:200]}",
                        "model_calls": self.calls,
                        "prompt_chars": self.prompt_chars,
                    }
                started = time.monotonic()
                result = self._make_the_call(ids, tools[name], answer, payload)
                thinking.tool_result(
                    self.model, name, result, is_error=not result.get("ok", True),
                    duration_ms=int((time.monotonic() - started) * 1000), place=self._place(),
                )
            self._note(name, result)
            if result.get("ok"):
                break
        ok = bool(result.get("ok"))
        outside = any(tool["action"] == OUTSIDE_EVIDENCE_ACTION for tool in tools.values())
        if not ok and outside and name != REPORT_PARKED:
            # W29: THE ONE STEP THAT MAY NOT END IN SILENCE. An approved
            # outside act whose work never came back: nobody is doing it, and
            # the person is owed that sentence in this same poll.
            self.log.warning(
                "step ask step %s: no call landed on the outside work (%s); parking the step",
                number, result.get("failure") or result.get("error"),
            )
            self._park_the_step(
                str(ids.get("deal_id") or ""), str(ids.get("step_id") or ""),
                step.get("rounds_used"), "",
                self._key(str(ids.get("step_id") or ""), REPORT_PARKED, result),
                answering=answering_for(unanswered_on_step(payload)),
            )
            result = dict(result, parked=True)
        self.log.info(
            "step ask step %s: %s %s after %d model call(s); prefix %d chars, tails %d "
            "chars (~%d tokens), input tokens %s, cached input %s",
            number, name,
            "made" if ok else f"refused ({result.get('failure')} {result.get('error')})",
            self.calls, len(self.prefix), self.prompt_chars,
            (len(self.prefix) + self.prompt_chars) // 4,
            self.input_tokens or "unreported", self.cached_tokens or "unreported",
        )
        out = {
            "ok": ok,
            "road": ROAD_STEP_ASK,
            "move": tools[name]["action"] if name in tools else None,
            "call": name,
            "tool_count": len(definitions),
            "result": result,
            "model_calls": self.calls,
            "prompt_chars": self.prompt_chars,
            "prefix_chars": len(self.prefix),
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "trail": self.trail,
        }
        if not ok:
            out["error"] = str(result.get("error") or "step_ask_refused")
            out["failure"] = result.get("failure")
            out["message"] = result.get("message")
        return out
