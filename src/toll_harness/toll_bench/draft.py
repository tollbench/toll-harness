"""THE DRAFT LOOP — one plan, built up in pieces, at the bench's own door.

RULE 241 (Steven Ochs, 2026-09-09; bench contract 3.11):

    "send the outline for the full plan, then we send back the template for
     them to fill out, then they send it back and we send back each part that
     is refused until we get through the whole plan. If the plan is 3 steps or
     thirty that's how we get through it."

WHAT FORCED IT. Until this module the harness asked one model call for a WHOLE
proposal, handed the whole document to the validate door, and handed every
problem back at once. Overnight on 2026-09-08 every model but the strongest
answered that list by rewriting the whole document and breaking something new
on each pass; the next morning raw Sonnet 5 spent four whole-document passes on
an off-shelf want and never filed. The missing thing was never better
diagnostics. It was somewhere to put a PARTIAL answer, and the bench now has
one:

    PUT   /api/bench/targets/<id>/proposals/draft   the outline in
    PATCH /api/bench/targets/<id>/proposals/draft   one piece at a time
    GET   /api/bench/targets/<id>/proposals/draft   read it back
    POST  .../proposals {"from_draft": true}        file what the bench holds

So this is what the harness asks the model for, and it is all it asks for:

  1. an OUTLINE — steps in order, each an `ask` and a `title`, and for a step
     that touches the world the `tool` and the service it runs `on`. Not the
     whole brief, not a worked program, no promises, no money, no questions.
  2. one STEP's blanks at a time — the step's mechanics exactly as the bench
     expanded them, plus that step's blank paths with the bench's own sentence
     on each — answered as {path: value} patches.
  3. one NEXT_FIX at a time — one path, its current value, the code and the
     bench's one sentence of fix, with the step around it for context.

THE BOUNDS ARE THE BENCH'S AND THERE ARE NO KNOBS HERE. The bench caps the
rounds (three per opening problem, ceiling 200) and expires a draft at 24
hours; when it says `closed` this module opens ONE fresh outline and, if that
closes too, gives up on the want for this cycle. The only numbers below are a
stall guard (the same fix named over and over is not progress) and a prompt
budget that a loop prompt should never come near.

THE RUNTIME NEVER ASSUMES THE SEQUENCE; IT DOES WHAT THE DOOR'S ANSWER NAMES
NEXT. Every answer from the draft door is read for what it asks for before
anything is assumed about where the loop is: `next` names a round the door
wants before the template (today: `"outline"`), `blanks` a step to fill,
`next_fix` one thing to change, `ready` the filing. The plan road is the
case that forced this (Steven, 2026-09-09 20:55: "peter has no memory... so
we were missing a step basically"): the informed plan used to open empty and
go straight to blanks, so the agent never saw its own bid steps beside the
person's selection answers, and a step that asked the person to type the
two addresses they had already picked in the Contact book rode into the plan
unchanged. Now the bench answers that first PUT with `next: "outline"`,
`steps_you_bid` and `the_person_answered`, and this module makes ONE outline
ask with those in front of the model and PUTs the outline back. A door that
one day names another round is followed the same way, not scripted here.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from toll_harness.core.types import ModelMessage
from toll_harness.toll_bench import blocks, programs

_LOGGER = logging.getLogger("toll_harness.draft")

# THE BENCH'S BOUND IS THE ONLY BOUND (Steven, 2026-09-09: "we don't have to
# have a three strike rule ... 3 strikes on a 30 step job is too little", "I
# don't think we should do any levers"). The loop runs until the answer says
# `ready` or says `closed`. A draft is over when the bench says it is over --
# the rounds cap is its arithmetic, three per opening problem, so a thirty-step
# plan gets a thirty-step plan's worth of rounds and a three-step plan does
# not.
#
# ONE PUT PER RUN, AND A RUN RESUMES WHAT IS STANDING. A PUT is not a read: it
# REPLACES whatever draft the bench is holding and sets the rounds back to
# zero, so every answer already given is thrown away. The watch loop comes back
# to the same want every scan interval and a `file_informed_plan` obligation
# stands in the queue until the plan files -- so a loop that opens with a PUT
# opens a NEW draft every cycle and never finishes one. Live on 2026-09-09:
# dozens of PUTs on the same targets in two minutes, red the same number every
# time, almost no PATCH between them. A run now READS first (a GET costs no
# round) and PUTs only when there is nothing standing to carry on with.
# A loop prompt is SMALL BY DESIGN -- one step, or one problem, never the
# document. The 90k context budget in core/budget.py still guards a runtime run;
# these are the guards for a prompt this module builds itself. Four characters
# to a token, so 8,000 characters is about 2,000 tokens of tail on top of a
# cached prefix.
PROMPT_CHAR_BUDGET = 8_000
# ONE STEP, and one step is not a document. A thirty-step draft has thirty of
# these and the round is only ever shown the one it is about.
STEP_CHAR_BUDGET = 3_000
# The plan's SHAPE for a fix round: one line per step, so the model can see
# where the thing it is changing sits without being handed the whole draft.
OUTLINE_LINE_CHARS = 80
# The keys of an expanded step that are the platform's own machinery and the
# first thing to shed when a step's context runs long. The blanks themselves
# are never shed: they are the question.
_HEAVY_STEP_KEYS = ("service_setup", "statement", "har_blocks", "examples")

# THE FRONT DOOR — said ONCE, at the top of every call, byte for byte.
#
# This is the stable prefix. It does not move between the outline, a blanks
# round and a fix round, and it does not move between rounds on the same want,
# because that is what makes it cacheable: a provider that has seen this block
# already charges a fraction for it. Everything that CHANGES rides the user
# message underneath. Steven measured ~25,000 input tokens to write one plan
# and asked for ~8,000; repeating the rules of the game in every round is where
# most of the difference was.
FRONT_DOOR = (
    "You are writing ONE plan for a Toll Bench want, and the bench is holding "
    "it for you while you write.\n"
    "HOW THIS GOES. You are asked for one piece at a time -- the outline, then "
    "one step's blanks, then one problem -- and you answer with that piece and "
    "nothing else. NEVER rewrite the whole document: that is the failure this "
    "door exists to stop.\n"
    "A STEP carries an `ask` (APPROVE, CHOOSE, PROVIDE, GRANT or CONTACT) and a "
    "`title` in your own words. A step that touches the world outside this "
    "platform -- an email, a booking, a calendar event, a publish, a purchase -- "
    "also names the tool it runs and the service it runs `on`: "
    '{"ask": "APPROVE", "title": "Offer the times and book it", "tool": '
    '"gmail.message.send", "on": "google-gmail"}. A step that touches nothing '
    "outside names its block instead, or just its ask: "
    '{"ask": "APPROVE", "title": "Find three cafes", "block": "research"}.\n'
    "THE BENCH FILLS EVERY MECHANIC IT OWNS -- the account row for each service, "
    "the required arguments of each call, the platform's own statement, the "
    "approve control -- and hands back every field that is YOURS as a blank with "
    "one sentence saying what belongs there. You never write a mechanic and "
    "nothing is ever invented for you.\n"
    "PATHS ARE DOTTED (`steps.2.outcome_promise`, "
    "`steps.1.acts.0.runs.1.args.subject`) and that is the form a patch takes. "
    "If a path names a list, send the whole list.\n"
    "ANSWER WITH JSON and no other words: no code fence, no explanation."
)

# The same door, for a provider that caches NOTHING. Everything the long one
# says is still said -- once, on the outline call, where the choice of shape is
# actually made -- because repeating 470 tokens of rules in front of thirty
# rounds that nobody caches is 15,000 tokens spent to say what the model was
# told at the start. Measured on a thirty-step plan: 40,200 input tokens with
# the long door repeated uncached, 35,300 with this one.
SHORT_FRONT_DOOR = (
    "You are writing ONE plan for a Toll Bench want, one piece at a time, and "
    "the bench is holding it for you. Answer the piece you are asked for and "
    "nothing else; never rewrite the whole document. Paths are dotted "
    "(`steps.2.outcome_promise`) and that is the form a patch takes. ANSWER "
    "WITH JSON and no other words: no code fence, no explanation."
)

OUTLINE_INSTRUCTION = (
    "Write the OUTLINE of your plan for the want below: the steps, in the order "
    "they happen, and nothing else. No promises, no pitch, no questions, no "
    "money, no odds, no arguments, no connection rows -- you are asked for each "
    "of those, one at a time, after the bench hands back the form.\n"
    'Answer: {"steps": [ ... ]}.'
)

# THE PLAN'S OUTLINE ROUND. The same shape as the bid road's outline ask
# (the same stable prefix, so the provider cache is shared), with the two
# things the bench put in front of us in the tail.
PLAN_OUTLINE_INSTRUCTION = (
    "The person picked your bid and answered your questions. Below are "
    "`steps_you_bid` -- your own steps, one line each, numbered -- and "
    "`the_person_answered` -- their answers, each with the question it "
    "answers; a contact pick is the people by name. Write the OUTLINE of the "
    "plan you will actually run now that they have answered: keep a step by "
    "its `bid_step` number (everything you wrote on it comes with it), DROP a "
    "step the answers already cover -- never ask for what they have already "
    "given -- and add a step only where the answers call for one. No promises, "
    "no pitch, no money, no odds: each of those is asked for afterwards, one "
    "at a time.\n"
    'Answer: {"steps": [{"bid_step": 2, "ask": "APPROVE", "title": "..."}, ...]}.'
)

# LAW A (Steven, 2026-09-09, 21:20): "EVERYTHING THE PERSON HAS SAID THAT BEARS
# ON THIS STEP RIDES EVERY ASK, ALWAYS." An agent is stateless on purpose; the
# bench is the memory and hands it over as `the_person_said` on every answer.
# The runtime puts it in the TAIL of the blanks ask, the fix ask and the step
# ask, verbatim, under this one line -- never in the prefix, which stays byte
# for byte the same so the provider cache still hits. Absent on an older
# bench, nothing changes.
PERSON_SAID_INSTRUCTION = (
    "This is what the person said and picked. Write from it. Never invent a "
    "name, an address or a reason; the platform sends to the picks."
)
PERSON_SAID_KEY = "the_person_said"


def with_the_person_said(instruction: str, payload: dict, answer: Any) -> str:
    """Put `the_person_said` from a bench answer into the tail payload and
    hang the one instruction line under it. The instruction comes back
    unchanged when the bench sent nothing."""
    said = answer.get(PERSON_SAID_KEY) if isinstance(answer, dict) else None
    if not isinstance(said, list):
        return instruction
    payload[PERSON_SAID_KEY] = list(said)
    return instruction + "\n" + PERSON_SAID_INSTRUCTION


BLANKS_INSTRUCTION = (
    "Below is ONE step of your plan as the bench expanded it, and every blank on "
    "it that is yours to write, each with the bench's own sentence saying what "
    "belongs there. Fill them in your own words; leave nothing you can answer "
    "empty; change nothing else.\n"
    'Answer: {"patches": [{"path": "<the exact path>", "value": <your value>}, '
    "...]}."
)

REPEATED_FIX_INSTRUCTION = (
    "THE BENCH IS NAMING THE SAME THING AGAIN. Your last patch did not clear "
    "it. `you_sent_last_round` is exactly what you sent and "
    "`what_is_in_the_document_now` is what the bench has for that path after "
    "it. Sending the same value a third time cannot work: read the code and "
    "the fix sentence again and send something DIFFERENT -- a different "
    "value, or the same idea in the shape the code asks for. If the path is "
    "a list, send the whole list, not one entry of it.\n"
)
EMPTY_ANSWER_INSTRUCTION = (
    "NOTHING CAME BACK LAST TIME. Answer with ONE patch for the path named in "
    "`fix_this`, as JSON: {\"patches\": [{\"path\": <that path>, \"value\": ...}]}. "
)


FIX_INSTRUCTION = (
    "The bench read the plan and named ONE thing to change. Change exactly that "
    "one thing. Do not touch any other path and do not resend the document. "
    "`plan` below is the shape of the whole plan, one line per step, so you can "
    "see where this sits; `step` is the only step you are changing.\n"
    'Answer: {"patches": [{"path": "<the path named below>", '
    '"value": <the new value>}]}.'
)


# ---------------------------------------------------------------------------
# READING THE MODEL — JSON, however it wrapped it
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def read_json_object(text: Any) -> dict[str, Any]:
    """The first JSON object in a model's answer, or {}.

    Models fence their JSON, prefix it with a sentence, or answer with the
    object alone. All three read the same here; anything else reads as {} and
    the caller treats it as a round that said nothing.
    """
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    fenced = _FENCE.search(raw)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def read_patches(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """[{path, value}] out of whatever shape the model answered in.

    Three shapes are accepted because a raw model writes whichever one it
    read: the `patches` list the door takes, a bare {path: value} map, and a
    single {"path": ..., "value": ...}.
    """
    if not isinstance(answer, dict):
        return []
    listed = answer.get("patches")
    out: list[dict[str, Any]] = []
    if isinstance(listed, list):
        for entry in listed:
            if isinstance(entry, dict) and str(entry.get("path") or "").strip():
                out.append({"path": str(entry["path"]).strip(), "value": entry.get("value")})
        return out
    if str(answer.get("path") or "").strip():
        return [{"path": str(answer["path"]).strip(), "value": answer.get("value")}]
    for key, value in answer.items():
        if key in ("kind", "note", "notes", "reasoning") or not isinstance(key, str):
            continue
        if "." in key or "[" in key:
            out.append({"path": key.strip(), "value": value})
    return out


def read_outline(answer: dict[str, Any]) -> dict[str, Any]:
    """The outline out of the model's answer. `steps` is the only key that
    rides; anything else the model volunteered is a word it was not asked for
    yet and the bench would only hand back as a blank."""
    steps = answer.get("steps") if isinstance(answer, dict) else None
    if not isinstance(steps, list):
        for key in ("outline", "plan", "proposal"):
            holder = answer.get(key) if isinstance(answer, dict) else None
            if isinstance(holder, dict) and isinstance(holder.get("steps"), list):
                steps = holder["steps"]
                break
    if not isinstance(steps, list):
        return {}
    return {"steps": [step for step in steps if isinstance(step, dict)]}


def cached_input_tokens(usage: Any) -> int | None:
    """The share of this call's input the provider served from cache, or None.

    Every provider calls it something else and some report nothing at all;
    None means "not reported", which is not the same as zero and must not read
    as it.
    """
    raw = getattr(usage, "raw", None)
    if not isinstance(raw, dict):
        return None
    for key in (
        "cache_read_input_tokens",   # Anthropic
        "cacheReadInputTokens",      # Bedrock Converse
        "cached_tokens",             # OpenAI / OpenRouter
        "cached_input_tokens",
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    details = raw.get("prompt_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), (int, float)):
        return int(details["cached_tokens"])
    return None


# ---------------------------------------------------------------------------
# THE PIECES — a step at a time, a fix at a time
# ---------------------------------------------------------------------------
_STEP_PATH = re.compile(r"^steps\.(\d+)(?:\.|$)")


def step_of(path: Any) -> int | None:
    match = _STEP_PATH.match(str(path or ""))
    return int(match.group(1)) if match else None


def group_blanks(blank_rows: Any) -> list[tuple[int | None, list[dict[str, Any]]]]:
    """The blanks grouped a STEP AT A TIME, in the order the document names
    them. `None` is the group of top-level fields (the pitch, the strategy,
    the questions, the money) -- one piece too, and asked for like one."""
    groups: list[tuple[int | None, list[dict[str, Any]]]] = []
    index: dict[int | None, list[dict[str, Any]]] = {}
    for row in blank_rows if isinstance(blank_rows, list) else []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        key = step_of(row["path"])
        if key not in index:
            index[key] = []
            groups.append((key, index[key]))
        index[key].append(row)
    return groups


def step_context(document: Any, index: int | None) -> Any:
    """One step of the draft as it stands, for the model to write into. None
    for the top-level group, where the context is the want itself."""
    if index is None or not isinstance(document, dict):
        return None
    steps = document.get("steps")
    if not isinstance(steps, list) or index >= len(steps):
        return None
    return steps[index]


def outline_summary(document: Any) -> list[str]:
    """The plan in one line per step: number, ask, title.

    A fix round needs to know where the step it is changing SITS, and that is
    a list of titles, not a copy of the draft.
    """
    steps = (document or {}).get("steps") if isinstance(document, dict) else None
    lines = []
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        title = " ".join(str(step.get("title") or "").split())
        if len(title) > OUTLINE_LINE_CHARS:
            title = title[: OUTLINE_LINE_CHARS - 1] + "\u2026"
        ask = str(step.get("ask") or "").upper()
        lines.append(f"{index + 1} {ask} {title}".rstrip())
    return lines


def _fit(value: Any, budget: int = STEP_CHAR_BUDGET) -> Any:
    """A step small enough to hand over. The platform's own machinery comes
    off first; the agent's words and the blanks never do."""
    if value is None:
        return None
    text = json.dumps(value, separators=(",", ":"), default=str)
    if len(text) <= budget or not isinstance(value, dict):
        return value
    trimmed = dict(value)
    for key in _HEAVY_STEP_KEYS:
        if key in trimmed:
            trimmed.pop(key)
            if len(json.dumps(trimmed, separators=(",", ":"), default=str)) <= budget:
                break
    return trimmed


