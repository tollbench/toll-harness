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

RULE 236 (Steven, 2026-09-08: "fix it, remove the old path and lets do it") --
A CONNECTION IS NOT A STEP, IT IS PART OF THE ACTION THAT NEEDS IT. A block
that runs on the person's account carries a ``connect_account`` ROW inside the
step that uses it: the card is the account rows, then what the step does, then
one button that stays asleep until every row is settled. ``plan_template`` for
a meeting want is now ONE step -- a google-calendar row, a google-gmail row and
the meeting block on a single card -- and a NEW plan that lifts a registry
connector back into a GRANT step of its own is refused REJ-38
(grant_step_removed) at the validate door and at the bid door.

WHAT FORCED THE REWRITE HERE. This module carried the two-step law as a
hardcoded fact (``BLOCK_GRANTS = {"meeting": "google-calendar"}``) and counted
only an ``ask == "GRANT"`` step as a connection. Against the one-step template
the bench now publishes, a CORRECT plan looked to the harness like a meeting
block nothing opened the calendar for, so it manufactured a REJ-35 the bench
never emits and refused the plan at home -- ``local_validation_failed``, no
filing, the round spent on nothing. So nothing about which kind runs on which
connection is written here any more: a row on the step counts, the brief's own
template is the only source of the shape, and the fact that a kind needs a
connection at all comes off the act registry (``requires_grants``) when the
caller has read it. A GRANT step is still the right shape for access the
connector registry has no recipe for -- the ``access`` mold -- and a bench that
still hands out a two-step template is still filed exactly as it hands it over.

This module is the harness's deterministic half of that: it reads the blocks
off the brief, fills the template's blanks from the model's own plan, and
checks a declared meeting against the kind's published grammar BEFORE the
filing is spent on it. The model is told to copy the template; this is what
happens when it does not.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

# The kinds whose fields this module knows how to check at home. Everything
# else is left to the server's own sentence (REJ-33): a local check we cannot
# keep in step with the kind would refuse a legal block.
CHECKED_KINDS: frozenset[str] = frozenset({"meeting"})

# The bench's refusal for a block whose account nothing on its step opens.
REJ_BLOCK_GRANT = "REJ-35"

# RULE 236: the bench's refusal for a connection filed as a STEP OF ITS OWN.
# It carries no ``plan_template``: what it hands back is the ROW, written into
# the refusal's own words, and the brief's template is where that row is read
# from. A GRANT step for a provider the connector registry does not know is the
# ``access`` mold and is not refused.
REJ_GRANT_STEP_REMOVED = "REJ-38"

# RULE 236: RETIRED, AND DELIBERATELY EMPTY. This used to say a meeting block
# needs google-calendar opened by a step of its own, and that one hardcoded
# fact refused the one-step template the bench now publishes. Which kinds run
# on which connection is the act REGISTRY's answer (`requires_grants`), read
# off the bench and passed in as ``needs=``. With nothing passed the local
# mirror says nothing at all and the bench's free validate door is the judge --
# which is the right way round for a fact that lives on the server. A kind
# added tomorrow is held to whatever the registry says, with no edit here.
BLOCK_GRANTS: dict[str, tuple[str, ...]] = {}

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
    # Rule 235: a mailbox connection that cannot send is not the access an
    # outgoing message runs under, whatever else it names. Same floor the
    # door holds, so the mirror and the door agree on what a row opens.
    "google-gmail": ("gmail.message.send",),
}

# The HAR formats that ARE the connection, for a grant step that names its
# provider on the block rather than in grant_request.
_CONNECT_FORMATS = frozenset({"connect_account", "grant_access"})

# RULE 236: the one format that is a connection ROW on the step that uses it.
# Read strictly, exactly as the bench's own `connect_rows` reads it.
CONNECT_FORMAT = "connect_account"

