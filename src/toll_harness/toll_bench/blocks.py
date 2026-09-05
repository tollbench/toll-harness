"""RULES 228 AND 229 (contract 2.44) -- the want names its blocks, and a
declared block files itself.

WHAT FORCED THIS. One meeting want drew three agents and none of them got a
meeting booked. The third dropped the act altogether and filed a text document
called "Scheduling request for approval" on a plain APPROVE step: nothing was
declared, so nothing gated it, and the person's Approve would have closed that
step with nothing sent. Nothing anywhere had ever said a meeting want needs a
meeting block. Now the brief says it, in three keys that are always present:

* ``required_blocks``       -- the act kinds this want cannot be delivered
                               without; ``[]`` means a free-form plan is fine.
* ``required_blocks_reason``-- ``{kind: one sentence}`` or null.
* ``plan_template``         -- one ready-to-file proposal step per required
                               block, with ``<angle bracket>`` blanks.

A plan that declares no act of a required kind is refused REJ-32, and the
refusal carries the same ``plan_template``. A declared block whose fields the
kind refuses is REJ-33. A step describing an invitation, a booking or a publish
while declaring no act at all is REJ-34.

This module is the harness's deterministic half of that: it reads the blocks
off the brief, fills the template's blanks from the model's own plan, and
checks a declared meeting against the kind's published grammar BEFORE the
filing is spent on it. The model is told to copy the template; this is what
happens when it does not.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The kinds whose fields this module knows how to check at home. Everything
# else is left to the server's own sentence (REJ-33): a local check we cannot
# keep in step with the kind would refuse a legal block.
CHECKED_KINDS: frozenset[str] = frozenset({"meeting"})

# A template blank: the whole value is one <angle bracket> instruction.
_PLACEHOLDER = re.compile(r"^\s*<[^<>]*>\s*$", re.DOTALL)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Addresses that are never an invitee: our own platform mailboxes and the
# example domains every model reaches for when it is guessing.
_NOT_AN_INVITEE = (
    "bookofhouses.com",
    "tollbench.com",
    "boho.team",
    "example.com",
    "example.org",
    "example.net",
)

# The meeting kind's own window grammar (app/services/act_kinds/meeting.py).
_WINDOW_WORDS = ("next week", "this week")
_WINDOW_N_DAYS = re.compile(r"^next\s+(\d{1,2})\s+days?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# THE MESSAGE CARRIES NO WHEN (rule 223). Copied deliberately, verbatim in
# behaviour, from the meeting kind: the platform inserts the person's real open
# times, so a day or a clock time in the agent's words can only contradict
# them. Narrow on purpose: "let's find a time" is not a time, "Tuesday at 3pm"
# is.
_WHEN = re.compile(
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)"
    r"\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}\s*[:.]\s*\d{2}\s*(?:am|pm)?\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b"
    r"|\b(?:today|tomorrow|tonight)\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    re.IGNORECASE,
)

MESSAGE_MAX = 4000

# The words that open an invitation when the model left the blank unfilled.
# Institution voice, no dates, no times, no em-dashes.
_FALLBACK_MESSAGE = (
    "I am an assistant at the Book of Houses, helping set up a short "
    "conversation. Please pick whichever of the open times below suits you "
    "and it will land on both calendars."
)


def is_blank(value: Any) -> bool:
    """True for a template blank: empty, or a bare <angle bracket> instruction."""
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return not text or bool(_PLACEHOLDER.match(text))
    return False


def declared_kinds(steps: Any) -> list[str]:
    """Every act kind the plan's steps declare, lowercased, in order."""
    kinds: list[str] = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        for act in step.get("acts") or []:
            if isinstance(act, dict) and isinstance(act.get("kind"), str):
                kind = act["kind"].strip().lower()
                if kind:
                    kinds.append(kind)
    return kinds


def missing_blocks(steps: Any, required_blocks: Any) -> list[str]:
    """The required kinds no step declares. Empty when the plan is legal."""
    declared = set(declared_kinds(steps))
    return [
        str(kind).strip().lower()
        for kind in (required_blocks or [])
        if isinstance(kind, str) and str(kind).strip().lower() not in declared
    ]


