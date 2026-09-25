"""THE STEP ASK -- one step of a signed plan is one small question and one call.

WHAT FORCED THIS MODULE (Steven, 2026-09-09, 19:55): "It's just stepping
through the plan. why would that cost so much? ... fix that please."

Measured on the fleet that morning (one fleet unit, market.log, 10:36 local): one
deal-step dispatch handed the model a 10,002-character goal and 32 tools,
opened at 12,675 input tokens, and went round twenty times at about 13,000
each -- 261,749 input tokens on ONE step, and it hit the iteration cap
without filing anything. A bid, by then, cost about 9,000 input tokens for a
whole ten-call draft loop (0.35.4 to 0.35.6): a prefix the provider caches
and a small tail per ask. The step was still the old road -- the runtime's
whole instruction sheet, every tool, and every tool result glued into the
conversation and re-sent on every call.

So a deal step is asked the way a plan is written. THREE THINGS:

  1. WHOSE MOVE IS IT. A block the platform runs (rule 229), an act waiting
     on the person's Allow, an act the platform is executing, a step the
     person is reviewing, a declared wait on the outside world (rule 216) --
     none of those is the agent's to move, and none of them starts a model
     run. `platform_move` names the reason in a few words and the dispatch
     logs one line.
  2. ONE MOVE, ONE CALL. When the move IS the agent's -- answer a reply the
     bench says is owed, answer the person, re-file an act that came back,
     file a declared act, hand the step back -- `the_move` names it and the
     tail carries ONLY this step: its title, ask and promise, what changed
     since the last look and the one thing to produce. The model gets the
     calls that move allows as TOOLS, one per form the bench publishes on
     `submission.actions`, with the bench's schema; it answers with exactly
     one tool call, checked against that full schema, and the runtime makes
     the call, done. The prefix is `draft.stable_prefix`, byte for byte, so the
     provider cache is shared with the bid loop.
  3. THE OLD ROAD IS STILL THERE for a move this ask cannot shape: a step
     that hands back bytes or a link (rule 230), an approved `outside` act
     the agent goes and does itself, a message debt on a step whose words
     are not on the current-step payload, and any step the model says needs
     a live search, a browser, a file or a tool (`need_tools`). The dispatch
     says which road it took, every time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

from toll_harness.core.budget import ContextBudget
from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.toll_bench.draft import (
    PERSON_SAID_INSTRUCTION,
    PERSON_SAID_KEY,
    PROMPT_CHAR_BUDGET,
    _fit,
    cached_input_tokens,
    stable_prefix,
)

_LOGGER = logging.getLogger("toll_harness.step")

# The one-ball state that means the agent holds the step.
AGENT_STEP_STATE = "agent_working"
# An act in one of these states is waiting on the person's Allow, or the
# platform is carrying it out. Nothing of the agent's moves it.
HELD_ACT_STATES = frozenset({"pending", "held", "approved"})
# The act came back with the person's words on it: the step is the agent's.
RETURNED_ACT_STATES = frozenset({"sent_back", "denied", "failed", "stopped"})
# The platform did it; what is left is the agent's outcome quoting the receipt.
DONE_ACT_STATES = frozenset({"executed", "sent"})
OUTSIDE_KIND = "outside"
# Acts the agent files itself at the act door. A block kind (meeting, and
# whatever else the registry publishes a declaration for) is the platform's
# to file when the step opens, so it is never on this list.
AGENT_FILED_ACT_KINDS = frozenset({"email", "calendar_event"})
# A deliverable on one of these channels is bytes, not words (rule 230), and
# needs the run folder and the delivery doors: the old road.
BYTES_CHANNELS = frozenset({"file", "link"})

# W29 (prod deal 4f061f54 step 3, 17 September). The bench's own names for the
# two doors an approved outside act needs: the one that brings the work back,
# and the one that says out loud that nobody is working on it.
OUTSIDE_EVIDENCE_ACTION = "file_outside_evidence"
WORKER_STATUS_ACTION = "report_worker_status"
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

# How much of a thread rides the tail. The person's newest words are the
# input; older ones were already answered or the bench would say so.
PERSON_MESSAGE_LIMIT = 6
ACT_LIMIT = 4
MATERIAL_LIMIT = 8


def _kind(act: Any) -> str:
    return str((act or {}).get("kind") or "").strip().lower()


def _state(act: Any) -> str:
    return str((act or {}).get("state") or "").strip().lower()


def _wait_is_over(wait: dict[str, Any], now: datetime | None = None) -> bool:
    """A declared wait that has ended, or whose own `until` has passed."""
    if wait.get("ended_at") or wait.get("ended"):
        return True
    until = wait.get("until")
    if not until:
        return False
    try:
        due = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due <= (now or datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 1. WHOSE MOVE IS IT
# ---------------------------------------------------------------------------
def platform_move(payload: Any, owned: Any = None) -> str | None:
    """Why this step is NOT the agent's to move right now, in a few words.

    None means the agent has a move. `owned` is the provider's rule-229 memo
    for this step ({"kinds": {kind: state}}) -- a block the platform filed
    and will close -- fed by the same current_step read that produced
    `payload`. The person's new words always come first: a message, an owed
    reply, or an act that came back is the agent's move whatever else is
    standing on the step.
    """
    if not isinstance(payload, dict):
        return None
    step = payload.get("current_step") or {}
    thread = payload.get("step_thread") or {}
    if (
        thread.get("unread_from_person")
        or thread.get("unanswered_elsewhere")
        or payload.get("owed_replies")
    ):
        return None
    acts = [act for act in payload.get("acts") or [] if isinstance(act, dict)]
    # The server sends acts oldest first; the newest one is where the step is.
    if acts and _state(acts[-1]) in RETURNED_ACT_STATES:
        return None
    state = str(step.get("state") or "").strip().lower()
    if state and state != AGENT_STEP_STATE:
        # waiting_on_you and its kin: the person holds the ask.
        return f"step state {state}, the person's"
    if isinstance(owned, dict) and owned.get("kinds"):
        kinds = owned["kinds"]
        return (
            ", ".join(f"{kind} block {kinds[kind]}" for kind in sorted(kinds))
            + " (rule 229)"
        )
    for act in reversed(acts):
        kind, act_state = _kind(act), _state(act)
        if act_state not in HELD_ACT_STATES:
            continue
        if kind == OUTSIDE_KIND and act_state == "approved":
            return None  # the person tapped Allow: the agent goes and does it
        if act_state == "approved":
            return f"{kind} act approved, the platform is carrying it out"
        return f"{kind} act {act_state}, waiting on the person's Allow"
    wait = payload.get("waiting_outside")
    if (
        isinstance(wait, dict)
        and wait
        and not payload.get("inbound_replies")
        and not _wait_is_over(wait)
    ):
        who = wait.get("who") or wait.get("on") or "a reply"
        return f"waiting outside on {who} (rule 216)"
    return None


# ---------------------------------------------------------------------------
# 2. THE MOVE, AND THE ONE CALL THAT MAKES IT
# ---------------------------------------------------------------------------
def _unfiled_declared_kinds(payload: dict[str, Any]) -> list[str]:
    """Kinds the signed plan declared on this step that the agent has not filed."""
    filed = {_kind(act) for act in payload.get("acts") or [] if isinstance(act, dict)}
    out: list[str] = []
    for entry in payload.get("declared_acts") or []:
        if not isinstance(entry, dict):
            continue
        kind = _kind(entry)
        if kind not in AGENT_FILED_ACT_KINDS or kind in filed:
            continue
        try:
            if int(entry.get("filed") or 0) > 0:
                continue
        except (TypeError, ValueError):
            pass
        out.append(kind)
    return out


def outside_evidence_form(payload: Any) -> dict[str, Any] | None:
    """The bench's `file_outside_evidence` form on this step, or None.

    Published on `submission.actions` since the server's f5e7085f2 (rule 232,
    W26). A bench that does not publish it has no door for the work to come
    back through, and the step takes the old road as it always did.
    """
    submission = (payload or {}).get("submission") if isinstance(payload, dict) else None
    for action in (submission or {}).get("actions") or []:
        if (isinstance(action, dict)
                and action.get("action") == OUTSIDE_EVIDENCE_ACTION
                and isinstance(action.get("schema"), dict)):
            return action
    return None


def worker_status_form(payload: Any) -> dict[str, Any] | None:
    """The bench's worker-status form on this step, or None. It rides
    `submission.worker_status`, beside `actions` rather than in it."""
    submission = (payload or {}).get("submission") if isinstance(payload, dict) else None
    form = (submission or {}).get("worker_status")
    if isinstance(form, dict) and isinstance(form.get("schema"), dict):
        return form
    return None


def parked_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The worker-status form, narrowed to the one report this move makes.

    `running` is the runtime's own word and is reported by the watch loop, not
    by the model; the only thing the model says here is that it has stopped,
    and why. The reason rides the same call: the door ignores a field it does
    not read, and the words also go on the thread where the person can see
    them.
    """
    properties = dict(schema.get("properties") or {})
    properties["state"] = {"const": "parked"}
    properties["reason"] = {"type": "string", "minLength": 1, "maxLength": 280}
    return {"type": "object", "properties": properties,
            "required": ["state", "round", "reason"]}