# The one sentence every planning surface carries about a connection, kept here
# so the prompt, the tool words and the refusal cannot drift apart (rule 236).
CONNECTION_IN_THE_ACTION_SENTENCE = (
    "The connection is a `connect_account` ROW inside the step that uses it, "
    "never a step of its own: the card is the account rows, then what the step "
    "does, then one button that stays asleep until every row is settled. The "
    "meeting plan is ONE step -- a Google Calendar row, a Gmail row and the "
    "meeting block on a single card. Copy `block_templates[<kind>]` from the "
    "brief whole rather than composing the steps yourself; a new plan that "
    "lifts a registry connector back into a GRANT step of its own is refused "
    "REJ-38. Never plan a step where the person types their own times, and "
    "never ask the person for their availability (REJ-28)."
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


def connect_row_providers(step: Any) -> set[str]:
    """The providers this step opens with `connect_account` ROWS of its own.

    RULE 236 -- THE CONNECTION LIVES IN THE ACTION. This is the bench's own
    ``connect_rows`` + ``_useful`` read, mirrored: the provider sits on
    ``config.grant_request.connector`` and it must name the actions the block
    cannot run without, because naming the account and none of its actions is
    the same nothing a GRANT step naming no actions always was.
    """
    open_here: set[str] = set()
    if not isinstance(step, dict):
        return open_here
    for block in step.get("har_blocks") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("format") or "").strip().lower() != CONNECT_FORMAT:
            continue
        config = block.get("config")
        if not isinstance(config, dict):
            continue
        request = config.get("grant_request")
        connector = request.get("connector") if isinstance(request, dict) else None
        if not isinstance(connector, dict):
            continue
        provider = str(connector.get("provider") or "").strip().lower()
        if not provider:
            continue
        actions = {
            str(action).strip()
            for action in (connector.get("actions") or [])
            if isinstance(action, str)
        }
        if not all(action in actions for action in GRANT_MIN_ACTIONS.get(provider, ())):
            continue
        open_here.add(provider)
    return open_here


def step_opens(step: Any) -> set[str]:
    """Every provider this ONE step opens, by a row or by being the grant.

    Both shapes count, on purpose: the row is the law (rule 236) and the GRANT
    step is what an older bench still hands out and what a signed deal still
    walks.
    """
    open_here = connect_row_providers(step)
    provider = grant_provider(step)
    if provider:
        open_here.add(provider)
    return open_here