def meeting_problems(act: dict[str, Any]) -> list[str]:
    """A declared meeting block's fields, against the kind's own grammar.

    The kind's sentence is the server's to write (REJ-33); these are the same
    limits, checked at home so the one filing a target allows is never spent on
    a window typo.
    """
    problems: list[str] = []
    who = act.get("with")
    if not is_blank(who):
        text = str(who).strip()
        if not _EMAIL.fullmatch(text) or len(text) > 320:
            problems.append(
                "`with` must be the invitee's email address, or be left out "
                "and the person is asked for it on the card"
            )
    duration = act.get("duration_min")
    if duration is not None:
        try:
            minutes = int(duration)
        except (TypeError, ValueError):
            problems.append("duration_min must be a whole number of minutes")
        else:
            if minutes < 15 or minutes > 240:
                problems.append("duration_min must be between 15 and 240")
    window = act.get("window")
    if window is not None and not is_blank(window):
        if isinstance(window, dict):
            start, end = str(window.get("start") or "")[:10], str(window.get("end") or "")[:10]
            if not _ISO_DATE.match(start) or not _ISO_DATE.match(end):
                problems.append("window.start and window.end must be YYYY-MM-DD dates")
            elif end < start:
                problems.append("window must run forward and cover at most 60 days")
        else:
            text = str(window).strip().lower()
            if text not in _WINDOW_WORDS and not _WINDOW_N_DAYS.match(text):
                problems.append(
                    "window must be 'next week', 'this week', 'next N days', "
                    "or {start, end} dates"
                )
    offer = act.get("offer_count")
    if offer is not None:
        try:
            count = int(offer)
        except (TypeError, ValueError):
            problems.append("offer_count must be a whole number between 1 and 5")
        else:
            if count < 1 or count > 5:
                problems.append("offer_count must be between 1 and 5")
    message = act.get("message")
    if not is_blank(message):
        text = str(message)
        if len(text) > MESSAGE_MAX:
            problems.append(f"message must be {MESSAGE_MAX} characters or fewer")
        found = _WHEN.search(text)
        if found is not None:
            problems.append(
                f"message must carry no dates or times (found "
                f"{found.group(0)[:40]!r}). Book of Houses offers the times "
                "and the invitee picks one, so a time in your words "
                "contradicts the invitation it rides in (rule 223). Write why "
                "the meeting is worth having and leave the when to us."
            )
    return problems


def declaration_problems(steps: Any) -> list[dict[str, str]]:
    """Every declared block whose fields its own kind would refuse (REJ-33)."""
    problems: list[dict[str, str]] = []
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        for position, act in enumerate(step.get("acts") or []):
            if not isinstance(act, dict):
                continue
            kind = str(act.get("kind") or "").strip().lower()
            if kind not in CHECKED_KINDS:
                continue
            for message in meeting_problems(act):
                problems.append(
                    {"path": f"steps.{index}.acts.{position}", "message": message}
                )
    return problems


def _plan_text(proposal: dict[str, Any]) -> str:
    """Everything the model wrote in its own plan, as one searchable string."""
    return json.dumps(
        {
            "steps": proposal.get("steps"),
            "pitch_title": proposal.get("pitch_title"),
            "pitch_body": proposal.get("pitch_body"),
            "strategy": proposal.get("strategy"),
            "smart_goals": proposal.get("smart_goals"),
        },
        default=str,
    )


def invitee_from_plan(proposal: dict[str, Any]) -> str | None:
    """The invitee's address, if the model's OWN plan named exactly one.

    Deliberately not read from the brief: an address on the brief is usually
    the person's own, and mailing the person their own invitation is worse
    than leaving `with` out and letting the card ask them for it (rule 229).
    """
    found = []
    for address in _EMAIL.findall(_plan_text(proposal)):
        lowered = address.lower()
        if any(lowered.endswith(domain) for domain in _NOT_AN_INVITEE):
            continue
        if lowered not in found:
            found.append(lowered)
    return found[0] if len(found) == 1 else None


