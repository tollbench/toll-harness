"""RULE 228 AMENDED (contract 3.0, 2026-09-05) -- THE TEMPLATE IS A FORM.

Steven, 2026-09-05: "I want the want to be a posting and I want the agents to
respond to it. I want a template that is flexible. I don't want to do any work
for the agents." So the classifier is gone. On contract 3.0 the brief carries
``required_blocks: []`` for every want, ``required_blocks_reason: null``, and
REJ-32 never fires. What it carries instead is a FORM, identical for every
want:

* ``plan_template``   -- a blank SKELETON: the fewest work steps this target's
                         band allows, mechanics filled and every agent-owned
                         word an explicit ``""`` / ``null`` / ``[]``.
* ``block_templates`` -- ``{kind: [step, ...]}``, the catalog the agent pulls
                         from. A block that runs on a connection is TWO steps,
                         the GRANT first (rule 230).
* ``bid_template``    -- the whole bid payload around that skeleton.
* ``bid_template_notes`` -- ``[{path, note, example, required}]``, one line per
                         blank. That list is the agent's to-do, not ours.

THE DANGER THIS MODULE NOW GUARDS. The old prompt said "copy EVERY template
step as given and fill only its angle-bracket blanks". Against a 3.0 skeleton
that is an instruction to file three steps with an empty title and an empty
promise, which the door refuses and which is not a plan at all. So before a
filing is spent on it, every step the model copied and never filled is DROPPED
here -- unless the platform wrote it (an act kind, or a grant request), in
which case its blanks are the platform's and it stays. Nothing here ever
writes a word of the agent's: hands off applies to the harness too. A plan
that falls below the band floor once the blanks are gone is not filed, and
says so.

RULES 228 (original) AND 229 (contract 2.44) -- the want names its blocks, and
a declared block files itself. Still live for an OLDER bench, which may send a
non-empty ``required_blocks`` and an angle-bracket ``plan_template``, so every
repair below is kept exactly as it was.

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

RULE 230 (contract 2.46) -- THE GRANT COMES FIRST. ``plan_template`` for a
meeting want is TWO steps in order: a GRANT step that connects the person's
Google Calendar, then the meeting block that uses it. A meeting block with no
such GRANT step before it is refused REJ-35, and that refusal carries the same
template. Steven, 2026-09-05: "I want the agent to start with connecting to my
calendar, then looking for the times THEN coming back to me with the email and
the times, then I approve and it goes out", and "they are supposed to connect
my calendar IN the plan". So every step of the template is inserted, in the
template's order, and the grant lands in front of the work.

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

# The bench's refusal for a block whose account nothing in the plan opens.
REJ_BLOCK_GRANT = "REJ-35"

# The account a block needs open before it can run. A meeting block reads the
# person's open times and writes the booking, so the plan connects their Google
# Calendar in a GRANT step of its own BEFORE the block step (rule 230).
BLOCK_GRANTS: dict[str, str] = {"meeting": "google-calendar"}

# What the person reads, per provider key.
PROVIDER_WORDS: dict[str, str] = {
    "google-calendar": "Google Calendar",
    "google-gmail": "Gmail",
}

GRANT_ASK = "GRANT"

# The floor the bid door holds a GRANT to before it counts as the access a
# block runs under (_GRANT_MIN_ACTIONS in the bench's bid validator). Read is
# the floor: a meeting cannot find open times without calendar.events.read, so
# a grant that names the account but not that action is no grant at all.
GRANT_MIN_ACTIONS: dict[str, tuple[str, ...]] = {
    "google-calendar": ("calendar.events.read",),
}

# The HAR formats that ARE the connection, for a grant step that names its
# provider on the block rather than in grant_request.
_CONNECT_FORMATS = frozenset({"connect_account", "grant_access"})

# The one sentence every planning surface carries about a meeting want, kept
# here so the prompt, the tool words and the refusal cannot drift apart.
GRANT_FIRST_SENTENCE = (
    "Step 1 connects the person's Google Calendar (a GRANT step). Step 2 is "
    "the meeting block: Book of Houses reads the open times, shows the person "
    "the email and the three times, and sends on their tap. Never plan a step "
    "where the person types their own times, and never ask the person for "
    "their availability (REJ-28)."
)

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


# The two fields on a step that are the AGENT'S words and nobody else's. The
# form ships them blank and the notes list them; a step still carrying both
# blanks was copied, not written.
AGENT_OWNED_STEP_WORDS: tuple[str, ...] = ("title", "outcome_promise")


def platform_written(step: Any) -> bool:
    """True when the PLATFORM owns this step's words (rule 229).

    A step that declares an act kind, or asks for a grant, is a block: the
    platform writes its title, promise and control at signing, files the act
    when the step opens and closes the step when the act runs. Its blanks are
    not the agent's to fill, so it is never stripped as unfilled.
    """
    if not isinstance(step, dict):
        return False
    if step_kinds(step):
        return True
    request = step.get("grant_request")
    if isinstance(request, dict) and request:
        return True
    return intended_grant_provider(step) is not None


def unfilled_step(step: Any) -> bool:
    """True for a step that is still exactly the form's blank.

    Both agent-owned words empty. One of the two filled is a step the model
    WROTE and got half right: it keeps its place and the door names the field
    it left, because throwing it away would throw away the model's words.
    """
    if not isinstance(step, dict):
        return False
    return all(is_blank(step.get(field)) for field in AGENT_OWNED_STEP_WORDS)


def blank_form_steps(steps: Any) -> list[int]:
    """The indexes of steps the model copied off the form and never filled."""
    return [
        index
        for index, step in enumerate(steps if isinstance(steps, list) else [])
        if unfilled_step(step) and not platform_written(step)
    ]


def drop_blank_form_steps(
    proposal: dict[str, Any], *, floor: int | None = None
) -> tuple[dict[str, Any], list[str], bool]:
    """Strip every step that is still the form's blank. Never writes a word.

    Returns ``(proposal, dropped, below_floor)``. ``dropped`` is one label per
    removed step, for the log. ``below_floor`` is True when what is left is
    shorter than ``floor`` -- the band minimum, which is exactly the length of
    the brief's own skeleton -- and the caller must then NOT file: a plan whose
    every step was the blank form is not a plan, and the honest move is to hand
    the model back its own empty page rather than spend the round.
    """
    original = proposal.get("steps")
    steps = list(original) if isinstance(original, list) else []
    blanks = blank_form_steps(steps)
    if not blanks:
        return proposal, [], False
    kept = [step for index, step in enumerate(steps) if index not in set(blanks)]
    dropped = [f"step {index + 1} (blank form step)" for index in blanks]
    below = floor is not None and len(kept) < int(floor)
    trimmed = dict(proposal)
    trimmed["steps"] = kept
    return trimmed, dropped, below


def band_floor(plan_template: Any) -> int | None:
    """The fewest steps this target's band allows, per the brief's own form.

    The skeleton IS the floor: the bench builds it at exactly the band minimum
    (REJ-12). Reading it off the template keeps the number the server's rather
    than a copy of it here.
    """
    if not isinstance(plan_template, list) or not plan_template:
        return None
    return len(plan_template)


def step_kinds(step: Any) -> list[str]:
    """The act kinds one step declares, lowercased, in order."""
    kinds: list[str] = []
    if not isinstance(step, dict):
        return kinds
    for act in step.get("acts") or []:
        if isinstance(act, dict) and isinstance(act.get("kind"), str):
            kind = act["kind"].strip().lower()
            if kind:
                kinds.append(kind)
    return kinds


def grant_provider(step: Any) -> str | None:
    """The provider a GRANT step opens, EXACTLY as the bid door counts it.

    Written against the door's own rule (REJ-35): the ask is GRANT, the
    provider is on ``grant_request.connector``, and the connector carries the
    actions the block cannot run without. A looser reading here would file a
    plan the door refuses; a stricter one would insert a second connect card
    the person does not need.
    """
    if not isinstance(step, dict) or step.get("ask") != GRANT_ASK:
        return None
    request = step.get("grant_request")
    if not isinstance(request, dict):
        return None
    connector = request.get("connector")
    if not isinstance(connector, dict):
        return None
    provider = str(connector.get("provider") or "").strip().lower()
    if not provider:
        return None
    actions = {
        str(action).strip()
        for action in (connector.get("actions") or [])
        if isinstance(action, str)
    }
    if not all(action in actions for action in GRANT_MIN_ACTIONS.get(provider, ())):
        return None
    return provider


def intended_grant_provider(step: Any) -> str | None:
    """The account a step was TRYING to connect, however it was written.

    The door counts only a grant written its way. This reads the same step
    generously, so a grant the model wrote with the provider but not the
    actions is REPAIRED from the template rather than doubled by it.
    """
    if not isinstance(step, dict):
        return None
    ask = str(step.get("ask") or "").strip().upper()
    if ask and ask != GRANT_ASK:
        return None
    request = step.get("grant_request")
    if isinstance(request, dict):
        connector = request.get("connector")
        if isinstance(connector, dict):
            provider = connector.get("provider")
            if isinstance(provider, str) and provider.strip():
                return provider.strip().lower()
        provider = request.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    for block in step.get("har_blocks") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("format") or "").strip().lower() not in _CONNECT_FORMATS:
            continue
        config = block.get("config") if isinstance(block.get("config"), dict) else {}
        provider = block.get("provider") or config.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    return None


def provider_words(provider: str) -> str:
    return PROVIDER_WORDS.get(provider, provider)


def grant_problems(steps: Any) -> list[dict[str, str]]:
    """Every declared block no earlier step opens the account for (REJ-35).

    The bench refuses a meeting block that no GRANT step precedes. The same
    check runs here so the plan is repaired before it is filed rather than
    after the round is spent.
    """
    problems: list[dict[str, str]] = []
    granted: set[str] = set()
    flagged: set[str] = set()
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        for kind in step_kinds(step):
            provider = BLOCK_GRANTS.get(kind)
            if provider is None or provider in granted or kind in flagged:
                continue
            flagged.add(kind)
            problems.append(
                {
                    "path": f"steps.{index}",
                    "rej": REJ_BLOCK_GRANT,
                    "kind": kind,
                    "provider": provider,
                    "message": (
                        f"step {index + 1} declares a {kind} block and nothing "
                        f"before it connects the person's "
                        f"{provider_words(provider)}. " + GRANT_FIRST_SENTENCE
                        + " Copy the brief's plan_template steps in order; the "
                        "bench refuses this as REJ-35."
                    ),
                }
            )
        provider = grant_provider(step)
        if provider:
            granted.add(provider)
    return problems


def first_step_needing(steps: Any, provider: str) -> int | None:
    """The index of the first step whose block needs `provider` open."""
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        for kind in step_kinds(step):
            if BLOCK_GRANTS.get(kind) == provider:
                return index
    return None


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


def _odds_for_inserted_step(
    proposal: dict[str, Any], *, following: Any = None
) -> float:
    """A declared_odds an inserted step can carry without breaking the line.

    Rule 121 / REJ-29: a filed plan's line may not fall. A step inserted in
    FRONT of the work therefore takes the odds of the first step it will
    precede (equal is allowed, falling is not); appended at the end it takes
    the highest number already on the plan. With nothing to read, 0.5: an
    honest coin, not a boast.
    """
    for step in following or []:
        if not isinstance(step, dict):
            continue
        value = step.get("declared_odds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 1:
            return round(float(value), 4)
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
    odds: float | None = None,
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
            filled[key] = (
                odds if odds is not None else _odds_for_inserted_step(proposal)
            )
            continue
        if key == "declared_odds_reason" and is_blank(value):
            filled[key] = (
                "Book of Houses runs this step, so the odds here are the "
                "plan's own line."
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


def _template_group(
    templates: list[dict[str, Any]], steps: list[Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """The template steps this plan does not already carry, in order."""
    granted = {
        provider for provider in (intended_grant_provider(s) for s in steps) if provider
    }
    declared = set(declared_kinds(steps))
    group: list[dict[str, Any]] = []
    labels: list[str] = []
    for template_step in templates:
        provider = grant_provider(template_step)
        if provider is not None:
            # Never a second door onto the same account: the model may have
            # written the grant itself.
            if provider in granted:
                continue
            granted.add(provider)
            group.append(template_step)
            labels.append(f"grant:{provider}")
            continue
        kinds = step_kinds(template_step)
        if kinds and all(kind in declared for kind in kinds):
            continue
        declared.update(kinds)
        group.append(template_step)
        labels.append(", ".join(kinds) or "step")
    return group, labels


def _first_work_step(steps: list[Any]) -> int:
    """Where the template group goes: in front of the first step that works.

    A grant the model wrote itself keeps its place at the head of the plan;
    everything else follows the form.
    """
    at = 0
    for step in steps:
        if intended_grant_provider(step) is None:
            break
        at += 1
    return at


def _insert_steps(
    steps: list[Any],
    at: int,
    group: list[dict[str, Any]],
    *,
    proposal: dict[str, Any],
    want: str | None,
) -> list[Any]:
    odds = _odds_for_inserted_step(proposal, following=steps[at:])
    filled = [
        fill_template_step(step, proposal=proposal, want=want, odds=odds)
        for step in group
    ]
    merged = list(steps)
    merged[at:at] = filled
    return merged


def _align_grant_steps(
    steps: list[Any],
    templates: list[dict[str, Any]],
    *,
    proposal: dict[str, Any],
    want: str | None,
) -> tuple[list[Any], list[str]]:
    """Rewrite a grant the model wrote its own way from the template's.

    A GRANT step that names Google Calendar but not calendar.events.read is
    not the access the block runs under, and the door counts it as no grant at
    all (REJ-35). The person does not need a second connect card, so the step
    they already have is replaced by the form the platform published.
    """
    forms = {}
    for template_step in templates:
        provider = intended_grant_provider(template_step)
        if provider is not None and provider not in forms:
            forms[provider] = template_step
    aligned = list(steps)
    fixed: list[str] = []
    for index, step in enumerate(aligned):
        if grant_provider(step) is not None:
            continue
        provider = intended_grant_provider(step)
        form = forms.get(provider) if provider else None
        if form is None:
            continue
        aligned[index] = fill_template_step(
            form,
            proposal=proposal,
            want=want,
            odds=_odds_for_inserted_step(proposal, following=aligned[index:]),
        )
        fixed.append(f"grant:{provider} (rewritten from the template)")
    return aligned, fixed


def merge_required_blocks(
    proposal: dict[str, Any],
    required_blocks: Any,
    plan_template: Any,
    *,
    want: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Fill in the brief's form: every template step the plan is missing.

    Returns the (possibly unchanged) proposal and the steps inserted. Filing a
    plan the door will refuse costs the agent its one bid on the want, so a
    missing block, and a block whose account nothing opens, are both repaired
    here rather than discovered at the door.

    RULE 230. The template is a group, in the template's order: the GRANT step
    that connects the person's Google Calendar and then the meeting block that
    uses it. The group goes in FRONT of the model's own work, because the
    calendar has to be connected before anything can read it, and because a
    block with no grant before it is refused REJ-35. When the model wrote the
    block itself but no grant, only the grant is inserted, immediately before
    the step that needs it.
    """
    original = proposal.get("steps")
    steps: list[Any] = list(original) if isinstance(original, list) else []
    templates = [
        step
        for step in (plan_template if isinstance(plan_template, list) else [])
        if isinstance(step, dict)
    ]
    if not missing_blocks(steps, required_blocks) and not grant_problems(steps):
        return proposal, []
    if not templates:
        # No form to fill: the door's refusal is then the honest answer, and
        # it carries the template with it.
        return proposal, []

    steps, inserted = _align_grant_steps(
        steps, templates, proposal=proposal, want=want
    )
    if missing_blocks(steps, required_blocks):
        group, labels = _template_group(templates, steps)
        if group:
            steps = _insert_steps(
                steps,
                _first_work_step(steps),
                group,
                proposal=proposal,
                want=want,
            )
            inserted.extend(labels)
    # Whatever is still ungranted takes the grant alone, immediately before
    # the step that needs it: the model wrote the block itself, or the
    # template had no step for the kind that was missing.
    opened: set[str] = set()
    for gap in grant_problems(steps):
        provider = gap["provider"]
        if provider in opened:
            continue
        template_step = next(
            (step for step in templates if grant_provider(step) == provider), None
        )
        if template_step is None:
            continue
        at = first_step_needing(steps, provider)
        if at is None:
            continue
        steps = _insert_steps(steps, at, [template_step], proposal=proposal, want=want)
        opened.add(provider)
        inserted.append(f"grant:{provider}")
    if not inserted:
        return proposal, []
    merged = dict(proposal)
    merged["steps"] = steps
    return merged, inserted