# ---------------------------------------------------------------------------
# WHAT THE OUTLINE CALL IS TOLD — the want, the strategy, the grammar, the tools
# ---------------------------------------------------------------------------
BLOCK_GRAMMAR = (
    "A step is either work you do yourself or one BLOCK the platform runs. "
    "A block that touches the world names the tool and the service; the "
    "connection to that service is a row ON the same step and never a step of "
    "its own (rule 236). A step that only hands something back, asks a "
    "question, or asks for access needs no tool."
)


def block_grammar_summary(brief: Any, act_kinds: Any = None) -> str:
    """One short paragraph: the grammar, and the block kinds this bench has.

    The kinds are read off whatever the bench published -- the brief's block
    catalog, else the act registry -- and never invented here.
    """
    kinds: list[str] = []
    catalog = (brief or {}).get("block_templates") if isinstance(brief, dict) else None
    if isinstance(catalog, dict):
        kinds = [str(kind) for kind in catalog]
    if not kinds and isinstance(act_kinds, dict):
        registry = act_kinds.get("act_kinds") or act_kinds.get("kinds") or act_kinds
        if isinstance(registry, dict):
            kinds = [str(kind) for kind in registry]
        elif isinstance(registry, list):
            kinds = [
                str(entry.get("kind"))
                for entry in registry
                if isinstance(entry, dict) and entry.get("kind")
            ]
    kinds = sorted({kind for kind in kinds if kind})[:24]
    if not kinds:
        return BLOCK_GRAMMAR
    return BLOCK_GRAMMAR + " The blocks this bench has: " + ", ".join(kinds) + "."