def the_move(payload: dict[str, Any], obligation: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one thing the agent produces on this step, and the calls that file it.

    {"move": name, "calls": [...]} for a move the small ask shapes;
    {"move": None, "road": "agentic", "why": ...} for one it cannot.
    """
    obligation = obligation or {}
    step = payload.get("current_step") or {}
    thread = payload.get("step_thread") or {}
    step_id = str(step.get("id") or "")
    if payload.get("owed_replies"):
        return {"move": "answer_reply", "calls": ["propose_act", "dismiss_reply"]}
    asked_step = str(obligation.get("step_id") or "")
    if thread.get("unread_from_person"):
        if asked_step and step_id and asked_step != step_id:
            return {
                "move": None,
                "road": ROAD_AGENTIC,
                "why": f"the person's words are on step {asked_step}, not on this payload",
            }
        return {"move": "answer_person", "calls": ["reply_step_message"]}
    if thread.get("unanswered_elsewhere"):
        return {
            "move": None,
            "road": ROAD_AGENTIC,
            "why": "the person wrote on another step and its words are not on this payload",
        }
    acts = [act for act in payload.get("acts") or [] if isinstance(act, dict)]
    if acts and _state(acts[-1]) in RETURNED_ACT_STATES:
        return {"move": "refile_act", "calls": ["propose_act", "reply_step_message"]}
    if any(_kind(act) == OUTSIDE_KIND and _state(act) == "approved" for act in acts):
        # W29 (prod deal 4f061f54, 17 September). WHAT FORCED IT: the person
        # tapped Allow, the bench said "Agent working ... file what you
        # brought back" on every poll for seven minutes, and this branch sent
        # the step to the old road, where the model filed nothing, posted
        # nothing and never reported itself parked. The step was failed by
        # hand. An approved outside act IS the agent's move, so it is asked
        # as one: bring the work back, say you cannot and be parked in the
        # same round, or ask for tools and take the old road deliberately.
        if outside_evidence_form(payload) is not None:
            return {
                "move": "outside_work",
                "calls": ["file_outside_evidence", "reply_step_message", "report_parked"],
            }
        return {
            "move": None,
            "road": ROAD_AGENTIC,
            "why": "an approved outside act and no evidence form on this bench",
        }
    deliverable = step.get("deliverable")
    if isinstance(deliverable, dict):
        channel = str(deliverable.get("channel") or "").strip().lower()
        if channel in BYTES_CHANNELS:
            return {
                "move": None,
                "road": ROAD_AGENTIC,
                "why": f"the step hands back a {channel} (rule 230), which takes the run folder",
            }
    if _unfiled_declared_kinds(payload):
        return {"move": "file_act", "calls": ["propose_act", "reply_step_message"]}
    return {
        "move": "hand_back",
        "calls": ["file_outcome", "reply_step_message", "wait_outside", "post_check_in"],
    }


# The tail's opening, and what each move is asked for. Short by design: the
# rules of the game are the prefix's job, and the bench's own refusal comes
# back verbatim when a call is refused.
STEP_DOOR = (
    "THIS IS NOT A PLAN ROUND. The plan is signed and you are walking ONE step "
    "of it. Below: the step, what changed since you last looked, and the one "
    "move that is yours. Make exactly ONE call with the tools you are given "
    "and nothing else. Never write 'click Approve to send' or 'from your mailbox' to the "
    "person; never ask for a password, code, session or cookie; no bare URLs "
    "in a pulse."
)

MOVE_INSTRUCTIONS: dict[str, str] = {
    "answer_reply": (
        "Someone outside answered your email and the bench says you owe them a "
        "reply before anything else moves on this step (rule 220). Answer in "
        "their thread with the propose_act tool for that reply -- the bench "
        "fills to and subject from the thread. If it is not a question (spam, "
        "a bounce, an out-of-office) use the dismiss_reply tool for that reply "
        "and say why in one plain sentence."
    ),
    "answer_person": (
        "The person wrote on this step and nothing has gone back. Answer them, "
        "plainly, and nothing else this round, with reply_step_message. This is "
        "chat: it does not close the step and does not open an ask."
    ),
    "refile_act": (
        "The person sent your act back, or it was denied or failed, and their "
        "reason is in `note` on that act. That act is DEAD: never re-file the "
        "same words and never wait on it. File ONE changed act that answers "
        "them with a propose_act tool. If their reason is not something you "
        "can act on, say so on the thread with reply_step_message instead."
    ),
    "file_act": (
        "Your signed plan declared an act on this step and it is not filed "
        "yet; the bench refuses the outcome until it is (acts_not_filed). File "
        "it with the propose_act tool for its kind; an email names its "
        "recipient by `contact_ref` from the_person_said.people. The person "
        "approves it word for word and Book of Houses sends it; you never send "
        "it and never ask them to. If you do not know the recipient, ask the "
        "person with reply_step_message."
    ),
    "outside_work": (
        "The person tapped Allow on an outside act: this piece of the work is "
        "yours to go and do YOURSELF, off this site, in your own name (rule "
        "232). Nothing on this step moves until you bring it back.\n"
        "IF YOU HAVE DONE IT, file what you brought back with "
        "file_outside_evidence: what you did in your own plain words, who you "
        "dealt with, how it ended, and up to five links the person can open.\n"
        "IF YOU HAVE A TOOL FOR IT and have not used it yet, answer "
        "need_tools and say which service; you will be handed your tools and "
        "asked again.\n"
        "IF YOU CANNOT DO IT AT ALL -- no tool for that service, no browser, "
        "no phone -- DO NOT SIT SILENT and do not wait for yourself. Answer "
        "report_parked with the reason in one plain sentence: that stops the "
        "clock out loud, posts your sentence on the step so the person reads "
        "it, and hands them the decision. Silence on this step is the one "
        "answer that is always wrong."
    ),
    "hand_back": (
        "Hand this step back: write the thing the step promised, from what is "
        "in front of you, and file it with file_outcome. If "
        "`deliverable.fields` is named, the document carries a `cards` block: "
        "items are objects keyed by exactly those field names, at least "
        "min_count of them, every value filled. If an act on this step reads "
        "sent or executed, quote its receipt in the document and send nothing "
        "yourself. If you asked someone outside and nothing can move until "
        "they answer, use wait_outside. If you must ask the person one thing "
        "first, use reply_step_message. If this step cannot be done without a "
        "live search, a browser, a file or a tool, use need_tools and nothing "
        "else."
    ),
}

PULSE_DUE_INSTRUCTION = (
    "A progress pulse is due (rule 100). If you are filing the outcome now, "
    "the pulse rides with it. If you are not, post one honest pulse with "
    "post_check_in -- never flat progress with no blocker on an ask the person "
    "cannot see yet."
)

REFUSED_INSTRUCTION = (
    "Your last answer was refused; the words are in `the_bench_refused`. "
    "Fix exactly what it names and make the call once more."
)

# The bench's action name for each call this module makes.
BENCH_ACTIONS: dict[str, str] = {
    "propose_act": "propose_act",
    "dismiss_reply": "dismiss_reply",
    "reply_step_message": "post_step_message",
    "post_check_in": "post_work_pulse",
    "wait_outside": "wait_outside",
    "file_outcome": "submit_step_outcome",
    "file_outside_evidence": OUTSIDE_EVIDENCE_ACTION,
}
NEED_TOOLS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"why": {"type": "string", "minLength": 1}},
    "required": ["why"],
}
_REPLY_IN_ENDPOINT = re.compile(r"/replies/([^/]+)/dismiss$")


def step_tools(move: dict[str, Any], payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Tool name -> {call, schema, action}: one tool per form the bench
    publishes for a call this move allows. Two forms for one call (two owed
    replies, two declared acts) are two tools, `propose_act`, `propose_act_2`.
    The schema is the bench's, whole."""
    submission = payload.get("submission")
    actions = submission.get("actions") if isinstance(submission, dict) else None
    tools: dict[str, dict[str, Any]] = {}
    for call in move.get("calls") or []:
        forms = [
            action for action in actions or []
            if isinstance(action, dict)
            and action.get("action") == BENCH_ACTIONS.get(call)
            and isinstance(action.get("schema"), dict)
        ]
        for index, action in enumerate(forms, start=1):
            name = call if index == 1 else f"{call}_{index}"
            tools[name] = {"call": call, "schema": action["schema"], "action": action}
    if "report_parked" in (move.get("calls") or []):
        # The parked report is not on `actions`; it rides `submission.
        # worker_status`, so it is picked up by name rather than by the loop.
        form = worker_status_form(payload)
        if form is not None:
            tools["report_parked"] = {
                "call": "report_parked",
                "schema": parked_schema(form["schema"]),
                "action": form,
            }
    if tools and move.get("move") in ("hand_back", "outside_work"):
        tools[NEED_TOOLS] = {"call": NEED_TOOLS, "schema": NEED_TOOLS_SCHEMA, "action": {}}
    return tools


def _definition(name: str, tool: dict[str, Any]) -> ToolDefinition:
    action = tool["action"]
    words = [str(action.get("result") or "")]
    if action.get("template") is not None:
        words.append("Template: " + json.dumps(action["template"], default=str))
    if action.get("field_notes"):
        words.append("Fields: " + json.dumps(action["field_notes"], default=str))
    if tool["call"] == NEED_TOOLS:
        words = ["This step needs a live search, a browser, a file or a tool."]
    return ToolDefinition(name, " ".join(word for word in words if word), tool["schema"])


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
# 3. THE TAIL -- only this step
# ---------------------------------------------------------------------------
def what_changed(previous: Any, current: Any) -> list[str]:
    """What is new on the step since the last dispatched look, in plain lines.

    `previous` and `current` are the fingerprint bases the dispatch keeps
    (`cli._deal_step_fingerprint`), parsed. No previous look: one line says so.
    """
    if not isinstance(current, dict):
        return []
    if not isinstance(previous, dict):
        return ["first look at this step"]
    lines: list[str] = []
    old_ids = set(previous.get("message_ids") or [])
    new_ids = [mid for mid in current.get("message_ids") or [] if mid not in old_ids]
    if new_ids:
        names = ", ".join(map(str, new_ids))
        lines.append(f"{len(new_ids)} new message(s) from the person: {names}")
    if (current.get("unread_from_person") or 0) != (previous.get("unread_from_person") or 0):
        lines.append(f"unread_from_person is now {current.get('unread_from_person') or 0}")
    old_acts = {
        tuple(item[:2])
        for item in previous.get("acts") or []
        if isinstance(item, (list, tuple))
    }
    for item in current.get("acts") or []:
        if isinstance(item, (list, tuple)) and tuple(item[:2]) not in old_acts:
            act_id, state = (list(item) + [None, None])[:2]
            lines.append(f"act {act_id} is now {state}")
    old_owed = {
        tuple(item[:1])
        for item in previous.get("owed_replies") or []
        if isinstance(item, (list, tuple))
    }
    for item in current.get("owed_replies") or []:
        if isinstance(item, (list, tuple)) and tuple(item[:1]) not in old_owed:
            lines.append(f"a reply is owed: {item[0]}")
    old_dead = {
        tuple(item[:1])
        for item in previous.get("drafts_sent_back") or []
        if isinstance(item, (list, tuple))
    }
    for item in current.get("drafts_sent_back") or []:
        if isinstance(item, (list, tuple)) and tuple(item[:1]) not in old_dead:
            lines.append(f"draft {item[0]} came back")
    materials_now = current.get("released_materials_count") or 0
    materials_then = previous.get("released_materials_count") or 0
    if materials_now != materials_then:
        lines.append(
            f"released materials: {materials_now} (was {materials_then})"
        )
    if current.get("grants") != previous.get("grants"):
        lines.append("the deal's grants changed")
    return lines or ["nothing new from the person since the last look"]


def _clip(value: Any, limit: int) -> Any:
    """`_fit` sheds a step's heavy keys; this clips the WORDS. A person can
    paste a page into a message, and one page is more than a tail."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "\u2026"
    if isinstance(value, dict):
        return {key: _clip(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip(item, limit) for item in value]
    return value


def _person_messages(thread: dict[str, Any], limit: int = PERSON_MESSAGE_LIMIT) -> list[Any]:
    messages = [
        item for item in thread.get("messages") or []
        if isinstance(item, dict) and item.get("who") != "agent"
    ]
    return [_clip(_fit(item, 600), 600) for item in messages[-limit:]]


def _act_row(act: dict[str, Any]) -> dict[str, Any]:
    row = {
        key: act.get(key)
        for key in ("act_id", "kind", "state", "note", "next")
        if act.get(key) is not None
    }
    for key in ("to", "subject", "with", "summary", "receipt", "sent_at", "executed_at"):
        if act.get(key) is not None:
            row[key] = _fit(act.get(key), 200)
    return _clip(row, 400)


def _material_row(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    return {
        key: item.get(key)
        for key in ("file_id", "id", "filename", "name", "title", "kind", "size_bytes")
        if item.get(key) is not None
    }


def step_tail(
    payload: dict[str, Any],
    obligation: dict[str, Any],
    move: dict[str, Any],
    changed: list[str],
    *,
    pulse_due: bool = False,
    refused: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the model needs for THIS move on THIS step, and nothing else."""
    step = payload.get("current_step") or {}
    deal = payload.get("deal") or {}
    thread = payload.get("step_thread") or {}
    acts = [act for act in payload.get("acts") or [] if isinstance(act, dict)]
    tail: dict[str, Any] = {
        "ids": {
            "deal_id": deal.get("id") or obligation.get("deal_id"),
            "step_id": step.get("id") or obligation.get("step_id"),
            "target_id": deal.get("target_goal_id") or obligation.get("target_id"),
        },
        "step": {
            "number": step.get("number"),
            "title": step.get("title"),
            "ask": step.get("ask"),
            "outcome_promise": _clip(step.get("outcome_promise"), 600),
            "state": step.get("state"),
            "deliverable": step.get("deliverable"),
        },
        "person_sees_control": payload.get("person_sees_control"),
        "open_ask_move": _fit(payload.get("open_ask_move"), 300),
        "pulse": _fit(payload.get("latest_work_pulse"), 400),
        "changed_since_last_look": list(changed or []),
        "from_the_person": _person_messages(thread),
        "owed_replies": [
            _clip(_fit(item, 1200), 1200) for item in (payload.get("owed_replies") or [])[:3]
        ],
        "acts": [_act_row(act) for act in acts[-ACT_LIMIT:]],
        "declared_acts": [
            _fit(item, 800) for item in (payload.get("declared_acts") or [])[:ACT_LIMIT]
        ],
        "drafts_sent_back": [
            {
                "approval_id": item.get("approval_id") or item.get("id"),
                "sent_back_reason": _fit(item.get("sent_back_reason"), 400),
            }
            for item in (payload.get("drafts_sent_back") or [])[:ACT_LIMIT]
            if isinstance(item, dict)
        ],
        "released_materials": [
            _material_row(item)
            for item in (payload.get("released_materials") or [])[:MATERIAL_LIMIT]
        ],
        "released_materials_count": payload.get("released_materials_count", 0),
        "waiting_outside": _fit(payload.get("waiting_outside"), 300),
        "inbound_replies": [
            _clip(_fit(item, 800), 800) for item in (payload.get("inbound_replies") or [])[:2]
        ],
        "your_move": move.get("move"),
        "calls_you_may_make": list(move.get("calls") or []),
    }
    if isinstance(payload.get("submission"), dict):
        tail["submission"] = payload["submission"]
    if obligation.get("kind") == "draft_sent_back" and obligation.get("sent_back_reason"):
        tail["sent_back_reason"] = _fit(obligation.get("sent_back_reason"), 400)
    # LAW A (Steven, 2026-09-09): what the person said and picked rides the
    # tail of the step ask, verbatim, when the bench sent it. It is the one
    # thing a shed never drops: without it a stateless agent on step 3 of
    # "connect two people by email" wrote john.doe@example.com.
    said = payload.get(PERSON_SAID_KEY)
    if isinstance(said, list):
        tail[PERSON_SAID_KEY] = list(said)
    if pulse_due:
        tail["pulse_due"] = True
    if refused:
        tail["the_bench_refused"] = _fit(refused, 1500)
    return tail


def _shed(tail: dict[str, Any]) -> bool:
    """One step of trimming, for a tail that runs long. True when it did something."""
    for key, floor in (("from_the_person", 2), ("acts", 1), ("declared_acts", 1)):
        rows = tail.get(key)
        if isinstance(rows, list) and len(rows) > floor:
            tail[key] = rows[-floor:]
            return True
    for key in ("released_materials", "inbound_replies", "drafts_sent_back"):
        if tail.get(key):
            tail[key] = []
            return True
    return False


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
    """One deal step: whose move, one small question, one call to the bench."""

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

    # -- the model ---------------------------------------------------------
    def _ask(
        self, instruction: str, tail: dict[str, Any], what: str, tools: list[ToolDefinition]
    ) -> Any:
        body = json.dumps(tail, separators=(",", ":"), sort_keys=True, default=str)
        text = STEP_DOOR + "\n\n" + instruction + "\n\n" + body
        while len(text) > PROMPT_CHAR_BUDGET and _shed(tail):
            body = json.dumps(tail, separators=(",", ":"), sort_keys=True, default=str)
            text = STEP_DOOR + "\n\n" + instruction + "\n\n" + body
        if len(text) > PROMPT_CHAR_BUDGET:
            self.log.warning(
                "step ask tail ran to %d characters (budget %d) after shedding; "
                "the step ask is small by design, so this is worth reading",
                len(text),
                PROMPT_CHAR_BUDGET,
            )
        self.calls += 1
        self.prompt_chars += len(text)
        response = self.model.invoke(
            system=self.prefix,
            messages=[ModelMessage.text("user", text)],
            tools=tools,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.budget.record(usage, len(self.prefix) + len(text))
            self.input_tokens += max(0, int(getattr(usage, "input_tokens", 0) or 0))
            self.log.info("%s", self.budget.line())
        cached = cached_input_tokens(usage)
        if cached:
            self.cached_tokens += cached
        self.log.info(
            "step ask %s: prefix=%d chars (cacheable) tail=%d chars (~%d tokens) "
            "cached_in=%s",
            what,
            len(self.prefix),
            len(text),
            (len(self.prefix) + len(text)) // 4,
            "unreported" if cached is None else cached,
        )
        return response

    @staticmethod
    def _one_call(
        response: Any, tools: dict[str, dict[str, Any]]
    ) -> tuple[str, dict[str, Any]] | dict[str, Any]:
        """(tool name, arguments) when the answer is exactly one permitted tool
        call whose arguments fit the bench's schema; otherwise the refusal,
        named for what was wrong. Nothing runs before this says yes."""
        allowed = sorted(tools)
        calls = list(getattr(response, "tool_calls", None) or [])
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
        call = tool["call"]
        deal_id = str(ids.get("deal_id") or "")
        step_id = str(ids.get("step_id") or "")
        key = self._key(step_id, call, answer)
        try:
            if call == "propose_act":
                result = self.provider.propose_act(deal_id, step_id, answer, key)
            elif call == "dismiss_reply":
                found = _REPLY_IN_ENDPOINT.search(str(tool["action"].get("endpoint") or ""))
                result = self.provider.dismiss_reply(
                    deal_id, step_id, found.group(1) if found else "",
                    {"reason": str(answer.get("reason") or "")}, key,
                )
            elif call == "reply_step_message":
                result = self._reply(
                    deal_id, step_id, str(answer.get("reply") or "")[:4000], key,
                    answering_for(unanswered_on_step(payload), answer.get("answering")),
                )
            elif call == "post_check_in":
                result = self.provider.post_check_in(deal_id, answer, key)
            elif call == "wait_outside":
                result = self.provider.wait_outside(deal_id, step_id, answer, key)
            elif call == "file_outside_evidence":
                result = self.provider.file_evidence(
                    deal_id, step_id,
                    summary=str(answer.get("summary") or ""),
                    links=list(answer.get("links") or []),
                    receipt_ids=list(answer.get("receipt_ids") or []),
                )
            elif call == "report_parked":
                result = self._park_the_step(
                    deal_id, step_id, answer.get("round"),
                    str(answer.get("reason") or ""), key,
                    answering=answering_for(unanswered_on_step(payload)),
                )
            else:
                result = self._file_the_outcome(ids, answer, payload, key)
        except Exception as error:  # noqa: BLE001 - a refusal is an answer, not a crash
            result = {
                "ok": False,
                "error": getattr(error, "code", None) or type(error).__name__,
                "status": getattr(error, "status", None),
                "message": getattr(error, "message", None) or str(error),
            }
        if isinstance(result, dict) and not result.get("ok", True):
            result = dict(result, failure="server_rejected")
        return result

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
                # A provider written before 0.56.1 takes no `answering`. The
                # reply still goes; the log says the debt was not named.
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
        person can read it.

        The worker-status door is machine-facing -- it moves nothing, owns no
        review round and the person never sees it -- so a parked report alone
        is still silence as far as the person is concerned. The same sentence
        goes on the step thread. The thread is what makes this honest, so a
        refused status report does not stop it and does not fail the move.
        """
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
        # A parked explanation IS an answer when the person is owed one; with
        # nothing owed it claims nothing.
        return self._reply(deal_id, step_id, words[:4000], key, answering)

    def _file_the_outcome(
        self, ids: dict[str, Any], answer: dict[str, Any], payload: dict[str, Any], key: str
    ) -> dict[str, Any]:
        """The 100% pulse, then the outcome. A first pulse of 100 is legal on
        the bench (Steven, 2026-08-25); a pulse already at 100 is not repeated."""
        outcome = dict(answer)
        outcome.setdefault("step_ref", str(ids.get("step_id") or ""))
        latest = payload.get("latest_work_pulse") or {}
        try:
            at_full = int(latest.get("progress_percent") or 0) >= 100
        except (TypeError, ValueError):
            at_full = False
        automatic = (payload.get("submission") or {}).get("completion_recorded_on_outcome") is True
        if not at_full and not automatic:
            note = str(outcome.get("note") or "")[:280]
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
        return self.provider.file_outcome(str(ids.get("target_id") or ""), outcome, key)

    def _note(self, call: Any, result: dict[str, Any]) -> None:
        self.trail.append({
            "call": call,
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "failure": result.get("failure"),
        })

    # -- the run -----------------------------------------------------------
    def run(
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
        """One step, one move, one call (asked twice at most: once, and once
        more with the bench's refusal). Says which road when it is not this one.

        `refused` is the bench's refusal from a PREVIOUS cycle on this same
        state: it rides the first ask, so the second try in the harness's three
        starts from what the bench said rather than from a blank slate.
        """
        move = the_move(payload, obligation)
        step = payload.get("current_step") or {}
        number = step.get("number")
        if not move.get("move"):
            return {"ok": False, "road": ROAD_AGENTIC, "why": move.get("why"), "model_calls": 0}
        if move["move"] == "answer_person":
            latest = answering_for(unanswered_on_step(payload))
            if isinstance(latest, str) and reply_is_held(latest):
                # 0.56.1: the last reply that named this message came back
                # unpaid. Answering it again is how Rick sent six; hold, say
                # so, and let a new message (or the hold running out) move it.
                self.log.warning(
                    "step %s: message %s was answered and the bench did not "
                    "mark it paid; not answering it again this cycle",
                    number, latest,
                )
                return {
                    "ok": True,
                    "road": ROAD_STEP_ASK,
                    "move": move["move"],
                    "call": None,
                    "held": latest,
                    "why": f"a reply naming {latest} already landed unpaid",
                    "tool_count": 0,
                    "result": {"ok": True, "held": latest},
                    "model_calls": 0,
                    "prompt_chars": 0,
                    "prefix_chars": 0,
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "trail": [],
                }
        caches = bool(getattr(self.model, "caches_a_stable_prefix", lambda: False)())
        self.prefix = stable_prefix(brief, act_kinds, with_tools=caches)
        instruction = MOVE_INSTRUCTIONS[move["move"]]
        if pulse_due and move["move"] == "hand_back":
            instruction = instruction + " " + PULSE_DUE_INSTRUCTION
        if isinstance(payload.get(PERSON_SAID_KEY), list):
            # LAW A: the one instruction line rides the tail with the block;
            # the prefix does not move.
            instruction = instruction + "\n" + PERSON_SAID_INSTRUCTION
        tail = step_tail(
            payload, obligation, move, changed or [], pulse_due=pulse_due, refused=refused
        )
        ids = dict(tail["ids"])
        tools = step_tools(move, payload)
        if not tools:
            return {"ok": False, "road": ROAD_AGENTIC, "model_calls": 0,
                    "why": f"the bench publishes no form for the {move['move']} calls"}
        definitions = [_definition(name, tool) for name, tool in tools.items()]
        name: str | None = None
        result: dict[str, Any] = {}
        # Asked twice at most: once, and once more with the refusal.
        for attempt in (1, 2):
            if attempt == 2:
                self.log.warning(
                    "step ask %s step %s: %s refused (%s %s); asking once more with the refusal",
                    move["move"], number, name, result.get("failure"), result.get("error"),
                )
                tail = step_tail(
                    payload, obligation, move, changed or [], pulse_due=pulse_due, refused=result
                )
            response = self._ask(
                (REFUSED_INSTRUCTION + " " + instruction)
                if (refused or attempt == 2) else instruction,
                tail,
                f"{move['move']} step {number}" + (" again" if attempt == 2 else ""),
                definitions,
            )
            picked = self._one_call(response, tools)
            if isinstance(picked, dict):
                name, result = picked.get("call"), picked
            else:
                name, answer = picked
                if tools[name]["call"] == NEED_TOOLS:
                    return {
                        "ok": False,
                        "road": ROAD_AGENTIC,
                        "why": f"the model says it needs tools: {str(answer.get('why'))[:200]}",
                        "model_calls": self.calls,
                        "prompt_chars": self.prompt_chars,
                    }
                result = self._make_the_call(ids, tools[name], answer, payload)
            self._note(name, result)
            if result.get("ok"):
                break
        ok = bool(result.get("ok"))
        if not ok and move["move"] == "outside_work" and name != "report_parked":
            # W29: THE ONE STEP THAT MAY NOT END IN SILENCE. The model
            # answered with nothing, with a tool it was not given, or with
            # arguments the door refused, twice. Whatever it was, nobody is
            # doing this work and the person is owed that sentence in this
            # same poll, not after seven minutes of nothing.
            self.log.warning(
                "step ask outside_work step %s: no call landed (%s); parking the step",
                number, result.get("failure") or result.get("error"),
            )
            self._park_the_step(
                str(ids.get("deal_id") or ""), str(ids.get("step_id") or ""),
                step.get("rounds_used"), "",
                self._key(str(ids.get("step_id") or ""), "report_parked", result),
                answering=answering_for(unanswered_on_step(payload)),
            )
            result = dict(result, parked=True)
        self.log.info(
            "step ask %s step %s: %s %s after %d model call(s); prefix %d chars sent "
            "once and cached, tails %d chars (~%d tokens), input tokens %s, cached input %s",
            move["move"], number, name,
            "made" if ok else f"refused ({result.get('failure')} {result.get('error')})",
            self.calls, len(self.prefix), self.prompt_chars,
            (len(self.prefix) + self.prompt_chars) // 4,
            self.input_tokens or "unreported", self.cached_tokens or "unreported",
        )
        out = {
            "ok": ok,
            "road": ROAD_STEP_ASK,
            "move": move["move"],
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