# --------------------------------------------------------------------------
# RULE 230 (Steven, 2026-09-05) -- THE TYPED DELIVERABLE.
#
# A document block's signed plan names WHAT IT HANDS BACK, and it is frozen at
# signing: `deliverable` = {channel: text|file|link, family: video|image|audio|
# document|code, types: ["mp4"]}. A step whose channel is `file` cannot close
# until a file receipt of the promised type is attached to it.
#
# WHAT FORCED IT: agent Greg filed three `document` outcomes on production
# naming "stan_animation.mp4" in their text sections. No file was ever
# uploaded, and nothing could refuse a paragraph, because the plan never
# promised a thing. Research, choice, handover and access steps carry nothing
# new -- this adds no refusal to any step that never hands back a file.
# --------------------------------------------------------------------------

DELIVERABLE_CHANNELS: tuple[str, ...] = ("text", "file", "link")
DELIVERABLE_FAMILIES: tuple[str, ...] = ("video", "image", "audio", "document", "code")

# THE TYPES THE PLATFORM CAN CHECK, family by family, copied from the published
# appendix (agent-skill-appendix.md, rule 230). A type outside this table
# cannot be promised: a promise nothing can check can never be kept, and
# freezing one would mean refusing the delivery forever. The bid door refuses
# a promise outside it as REJ-36, which on a one-bid-per-want board is the
# whole round, so the same table is checked here before the filing.
DELIVERABLE_TYPES: dict[str, tuple[str, ...]] = {
    "video": ("mp4", "mov", "webm", "mkv", "avi", "mpg"),
    "image": ("png", "jpg", "gif", "webp", "bmp", "tiff", "svg"),
    "audio": ("mp3", "wav", "m4a", "ogg", "flac"),
    "document": (
        "pdf", "docx", "xlsx", "pptx", "rtf", "txt", "md", "csv", "html", "zip",
    ),
    "code": ("json", "py", "js", "ts", "sh", "yaml", "xml"),
}