# The outline needs three things about a tool: what it is called, what service
# it runs on, and what it does in one line. The bench's index also carries each
# call's argument list and shapes, and those are NOT an outline's business --
# the draft door writes the required arguments into the document and hands them
# back as blanks with their own sentences, one step at a time. So the index is
# narrowed here rather than pasted in.
TOOLS_CHAR_BUDGET = 4_000


def _tool_row(row: Any) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    tool = str(row.get("tool") or "").strip()
    if not tool:
        return None
    out = {"tool": tool, "on": str(row.get("provider") or row.get("on") or "")}
    line = str(row.get("one_line") or row.get("does") or "").strip()
    if line:
        out["does"] = line
    return out


def tools_index(brief: Any, budget: int = TOOLS_CHAR_BUDGET) -> list[dict[str, str]]:
    """The tools an outline may name.

    THE BENCH PUBLISHES THIS NOW (bench 965e61c5a): `tools` on the brief is
    every call a `calls` act can name -- {family, provider, tool, required,
    fields, one_line, shapes} -- built by the same `tool_arguments` the draft
    door fills a run's arguments with, so the index and the form cannot
    disagree. It is ALWAYS A LIST, including empty, and it replaces reading a
    worked program to find out what calls exist: since Steven's ruling on
    2026-09-09 the brief carries no `nearest_program` and no `plan_examples`
    at all (the programs stay public at GET /api/bench/plan-examples and are
    never pushed), and nothing in this loop reads them.

    The last row of the bench's index is the wildcard -- composio:<service>/
    <TOOL>, the door to ~1,500 other services -- so it is kept whatever the
    budget does to the rows above it: an index that silently ends at the
    budget would read as "these are all the tools there are".
    """
    published = (brief or {}).get("tools") if isinstance(brief, dict) else None
    if isinstance(published, list) and published:
        rows = [row for row in (_tool_row(entry) for entry in published) if row]
        if not rows:
            return []
        wildcard = rows[-1] if "<" in rows[-1]["tool"] else None
        body = rows[:-1] if wildcard else rows
        kept: list[dict[str, str]] = []
        spent = len(json.dumps(wildcard, default=str)) if wildcard else 0
        for row in body:
            spent += len(json.dumps(row, default=str))
            if spent > budget and kept:
                break
            kept.append(row)
        if wildcard:
            kept.append(wildcard)
        return kept
    # A bench that publishes no index: the platform's own four, and the verbs
    # this package holds a connection floor for. Nothing is invented, and no
    # program is read to find one.
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(tool: Any, service: Any = "") -> None:
        name = str(tool or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        found.append({"tool": name, "on": str(service or "")})

    for provider_key, actions in blocks.GRANT_MIN_ACTIONS.items():
        for action in actions:
            add(action, provider_key)
    for tool in blocks.PLATFORM_TOOLS:
        add(tool, "")
    return found


# What the person said, in the order it matters to an outline. The list is
# also the order things come OFF when the answer runs long: the last line the
# person wrote about this want beats a list of facts from every want they ever
# posted.
_STRATEGY_KEYS = (
    "want_in_own_words",
    "feedback_first",
    "person_context",
    "required_blocks",
    "person_already_connected",
    "contact_research_note",
    "timeline_days",
    "public_answers",
    "person_facts",
)
# The outline call is the one prompt that carries free text the harness did not
# write. A brief can hold a lot of it, and the loop's whole point is a small
# question, so this is where it stops.
STRATEGY_CHAR_BUDGET = 3_000


def stable_prefix(brief: Any = None, act_kinds: Any = None, with_tools: bool = True) -> str:
    """THE PREFIX EVERY CALL ON A WANT OPENS WITH, byte for byte.

    The front door, the tools this bench publishes, and the blocks it has. It
    is built ONCE per want and never varies between the outline, a blanks round
    and a fix round -- which is the whole point: a provider that has seen this
    block already charges a fraction for it, and the adapters mark it cacheable
    where the provider takes a marker.

    Nothing per-round and nothing per-step is allowed in here. Anything that
    changes rides the user message and breaks no cache.

    `with_tools` is FALSE for a provider that caches nothing (the adapter says
    so: `caches_a_stable_prefix`). Repeating a 5KB index in front of thirty
    rounds at full price is not a saving, it is the bill doubled -- measured,
    on a thirty-step plan: 35,600 input tokens the old way, 25,300 with the
    prefix cached, 71,200 with it repeated and never cached. So where nothing
    caches, the tools go back to riding the outline call once and the prefix
    is the front door alone.
    """
    if not with_tools:
        # Nothing caches here, so the prefix is the short door and the rules
        # ride the outline call once (`the_rules` in its payload).
        return SHORT_FRONT_DOOR
    parts = [FRONT_DOOR, block_grammar_summary(brief, act_kinds)]
    tools = tools_index(brief)
    if tools:
        parts.append(
            "THE TOOLS YOU MAY NAME (`tool`, the service it runs `on`, what it "
            "does):\n"
            + json.dumps(tools, separators=(",", ":"), sort_keys=True, default=str)
        )
    return "\n\n".join(part for part in parts if part)


def person_strategy(brief: Any) -> Any:
    """What the person said about how they want this done, in their words.

    Not a summary and not this module's opinion: the fields the brief already
    publishes for it, in priority order, up to the budget, and null where the
    person said nothing.
    """
    if not isinstance(brief, dict):
        return None
    strategy: dict[str, Any] = {}
    for key in _STRATEGY_KEYS:
        value = brief.get(key)
        if not value:
            continue
        candidate = dict(strategy)
        candidate[key] = value
        if len(json.dumps(candidate, default=str)) > STRATEGY_CHAR_BUDGET and strategy:
            continue
        strategy = candidate
    return strategy or None


# ---------------------------------------------------------------------------
# AN AGENT'S OWN WINS ARE ITS SHELF (Steven, 2026-09-09)
# ---------------------------------------------------------------------------
# The bench used to push worked programs onto every brief; it does not any
# more, and the right shelf was never a stranger's plan. It is the plans THIS
# agent has already had accepted. A want that needs the same tools as a job it
# already won is that job again with different words, so the outline for it is
# that plan's shape and the model is asked only what changes.
#
# Nothing here reads the bench's programs, and nothing here is a model call:
# the pick is token overlap and tool families, deterministic and explainable in
# one line, so the log can always say which win seeded an outline.
WIN_STATUSES = frozenset({"accepted", "selected"})
# A shared tool family is the thing that makes one job the same shape as
# another: a want that needs a mailbox and a calendar is the same work as the
# last want that needed a mailbox and a calendar, whatever either was about.
FAMILY_WEIGHT = 3
WORD_WEIGHT = 1


def _plan_tools(plan: Any) -> set[str]:
    """Every tool the runs of a plan's steps actually name."""
    found: set[str] = set()
    for step in (plan or {}).get("steps", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        for act in step.get("acts") or []:
            if not isinstance(act, dict):
                continue
            for run in act.get("runs") or []:
                if isinstance(run, dict) and run.get("tool"):
                    found.add(str(run["tool"]))
    return found


def _family_of(tool: str, index: Any) -> str:
    for row in index if isinstance(index, list) else []:
        if isinstance(row, dict) and str(row.get("tool") or "") == tool:
            return str(row.get("family") or "")
    # No index, or a tool it does not list: the head of the name is the family
    # this package can still tell ("gmail.message.send" -> "gmail").
    return tool.split(".", 1)[0].split(":")[-1]


def _families(tools: Any, index: Any) -> set[str]:
    return {_family_of(str(tool), index) for tool in tools if tool}


def families_the_want_needs(brief: Any) -> set[str]:
    """The tool families this want reads like it needs.

    Read off the bench's own index: a family whose name, or the words of one
    of its calls, appears in the want. No model call and no guessing at what a
    plan should be -- just which shelves are worth looking at.
    """
    if not isinstance(brief, dict):
        return set()
    asked = programs._tokens(
        [brief.get("want"), brief.get("want_in_own_words")]
    )
    if not asked:
        return set()
    found: set[str] = set()
    for row in brief.get("tools") if isinstance(brief.get("tools"), list) else []:
        if not isinstance(row, dict):
            continue
        family = str(row.get("family") or "")
        if not family or "<" in family:
            continue
        words = programs._tokens([family, row.get("tool"), row.get("one_line")])
        if asked & words:
            found.add(family)
    return found


def own_wins(proposals: Any, brief: Any = None) -> list[dict[str, Any]]:
    """The plans this agent already got picked for, newest first.

    Accepted, selected (a finalist ordinal is the person's pick), or a walk
    the brief says this agent finished. A row with no steps is not a shelf.
    """
    finished = {
        str(row.get("deal_id") or "")
        for row in ((brief or {}).get("your_finished_walks") or [])
        if isinstance(row, dict)
    }
    wins = []
    for row in proposals if isinstance(proposals, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("steps"), list):
            continue
        if not row["steps"]:
            continue
        deal = row.get("deal") if isinstance(row.get("deal"), dict) else {}
        won = (
            str(row.get("status") or "").lower() in WIN_STATUSES
            or row.get("finalist_ordinal") is not None
            or (finished and str(deal.get("deal_id") or "") in finished)
        )
        if won:
            wins.append(row)
    return wins


def score_win(win: Any, brief: Any, wanted_families: set[str] | None = None) -> int:
    """How near one of this agent's own wins is to this want.

    Three points for a tool FAMILY the want needs and this plan already ran --
    that is what makes two jobs the same shape -- and one for a word the two
    wants share.
    """
    if not isinstance(win, dict):
        return 0
    index = (brief or {}).get("tools") if isinstance(brief, dict) else None
    wanted = (
        families_the_want_needs(brief) if wanted_families is None else wanted_families
    )
    mine = _families(_plan_tools(win), index)
    asked = programs._tokens(
        [(brief or {}).get("want"), (brief or {}).get("want_in_own_words")]
    )
    theirs = programs._tokens(
        [win.get("want"), win.get("pitch_title"), win.get("finish_line")]
    )
    return FAMILY_WEIGHT * len(wanted & mine) + WORD_WEIGHT * len(asked & theirs)


def nearest_win(wins: Any, brief: Any) -> tuple[dict[str, Any] | None, int]:
    """The one win worth copying, or (None, 0).

    A win only seeds an outline when it shares a TOOL FAMILY with what this
    want needs. Shared words alone are not a shape: two wants can both be
    about a wedding and need nothing in common to run.
    """
    wanted = families_the_want_needs(brief)
    if not wanted:
        return None, 0
    best, best_score = None, 0
    index = (brief or {}).get("tools") if isinstance(brief, dict) else None
    for win in wins if isinstance(wins, list) else []:
        if not (wanted & _families(_plan_tools(win), index)):
            continue
        score = score_win(win, brief, wanted)
        if score > best_score:
            best, best_score = win, score
    return best, best_score


def outline_of(plan: Any) -> dict[str, Any]:
    """One of this agent's own plans, read back as an OUTLINE: per step the
    ask, the title, and the tool and service its first run stood on."""
    steps = []
    for step in (plan or {}).get("steps", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        spec: dict[str, Any] = {
            "ask": str(step.get("ask") or "APPROVE").upper(),
            "title": str(step.get("title") or ""),
        }
        for act in step.get("acts") or []:
            if not isinstance(act, dict):
                continue
            runs = [run for run in (act.get("runs") or []) if isinstance(run, dict)]
            tools = [
                {"tool": str(run.get("tool")), "on": str(run.get("on") or run.get("row") or "")}
                for run in runs
                if run.get("tool")
            ]
            if tools:
                spec["tool"] = tools[0]["tool"]
                spec["on"] = tools[0]["on"]
                if len(tools) > 1:
                    spec["tools"] = tools
                break
        steps.append(spec)
    return {"steps": steps}


SEEDED_OUTLINE_INSTRUCTION = (
    "You have done a job like this one before and it was accepted. Below is "
    "`outline_you_ran`: that plan, step by step, as an outline. Send it back "
    "ADJUSTED for the want above -- retitle each step in this want's words, "
    "drop a step this want does not need, add one it does, change a tool only "
    "where this want needs a different one. Keep the shape that worked.\n"
    'Answer: {"steps": [ ... ]}.'
)


# ---------------------------------------------------------------------------
# THE LOOP
# ---------------------------------------------------------------------------
def rounds_left(answer: dict[str, Any]) -> int | None:
    """The rounds the BENCH says are left, or None when it does not say.

    This is the whole safety net. It is not a harness lever: it is the bench's
    own arithmetic read back off its own answer, so a bench that would answer
    for ever still stops the loop at the number it published.
    """
    rounds = answer.get("rounds") if isinstance(answer, dict) else None
    if not isinstance(rounds, dict):
        return None
    left = rounds.get("left")
    return int(left) if isinstance(left, (int, float)) else None


def _spent(answer: dict[str, Any]) -> bool:
    return rounds_left(answer) == 0


class DraftLoop:
    """Drives rule 241 against one want: outline in, pieces back, then file.

    The model is asked small questions and never sees the whole document; the
    bench is the only validator. Nothing here decides whether a plan is good.
    """

    def __init__(self, model: Any, provider: Any, *, logger: logging.Logger | None = None):
        self.model = model
        self.provider = provider
        self.log = logger or _LOGGER
        self.rounds = 0
        self.calls = 0
        self.trail: list[dict[str, Any]] = []
        # Every patch sent, in order, so a repeated fix can be handed back with
        # what the agent already tried.
        self._sent: list[tuple[str, Any]] = []
        # The stable prefix for this run, built once in `run` and unchanged
        # after. Until then, the front door alone.
        self.prefix: str = FRONT_DOOR
        self.prompt_chars = 0
        self.cached_tokens = 0
        # This agent's own accepted plans, read ONCE per run (the client's
        # ETag rail makes the repeat read free anyway, but a cycle should not
        # ask twice). None means "not asked yet".
        self._wins: list[dict[str, Any]] | None = None
        self.seeded_by: str | None = None
        # A provider that caches nothing gets the tools index ONCE, on the
        # outline, instead of in front of every round.
        self._tools_ride_the_outline = False
        self._act_kinds: Any = None

    # -- the model ---------------------------------------------------------
    def _ask(self, instruction: str, payload: dict[str, Any], what: str = "") -> dict[str, Any]:
        """One question: the stable prefix as the system, the variable tail as
        the message. Every call is measured, and what the cache did is logged
        where the provider reports it."""
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        tail = instruction + "\n\n" + body
        if len(tail) > PROMPT_CHAR_BUDGET:
            # A loop prompt should never come near this. Say so out loud
            # rather than quietly spending a window on it.
            self.log.warning(
                "draft loop tail ran to %d characters (budget %d); the loop's "
                "prompts are small by design, so this is worth reading",
                len(tail),
                PROMPT_CHAR_BUDGET,
            )
        self.calls += 1
        self.prompt_chars += len(tail)
        response = self.model.invoke(
            system=self.prefix,
            messages=[ModelMessage.text("user", tail)],
            tools=[],
        )
        cached = cached_input_tokens(getattr(response, "usage", None))
        if cached:
            self.cached_tokens += cached
        self.log.info(
            "draft loop ask %s: prefix=%d chars (cacheable) tail=%d chars "
            "(~%d tokens) cached_in=%s",
            what or "?",
            len(self.prefix),
            len(tail),
            (len(self.prefix) + len(tail)) // 4,
            "unreported" if cached is None else cached,
        )
        return read_json_object(getattr(response, "text", ""))

    # -- the log -----------------------------------------------------------
    def _record(self, target_id: str, kind: str, answer: dict[str, Any], what: str) -> None:
        """ONE LINE PER ROUND. Round, what is left, and the one thing the
        bench named -- so a stuck loop is readable in the run log without
        turning the log into a copy of the plan."""
        fix = answer.get("next_fix") or {}
        rounds = answer.get("rounds") or {}
        line = {
            "target": target_id,
            "kind": kind,
            "round": self.rounds,
            "on": what,
            "remaining": answer.get("remaining"),
            "ready": bool(answer.get("ready")),
            "used": rounds.get("used"),
            "left": rounds.get("left"),
            "next_fix": fix.get("path"),
            "code": fix.get("code"),
            "closed": answer.get("closed"),
        }
        self.trail.append(line)
        self.log.info(
            "draft loop %s target=%s round=%d on=%s remaining=%s rounds=%s/%s "
            "next_fix=%s (%s)%s",
            kind,
            target_id,
            self.rounds,
            what,
            line["remaining"],
            line["used"],
            rounds.get("cap"),
            line["next_fix"],
            line["code"],
            " CLOSED" if line["closed"] else (" READY" if line["ready"] else ""),
        )

    # -- the three doors ---------------------------------------------------
    def _put(self, target_id: str, kind: str, outline: dict[str, Any]) -> dict[str, Any]:
        answer = self.provider.put_draft(target_id, outline, kind=kind)
        self.rounds = 0
        self._record(target_id, kind, answer, "outline")
        return answer

    PREVIEW = 120

    def _last_sent_for(self, path: str) -> dict[str, Any] | None:
        """The last patch that TOUCHED this path, whatever it was addressed to.

        A path is not always answered at its own address: the bench names
        `finalist_questions.0.0` and the agent patches `finalist_questions`,
        which is the right move (the whole list goes back, not one entry of
        it). An exact-match lookup finds nothing there and tells the agent it
        sent nothing, which is worse than saying nothing at all. So an
        ancestor counts, and so does a descendant.
        """
        if not path:
            return None
        for sent_path, value in reversed(self._sent):
            if (
                sent_path == path
                or path.startswith(sent_path + ".")
                or sent_path.startswith(path + ".")
            ):
                return {"path": sent_path, "value": value}
        return None

    def _preview(self, value: Any) -> str:
        try:
            text = value if isinstance(value, str) else json.dumps(value, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            text = str(value)
        text = " ".join(str(text).split())
        return text if len(text) <= self.PREVIEW else text[: self.PREVIEW - 1] + "\u2026"

    def _patch(
        self, target_id: str, kind: str, patches: Sequence[dict[str, Any]], what: str
    ) -> dict[str, Any]:
        # WHAT WENT OUT, EVERY ROUND. A stall is unreadable without it: on
        # 2026-09-09 a run spent rounds 123-132 on one REJ-15 and the log could
        # say only that the bench kept naming the same path, never what the
        # agent kept answering with.
        for entry in patches:
            path = str(entry.get("path") or "")
            self.log.info(
                "draft loop %s target=%s round=%d patch %s = %s",
                kind,
                target_id,
                self.rounds + 1,
                path or "?",
                self._preview(entry.get("value")),
            )
            self._sent.append((path, entry.get("value")))
        answer = self.provider.patch_draft(target_id, list(patches), kind=kind)
        self.rounds += 1
        self._record(target_id, kind, answer, what)
        return answer

    # What the first read of a cycle can find. Only ONE of these ends in a PUT.
    NOTHING_STANDING = "nothing_standing"   # 404 no_draft: open one
    STANDING = "standing"                   # carry it on, never PUT over it
    OVER = "over"                           # closed, or a door that will not
                                            # answer: leave the want this cycle

    def _read_first(self, target_id: str, kind: str) -> tuple[str, dict[str, Any]]:
        """THE FIRST STEP OF EVERY CYCLE. `GET .../proposals/draft` (and
        `?kind=plan` for a plan), which costs no round.

        A PUT over a standing draft is not a retry, it is a demolition: it
        replaces the document and sets the rounds back to zero. Live on
        2026-09-09, three units did exactly that on their own drafts -- one at
        10 rounds with 9 problems left and one at 18 rounds with 3, both a few
        answers from ready, both thrown away by the next cycle's outline. And
        since ebea60922 the bench COUNTS a repeated PUT as a round and keeps a
        used-up draft closed until it expires, so starting over now costs the
        want for a day. So: resume what stands, open only what is not there,
        and when the bench says the draft is over, leave the want alone until
        it expires.
        """
        try:
            answer = self.provider.read_draft(target_id, kind=kind)
        except Exception:  # noqa: BLE001 - a read that will not answer is not a licence to PUT
            self.log.warning(
                "draft loop %s target=%s: the draft could not be read; PUTting "
                "nothing this cycle",
                kind,
                target_id,
                exc_info=True,
            )
            return self.OVER, {}
        if not isinstance(answer, dict):
            return self.OVER, {}
        if answer.get("closed"):
            self.log.info(
                "draft loop %s target=%s: the standing draft is closed (%s); "
                "no PUT until it expires",
                kind,
                target_id,
                answer.get("closed"),
            )
            return self.OVER, answer
        error = str(answer.get("error") or "")
        if error == "no_draft" or int(answer.get("status") or 0) == 404:
            return self.NOTHING_STANDING, answer
        if not answer.get("ok"):
            return self.OVER, answer
        document = answer.get("draft")
        if not isinstance(document, dict) or not document:
            # An answer with no document is not a draft to carry on with, and
            # not a refusal either. Treat it as nothing standing.
            return self.NOTHING_STANDING, answer
        self.rounds = int((answer.get("rounds") or {}).get("used") or 0)
        self._record(target_id, kind, answer, "resume")
        return self.STANDING, answer

    def _own_wins(self, brief: Any) -> list[dict[str, Any]]:
        """The plans this agent already got picked for. One bench call a run."""
        if self._wins is not None:
            return self._wins
        rows: Any = []
        for name in ("_owned_proposals", "list_proposals"):
            reader = getattr(self.provider, name, None)
            if not callable(reader):
                continue
            try:
                answer = reader()
            except Exception:  # noqa: BLE001 - no shelf is not a failed run
                self.log.warning("draft loop: own proposals unreadable", exc_info=True)
                continue
            rows = answer if isinstance(answer, list) else (answer or {}).get("proposals")
            if rows:
                break
        self._wins = own_wins(rows, brief)
        return self._wins

    def _seed(self, brief: Any) -> tuple[dict[str, Any] | None, int]:
        win, score = nearest_win(self._own_wins(brief), brief)
        if win is None:
            self.log.info(
                "draft loop: no accepted plan of this agent's shares a tool "
                "family with this want; writing the outline from scratch"
            )
            return None, 0
        outline = outline_of(win)
        if not outline.get("steps"):
            return None, 0
        self.seeded_by = str(win.get("id") or win.get("proposal_id") or "")
        self.log.info(
            "draft loop: outline seeded by this agent's own accepted plan %s "
            "(%s, %d step(s), score %d)",
            self.seeded_by or "?",
            " ".join(str(win.get("pitch_title") or win.get("want") or "").split())[:60],
            len(outline["steps"]),
            score,
        )
        return outline, score

    def _outline_payload(
        self, want: Any, strategy: Any, brief: Any, seed: dict[str, Any] | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"want": want, "what_the_person_said": strategy}
        if seed:
            payload["outline_you_ran"] = seed["steps"]
        elif self._tools_ride_the_outline:
            # Nothing here caches, so the rules and the index are sent ONCE, on
            # the only call that chooses a shape and a tool. A SEEDED call gets
            # neither: the shape is in front of it and the tools are the ones
            # that already worked, so the index would be paid for twice and
            # read once.
            payload["the_rules"] = FRONT_DOOR
            payload["blocks"] = block_grammar_summary(brief, self._act_kinds)
            payload["tools"] = tools_index(brief)
        return payload

    def _open(
        self, target_id: str, kind: str, want: Any, strategy: Any, brief: Any, act_kinds: Any
    ) -> dict[str, Any] | None:
        """THE ONE PUT. None when the model was asked for an outline and gave
        none."""
        if kind == "plan":
            # THE PLAN STARTS FROM THE STEPS ALREADY FILED (rule 113). An
            # outline here would replace the bid the person picked, so the
            # draft is opened empty. The bench answers with the owned plan
            # expanded -- or, when the person answered questions at the pick,
            # with `next: "outline"` and the answers beside the bid's steps;
            # `run` follows whichever it names (see `_follow`).
            return self._put(target_id, kind, {})
        # AN AGENT'S OWN WINS ARE ITS SHELF. A want that needs the tools of a
        # job this agent already won is that job again in other words, so the
        # outline starts as that plan's shape and the one call asks only what
        # changes.
        seed, _score = self._seed(brief)
        outline = read_outline(
            self._ask(
                SEEDED_OUTLINE_INSTRUCTION if seed else OUTLINE_INSTRUCTION,
                self._outline_payload(want, strategy, brief, seed),
                "outline seeded" if seed else "outline",
            )
        )
        if seed and not outline.get("steps"):
            # The model was shown a shape that worked and answered with
            # nothing. The shape is still the best thing anyone has for this
            # want, so it goes in as it stands rather than costing the want.
            self.log.warning(
                "draft loop: the adjust call answered with no steps; sending "
                "the plan that was accepted before, unchanged"
            )
            outline = seed
        if not outline.get("steps"):
            return None
        return self._put(target_id, kind, outline)

    def _follow(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any, strategy: Any
    ) -> dict[str, Any]:
        """WHAT THE DOOR NAMES NEXT IS WHAT HAPPENS NEXT. `next: "outline"`
        is the one name today: the bench has put `steps_you_bid` beside
        `the_person_answered` and wants the outline back before it builds a
        template. One model ask, one PUT, and the answer to that PUT is the
        template the rest of the loop reads. An answer with no `next` is
        handed back untouched."""
        if not isinstance(answer, dict) or answer.get("next") != "outline":
            return answer
        bid_steps = [s for s in (answer.get("steps_you_bid") or []) if isinstance(s, dict)]
        payload = {
            "want": want,
            "what_the_person_said": strategy,
            "steps_you_bid": bid_steps,
            "the_person_answered": answer.get("the_person_answered") or [],
        }
        outline = read_outline(self._ask(PLAN_OUTLINE_INSTRUCTION, payload, "plan outline"))
        if not outline.get("steps"):
            # The model was shown its own steps and the answers and said
            # nothing. The steps it bid are still the best thing anyone has,
            # so they go back as they stand rather than costing the plan.
            self.log.warning(
                "draft loop %s target=%s: the outline round answered with no "
                "steps; sending the steps already bid, unchanged",
                kind,
                target_id,
            )
            outline = {
                "steps": [
                    {
                        k: step.get(k)
                        for k in ("bid_step", "ask", "title")
                        if step.get(k) is not None
                    }
                    for step in bid_steps
                ]
            }
        if not outline.get("steps"):
            return answer
        self.log.info(
            "draft loop %s target=%s: outline round -- %d step(s) bid, %d "
            "answer(s) from the person, %d step(s) sent back",
            kind,
            target_id,
            len(bid_steps),
            len(payload["the_person_answered"]),
            len(outline["steps"]),
        )
        return self._put(target_id, kind, outline)

    # -- the pieces --------------------------------------------------------
    def _fill_the_blanks(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any
    ) -> dict[str, Any]:
        """One step at a time: the step as the bench expanded it, its blanks,
        and the patches that answer them."""
        for index, rows in group_blanks(answer.get("blanks")):
            if answer.get("closed") or answer.get("ready") or _spent(answer):
                break
            payload = {
                "want": want,
                "step_number": None if index is None else index + 1,
                # ONE step. The draft has every other one and the model needs
                # none of them to write this one's words.
                "step": _fit(step_context(answer.get("draft"), index)),
                "blanks": [
                    {
                        "path": row.get("path"),
                        "what_belongs_there": row.get("note"),
                        "example": row.get("example"),
                        "required": bool(row.get("required")),
                    }
                    for row in rows
                ],
            }
            if index is None:
                payload["these_are"] = (
                    "the fields of the bid itself, not of any one step"
                )
            # LAW A: the person's words and picks ride the tail of every
            # blanks ask, so a blank is filled from them and never invented.
            instruction = with_the_person_said(BLANKS_INSTRUCTION, payload, answer)
            patches = read_patches(
                self._ask(
                    instruction,
                    payload,
                    "blanks" if index is None else f"step {index + 1}",
                )
            )
            if not patches:
                self.log.warning(
                    "draft loop %s target=%s: the model answered a step's blanks "
                    "with nothing patchable; moving to the next piece",
                    kind,
                    target_id,
                )
                continue
            answer = self._patch(
                target_id,
                kind,
                patches,
                "blanks" if index is None else f"step {index + 1}",
            )
        return answer

    def _answer_the_fixes(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any
    ) -> dict[str, Any]:
        """ONE problem at a time, for as long as the bench names one.

        Advancing the draft is useful work. Repeating an identical draft and
        problem is not: stop after three unchanged responses and let the
        watcher advance to another target. There is no total step/round cap.

        WHAT A REPEAT GETS IS A BETTER PROMPT, NOT A LIMIT. When the bench
        names the same path with the same code twice running, the next ask
        carries what was sent last round and what the bench has for that path
        now. Live on 2026-09-09, Greg spent rounds 123-132 on one REJ-15 on
        `finalist_questions.0.0`, asked the same question in the same words
        every time, and answered it the same way every time.
        """
        last_named: tuple[str, str] | None = None
        last_progress = None
        unchanged = 0
        while (
            not answer.get("ready")
            and not answer.get("closed")
            and not _spent(answer)
            and isinstance(answer.get("next_fix"), dict)
        ):
            fix = answer["next_fix"]
            path = str(fix.get("path") or "")
            code = str(fix.get("code") or "")
            progress = json.dumps({"draft": answer.get("draft"),
                                   "next_fix": fix, "remaining": answer.get("remaining")},
                                  sort_keys=True, default=str)
            unchanged = unchanged + 1 if progress == last_progress else 0
            last_progress = progress
            if unchanged >= 3:
                return dict(answer, ok=False, error="draft_stalled",
                            message="The draft and its next problem were unchanged after three patches; paused to avoid repeated model spending.")
            index = step_of(path)
            payload: dict[str, Any] = {
                "want": want,
                "fix_this": {
                    "path": fix.get("path"),
                    "current_value": fix.get("current"),
                    "code": fix.get("code"),
                    "fix": fix.get("fix"),
                    "detail": fix.get("detail"),
                },
                "step_number": None if index is None else index + 1,
                # The plan in one line per step, and the ONE step being
                # changed. Never the draft: a thirty-step document in front of
                # a one-field fix is the cost this loop exists to avoid.
                "plan": outline_summary(answer.get("draft")),
                "step": _fit(step_context(answer.get("draft"), index)),
            }
            # SAME PATH, SAME CODE AS LAST ROUND: say so, and hand back what
            # was sent beside what the bench has now. No strike rule and no
            # round limit -- a model that is told its last answer did not land,
            # and shown it, can send something else; a model that is asked the
            # same question in the same words answers it the same way.
            instruction = FIX_INSTRUCTION
            if (path, code) == last_named and path:
                instruction = REPEATED_FIX_INSTRUCTION + FIX_INSTRUCTION
                payload["your_last_patch_did_not_clear_this"] = {
                    "path": path,
                    "you_sent_last_round": self._last_sent_for(path),
                    "what_is_in_the_document_now": fix.get("current"),
                }
                self.log.info(
                    "draft loop %s target=%s round=%d: %s (%s) again; telling "
                    "the agent what it sent last round",
                    kind,
                    target_id,
                    self.rounds + 1,
                    path,
                    code or "?",
                )
            last_named = (path, code)
            # LAW A: and the tail of every fix ask.
            instruction = with_the_person_said(instruction, payload, answer)
            patches = read_patches(self._ask(instruction, payload, f"fix {path or '?'}"))
            if not patches:
                # ONCE MORE, SAYING SO. An empty answer once is a hiccup (a
                # fenced reply with nothing in it, a refusal, a timeout); twice
                # is the model's answer. Live on 2026-09-09 two agents lost a
                # whole draft, ten rounds in, to one empty reply.
                self.log.info(
                    "draft loop %s target=%s: nothing came back for %s; asking once more",
                    kind,
                    target_id,
                    path,
                )
                patches = read_patches(
                    self._ask(EMPTY_ANSWER_INSTRUCTION + instruction, payload,
                              f"fix {path or '?'} (again)")
                )
            if not patches:
                self.log.warning(
                    "draft loop %s target=%s: no patch came back for %s twice; "
                    "stopping this draft",
                    kind,
                    target_id,
                    path,
                )
                break
            patches = self._aim(patches, path, kind, target_id)
            answer = self._patch(target_id, kind, patches, f"fix {path or '?'}")
        return answer

    def _aim(
        self, patches: list[dict[str, Any]], path: str, kind: str, target_id: str
    ) -> list[dict[str, Any]]:
        """THE ASKED PATH IS THE PATH. The bench named one path; a single patch
        that came back for a different one is the answer to the question that
        was asked, filed where it was asked. Live on 2026-09-09 Cindy was asked
        for `steps.1.outcome_promise` sixty rounds running and answered
        `steps.2.outcome_promise` every time -- the same words, one step off,
        until the person picked someone else. Only the same field at another
        address moves; two or more patches, a parent path, or a different
        field are left as they are.
        """
        if not path or len(patches) != 1:
            return patches
        sent = str(patches[0].get("path") or "")
        if sent == path:
            return patches
        # Only the SAME FIELD at another address moves. A patch on a parent
        # of the asked path (`finalist_questions` for
        # `finalist_questions.0.0`) is the whole list coming back, which is
        # right; a patch on some other field is another change, and the
        # bench's next answer says whether it landed.
        if path.startswith(sent + ".") or sent.rsplit(".", 1)[-1] != path.rsplit(".", 1)[-1]:
            return patches
        self.log.info(
            "draft loop %s target=%s: the patch for %s is filed at the asked path %s",
            kind,
            target_id,
            sent,
            path,
        )
        return [{"path": path, "value": patches[0].get("value")}]

    # -- the whole thing ---------------------------------------------------
    def run(
        self,
        target_id: str,
        *,
        kind: str = "bid",
        brief: Any = None,
        act_kinds: Any = None,
        proposal_id: str | None = None,
        idempotency_key: str = "",
        file: bool = True,
    ) -> dict[str, Any]:
        """Outline -> blanks -> fixes -> file. One want, one answer.

        `file` false runs the whole loop and stops at the door: the document
        the bench holds comes back on `draft` and nothing is filed.
        """
        want = (brief or {}).get("want") if isinstance(brief, dict) else None
        strategy = person_strategy(brief)
        # ONCE, and byte-identical from here to the end of the run: the front
        # door, the block index and the tools index. Everything after this is
        # a small tail on top of a prefix the provider has already seen.
        self._act_kinds = act_kinds
        caches = bool(getattr(self.model, "caches_a_stable_prefix", lambda: False)())
        self.prefix = stable_prefix(brief, act_kinds, with_tools=caches)
        self._tools_ride_the_outline = not caches
        # READ FIRST, EVERY CYCLE. This is the loop's first step and the only
        # thing that decides whether an outline goes out at all.
        state, answer = self._read_first(target_id, kind)
        if state == self.OVER:
            return self._gave_up(
                target_id, kind,
                "draft_closed" if answer.get("closed") else
                str(answer.get("error") or "draft_unreadable"),
                str(
                    answer.get("closed")
                    or answer.get("message")
                    or "The draft on this want could not be read, so nothing was sent."
                ),
                answer,
            )
        if state == self.NOTHING_STANDING:
            opened = self._open(target_id, kind, want, strategy, brief, act_kinds)
            if opened is None:
                return self._gave_up(
                    target_id, kind, "no_outline",
                    "The model was asked for an outline and answered with no steps.",
                )
            answer = opened
        # THE DOOR SAYS WHAT COMES NEXT. Read it before assuming a template.
        answer = self._follow(target_id, kind, answer, want, strategy)
        if answer.get("next"):
            return self._gave_up(
                target_id, kind, "no_outline",
                f"The door asked for the {answer.get(next)} and the model gave none.",
                answer,
            )
        if not answer.get("ok") and not answer.get("closed"):
            return self._gave_up(
                target_id, kind,
                str(answer.get("error") or "draft_door_refused"),
                str(answer.get("message") or answer.get("error") or ""),
                answer,
            )
        if not answer.get("closed"):
            answer = self._fill_the_blanks(target_id, kind, answer, want)
        if not answer.get("closed"):
            answer = self._answer_the_fixes(target_id, kind, answer, want)
        if answer.get("error") == "draft_stalled":
            return self._gave_up(target_id, kind, "draft_stalled", answer["message"], answer)
        if answer.get("closed"):
            # A closed draft is never re-PUT, in this run or the next: the
            # bench counts a repeated PUT as a round and holds a used-up draft
            # closed until it expires, so starting over costs the want a day.
            self.log.warning(
                "draft loop %s target=%s closed: %s",
                kind,
                target_id,
                answer.get("closed"),
            )
        if not answer.get("ready"):
            return self._gave_up(
                target_id, kind,
                "draft_closed" if answer.get("closed") else "draft_not_ready",
                str(
                    answer.get("closed")
                    or "The draft still has problems and the loop stopped before filing."
                ),
                answer,
            )
        if not file:
            return {
                "ok": True,
                "filed": False,
                "dry_run": True,
                "kind": kind,
                "draft": answer.get("draft"),
                "rounds": self.rounds,
                "model_calls": self.calls,
                "trail": self.trail,
            }
        if kind == "plan":
            filed = self.provider.file_plan_from_draft(
                target_id, str(proposal_id or ""), idempotency_key
            )
        else:
            filed = self.provider.file_from_draft(target_id, idempotency_key)
        self.log.info(
            "draft loop %s target=%s filed=%s after %d round(s) and %d model "
            "call(s); prefix %d chars sent once and cached, tails %d chars "
            "(~%d tokens), cached input %s",
            kind,
            target_id,
            bool(filed.get("ok")),
            self.rounds,
            self.calls,
            len(self.prefix),
            self.prompt_chars,
            (len(self.prefix) + self.prompt_chars) // 4,
            self.cached_tokens or "unreported",
        )
        return {
            "ok": bool(filed.get("ok")),
            "filed": bool(filed.get("ok")),
            "kind": kind,
            "response": filed,
            "proposal_id": filed.get("proposal_id"),
            "rounds": self.rounds,
            "model_calls": self.calls,
            "trail": self.trail,
        }

    def _gave_up(
        self,
        target_id: str,
        kind: str,
        error: str,
        message: str,
        answer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.log.warning(
            "draft loop %s target=%s gave up after %d round(s): %s (%s)",
            kind,
            target_id,
            self.rounds,
            error,
            message,
        )
        out = {
            "ok": False,
            "filed": False,
            "kind": kind,
            "error": error,
            "message": message,
            "rounds": self.rounds,
            "model_calls": self.calls,
            "trail": self.trail,
        }
        if answer:
            out["remaining"] = answer.get("remaining")
            out["next_fix"] = answer.get("next_fix")
            if answer.get("status") is not None:
                out["status"] = answer.get("status")
        return out