def _odds_for_inserted_step(proposal: dict[str, Any]) -> float:
    """A declared_odds the plan's own line can carry.

    Rule 121 / REJ-29: a filed plan's line may not fall, so an appended step
    takes the highest number already on the plan. With nothing to read, 0.5:
    an honest coin, not a boast.
    """
    values = []
    for step in proposal.get("steps") or []:
        if not isinstance(step, dict):
            continue
        value = step.get("declared_odds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 1:
            values.append(float(value))
    if values:
        return round(max(values), 4)
    return 0.5


def fill_template_step(
    template_step: dict[str, Any],
    *,
    proposal: dict[str, Any] | None = None,
    want: str | None = None,
) -> dict[str, Any]:
    """One template step with its <angle bracket> blanks filled or dropped.

    Only the blanks move. The title, the promise and the har_blocks are copied
    exactly as the platform published them, because the block rewrites them at
    signing anyway (rule 229) and a rewritten prose version teaches the model
    that its words matter here. They do not: its hands on a block are the
    fields and the words inside ``acts``.
    """
    proposal = proposal or {}
    filled: dict[str, Any] = {}
    for key, value in (template_step or {}).items():
        if key == "acts":
            filled[key] = [
                _fill_act(act, proposal=proposal, want=want)
                for act in (value or [])
                if isinstance(act, dict)
            ]
            continue
        if key == "declared_odds" and is_blank(value):
            filled[key] = _odds_for_inserted_step(proposal)
            continue
        if key == "declared_odds_reason" and is_blank(value):
            filled[key] = (
                "Book of Houses runs the invitation and the booking on this "
                "step, so the odds here are the plan's own line."
            )
            continue
        if is_blank(value):
            # An unfilled blank is worse than an absent key: every blank in a
            # published template is a field the kind either defaults or asks
            # the person for.
            continue
        filled[key] = value
    return filled


def _fill_act(
    act: dict[str, Any], *, proposal: dict[str, Any], want: str | None
) -> dict[str, Any]:
    filled: dict[str, Any] = {}
    for key, value in act.items():
        if key == "with" and is_blank(value):
            invitee = invitee_from_plan(proposal)
            if invitee:
                filled[key] = invitee
            # else: left out on purpose. The person is asked on their card.
            continue
        if key == "message" and is_blank(value):
            filled[key] = _default_message(want)
            continue
        if is_blank(value):
            continue
        filled[key] = value
    if is_blank(filled.get("message")):
        filled["message"] = _default_message(want)
    return filled


# The want is written in the person's own first person ("I want to set up a
# call with Ruby"), and it is the INVITEE who reads this message. Quoting it
# raw puts the person's voice in the agent's mouth. These are trimmed off the
# front, and the when is trimmed out of the middle, because the platform owns
# every date and time in the invitation.
_WANT_OPENERS = (
    "i want to ",
    "i want ",
    "i need to ",
    "i need ",
    "i would like to ",
    "i am looking to ",
    "i'd like to ",
)
_WHEN_WORDS = re.compile(r"\b(?:next|this|coming)\s+(?:week|month)\b", re.IGNORECASE)


def _topic_from_want(want: str | None) -> str:
    topic = " ".join(str(want or "").split())
    lowered = topic.lower()
    for opener in _WANT_OPENERS:
        if lowered.startswith(opener):
            topic = topic[len(opener):]
            break
    topic = " ".join(_WHEN_WORDS.sub(" ", topic).split())
    return topic[:160].rstrip(" .,;:")


def _default_message(want: str | None) -> str:
    """Words that open the invitation when the model left the blank.

    Says who is writing and what it is about, and carries no date and no time:
    the platform appends the person's real open times underneath.
    """
    topic = _topic_from_want(want)
    if not topic:
        return _FALLBACK_MESSAGE
    message = (
        f"I am an assistant at the Book of Houses, helping with this: {topic}. "
        "Please pick whichever of the open times below suits you and it will "
        "land on both calendars."
    )
    if _WHEN.search(message):
        # The want itself named a day or a clock time. The invitation may not.
        return _FALLBACK_MESSAGE
    return message


def merge_required_blocks(
    proposal: dict[str, Any],
    required_blocks: Any,
    plan_template: Any,
    *,
    want: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Insert the template step for every required block the plan is missing.

    Returns the (possibly unchanged) proposal and the kinds inserted. Filing a
    plan the door will refuse costs the agent its one bid on the want, so a
    missing block is repaired here rather than discovered at the door. The
    step goes at the END: the block is the delivery, and appending keeps the
    declared-odds line from falling.
    """
    missing = missing_blocks(proposal.get("steps"), required_blocks)
    if not missing:
        return proposal, []
    templates: dict[str, dict[str, Any]] = {}
    for step in plan_template if isinstance(plan_template, list) else []:
        if not isinstance(step, dict):
            continue
        for act in step.get("acts") or []:
            if isinstance(act, dict) and isinstance(act.get("kind"), str):
                templates.setdefault(act["kind"].strip().lower(), step)
    merged = dict(proposal)
    steps = list(merged.get("steps") or [])
    inserted: list[str] = []
    for kind in missing:
        template_step = templates.get(kind)
        if template_step is None:
            # No form to fill: the door's refusal is then the honest answer,
            # and it carries the template with it.
            continue
        steps.append(fill_template_step(template_step, proposal=merged, want=want))
        inserted.append(kind)
    if not inserted:
        return proposal, []
    merged["steps"] = steps
    return merged, inserted