def _needed_providers(kind: str, needs: Any) -> tuple[str, ...]:
    """The providers one act kind runs on, off whatever the caller was told.

    ``needs`` is {kind: provider | [providers]} -- the act registry's own
    ``requires_grants``. Nothing here knows any kind by name.
    """
    if not isinstance(needs, dict):
        return ()
    value = needs.get(kind)
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip().lower(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(
            provider.strip().lower()
            for provider in value
            if isinstance(provider, str) and provider.strip()
        )
    return ()


def template_connect_rows(templates: Any, provider: str) -> list[dict[str, Any]]:
    """The published `connect_account` row(s) for one provider.

    Read off the brief's own template, so what lands on the step is the
    PLATFORM's row, word for word. The harness writes none of it.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for step in templates if isinstance(templates, list) else []:
        if not isinstance(step, dict):
            continue
        for block in step.get("har_blocks") or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("format") or "").strip().lower() != CONNECT_FORMAT:
                continue
            config = block.get("config") if isinstance(block.get("config"), dict) else {}
            request = config.get("grant_request")
            connector = request.get("connector") if isinstance(request, dict) else None
            named = (
                str(connector.get("provider") or "").strip().lower()
                if isinstance(connector, dict)
                else ""
            )
            if named != provider:
                continue
            key = str(block.get("id") or "") or json.dumps(block, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            rows.append(block)
    return rows


def add_connect_rows(
    step: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], bool]:
    """Put the platform's published rows on the step that needs them."""
    existing = step.get("har_blocks")
    har = list(existing) if isinstance(existing, list) else []
    have = {str(block.get("id") or "") for block in har if isinstance(block, dict)}
    added = False
    for row in rows:
        if str(row.get("id") or "") in have:
            continue
        har.append(copy.deepcopy(row))
        have.add(str(row.get("id") or ""))
        added = True
    if not added:
        return step, False
    return {**step, "har_blocks": har}, True


def published_template_steps(
    plan_template: Any, block_templates: Any = None
) -> list[dict[str, Any]]:
    """Every step the PLATFORM published on this brief, flattened.

    CONTRACT 3.0 put the rows somewhere this module used to look right past.
    ``plan_template`` is a blank SKELETON -- the band's work steps, no blocks --
    and the blocks live in ``block_templates`` ({kind: [steps]}), the catalog
    the agent pulls from. A rule-236 repair reads the published `connect
    _account` row, so it has to read BOTH or it finds nothing on a live brief
    and quietly does nothing.
    """
    steps: list[dict[str, Any]] = []
    for step in plan_template if isinstance(plan_template, list) else []:
        if isinstance(step, dict):
            steps.append(step)
    if isinstance(block_templates, dict):
        for group in block_templates.values():
            for step in group if isinstance(group, list) else []:
                if isinstance(step, dict):
                    steps.append(step)
    return steps


def retired_grant_providers(plan_template: Any) -> set[str]:
    """The providers this bench has MOVED into the action (rule 236).

    Positive evidence only: the brief's template publishes a `connect_account`
    row for the provider and NO GRANT step for it. A bench still handing out
    the two-step shape answers empty here, and a provider the connector
    registry has no recipe for never appears in a template row at all, so the
    ``access`` mold is never caught by this.
    """
    templates = [
        step
        for step in (plan_template if isinstance(plan_template, list) else [])
        if isinstance(step, dict)
    ]
    rows: set[str] = set()
    grants: set[str] = set()
    for step in templates:
        rows |= connect_row_providers(step)
        provider = intended_grant_provider(step)
        if provider is not None and str(step.get("ask") or "").strip().upper() == GRANT_ASK:
            grants.add(provider)
    return rows - grants


def retire_grant_steps(
    proposal: dict[str, Any], plan_template: Any
) -> tuple[dict[str, Any], list[str]]:
    """RULE 236 / REJ-38: a connection filed as a step of its own, moved back
    into the action that needs it.

    The refusal says it in one line -- "Delete the GRANT step and put its
    connection on the step that uses it" -- so this does exactly that and
    nothing else: the step goes, its provider's published ROW lands on the
    first step that declares an act and does not already open it, and not one
    word of the agent's is touched. It fires only on the positive evidence in
    ``retired_grant_providers``, so against a bench that still hands out a
    two-step template it does nothing at all. When there is no step to move
    the row onto, the plan is left exactly as it was: the GRANT step was the
    only work there, and burying it here would be worse than the door's own
    refusal.
    """
    retired = retired_grant_providers(plan_template)
    if not retired:
        return proposal, []
    original = proposal.get("steps")
    steps: list[Any] = list(original) if isinstance(original, list) else []
    moved: list[str] = []
    for provider in sorted(retired):
        index = next(
            (
                position
                for position, step in enumerate(steps)
                if isinstance(step, dict)
                and str(step.get("ask") or "").strip().upper() == GRANT_ASK
                and intended_grant_provider(step) == provider
            ),
            None,
        )
        if index is None:
            continue
        rows = template_connect_rows(plan_template, provider)
        if not rows:
            continue
        target = next(
            (
                position
                for position, step in enumerate(steps)
                if position != index
                and isinstance(step, dict)
                and step_kinds(step)
                and provider not in connect_row_providers(step)
            ),
            None,
        )
        if target is None:
            continue
        steps[target], added = add_connect_rows(steps[target], rows)
        if not added:
            continue
        steps.pop(index)
        moved.append(
            f"{provider}: GRANT step {index + 1} removed, its connect row put on "
            f"the step that uses it"
        )
    if not moved:
        return proposal, []
    return {**proposal, "steps": steps}, moved


def provider_words(provider: str) -> str:
    return PROVIDER_WORDS.get(provider, provider)


def grant_problems(steps: Any, needs: Any = None) -> list[dict[str, str]]:
    """Every declared block whose connection nothing on the plan opens (REJ-35).

    RULE 236: the `connect_account` ROW on the block's OWN step is counted
    first, because that is where the connection now lives. A GRANT step at or
    before the block still counts too -- an older bench hands out that shape
    and a signed deal still walks it -- so this refuses neither half.

    ``needs`` is {kind: provider | [providers]}, the act registry's own
    ``requires_grants``. NOTHING IS HARDCODED HERE: called with no ``needs``
    this returns nothing at all, and the bench's free validate door is the
    judge. That is deliberate -- the last hardcoded copy of this fact refused
    the correct plan for a day.
    """
    table = BLOCK_GRANTS if needs is None else needs
    problems: list[dict[str, str]] = []
    granted: set[str] = set()
    flagged: set[tuple[str, str]] = set()
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        here = step_opens(step)
        for kind in step_kinds(step):
            for provider in _needed_providers(kind, table):
                if provider in here or provider in granted:
                    continue
                if (kind, provider) in flagged:
                    continue
                flagged.add((kind, provider))
                problems.append(
                    {
                        "path": f"steps.{index}",
                        "rej": REJ_BLOCK_GRANT,
                        "kind": kind,
                        "provider": provider,
                        "message": (
                            f"step {index + 1} declares a {kind} block, and that "
                            f"block runs on the person's "
                            f"{provider_words(provider)} connection, which "
                            f"nothing on that step opens. "
                            + CONNECTION_IN_THE_ACTION_SENTENCE
                            + " The bench refuses this as REJ-35."
                        ),
                    }
                )
        provider = grant_provider(step)
        if provider:
            granted.add(provider)
    return problems


def first_step_needing(steps: Any, provider: str, needs: Any = None) -> int | None:
    """The index of the first step whose block needs `provider` open."""
    table = BLOCK_GRANTS if needs is None else needs
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        for kind in step_kinds(step):
            if provider in _needed_providers(kind, table):
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
    needs: Any = None,
    block_templates: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """Fill in the brief's form: every template step the plan is missing.

    Returns the (possibly unchanged) proposal and the steps inserted. Filing a
    plan the door will refuse costs the agent its one bid on the want, so a
    missing block, and a block whose account nothing opens, are both repaired
    here rather than discovered at the door.

    RULE 236. The template is a group and it is copied in the template's own
    order, whatever that order is: ONE step carrying the account rows and the
    block on this bench, a GRANT step and then the block on an older one. The
    group goes in FRONT of the model's own work. When the model wrote the block
    itself and left its connection out, what goes in is the published `connect
    _account` ROW, onto the step that uses it -- never a GRANT step of the
    harness's own making, which is exactly what REJ-38 refuses.
    """
    original = proposal.get("steps")
    steps: list[Any] = list(original) if isinstance(original, list) else []
    templates = [
        step
        for step in (plan_template if isinstance(plan_template, list) else [])
        if isinstance(step, dict)
    ]
    if not missing_blocks(steps, required_blocks) and not grant_problems(steps, needs):
        return proposal, []
    published = published_template_steps(templates, block_templates)
    if not templates and not published:
        # No form to fill: the door's refusal is then the honest answer, and
        # it carries the template with it. RULE 236: the CATALOG counts as a
        # form, because on contract 3.0 `plan_template` is a blank skeleton
        # and the connect rows live in `block_templates` alone.
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
    # Whatever is still unopened is opened ON THE STEP THAT USES IT: the model
    # wrote the block itself, or the template had no step for the kind that was
    # missing. The ROW comes first (rule 236) and the GRANT step is the
    # fallback, which is how an older bench's two-step template still works and
    # how this one never manufactures the step REJ-38 refuses.
    opened: set[str] = set()
    for gap in grant_problems(steps, needs):
        provider = gap["provider"]
        if provider in opened:
            continue
        at = first_step_needing(steps, provider, needs)
        if at is None:
            continue
        rows = template_connect_rows(published, provider)
        if rows:
            steps[at], added = add_connect_rows(steps[at], rows)
            if added:
                opened.add(provider)
                inserted.append(f"connect row:{provider} (on the step that uses it)")
                continue
        template_step = next(
            (step for step in templates if grant_provider(step) == provider), None
        )
        if template_step is None:
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
        elif isinstance(step, dict) and "deliverable" in step and (
            clear_blank_fields(step.get("deliverable")) is not step.get("deliverable")
        ):
            # RULE 233: the same law for a field name still reading "<field>".
            copy = dict(step)
            copy["deliverable"] = clear_blank_fields(step.get("deliverable"))
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
        if channel == "text":
            # RULE 233: the shape of a text hand-back, only when it names one.
            problems.extend(shape_problems(path, value))
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


# --------------------------------------------------------------------------
# RULE 233 (Steven, 2026-09-05) -- WHAT YOU HAND BACK IN WORDS HAS A SHAPE TOO.
#
# A `text` deliverable may name the parts of each item it hands back
# (`fields`, one to twelve short names) and how many (`min_count`, 1 to 200,
# default 1). The work then arrives as a `cards` block on the document -- one
# item per thing, every named field filled -- and the door counts the EMPTY
# BOXES. The platform reads no word of the work. A step that names no fields
# is prose, exactly as before, and so is every step signed before the rule.
#
# WHAT FORCED IT: production deal 91221abe, 2026-09-05. Step 3 promised a stop
# card for each approved restaurant with address, hours, suggested order and
# one dish, and the agent filed a document whose blocks were those four words
# as headings with nothing under them. Rule 230 passed it because channel
# text had no check past non-empty; the person sent it back; the same shell
# came again. The bench grew the blank and the three refusals the same night,
# and the harness did not know the blank existed, so every railed agent kept
# promising prose. This section is the harness catching up.
# --------------------------------------------------------------------------

FIELDS_MAX = 12
FIELD_NAME_MAX = 40
MIN_COUNT_MAX = 200
_FIELD_URLISH = re.compile(r"(://|\bhttps?:|^www\.)", re.IGNORECASE)

# The bench's own words for the shape blank, kept beside the file blank so the
# prompt, the mirror and the refusal cannot drift apart.
FIELDS_BLANK_WORDS = (
    "For text you may also name the parts of each item you hand back in "
    'deliverable.fields, for example ["address", "hours"], with min_count for '
    "how many. Name them and the work must arrive as a cards block on the "
    "document, one item per thing, every named field filled; name nothing and "
    "the step is prose."
)

# The refusals the shape door speaks (rule 233). Surfaced VERBATIM like the
# file door's: the bench counts the boxes, and its sentence names the card.
SHAPE_DOOR_REFUSALS: tuple[str, ...] = (
    "deliverable_fields_missing",
    "deliverable_fields_blank",
    "deliverable_count_short",
)


def clean_field(value: Any) -> str | None:
    """One field name the way the bench reads it: trimmed, collapsed, lowercased."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()).strip().lower() or None


def spoken_fields(fields: Any) -> str:
    """"address, hours and dish" -- the person's list, as the refusal says it."""
    names = [item for item in (fields or []) if item]
    if not names:
        return "the named parts"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _fields_note(path: str) -> str:
    return (
        f"{path} names the parts of each item you hand back, for example "
        '["address", "hours"]'
    )


def normalize_fields(raw: Any) -> tuple[list[str], str | None]:
    """(fields, problem sentence): the bench's `_normalize_fields`, mirrored.

    Absent or empty is legal and means prose. An empty slot in the list is
    nothing, not a lie, and is skipped; a slot that is not a name is named.
    """
    if raw is None:
        return [], None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return [], _fields_note("deliverable.fields") + "; it has to be a list of short names."
    fields: list[str] = []
    for entry in raw:
        name = clean_field(entry)
        if not name:
            if entry is None or (isinstance(entry, str) and not entry.strip()):
                continue
            return fields, (
                _fields_note("deliverable.fields")
                + f'; "{str(entry)[:60]}" is not a field name.'
            )
        if len(name) > FIELD_NAME_MAX:
            return fields, (
                _fields_note("deliverable.fields")
                + f'; "{name[:60]}" is {len(name)} characters and a field name '
                f"has to be {FIELD_NAME_MAX} or fewer."
            )
        if _FIELD_URLISH.search(name):
            return fields, (
                _fields_note("deliverable.fields")
                + f'; "{name[:60]}" is an address, not a field name.'
            )
        if name in fields:
            return fields, (
                f'deliverable.fields names "{name}" twice. Each part is named once.'
            )
        fields.append(name)
    if len(fields) > FIELDS_MAX:
        return fields[:FIELDS_MAX], (
            f"deliverable.fields takes at most {FIELDS_MAX} names; you named "
            f"{len(fields)}."
        )
    return fields, None


def normalize_min_count(raw: Any) -> tuple[int, str | None]:
    """(min_count, problem sentence). Default 1: one filled item at least."""
    if raw is None or raw == "":
        return 1, None
    if isinstance(raw, bool):
        return 1, (
            "deliverable.min_count is how many of these you hand back, a whole "
            f"number from 1 to {MIN_COUNT_MAX}."
        )
    try:
        count = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1, (
            "deliverable.min_count is how many of these you hand back, a whole "
            f'number from 1 to {MIN_COUNT_MAX}; "{str(raw)[:40]}" is not one.'
        )
    if count < 1 or count > MIN_COUNT_MAX:
        return (1 if count < 1 else MIN_COUNT_MAX), (
            f"deliverable.min_count is {count}; it has to be a whole number "
            f"from 1 to {MIN_COUNT_MAX}."
        )
    return count, None


def shape_problems(path: str, value: dict[str, Any]) -> list[dict[str, str]]:
    """The rule-233 half of the validate mirror, for a `text` deliverable.

    Only a deliverable that CARRIES `fields` or `min_count` is checked; a
    text step that names nothing is prose and draws nothing new. A blank
    copied off the form (`["<field>"]`) is named in the door's words rather
    than filed as a promise.
    """
    problems: list[dict[str, str]] = []
    if "fields" not in value and "min_count" not in value:
        return problems
    raw_fields = value.get("fields")
    copied = [
        item
        for item in (raw_fields if isinstance(raw_fields, list) else [])
        if isinstance(item, str) and item.strip() and is_blank(item)
    ]
    if copied:
        problems.append(
            {
                "path": f"{path}.fields",
                "message": (
                    f'"{copied[0].strip()}" is the form\'s blank, not a field '
                    "name. " + FIELDS_BLANK_WORDS
                ),
            }
        )
        return problems
    fields, error = normalize_fields(raw_fields)
    if error:
        problems.append({"path": f"{path}.fields", "message": error})
        return problems
    if "min_count" in value and value.get("min_count") not in (None, ""):
        if not fields:
            problems.append(
                {
                    "path": f"{path}.min_count",
                    "message": (
                        "min_count says how many CARDS you hand back, so it "
                        "rides with deliverable.fields. " + FIELDS_BLANK_WORDS
                    ),
                }
            )
            return problems
        _count, error = normalize_min_count(value.get("min_count"))
        if error:
            problems.append({"path": f"{path}.min_count", "message": error})
    return problems


def clear_blank_fields(deliverable: Any) -> Any:
    """Drop field names that are still the form's `<blank>`. Writes no words.

    The same move as `clear_blank_deliverables`: a literal "<field>" filed as
    a promise would print on the person's card as "Delivers: cards with
    <field>". When nothing real is left the shape goes with it, and the step
    is prose -- the harness never invents a field name.
    """
    if not isinstance(deliverable, dict) or "fields" not in deliverable:
        return deliverable
    raw = deliverable.get("fields")
    if not isinstance(raw, list):
        return deliverable
    kept = [
        item
        for item in raw
        if not (item is None or (isinstance(item, str) and is_blank(item)))
    ]
    if len(kept) == len(raw):
        return deliverable
    copy = dict(deliverable)
    if kept:
        copy["fields"] = kept
    else:
        copy.pop("fields", None)
        copy.pop("min_count", None)
    return copy


def signed_fields(deliverable: Any) -> list[str]:
    """The field names this step promised, or []. Only a `text` channel has any."""
    if not isinstance(deliverable, dict):
        return []
    if str(deliverable.get("channel") or "").strip().lower() != "text":
        return []
    fields, error = normalize_fields(deliverable.get("fields"))
    if error:
        return []
    return fields


def signed_min_count(deliverable: Any) -> int:
    """How many filled cards this step promised. 1 when it named a shape."""
    if not signed_fields(deliverable):
        return 1
    count, _error = normalize_min_count(
        deliverable.get("min_count") if isinstance(deliverable, dict) else None
    )
    return max(1, count)


def shape_words(deliverable: Any) -> str:
    """"at least 2 cards with address and hours" -- the promise, spoken."""
    fields = signed_fields(deliverable)
    if not fields:
        return "words"
    count = signed_min_count(deliverable)
    return f"at least {count} cards with {spoken_fields(fields)}"


def cards_items(document: Any) -> list[tuple[int, dict[str, Any]]]:
    """[(card number, item)] across every cards block, numbered from 1 over
    the WHOLE document -- the way the person reads it and the refusal names it."""
    if isinstance(document, dict):
        raw_blocks = document.get("blocks")
    else:
        raw_blocks = document
    out: list[tuple[int, dict[str, Any]]] = []
    number = 0
    for block in raw_blocks if isinstance(raw_blocks, list) else []:
        if not isinstance(block, dict) or block.get("type") != "cards":
            continue
        for item in block.get("items") or []:
            number += 1
            if isinstance(item, dict):
                out.append((number, item))
    return out


def card_value(item: Any, field: str) -> str:
    """The value on this card for that field, matched the way the bench matches."""
    if not isinstance(item, dict):
        return ""
    for key, value in item.items():
        if isinstance(key, str) and clean_field(key) == field:
            return str(value or "").strip()
    return ""


def cards_example(fields: list[str]) -> dict[str, Any]:
    """The exact document to send next, in the bench's own `how` shape."""
    names = list(fields) or ["<field>"]
    return {
        "document": {
            "title": "<what the person calls this>",
            "blocks": [
                {
                    "type": "cards",
                    "items": [{name: f"<the {name}>" for name in names}],
                }
            ],
        },
        "never": (
            "A heading with nothing under it hands back nothing. The platform "
            "reads no word of the work; it counts empty boxes."
        ),
    }


def cards_shortfall(deliverable: Any, outcome: dict[str, Any]) -> dict[str, Any] | None:
    """The rule-233 door, run over the outcome in hand. None when it passes.

    Returns {error, message, fix, how, certain}. `certain` is True when the
    bench would refuse this filing whatever else is on the step (a blank box
    on a card in THIS document is refused by name regardless of other
    receipts); False when an earlier receipt on the same step could already
    carry the cards, so the door and not the mirror has the last word.
    """
    fields = signed_fields(deliverable)
    if not fields:
        return None
    min_count = signed_min_count(deliverable)
    promised = {"fields": list(fields), "min_count": min_count}
    document = outcome.get("document") if isinstance(outcome, dict) else None
    items = cards_items(document) if isinstance(document, dict) else []
    if not items:
        return {
            "error": "deliverable_fields_missing",
            "message": (
                f"This step promised {min_count} or more cards with "
                f"{spoken_fields(fields)}; the document has no cards block."
            ),
            "promised": promised,
            "fix": (
                "Put a cards block in the document: one item per thing you "
                "hand back, every named field filled."
            ),
            "how": cards_example(fields),
            "certain": False,
        }
    filled = 0
    for number, item in items:
        empty = [name for name in fields if not card_value(item, name)]
        if empty:
            left = f"{empty[0]} empty" if len(empty) == 1 else f"{spoken_fields(empty)} empty"
            return {
                "error": "deliverable_fields_blank",
                "message": (
                    f"Card {number} leaves {left}. Every card has to fill "
                    f"{spoken_fields(fields)}."
                ),
                "promised": promised,
                "blank": {"card": number, "fields": empty},
                "fix": (
                    "Fill every named field on every card, or drop the card "
                    "you cannot fill."
                ),
                "how": cards_example(fields),
                "certain": True,
            }
        filled += 1
    if filled < min_count:
        return {
            "error": "deliverable_count_short",
            "message": (
                f"This step promised at least {min_count} cards; the document "
                f"has {filled}."
            ),
            "promised": promised,
            "found": filled,
            "fix": (
                "Hand back at least the number of cards this step promised, "
                "every named field filled."
            ),
            "how": cards_example(fields),
            "certain": False,
        }
    return None


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
