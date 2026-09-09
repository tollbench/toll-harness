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
     since the last look, the one thing to produce and the exact call. The
     model answers with the payload for that one call, the runtime makes the
     call, done. The prefix is `draft.stable_prefix`, byte for byte, so the
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
from datetime import datetime, timezone
from typing import Any

from toll_harness.core.budget import ContextBudget
from toll_harness.core.types import ModelMessage
from toll_harness.toll_bench.draft import (
    PROMPT_CHAR_BUDGET,
    _fit,
    cached_input_tokens,
    read_json_object,
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
        return {
            "move": None,
            "road": ROAD_AGENTIC,
            "why": "an approved outside act is the agent's to go and do itself",
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
    "move that is yours. Answer with the payload for that one call and nothing "
    "else. Never write 'click Approve to send' or 'from your mailbox' to the "
    "person; never ask for a password, code, session or cookie; no bare URLs "
    "in a pulse."
)

MOVE_INSTRUCTIONS: dict[str, str] = {
    "answer_reply": (
        "Someone outside answered your email and the bench says you owe them a "
        "reply before anything else moves on this step (rule 220). Answer in "
        'their thread: {"call": "propose_act", "act": {"in_reply_to": "<the '
        'reply id>", "body_text": "<your words>"}} -- the bench fills to and '
        "subject from the thread. If it is not a question (spam, a bounce, an "
        'out-of-office) say why in one sentence: {"call": "dismiss_reply", '
        '"reply_id": "<id>", "reason": "<one plain sentence>"}.'
    ),
    "answer_person": (
        "The person wrote on this step and nothing has gone back. Answer them, "
        'plainly, and nothing else this round: {"call": "reply_step_message", '
        '"reply": "<your words, under 4000 characters>"}. This is chat: it does '
        "not close the step and does not open an ask."
    ),
    "refile_act": (
        "The person sent your act back, or it was denied or failed, and their "
        "reason is in `note` on that act. That act is DEAD: never re-file the "
        "same words and never wait on it. File ONE changed act that answers "
        'them: {"call": "propose_act", "act": {"kind": "email", "to": "...", '
        '"subject": "...", "body_text": "...", "purpose": "..."}} (kind '
        "calendar_event carries summary, start, end; kind meeting carries "
        "`with`). If their reason is not something you can act on, say so on "
        'the thread instead: {"call": "reply_step_message", "reply": "..."}.'
    ),
    "file_act": (
        "Your signed plan declared an act on this step and it is not filed "
        "yet; the bench refuses the outcome until it is (acts_not_filed). File "
        'it exactly: {"call": "propose_act", "act": {"kind": "email", "to": '
        '"...", "subject": "...", "body_text": "...", "purpose": "..."}} or '
        '{"call": "propose_act", "act": {"kind": "calendar_event", "summary": '
        '"...", "start": {"dateTime": "...", "timeZone": "..."}, "end": {...}}}. '
        "The person approves it word for word and Book of Houses sends it; you "
        "never send it and never ask them to. If you do not know the recipient, "
        'ask the person: {"call": "reply_step_message", "reply": "..."}.'
    ),
    "hand_back": (
        "Hand this step back: write the thing the step promised, from what is "
        'in front of you, and file it: {"call": "file_outcome", "pulse": '
        '{"changed": "<what you did, under 280 chars>", "now": "<one line>", '
        '"next": "<one line>"}, "outcome": {"note": "<overview and the '
        "person's next instruction, under 280 chars>\", \"document\": "
        '{"title": "...", "blocks": [{"type": "heading", "text": "..."}, '
        '{"type": "paragraph", "text": "..."}, {"type": "bullets", "items": '
        '["..."]}]}}}. An APPROVE step takes `document`; any other ask may '
        "carry `text` (a short string) instead of `document`. If "
        "`deliverable.fields` is named, the document carries a `cards` block: "
        "items are objects keyed by exactly those field names, at least "
        "min_count of them, every value filled. If an act on this step reads "
        "sent or executed, quote its receipt in the document and send nothing "
        "yourself. If you asked someone outside and nothing can move until "
        'they answer: {"call": "wait_outside", "wait": {"on": "email_reply", '
        '"who": "...", "what": "..."}} (on is email_reply, third_party or '
        'provider). If you must ask the person one thing first: {"call": '
        '"reply_step_message", "reply": "..."}. If this step cannot be done '
        "without a live search, a browser, a file or a tool: "
        '{"call": "need_tools", "why": "..."} and nothing else.'
    ),
}

PULSE_DUE_INSTRUCTION = (
    "A progress pulse is due (rule 100). If you are filing the outcome now, "
    "the pulse rides with it. If you are not, post one honest pulse: "
    '{"call": "post_check_in", "pulse": {"changed": "...", "now": "...", '
    '"next": "...", "progress_percent": 0|25|50|75, "blocker": "<optional>"}} '
    "-- never flat progress with no blocker on an ask the person cannot see yet."
)

REFUSED_INSTRUCTION = (
    "The bench refused your last answer; its own words are in `the_bench_refused`. "
    "Fix exactly what it names and answer once more with the same call shape."
)


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
    if obligation.get("kind") == "draft_sent_back" and obligation.get("sent_back_reason"):
        tail["sent_back_reason"] = _fit(obligation.get("sent_back_reason"), 400)
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
    def _ask(self, instruction: str, tail: dict[str, Any], what: str) -> dict[str, Any]:
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
            tools=[],
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
        return read_json_object(getattr(response, "text", ""))

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
        move: dict[str, Any],
        answer: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        call = str(answer.get("call") or "").strip()
        allowed = list(move.get("calls") or [])
        if call not in allowed:
            return {
                "ok": False,
                "error": "call_not_the_move",
                "allowed": allowed,
                "message": (
                    f"The move on this step is {move.get('move')}; the call must be one "
                    f"of {', '.join(allowed)}."
                ),
            }
        deal_id = str(ids.get("deal_id") or "")
        step_id = str(ids.get("step_id") or "")
        key = self._key(step_id, call, answer)
        try:
            if call == "propose_act":
                act = answer.get("act")
                if not isinstance(act, dict) or not act:
                    return {"ok": False, "error": "missing_act",
                            "message": "`act` must be an object."}
                return self.provider.propose_act(deal_id, step_id, act, key)
            if call == "dismiss_reply":
                return self.provider.dismiss_reply(
                    deal_id, step_id, str(answer.get("reply_id") or ""),
                    {"reason": str(answer.get("reason") or "")}, key,
                )
            if call == "reply_step_message":
                reply = str(answer.get("reply") or "").strip()
                if not reply:
                    return {"ok": False, "error": "missing_reply", "message": "`reply` is empty."}
                return self.provider.reply_step_message(deal_id, step_id, reply[:4000], key)
            if call == "post_check_in":
                pulse = answer.get("pulse")
                if not isinstance(pulse, dict):
                    return {"ok": False, "error": "missing_pulse",
                            "message": "`pulse` must be an object."}
                return self.provider.post_check_in(deal_id, pulse, key)
            if call == "wait_outside":
                wait = answer.get("wait")
                if not isinstance(wait, dict):
                    return {"ok": False, "error": "missing_wait",
                            "message": "`wait` must be an object."}
                return self.provider.wait_outside(deal_id, step_id, wait, key)
            if call == "file_outcome":
                return self._file_the_outcome(ids, answer, payload, key)
        except Exception as error:  # noqa: BLE001 - a refusal is an answer, not a crash
            return {
                "ok": False,
                "error": getattr(error, "code", None) or type(error).__name__,
                "status": getattr(error, "status", None),
                "message": getattr(error, "message", None) or str(error),
            }
        return {"ok": False, "error": "unknown_call", "message": f"No door for {call}."}

    def _file_the_outcome(
        self, ids: dict[str, Any], answer: dict[str, Any], payload: dict[str, Any], key: str
    ) -> dict[str, Any]:
        """The 100% pulse, then the outcome. A first pulse of 100 is legal on
        the bench (Steven, 2026-08-25); a pulse already at 100 is not repeated."""
        outcome = answer.get("outcome")
        if not isinstance(outcome, dict) or not outcome:
            return {"ok": False, "error": "missing_outcome",
                    "message": "`outcome` must be an object."}
        outcome = dict(outcome)
        outcome.setdefault("step_ref", str(ids.get("step_id") or ""))
        latest = payload.get("latest_work_pulse") or {}
        try:
            at_full = int(latest.get("progress_percent") or 0) >= 100
        except (TypeError, ValueError):
            at_full = False
        if not at_full:
            given = answer.get("pulse") if isinstance(answer.get("pulse"), dict) else {}
            note = str(outcome.get("note") or "")[:280]
            pulse = {
                "changed": str(given.get("changed") or note or "Finished the step's work")[:280],
                "now": str(given.get("now") or "Filing the handover")[:280],
                "next": str(given.get("next") or "Your review")[:280],
                "progress_percent": 100,
            }
            pulsed = self.provider.post_check_in(
                str(ids.get("deal_id") or ""), pulse, key + "-pulse"
            )
            if not (pulsed or {}).get("ok", True):
                self.log.warning("the 100%% pulse before the outcome was refused: %s", pulsed)
        return self.provider.file_outcome(str(ids.get("target_id") or ""), outcome, key)

    def _note(self, answer: dict[str, Any], result: dict[str, Any]) -> None:
        self.trail.append({
            "call": answer.get("call"),
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
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
    ) -> dict[str, Any]:
        """One step, one move, one call (asked twice at most: once, and once
        more with the bench's refusal). Says which road when it is not this one."""
        move = the_move(payload, obligation)
        step = payload.get("current_step") or {}
        number = step.get("number")
        if not move.get("move"):
            return {"ok": False, "road": ROAD_AGENTIC, "why": move.get("why"), "model_calls": 0}
        caches = bool(getattr(self.model, "caches_a_stable_prefix", lambda: False)())
        self.prefix = stable_prefix(brief, act_kinds, with_tools=caches)
        instruction = MOVE_INSTRUCTIONS[move["move"]]
        if pulse_due and move["move"] == "hand_back":
            instruction = instruction + " " + PULSE_DUE_INSTRUCTION
        tail = step_tail(payload, obligation, move, changed or [], pulse_due=pulse_due)
        ids = dict(tail["ids"])
        answer = self._ask(instruction, tail, f"{move['move']} step {number}")
        if str(answer.get("call") or "") == NEED_TOOLS:
            return {
                "ok": False,
                "road": ROAD_AGENTIC,
                "why": f"the model says it needs tools: {str(answer.get('why') or '')[:200]}",
                "model_calls": self.calls,
                "prompt_chars": self.prompt_chars,
            }
        result = self._make_the_call(ids, move, answer, payload)
        self._note(answer, result)
        if not result.get("ok"):
            self.log.warning(
                "step ask %s step %s: %s refused (%s); asking once more with the refusal",
                move["move"], number, answer.get("call"), result.get("error"),
            )
            tail = step_tail(
                payload, obligation, move, changed or [], pulse_due=pulse_due, refused=result
            )
            answer = self._ask(
                REFUSED_INSTRUCTION + " " + instruction, tail, f"{move['move']} step {number} again"
            )
            if str(answer.get("call") or "") == NEED_TOOLS:
                return {
                    "ok": False,
                    "road": ROAD_AGENTIC,
                    "why": f"the model says it needs tools: {str(answer.get('why') or '')[:200]}",
                    "model_calls": self.calls,
                    "prompt_chars": self.prompt_chars,
                }
            result = self._make_the_call(ids, move, answer, payload)
            self._note(answer, result)
        ok = bool(result.get("ok"))
        self.log.info(
            "step ask %s step %s: %s %s after %d model call(s); prefix %d chars sent "
            "once and cached, tails %d chars (~%d tokens), input tokens %s, cached input %s",
            move["move"], number, answer.get("call"),
            "made" if ok else f"refused ({result.get('error')})",
            self.calls, len(self.prefix), self.prompt_chars,
            (len(self.prefix) + self.prompt_chars) // 4,
            self.input_tokens or "unreported", self.cached_tokens or "unreported",
        )
        out = {
            "ok": ok,
            "road": ROAD_STEP_ASK,
            "move": move["move"],
            "call": answer.get("call"),
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
            out["message"] = result.get("message")
        return out