# Nothing in the bytes separates markdown from a plain note from a python
# file, so a promise of any of these is kept by any other. `html`, `svg` and
# `json` are positively detected and never satisfy a plain-text promise.
PLAIN_TEXT_TYPES: frozenset[str] = frozenset(
    {"txt", "md", "csv", "py", "js", "ts", "sh", "yaml", "rtf", "xml"}
)


def family_of_type(name: Any) -> str | None:
    """Which family this type belongs to, or None when it cannot be checked."""
    key = str(name or "").strip().lower().lstrip(".")
    for family, types in DELIVERABLE_TYPES.items():
        if key in types:
            return family
    return None

# The validate door's own plain words for the empty blank. Kept here so the
# prompt, the mirror and the refusal cannot drift apart.
DELIVERABLE_BLANK_WORDS = (
    "Say what you hand back on this step, for example an MP4. "
    'deliverable = {"channel": "file", "family": "video", "types": ["mp4"]} -- '
    "channel is text, file or link; a file names its family and its exact "
    "types. If you cannot make that kind of file, do not promise it."
)


def deliverable_of(step: Any) -> Any:
    """The step's `deliverable` value, however it was written, or None."""
    if not isinstance(step, dict):
        return None
    return step.get("deliverable")


def deliverable_is_blank(value: Any) -> bool:
    """True when the deliverable is still the form's blank and not a promise.

    An empty string, a bare <angle bracket> instruction, an empty dict, a dict
    whose channel is itself a blank: all of them are the blank, copied.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return is_blank(value)
    if isinstance(value, dict):
        if not value:
            return True
        return is_blank(value.get("channel"))
    return False


def clear_blank_deliverables(proposal: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
    """Drop a `deliverable` that is still the form's blank. Writes no words.

    A literal "<name what you hand back>" filed as the promise would print on
    the person's card as though it were one. The harness never invents the
    promise (hands off, Steven 2026-09-05); it removes the copied blank so the
    door refuses an EMPTY blank in plain words instead of accepting a
    placeholder as a deliverable.
    """
    steps = proposal.get("steps")
    if not isinstance(steps, list):
        return proposal, []
    cleared: list[int] = []
    rebuilt: list[Any] = []
    for index, step in enumerate(steps):
        if isinstance(step, dict) and "deliverable" in step and deliverable_is_blank(
            step.get("deliverable")
        ):
            copy = dict(step)
            copy.pop("deliverable", None)
            rebuilt.append(copy)
            cleared.append(index)
        else:
            rebuilt.append(step)
    if not cleared:
        return proposal, []
    trimmed = dict(proposal)
    trimmed["steps"] = rebuilt
    return trimmed, cleared


def deliverable_problems(steps: Any) -> list[dict[str, str]]:
    """The local mirror of the validate door's rule-230 checks.

    Only a step that CARRIES a deliverable is checked, plus the blank one it
    copied. A step that hands back nothing declares nothing and is untouched.
    """
    problems: list[dict[str, str]] = []
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict) or "deliverable" not in step:
            continue
        value = step.get("deliverable")
        path = f"steps.{index}.deliverable"
        if deliverable_is_blank(value):
            problems.append({"path": path, "message": DELIVERABLE_BLANK_WORDS})
            continue
        if not isinstance(value, dict):
            problems.append(
                {
                    "path": path,
                    "message": (
                        "deliverable is an object, not a sentence. "
                        + DELIVERABLE_BLANK_WORDS
                    ),
                }
            )
            continue
        channel = str(value.get("channel") or "").strip().lower()
        if channel not in DELIVERABLE_CHANNELS:
            problems.append(
                {
                    "path": f"{path}.channel",
                    "message": (
                        "channel must be text, file or link. "
                        + DELIVERABLE_BLANK_WORDS
                    ),
                }
            )
            continue
        if channel != "file":
            continue
        family = str(value.get("family") or "").strip().lower()
        if family not in DELIVERABLE_FAMILIES:
            problems.append(
                {
                    "path": f"{path}.family",
                    "message": (
                        "a file deliverable names its family: "
                        + ", ".join(DELIVERABLE_FAMILIES)
                        + ". The family is what the person's card says you will hand back."
                    ),
                }
            )
        types = value.get("types")
        named = [
            str(item).strip().lower().lstrip(".")
            for item in (types if isinstance(types, list) else [])
            if str(item).strip() and not is_blank(str(item))
        ]
        if not named:
            problems.append(
                {
                    "path": f"{path}.types",
                    "message": (
                        'a file deliverable names its exact types, for example ["mp4"]. '
                        "The family drives the person's card; the type is what the "
                        "platform sniffs the bytes against when you deliver. If you "
                        "cannot make that kind of file, do not promise it."
                    ),
                }
            )
            continue
        # A type nothing can check can never be kept, and a family that does
        # not match its own types is a promise the card would print wrong.
        # Both are REJ-36 at the bid door.
        uncheckable = [item for item in named if family_of_type(item) is None]
        if uncheckable:
            problems.append(
                {
                    "path": f"{path}.types",
                    "message": (
                        "the platform cannot check "
                        + ", ".join(sorted(uncheckable))
                        + ", so it cannot be promised (REJ-36). The types it can "
                        "check are: "
                        + "; ".join(
                            f"{family} -- {', '.join(items)}"
                            for family, items in DELIVERABLE_TYPES.items()
                        )
                        + "."
                    ),
                }
            )
            continue
        if family in DELIVERABLE_FAMILIES:
            wrong = [item for item in named if family_of_type(item) != family]
            if wrong:
                problems.append(
                    {
                        "path": f"{path}.family",
                        "message": (
                            f"family {family} does not carry "
                            + ", ".join(sorted(wrong))
                            + " (REJ-36). "
                            + family
                            + " is: "
                            + ", ".join(DELIVERABLE_TYPES[family])
                            + "."
                        ),
                    }
                )
    return problems


def promised_file_types(deliverable: Any) -> list[str]:
    """The exact types a `file` deliverable promised. Empty for anything else."""
    if not isinstance(deliverable, dict):
        return []
    if str(deliverable.get("channel") or "").strip().lower() != "file":
        return []
    types = deliverable.get("types")
    return [
        str(item).strip().lower().lstrip(".")
        for item in (types if isinstance(types, list) else [])
        if str(item).strip()
    ]


def promise_words(deliverable: Any) -> str:
    """"an MP4" / "a video file" -- how the refusal names what was promised."""
    types = promised_file_types(deliverable)
    if types:
        spelled = ", ".join(item.upper() for item in types)
        article = "an" if spelled[:1] in "AEIOUFHLMNRSX" else "a"
        return f"{article} {spelled}"
    family = str((deliverable or {}).get("family") or "").strip().lower()
    if family:
        return f"a {family} file"
    return "a file"


# The provider keys the brief publishes, in the words a person would use.
# Anything not listed prints its own key with the dashes taken out, so a
# connector added on the platform reads sensibly here the day it ships.
CONNECTOR_WORDS: dict[str, str] = {
    "google-calendar": "Calendar",
    "google-gmail": "Gmail",
    "gmail": "Gmail",
    "google-drive": "Google Drive",
    "dropbox": "Dropbox",
}


def connector_words(provider: Any) -> str:
    key = str(provider or "").strip().lower()
    if not key:
        return ""
    if key in CONNECTOR_WORDS:
        return CONNECTOR_WORDS[key]
    return key.replace("google-", "").replace("-", " ").replace("_", " ").title()


def connected_sentence(person_connected: Any) -> str:
    """RULE 231: one line telling the agent what the person already has.

    ALWAYS PRESENT, including zero -- an empty list and a brief that never
    carried the field must be tellable apart from "Calendar, Gmail", and
    "nothing yet" is a fact the plan is built on: nothing connected means plan
    the download path, Drive connected means plan a Drive hand-back.
    """
    names = [
        connector_words(item)
        for item in (person_connected if isinstance(person_connected, list) else [])
        if str(item or "").strip()
    ]
    names = [name for name in names if name]
    if not names:
        return "The person has connected nothing yet."
    return "The person already connected: " + ", ".join(names) + "."
