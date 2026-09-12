"""THE DRAFT LOOP — one plan, built up in pieces, at the bench's own door.

TWO STAGES (rules 243-245, Steven Ochs, 2026-09-11). A PROPOSAL is the agent's
short answer to a want -- seven fields, ONE call, no steps -- and it is what
the person chooses between. A PLAN is owed by the agent that was CHOSEN, it is
a FORM the bench hands over, and the loop below is how that form gets filled.
So `run(kind="bid")` is one model call and one filing; `run(kind="plan")` is
the loop. What forced the split: the agent was being asked to think up a plan
AND type it into a 130-slot form under 44 rejection rules, one blank at a
time, before anyone had picked it -- so most of that work was thrown away, and
on 10-11 September the fleet spun. GPT-6 Astra took a want from 139 problems
down to 5 in 36 rounds and died on two LENGTH CAPS (a line of 188 where the
cap is 140). Llama, Nova and Mistral each burned the full 200-round ceiling
and filed nothing. Over seven days the validate door refused 8,813 times and
passed 1,209. The form now answers in picks and short lines, the door TRIMS
what is long and says so on `bench_fixed` instead of refusing it, and a
correction is never a round.

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
# THE FORM ASK GETS ITS OWN, AND IT IS BIGGER (2026-09-11). The budget above
# was written for a loop of thirty small rounds; the form ask REPLACES those
# rounds -- one call, the whole plan -- and what rides on it is the thing that
# makes a weak model work: the bench's own finished example, whole. Steven's
# test 4 is a seventeen-step plan, and a seventeen-step example is ~4KB on its
# own, so at 8,000 the example was the first thing shed on exactly the wants
# that need it most. 16,000 characters is about 4,000 input tokens for the one
# call that writes the plan, against the 25,000-40,000 the old road spent
# writing it thirty times.
FORM_CHAR_BUDGET = 16_000
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

# THE PLAN'S OUTLINE ROUND — the older plan door, before the form.
#
# The bid road's own outline instruction went with rule 243 on 2026-09-11: a
# proposal is seven fields and one call, it opens no draft, and nothing asks a
# model for the outline of a plan nobody has picked it for.
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
    "belongs there. A blank that carries `question` is asked in plain words: "
    "ANSWER THAT QUESTION. A blank that carries `choices` is a PICK -- answer "
    "with one of those words exactly as it is written and nothing else. A "
    "blank that carries `up_to_characters` is trimmed to that length by the "
    "bench if you go over, never refused, so write it short rather than "
    "padding it. Fill them in your own words; leave nothing you can answer "
    "empty; change nothing else. A `you` blank is the person's own bullet for "
    "that part, pinned to it: write it as its note says (one line, starts with "
    "connect / approve / pick / answer, names the thing) rather than leaving "
    "the bench to print a flat line. A promise names the thing you deliver, "
    "never you; a work item starts with an -ing word.\n"
    'Answer: {"patches": [{"path": "<the exact path>", "value": <your value>}, '
    "...]}."
)

# THE CONTENT CODE THAT IS ABOUT THE WHOLE STEP, not a line of it.
RESTATES_THE_PICK = "restates_the_pick"

WHOLE_STEP_FIX_INSTRUCTION = (
    "THE BENCH NAMED THE WHOLE STEP, NOT ONE LINE OF IT. Rewording a line "
    "cannot clear this: the step itself is the problem. `step` below carries "
    "every field of it. TWO EXITS AND NO THIRD:\n"
    "  REPLACE IT -- the whole step back as the patch value, every field "
    "filled, doing something different: "
    '{"patches": [{"path": "<the path named below>", "value": {"verb": "...", '
    '"do_line": "...", "hand_over_line": "...", "who": "agent", ...}}]}\n'
    "  DROP IT -- the plan is better without it: "
    '{"drop": {"step": <step_number below>}}\n'
    "Answer with one or the other and nothing else. A patch to ONE FIELD of "
    "this step is not an answer to this question.\n"
)

RESTATING_STEP_INSTRUCTION = (
    "WHAT IS WRONG WITH THIS STEP: its whole work is handing back people the "
    "person already picked. The bench asks WHO on a step of its own, out of "
    "the person's private contact book, so there is nothing here for a step "
    "of yours to find, get, identify or list. A step that stays must DO "
    "SOMETHING WITH those people -- email them, meet them, call them, post to "
    "them, book something for them -- and its `verb` must say so (emails, "
    "meeting, calls, posts, books). If this step was only getting you the "
    "names, DROP IT: the step after it reads the picks straight off the "
    "person's own step, and no address ever rides the plan.\n"
)

REPEATED_STEP_EXITS_INSTRUCTION = (
    "REWORDING DID NOT WORK. You have changed the words on this path and the "
    "bench is naming it again, so the words are not what is wrong -- the STEP "
    "is. Changing one line of it a third time cannot clear it. There are two "
    "exits and no third:\n"
    '  REPLACE THE WHOLE STEP: {"patches": [{"path": "<steps_path below>", '
    '"value": {"verb": "...", "do_line": "...", "hand_over_line": "...", '
    '"who": "agent", ...}}]} -- every field, doing something different.\n'
    '  DROP THE STEP: {"drop": {"step": <step_number below>}}\n'
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


_DROP_WORD = re.compile(r"\bdrop(?:ped|ping)?\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")


def read_drop(answer: Any, asked_step: int | None = None) -> int | None:
    """The 0-BASED step a model asked to DROP, or None.

    Four shapes, because a raw model writes whichever one it read: the
    `{"drop": {"step": N}}` the door takes, a bare `{"drop": N}`,
    `{"action": "drop", "step": N}`, and the WORD drop with no number at all --
    which can only mean the step the bench just named, so `asked_step` is the
    answer. The number a model writes is the one the prompt showed it, which
    is the 1-based `step_number`; `asked_step` settles any doubt.
    """
    if not isinstance(answer, dict):
        return None
    told = answer.get("drop")
    if told is None and str(answer.get("action") or "").strip().lower() == "drop":
        told = answer.get("step", True)
    if told is None:
        return None
    number: Any = None
    if isinstance(told, dict):
        number = told.get("step", told.get("number"))
    elif isinstance(told, bool):
        number = None
    elif isinstance(told, (int, float)):
        number = told
    elif isinstance(told, str):
        if not _DROP_WORD.search(told) and not _DIGITS.search(told):
            return None
        found = _DIGITS.search(told)
        number = int(found.group(0)) if found else None
    if isinstance(number, bool) or number is None:
        return asked_step
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        return asked_step
    if asked_step is None:
        return wanted
    # The prompt shows the 1-based step number; the door counts from zero.
    # Either spelling of the step that was ASKED about reads as that step.
    if wanted in (asked_step, asked_step + 1):
        return asked_step
    return wanted


def _step_path(path: Any, index: int | None) -> str:
    """`form.steps.N` for the step a field path sits on."""
    text = str(path or "")
    if index is None:
        return text
    prefix = "form." if text.startswith("form.") else ""
    return f"{prefix}steps.{index}"


def _is_a_field_patch(patches: Any, asked: str) -> bool:
    """True when every patch names a FIELD of the step that was asked for
    whole. `form.steps.0.do_line` against `form.steps.0` is the wrong shape."""
    rows = [str(row.get("path") or "") for row in patches or [] if isinstance(row, dict)]
    if not rows:
        return False
    return all(row.startswith(asked + ".") for row in rows)


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
# A FORM PATH IS A STEP PATH TOO (2026-09-11). The plan door names the
# form's own paths -- `form.steps.2.do_line`, `form.span_days` -- and a
# blank that groups under no step is asked on its own, so a regex that
# only knew `steps.N` put every step of a form plan in the plan-level
# group and asked all seventeen at once.
_STEP_PATH = re.compile(r"^(?:form\.)?steps\.(\d+)(?:\.|$)")


def step_of(path: Any) -> int | None:
    match = _STEP_PATH.match(str(path or ""))
    return int(match.group(1)) if match else None


# A path that names a WHOLE STEP and no field on it. `form.steps.2` is the
# step; `form.steps.2.do_line` is one line of it, and the two are different
# questions (0.38.3).
_WHOLE_STEP_PATH = re.compile(r"^(?:form\.)?steps\.(\d+)$")


def whole_step_path(path: Any) -> int | None:
    """The 0-based index when the bench named a WHOLE step, else None."""
    match = _WHOLE_STEP_PATH.match(str(path or "").strip())
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
        # PUBLISHED IS PUBLISHED, INCLUDING EMPTY (2026-09-11). `{}` is the
        # bench saying this want has no blocks, and it is not the same thing as
        # a brief that carries no catalog at all. Falling through to the act
        # registry on an empty catalog offered the model kinds this want does
        # not have -- the same bug as the empty tools index below, one line
        # away from it.
        return BLOCK_GRAMMAR + (
            " The blocks this bench has: "
            + ", ".join(sorted({str(kind) for kind in catalog})[:24])
            + "."
            if catalog
            else " This want publishes no blocks, so a step is your own work."
        )
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

    AN EMPTY LIST IS AN ANSWER (2026-09-11). `tools: []` is the bench saying
    this want offers NO tools; only a MISSING key (or a null) means "this
    bench publishes no index". Until this fix an empty list fell through to
    the platform fallback below, so every plan on a want with no tools was
    offered GRANT_MIN_ACTIONS and PLATFORM_TOOLS and put a connection row it
    could never use on the front of a step. That one line is why Jan, Alice
    and Bobby each burned the full 200-round ceiling on the workshop want and
    filed nothing.
    """
    published = (brief or {}).get("tools") if isinstance(brief, dict) else None
    if isinstance(published, list):
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
# THE STANCE LINE — how the person wants it done, in the person's own words
# ---------------------------------------------------------------------------
# The three sliders on the want flow (Scrappy to Polished, Careful to
# Aggressive, Proven path to Creative) reach the brief as `strategy`. The bench
# renders them as ONE SENTENCE the person can read on their own want
# ("Approach this like a master who is hyper-creative and moves fast"), and
# that sentence opens the proposal ask and the plan ask, first line, before the
# form. It is the person's own instruction about how, and it is the kind of
# instruction models follow well. A bench that still publishes the raw sliders
# is rendered here instead, unchanged in meaning and never summarised.
STANCE_LABEL = "HOW THEY WANT IT DONE"
STANCE_NOTE = (
    "This is the person's own instruction about how, not what. Write to it."
)


def stance_line(brief: Any) -> str:
    """The person's stance, one line, or "" when they said nothing.

    `strategy` is the bench's rendered sentence when it publishes one. When
    the brief still carries the raw sliders, they are printed as they stand --
    name and value -- rather than turned into a sentence this module invented.
    """
    if not isinstance(brief, dict):
        return ""
    rendered = brief.get("strategy")
    if isinstance(rendered, str):
        return " ".join(rendered.split())
    if isinstance(rendered, dict):
        parts = [
            f"{str(name).replace('_', ' ')}: {value}"
            for name, value in rendered.items()
            if value not in (None, "", [], {})
        ]
        return "; ".join(parts)
    if isinstance(rendered, list):
        return "; ".join(" ".join(str(entry).split()) for entry in rendered if entry)
    return ""


def stance_of(answer: Any) -> str:
    """The stance line the DOOR carries on a plan answer, or "".

    The bench renders the person's three sliders into one sentence and puts it
    on `stance` (draft_routes._outline_ask, draft_door.form_answer). That
    sentence is the person's, so where the door sends one it wins over the one
    this module renders off the raw sliders.
    """
    if not isinstance(answer, dict):
        return ""
    value = answer.get("stance")
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def example_plan(answer: Any) -> Any:
    """The finished plan the BENCH chose to show beside the form, or None.

    The example shelf is the bench's (one accepted plan per kind of want,
    looked up, no model call), so the harness never picks one and never
    invents one: it prints what the door's answer carries on `example` and
    nothing else. For a weak model this is the single biggest lever there is,
    which is also why it is the FIRST thing shed when a tail runs long -- the
    question must always survive.
    """
    if not isinstance(answer, dict):
        return None
    # THE BENCH'S OWN KEY IS `example_plan` (draft_routes._outline_ask,
    # draft_door.form_answer). `example` is the older name and is still read,
    # because a bench that publishes it is publishing the same thing.
    return answer.get("example_plan") or answer.get("example") or None


# ---------------------------------------------------------------------------
# STAGE ONE — THE PROPOSAL, ONE CALL (rule 243, 2026-09-11)
# ---------------------------------------------------------------------------
# A PROPOSAL IS THE AGENT'S SHORT ANSWER TO A WANT, AND THE PLAN IS OWED BY
# THE ONE WHO WAS CHOSEN. Until 2026-09-11 this module wrote a whole plan for
# a want nobody had picked it for: an 18-step, ~33KB document through 44
# rejection rules, thrown away for every agent but one. Seven fields now, one
# reply, and the plan comes after the person picks.
#
# THE HARNESS DOES NOT TRIM AND DOES NOT COUNT CHARACTERS. The caps below are
# said out loud in the ask because a model writes better inside a stated cap,
# but the DOOR owns them: it trims a long title or paragraph to the cap and
# says what it trimmed on `bench_fixed`. A harness that trimmed too would cut
# the same sentence twice and hide the bench's own answer.
PROPOSAL_TITLE_MAX = 120
PROPOSAL_BODY_MAX = 600
PROPOSAL_LINKS_MIN = 1
PROPOSAL_LINKS_MAX = 3
PROPOSAL_QUESTIONS_MAX = 3
# THE FRAMES ARE DROPPED (Steven, 2026-09-11, contract 3.16: "fine drop them").
# The bench no longer writes half of anybody's sentence and no longer reads
# `fill`: "`fill` is not read any more, and the bench no longer writes half of
# your sentence. Write the whole question, in your own words, in `title`."
#
# WHAT FORCED THE CHANGE HERE: the first fleet cycle on 0.38.1 was refused
# REJ-15 on EVERY question of every bid, because this package was still filing
# {id, format, fill} against a door that had stopped reading it.
#
# THREE SHAPES AND NO FOURTH. A date, a number, a form or an upload is a
# control on a STEP of the plan, where the work is -- not a question asked
# before anybody has chosen this agent.
PROPOSAL_QUESTION_FORMATS: tuple[str, ...] = ("short_answer", "yes_no", "single_choice")
# A text box under another name, and a question with no shape at all: both are
# the box, and the door takes it.
_TEXT_BOX_SPELLINGS = frozenset({"", "written_response", "text", "short_text", "long_text"})
FINALIST_DEFAULT_FORMAT = "short_answer"
# Where the words of a question can be found, best first. `fill` is last and
# is read only so a model still writing the old shape keeps its question
# instead of having it thrown away.
_QUESTION_WORD_KEYS = ("title", "question", "prompt", "text", "fill")
# RULE 238 CORRECTED (Steven, 2026-09-11): THE CONTACT BOOK IS NOT A QUESTION.
# Who this goes to is a STEP of the plan that the BENCH stamps, after the
# person has chosen this agent, out of their own private book -- so a
# `contact_picker` on a PROPOSAL is refused REJ-15 at the door. The slug is
# kept here for one reason: to recognise it and DROP it if a model writes one.
CONTACT_PICKER_FORMAT = "contact_picker"

PROPOSAL_FIELDS = (
    "pitch_title",
    "pitch_body",
    "odds",
    "total_ask_cents",
    "research_links",
    "finalist_questions",
    "tools_needed",
)

PROPOSAL_INSTRUCTION = (
    "Answer the want below with a PROPOSAL: seven fields, ONE reply, nothing "
    "else. A proposal is your short answer to what this person wants; it is "
    "what they choose between. You do NOT write a plan here -- no steps, no "
    "blocks, no account rows, no deliverables, no grant requests, no finish "
    "line, no wins, no capabilities, no skill research, no separate strategy "
    "block. If they pick you, the bench hands you a form and you write the "
    "plan then.\n"
    f"`pitch_title` -- what you are offering, up to {PROPOSAL_TITLE_MAX} "
    "characters.\n"
    f"`pitch_body` -- ONE paragraph, up to {PROPOSAL_BODY_MAX} characters: "
    "what they get and roughly how. THIS IS YOUR STRATEGY; there is no other "
    "place for it.\n"
    "`odds` -- your honest chance this person ends up with the thing, between "
    "0 and 1.\n"
    "`total_ask_cents` -- what you charge, in whole cents, inside the want's "
    "budget.\n"
    f"`research_links` -- REQUIRED, {PROPOSAL_LINKS_MIN} to "
    f"{PROPOSAL_LINKS_MAX} of them, each "
    '{"url": ..., "note": "one line: what this link told you about this '
    'want"}. Links you went and found for THIS want. A proposal with none is '
    'refused. No "plan use" sentence.\n'
    f"`finalist_questions` -- up to {PROPOSAL_QUESTIONS_MAX} questions for "
    "this person, and `[]` if you need nothing. The plan gets built from "
    "their answers, so ask what you actually need before you can plan. WRITE "
    "THE WHOLE QUESTION YOURSELF, in `title`, in your own words. Nobody "
    "writes half of it for you and there is no `fill` field. Three shapes and "
    "no fourth:\n"
    '  short_answer -- they type one short line: {"title": "What do you want '
    'them to do next?", "format": "short_answer"}\n'
    '  yes_no -- they tap Yes or No: {"title": "Should I include background '
    'on each person?", "format": "yes_no"}\n'
    '  single_choice -- they tap one of the options you write, TWO OR MORE: '
    '{"title": "Which tone should the message have?", "format": '
    '"single_choice", "config": {"options": ["Warm", "Straight to the '
    'point"]}}\n'
    "A date, a number, a form or a file is a control on a STEP of the plan, "
    "never a question here.\n"
    "DO NOT ASK WHO THIS GOES TO. The bench asks that itself, on a step of the "
    "plan, after this person has chosen you, out of their own private contact "
    "book -- a contact question here is refused. Never ask a person to type an "
    "address or a phone number into a box.\n"
    "`tools_needed` -- the tools you will need, each one COPIED EXACTLY from "
    "the `tool` value of a row in `tools_on_this_want`. That exact string and "
    "nothing else: a provider name (\"google-gmail\") is not a tool, and "
    "neither is a slug you assembled out of a service and a verb. A service "
    "that is not on that list is named on the STEP that uses it, as an "
    "outside act, never here. File [] when you need none.\n"
    "A long title or paragraph is TRIMMED by the bench, not refused, so write "
    "it well and do not pad it.\n"
    'Answer: {"pitch_title": "...", "pitch_body": "...", "odds": 0.0, '
    '"total_ask_cents": 0, "research_links": [...], "finalist_questions": '
    '[...], "tools_needed": [...]}.'
)

LINKS_INSTRUCTION = (
    "Your proposal is written, but it carries no research links and the bench "
    f"refuses a proposal without them (REJ-31). Give {PROPOSAL_LINKS_MIN} to "
    f"{PROPOSAL_LINKS_MAX} links you went and found for THIS want, each "
    '{"url": "https://...", "note": "one line: what this link told you about '
    'this want"}. Real pages you read, not a search box and not this site. '
    'Answer with nothing else: {"research_links": [...]}'
)

# What a brief calls the money the person put up. Read in this order and
# passed through as `budget_cents` so the ask names one number, not three.
_BUDGET_KEYS = ("budget_ceiling_cents", "budget_cents", "budget")


def budget_of(brief: Any) -> Any:
    if not isinstance(brief, dict):
        return None
    for key in _BUDGET_KEYS:
        value = brief.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _question_words(entry: dict[str, Any]) -> str:
    """The whole question, in the model's own words.

    Read in order of how sure we are they are the question: `title` first,
    `fill` last. Nothing is stripped and no frame is taken off -- since
    contract 3.16 the sentence is the agent's from end to end.
    """
    for key in _QUESTION_WORD_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _options_of(config: Any) -> list[dict[str, str]] | None:
    """A choice's options as the door reads them: {id, label}.

    A model writes them as plain strings as often as not. Turning a string
    into a labelled option invents no words -- the label IS the string -- and
    a choice whose options the door cannot read is a refused proposal.
    """
    rows = config.get("options") if isinstance(config, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    out: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if isinstance(row, dict):
            label = str(row.get("label") or row.get("title") or "").strip()
            identifier = str(row.get("id") or "").strip() or f"o{index}"
        else:
            label = str(row or "").strip()
            identifier = f"o{index}"
        if label:
            out.append({"id": identifier, "label": label})
    return out or None


def read_question(entry: Any, ordinal: int) -> dict[str, Any] | None:
    """ONE finalist question as the door reads one, or None.

    CONTRACT 3.16: {id, title, format}, and `title` is the WHOLE question in
    the model's own words. `fill` is not filed any more -- a model still
    writing one has its words moved into `title` rather than its question
    thrown away.

    A SHAPE THE DOOR DOES NOT TAKE COMES DOWN TO A TEXT BOX rather than
    spending the bid. A date, a number, a form or an upload belongs on a step
    of the plan; asked here it is refused REJ-15, and the words are still a
    fair question the person can type an answer to. A `single_choice` with
    fewer than two real options is an empty dropdown, which is a text box
    wearing a control, and the door says so -- so it becomes one.

    A `contact_picker` is DROPPED (rule 238 corrected, 2026-09-11). The bench
    refuses one here -- "The contact book is not one of your questions: the
    person picks who this goes to on a step of the plan, after they have
    chosen you, out of their own private book" -- and one refused question
    costs the agent the whole bid on a one-bid-per-want board. Better to file
    the other two than to file nothing.
    """
    if isinstance(entry, str):
        entry = {"title": entry}
    if not isinstance(entry, dict):
        return None
    fmt = str(entry.get("format") or "").strip().lower()
    if fmt == CONTACT_PICKER_FORMAT:
        # Not ours to ask. The who step is the bench's (rule 238 corrected).
        return None
    words = _question_words(entry)
    if not words:
        return None
    options = _options_of(entry.get("config"))
    if fmt in _TEXT_BOX_SPELLINGS:
        fmt = FINALIST_DEFAULT_FORMAT
    if fmt == "single_choice" and (options is None or len(options) < 2):
        fmt = FINALIST_DEFAULT_FORMAT
    if fmt not in PROPOSAL_QUESTION_FORMATS:
        fmt = FINALIST_DEFAULT_FORMAT
    out: dict[str, Any] = {
        "id": str(entry.get("id") or "").strip() or f"q{ordinal}",
        "title": words,
        "format": fmt,
    }
    if fmt == "single_choice" and options:
        out["config"] = {"options": options}
    description = entry.get("description")
    if isinstance(description, str) and description.strip():
        out["description"] = description.strip()
    return out


def read_questions(rows: Any) -> list[dict[str, Any]]:
    """Up to three questions, each a block with an id of its own. ALWAYS A
    LIST, including empty: a proposal that asks nothing and a proposal that
    asked and the harness dropped it must be tellable apart. A contact
    question is dropped here rather than filed and refused."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ordinal, entry in enumerate(rows if isinstance(rows, list) else [], start=1):
        if len(out) >= PROPOSAL_QUESTIONS_MAX:
            break
        block = read_question(entry, ordinal)
        if block is None:
            continue
        if block["id"] in seen:
            block["id"] = f"q{ordinal}"
        seen.add(block["id"])
        out.append(block)
    return out


def tool_slugs(brief: Any) -> set[str]:
    """Every slug this want's list actually publishes, exactly as written.

    Read off `brief["tools"]` whole and never through the prompt budget: the
    catalog the door matches against is the bench's, not the part of it that
    fitted in one ask. The WILDCARD row is not a slug -- `composio:<service>/
    <TOOL>` is the door a `calls` run goes through on a plan step, and the
    proposal door matches `tools_needed` exactly.
    """
    published = (brief or {}).get("tools") if isinstance(brief, dict) else None
    found: set[str] = set()
    for row in published if isinstance(published, list) else []:
        slug = str((row.get("tool") if isinstance(row, dict) else row) or "").strip()
        if slug and "<" not in slug:
            found.add(slug)
    return found


def pick_tools(named: Any, brief: Any) -> tuple[list[str], list[str]]:
    """Only the slugs this want's list carries. Returns (kept, dropped).

    WHAT FORCED IT (first fleet cycle on 0.38.1): most bids were refused
    REJ-01 -- '"composio:facebook-ads/campaign.create" is not on this want's
    tool list' -- and the names the models wrote were a PROVIDER
    ("google-gmail") or a slug they had assembled themselves
    ("slack.post.publish"). One off-list name costs the whole bid, and a
    service that is not on the list belongs on the step that uses it as an
    outside act.

    Nothing here guesses which of forty-one rows a made-up name meant. An
    exact match, the same name in another case, or the tail after a provider
    prefix -- and anything else is dropped and logged.
    """
    catalog = tool_slugs(brief)
    rows = [str(entry or "").strip() for entry in (named or [])]
    rows = [slug for slug in rows if slug]
    if not catalog:
        # No list published: this package refuses nothing on its own opinion.
        return rows, []
    by_lower = {slug.lower(): slug for slug in catalog}
    kept: list[str] = []
    dropped: list[str] = []
    for slug in rows:
        match = slug if slug in catalog else by_lower.get(slug.lower())
        if match is None:
            for separator in ("/", ":"):
                tail = slug.rsplit(separator, 1)[-1].strip()
                if tail and tail != slug:
                    match = by_lower.get(tail.lower())
                    if match:
                        break
        if match is None:
            dropped.append(slug)
        elif match not in kept:
            kept.append(match)
    return kept, dropped


def read_proposal(answer: dict[str, Any]) -> dict[str, Any]:
    """The seven fields out of the model's answer, and nothing else.

    FIELD NAMES ARE A CONTRACT. A field the door does not name is a field the
    door will not read, so anything else the model volunteered is dropped here
    rather than filed. Nothing is trimmed and nothing is invented: an `odds`
    outside 0..1 is the one coercion, because a probability is the one field
    whose range is arithmetic rather than an opinion.
    """
    if not isinstance(answer, dict):
        return {}
    holder = answer
    for key in ("proposal", "bid"):
        inner = answer.get(key)
        if isinstance(inner, dict) and any(field in inner for field in PROPOSAL_FIELDS):
            holder = inner
            break
    out: dict[str, Any] = {}
    for field in ("pitch_title", "pitch_body"):
        value = holder.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()
    odds = holder.get("odds")
    if isinstance(odds, (int, float)) and not isinstance(odds, bool):
        out["odds"] = min(1.0, max(0.0, float(odds)))
    cents = holder.get("total_ask_cents")
    if isinstance(cents, (int, float)) and not isinstance(cents, bool):
        out["total_ask_cents"] = int(cents)
    links = []
    for entry in holder.get("research_links") or []:
        if isinstance(entry, dict) and str(entry.get("url") or "").strip():
            links.append(
                {
                    "url": str(entry["url"]).strip(),
                    "note": str(entry.get("note") or "").strip(),
                }
            )
        elif isinstance(entry, str) and entry.strip():
            links.append({"url": entry.strip(), "note": ""})
    if links:
        out["research_links"] = links[:PROPOSAL_LINKS_MAX]
    # ALWAYS PRESENT, INCLUDING EMPTY (the law this bench keeps everywhere):
    # a proposal that asks the person nothing says so with `[]`, and the door
    # can tell that apart from a field that never arrived.
    out["finalist_questions"] = read_questions(holder.get("finalist_questions"))
    tools = [
        str(entry.get("tool") if isinstance(entry, dict) else entry).strip()
        for entry in (holder.get("tools_needed") or [])
        if entry
    ]
    tools = [tool for tool in tools if tool]
    if tools:
        out["tools_needed"] = tools
    return out


def mend_the_small_proposal(
    proposal: Any, brief: Any = None
) -> tuple[dict[str, Any], list[str]]:
    """Every fix this package can make to a seven-field proposal, with no
    model call. Returns the (possibly unchanged) proposal and what was mended.

    Two things, and only the two the door refuses this package for:

      * THE QUESTION SHAPES (REJ-15). `read_questions` is idempotent, so
        running it again over a proposal built by hand, carried over from an
        older contract, or handed back by another caller puts every question
        in the shape the door takes: the whole question in `title`, one of the
        three formats, options where it is a choice, and no `fill`.
      * THE TOOL LIST (REJ-01). A name the want's list does not carry comes
        off; a service that is not on the list belongs on the step that uses
        it, as an outside act.

    It does not invent a research link, a title or a price. A field that is
    simply not there is not something this package can mend.
    """
    if not isinstance(proposal, dict):
        return {}, []
    mended: list[str] = []
    out = dict(proposal)
    asked_now = out.get("finalist_questions")
    # A FLAT list only. `[[...]]` is the old whole-plan bid's shape -- one
    # group of four -- and it has a road of its own; reading it as a list of
    # questions would drop every one of them.
    if isinstance(asked_now, list) and not any(
        isinstance(entry, list) for entry in asked_now
    ):
        asked = read_questions(asked_now)
        if asked != asked_now:
            out["finalist_questions"] = asked
            mended.append(f"finalist_questions: {len(asked)} in the door's shape")
    named = out.get("tools_needed")
    if named:
        kept, off_list = pick_tools(named, brief)
        if off_list:
            if kept:
                out["tools_needed"] = kept
            else:
                out.pop("tools_needed", None)
            mended.append(
                "tools_needed: dropped " + ", ".join(off_list) + " (not on this want's list)"
            )
    return (out, mended) if mended else (proposal, [])


def bench_fixed(answer: Any) -> list[Any]:
    """What the DOOR corrected on the way in, in the door's own words.

    A trim is not a refusal and never a reason to ask the model again: the
    bench took the words, shortened what was over a cap, and said so. This
    reads that list off any answer that carries one so the runtime can log it
    and carry on.
    """
    if not isinstance(answer, dict):
        return []
    fixed = answer.get("bench_fixed")
    if isinstance(fixed, list):
        return list(fixed)
    if isinstance(fixed, dict):
        return [f"{name}: {value}" for name, value in fixed.items()]
    if isinstance(fixed, str) and fixed.strip():
        return [fixed.strip()]
    return []


def is_small_proposal(proposal: Any) -> bool:
    """True when this is the rule-243 proposal: seven fields and no steps.

    The filing road for a proposal that carries steps is the old plan-shaped
    one -- required blocks merged in, contacts bound, the blank form dropped,
    the local mirror consulted -- and every one of those repairs reads
    `steps`. A seven-field proposal has none, so none of them can run over it,
    and a harness that ran them anyway would file a plan the agent never
    wrote on a want nobody had picked it for.
    """
    if not isinstance(proposal, dict):
        return False
    if proposal.get("steps"):
        return False
    return any(field in proposal for field in PROPOSAL_FIELDS)


# ---------------------------------------------------------------------------
# STAGE TWO — THE PLAN IS A FORM (rule 244, 2026-09-11)
# ---------------------------------------------------------------------------
# ONLY THE AGENT THE PERSON PICKED IS EVER ASKED FOR A PLAN, and what it is
# asked for is a FORM: picks and short lines. The bench does the typing --
# every connect row, grant request, block title, schedule row and pointer is
# stamped from a pick by `plan_form.expand_form` -- so the form's whole
# language is twelve field names, and the door publishes the question for each
# one in plain words with its choices listed.
#
# THE FORM IS FILLED IN ONE REPLY. The blanks round and the fix round below
# still exist for what the reply left open and for the four things that can
# still be wrong about the CONTENT, but the shape of the plan arrives whole.
FORM_STEP_FIELDS = (
    "verb",
    "do_line",
    "hand_over_line",
    "need_line",
    "declared_odds",
    "proof",
    "who",
    "only_if",
    "do_ask",
    "tool",
    "repeats",
    "bid_step",
)

FORM_INSTRUCTION = (
    "The person PICKED you. Now write the plan, and the plan is a FORM you "
    "fill in ONE reply.\n"
    "`form` is the blank form, one entry per step. "
    "`answer_these_on_every_step` is the bench's own question for each blank "
    "of a step -- they are written for step 1 and the same questions are "
    "asked of every step you write -- and `and_for_the_plan` is what the plan "
    "itself asks. A question that lists `choices` is a PICK: answer with one "
    "of those words exactly as it is written. A question that names a number "
    "of characters is a CAP: the bench TRIMS a long line to it and tells you "
    "what it trimmed, it never refuses one, so write short rather than "
    "padding to fill it.\n"
    "`you_may_also_use` are the extra picks a step MAY carry. Leave out what "
    "this plan does not need; a step that carries none is a normal step.\n"
    "WRITE AS MANY STEPS AS THE PLAN NEEDS -- two or twenty, there is no cap "
    "-- so add entries to the blank form or drop them freely. You never write "
    "a connect row, a grant request, a block title, a room list, a schedule "
    "row or a pointer: the bench writes every one of those from your picks.\n"
    'Answer: {"span_days": 14, "steps": [{"verb": "finds", "do_line": "...", '
    '"hand_over_line": "...", "need_line": "", "declared_odds": 0.4, '
    '"proof": "text", "who": "agent"}, ...]}.'
)


def form_of(answer: Any) -> dict[str, Any] | None:
    """The form the door is carrying on this answer, or None."""
    if not isinstance(answer, dict):
        return None
    form = answer.get("form")
    return form if isinstance(form, dict) else None


def form_step(form: Any, index: int | None) -> Any:
    """One step of a form, by its 0-based index, or None."""
    steps = (form or {}).get("steps") if isinstance(form, dict) else None
    if not isinstance(steps, list) or index is None or not 0 <= index < len(steps):
        return None
    return steps[index]


def the_form_is_blank(answer: Any) -> bool:
    """True when the door is ASKING for the form rather than holding one.

    The outline round answers with `draft: {}` -- there is no document yet,
    only the question. Once a form has been sent, every answer carries the
    expanded plan on `draft`, and re-filling the form there would throw away
    the one the bench is holding.
    """
    if not isinstance(answer, dict):
        return True
    document = answer.get("draft")
    return not (isinstance(document, dict) and document)


def read_form(answer: dict[str, Any]) -> dict[str, Any]:
    """The FORM out of the model's answer: the steps and how long, no more.

    FIELD NAMES ARE A CONTRACT, the same law `read_proposal` keeps: the form
    has twelve fields and the door reads no others, so anything else the model
    volunteered is dropped here rather than sent. Nothing is trimmed (the door
    owns the caps and says what it cut) and nothing is invented.
    """
    if not isinstance(answer, dict):
        return {}
    holder = answer
    for key in ("form", "plan"):
        inner = answer.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("steps"), list):
            holder = inner
            break
    rows = holder.get("steps")
    if not isinstance(rows, list):
        return {}
    steps: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        step = {
            name: row[name]
            for name in FORM_STEP_FIELDS
            if name in row and row[name] not in (None, "")
        }
        # `need_line` IS THE ONE BLANK WHOSE EMPTY ANSWER IS AN ANSWER: "I
        # need nothing from you here". An empty string says that; a missing
        # key leaves the question open and the door asks it again.
        if isinstance(row.get("need_line"), str):
            step["need_line"] = row["need_line"]
        if step:
            steps.append(step)
    if not steps:
        return {}
    form: dict[str, Any] = {"steps": steps}
    span = holder.get("span_days", answer.get("span_days"))
    if isinstance(span, (int, float)) and not isinstance(span, bool):
        form["span_days"] = int(span)
    return form


# THE PLAN THAT COULD NOT BE PRESENTED (rule 245, 2026-09-11)
# ---------------------------------------------------------------------------
# The plan door now gives up out loud. Three content tries, or a draft it
# cannot take, and the answer closes with the reason `plan_failed`: the bench
# scores the agent "selected, could not present a plan", tells the person in
# red and asks them to pick another. THAT IS THE END OF THIS TARGET FOR THIS
# AGENT -- re-opening the draft cannot change it and the person has already
# moved on -- so the runtime memoizes it the way it memoizes a closed bid
# (cli._TERMINAL_DOOR_ERRORS) and never comes back.
PLAN_FAILED = "plan_failed"


def closed_reason(answer: Any) -> str:
    """The door's own word for why it closed, flattened to one string."""
    closed = (answer or {}).get("closed") if isinstance(answer, dict) else None
    if isinstance(closed, dict):
        return str(closed.get("reason") or closed.get("error") or closed.get("message") or "")
    return str(closed or "")


def closed_error(answer: Any) -> str:
    """`plan_failed` when the door named it, else the generic close."""
    reason = closed_reason(answer)
    return PLAN_FAILED if PLAN_FAILED in reason else "draft_closed"


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
        # The person's own sentence about HOW, read once off the brief in
        # `run` and printed at the top of every ask that writes words.
        self.stance: str = ""
        # What the door said it corrected on the way in, over the whole run.
        # A trim is never retried; it is logged and carried.
        self.bench_fixed: list[Any] = []

    # -- the model ---------------------------------------------------------
    # WHAT COMES OFF WHEN A TAIL RUNS LONG, IN ORDER. The example goes first
    # -- it is the biggest thing and the most helpful, and a model that loses
    # it still has a question in front of it. The outline lines go next. THE
    # QUESTION NEVER GOES: a tail that dropped the form would spend a call
    # asking nothing.
    SHED_ORDER = ("example", "outline_you_ran", "steps_you_bid", "plan")

    def _tail(
        self,
        instruction: str,
        payload: dict[str, Any],
        head: Sequence[tuple[str, Any]] = (),
    ) -> str:
        """THE STANCE AND THE EXAMPLE RIDE ON TOP (2026-09-11).

        The person's instruction about how they want this done, and the
        finished plan the bench chose to show beside the form, are printed
        ABOVE the form -- first thing in the tail, before the question -- and
        never in the prefix, which stays byte for byte the same so the
        provider cache still hits.
        """
        parts = [str(block) for _name, block in head if block]
        parts.append(instruction)
        parts.append(
            json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        )
        return "\n\n".join(parts)

    def _ask(
        self,
        instruction: str,
        payload: dict[str, Any],
        what: str = "",
        *,
        head: Sequence[tuple[str, Any]] = (),
        budget: int = PROMPT_CHAR_BUDGET,
    ) -> dict[str, Any]:
        """One question: the stable prefix as the system, the variable tail as
        the message. Every call is measured, and what the cache did is logged
        where the provider reports it."""
        head = list(head)
        tail = self._tail(instruction, payload, head)
        if len(tail) > budget:
            # THE QUESTION IS THE LAST THING STANDING. Shed in the published
            # order -- the example, then the outline lines -- and say what
            # went, rather than quietly spending a window on a prompt this
            # loop was built to keep small.
            payload = dict(payload)
            for name in self.SHED_ORDER:
                if len(tail) <= budget:
                    break
                before = len(tail)
                shed_head = [row for row in head if row[0] != name]
                shed = len(shed_head) != len(head)
                head = shed_head
                if name in payload:
                    payload.pop(name)
                    shed = True
                if not shed:
                    continue
                tail = self._tail(instruction, payload, head)
                self.log.info(
                    "draft loop tail was %d characters (budget %d); dropped "
                    "`%s` and it is now %d",
                    before,
                    budget,
                    name,
                    len(tail),
                )
        if len(tail) > budget:
            # A loop prompt should never come near this. Say so out loud
            # rather than quietly spending a window on it.
            self.log.warning(
                "draft loop tail ran to %d characters (budget %d) with nothing "
                "left to shed but the question; the loop's prompts are small "
                "by design, so this is worth reading",
                len(tail),
                budget,
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

    def _head(self, answer: Any = None) -> list[tuple[str, str]]:
        """THE TWO THINGS THAT GO ABOVE THE FORM, in order.

        The stance line -- the person's own sentence about how they want this
        done -- then the bench's example plan when the door's answer carries
        one. Both are the person's or the bench's words, printed as they
        stand; neither is summarised here.
        """
        head: list[tuple[str, str]] = []
        # THE DOOR'S OWN STANCE LINE WINS. The bench renders the person's
        # three sliders into one sentence and sends it on every plan answer;
        # the one built off the raw sliders here is what an older bench gets.
        stance = stance_of(answer) or self.stance
        if stance:
            head.append(("stance", f"{STANCE_LABEL}: {stance}\n{STANCE_NOTE}"))
        example = example_plan(answer)
        if example:
            head.append(
                (
                    "example",
                    "A FINISHED PLAN FOR A WANT LIKE THIS, accepted, as the "
                    "bench filled it in. Yours is for THIS want, in your own "
                    "words -- copy the shape, never the sentences.\n"
                    + json.dumps(example, separators=(",", ":"), default=str),
                )
            )
        return head

    # -- the log -----------------------------------------------------------
    def _note_bench_fixed(self, target_id: str, kind: str, answer: Any) -> list[Any]:
        """A CORRECTION IS NOT A REFUSAL (2026-09-11).

        The door trims a long title or paragraph, raises an odds line that
        falls, and says what it did on `bench_fixed`. The runtime writes that
        to the log and carries on -- it never asks the model again over it.
        Asking again is what the old door did by refusing a cap, and it is why
        the strongest model on the fleet died five problems from filing.
        """
        fixed = bench_fixed(answer)
        if not fixed:
            return []
        self.bench_fixed.extend(fixed)
        self.log.info(
            "draft loop %s target=%s: the bench corrected %d thing(s) on the "
            "way in and took it anyway: %s",
            kind,
            target_id,
            len(fixed),
            "; ".join(self._preview(entry) for entry in fixed),
        )
        return fixed

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
        fixed = self._note_bench_fixed(target_id, kind, answer)
        if fixed:
            line["bench_fixed"] = fixed
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
    # The third consecutive round on the SAME (path, code) ends the draft.
    # Two is the better prompt (see `_answer_the_fixes`); three is an answer.
    SAME_PROBLEM_ROUNDS = 3

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
        if answer.get("next"):
            # THE DOOR IS ASKING FOR SOMETHING AND A ROW STANDS BEHIND THE
            # QUESTION. The outline round carries an empty `draft` on purpose
            # -- there is no document yet, only the ask -- and PUTting over it
            # would spend a round to be asked the same thing again.
            self.rounds = int((answer.get("rounds") or {}).get("used") or 0)
            self._record(target_id, kind, answer, "resume")
            return self.STANDING, answer
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

    # -- stage two: the form -----------------------------------------------
    def _the_form_ask(
        self, answer: dict[str, Any], want: Any, strategy: Any, brief: Any
    ) -> dict[str, Any]:
        """THE QUESTIONS ONCE, NOT ONCE PER STEP.

        The door names every blank of every step -- seven a step, so a
        seventeen-step form asks 120 questions and they are the same seven
        questions seventeen times. They go in ONCE, in the bench's own words,
        with the form beside them; sending all 120 would spend the whole
        prompt budget saying one thing.
        """
        groups = group_blanks(answer.get("blanks"))
        plan_rows = [row for index, rows in groups if index is None for row in rows]
        step_rows: list[Any] = []
        for index, rows in groups:
            if index is not None:
                step_rows = rows
                break
        payload: dict[str, Any] = {
            "want": want,
            "what_the_person_said": strategy,
            # The agent's own proposal and the person's answers to its
            # questions, as the door put them: the plan is built from those.
            "your_proposal": (
                answer.get("your_proposal") or answer.get("steps_you_bid") or []
            ),
            "the_person_answered": (
                answer.get("the_person_answered") or answer.get("selection_answers") or []
            ),
            "form": form_of(answer) or {},
            "answer_these_on_every_step": [self._blank_row(row) for row in step_rows],
            "and_for_the_plan": [self._blank_row(row) for row in plan_rows],
            "you_may_also_use": answer.get("optional_picks") or [],
        }
        send = answer.get("send") or ""
        if send:
            payload["how_to_send_it"] = send
        if self._tools_ride_the_outline:
            # The `tool` pick comes off this want's list, and on a provider
            # that caches nothing the list is not in the prefix.
            payload["tools_on_this_want"] = tools_index(brief)
        return payload

    def _fill_the_form(
        self,
        target_id: str,
        kind: str,
        answer: dict[str, Any],
        want: Any,
        strategy: Any,
        brief: Any,
    ) -> tuple[dict[str, Any], str]:
        """ONE MODEL CALL, and the form goes in. (answer, "" | "form")."""
        payload = self._the_form_ask(answer, want, strategy, brief)
        # LAW A: what the person said rides this ask too.
        instruction = with_the_person_said(FORM_INSTRUCTION, payload, answer)
        form = read_form(
            self._ask(
                instruction,
                payload,
                "form",
                head=self._head(answer),
                budget=FORM_CHAR_BUDGET,
            )
        )
        if not form.get("steps"):
            self.log.warning(
                "draft loop %s target=%s: the door asked for the form and the "
                "model answered with no steps",
                kind,
                target_id,
            )
            return answer, "form"
        self.log.info(
            "draft loop %s target=%s: the form came back with %d step(s) and "
            "span_days=%s",
            kind,
            target_id,
            len(form["steps"]),
            form.get("span_days"),
        )
        return self._put(target_id, kind, {"form": form}), ""

    def _step_for(self, answer: Any, index: int | None) -> Any:
        """THE STEP A BLANK IS ON.

        On a form draft that is the FORM's step, never the expanded
        document's: the expander stamps connect steps of its own in front of
        the ones the agent wrote (rule 236, rule 242), so `steps.3` of the
        document and step 4 of the form are not the same step.
        """
        form = form_of(answer)
        if form is not None:
            step = form_step(form, index)
            if step is not None:
                return step
        return step_context((answer or {}).get("draft"), index)

    # -- stage one: the proposal -------------------------------------------
    def _propose(
        self,
        target_id: str,
        brief: Any,
        idempotency_key: str,
        file: bool,
    ) -> dict[str, Any]:
        """THE PROPOSAL IS ONE CALL (rule 243).

        The want, the stance line, the questions the person will answer and
        the tools this want offers go in; seven fields come back; the bench's
        own proposal door takes them. No outline, no blanks, no fixes: there
        is no plan here to fix. A long title or paragraph is the DOOR's to
        trim, and what it trimmed comes back on `bench_fixed` and is logged,
        never retried.
        """
        payload: dict[str, Any] = {
            "want": (brief or {}).get("want") if isinstance(brief, dict) else None,
            "what_the_person_said": person_strategy(brief),
            "budget_cents": budget_of(brief),
            "tools_on_this_want": tools_index(brief),
        }
        if self._tools_ride_the_outline:
            # Nothing caches here, so the rules ride the one call that makes
            # the choice rather than a prefix nobody is charged less for.
            payload["the_rules"] = SHORT_FRONT_DOOR
        proposal = read_proposal(
            self._ask(PROPOSAL_INSTRUCTION, payload, "proposal", head=self._head())
        )
        if not proposal.get("pitch_title") and not proposal.get("pitch_body"):
            return self._gave_up(
                target_id,
                "bid",
                "no_proposal",
                "The model was asked for a proposal and answered with no "
                "title and no paragraph.",
            )
        # THE TOOL LIST IS THE WANT'S, NOT THE MODEL'S (REJ-01). One name the
        # list does not carry costs the whole bid, so an off-list name comes
        # off here and is logged; a service that is not on the list belongs on
        # the step that uses it, as an outside act.
        kept, off_list = pick_tools(proposal.get("tools_needed"), brief)
        if off_list:
            self.log.warning(
                "proposal for target=%s named %d tool(s) this want does not "
                "offer; dropped %s (a service off the list goes on the step "
                "that uses it, never in tools_needed)",
                target_id,
                len(off_list),
                ", ".join(off_list),
            )
        if kept:
            proposal["tools_needed"] = kept
        else:
            proposal.pop("tools_needed", None)
        # LINKS ARE REQUIRED (REJ-31) AND THE MODELS FILE NONE. One more small
        # ask rather than a bid the door has already told us it will refuse.
        if not proposal.get("research_links"):
            proposal = self._links_for(target_id, brief, proposal)
        if not proposal.get("research_links"):
            return self._gave_up(
                target_id,
                "bid",
                "no_research_links",
                "research_links is required (1..3 entries of {url, note}) and "
                "the model gave none, twice. Nothing was filed: the door has "
                "already said it would refuse this, and a bid spent on a "
                "refusal is the round.",
                proposal,
            )
        self.log.info(
            "proposal for target=%s: %d of %d fields, %d link(s), %d "
            "question(s), %d tool(s)",
            target_id,
            len(proposal),
            len(PROPOSAL_FIELDS),
            len(proposal.get("research_links") or []),
            len(proposal.get("finalist_questions") or []),
            len(proposal.get("tools_needed") or []),
        )
        if not file:
            return {
                "ok": True,
                "filed": False,
                "dry_run": True,
                "kind": "bid",
                "draft": proposal,
                "proposal": proposal,
                "rounds": 0,
                "model_calls": self.calls,
                "trail": self.trail,
            }
        filed = self.provider.submit_proposal(target_id, proposal, idempotency_key)
        filed = filed if isinstance(filed, dict) else {}
        fixed = self._note_bench_fixed(target_id, "bid", filed)
        out: dict[str, Any] = {
            "ok": bool(filed.get("ok")),
            "filed": bool(filed.get("ok")),
            "kind": "bid",
            "response": filed,
            "proposal": proposal,
            "proposal_id": filed.get("proposal_id"),
            "rounds": 0,
            "model_calls": self.calls,
            "trail": self.trail,
        }
        if fixed:
            out["bench_fixed"] = fixed
        if not out["ok"]:
            out["error"] = filed.get("error") or "proposal_refused"
            out["message"] = filed.get("message") or ""
            self.log.warning(
                "proposal for target=%s was not filed: %s (%s)",
                target_id,
                out["error"],
                self._preview(out["message"]),
            )
        return out

    def _links_for(
        self, target_id: str, brief: Any, proposal: dict[str, Any]
    ) -> dict[str, Any]:
        """ONE more small ask, for the links and nothing else.

        WHAT FORCED IT (first fleet cycle on 0.38.1): the models filed zero
        research links on nearly every want, and `research_links` is a
        required field -- so the validate door refused the proposal before it
        was filed and the filing door refused it again. A field the harness
        can get by asking for it is worth one more ask; the alternative is
        spending the want's one bid on a refusal.
        """
        answer = self._ask(
            LINKS_INSTRUCTION,
            {
                "want": (brief or {}).get("want") if isinstance(brief, dict) else None,
                "your_proposal": {
                    key: proposal.get(key)
                    for key in ("pitch_title", "pitch_body")
                    if proposal.get(key)
                },
            },
            "research links",
            head=self._head(),
        )
        links = read_proposal(answer).get("research_links") or []
        if links:
            self.log.info(
                "proposal for target=%s carried no links; one more ask found "
                "%d",
                target_id,
                len(links),
            )
            return {**proposal, "research_links": links}
        return proposal

    def _open(self, target_id: str, kind: str) -> dict[str, Any]:
        """THE ONE PUT, AND IT IS EMPTY (rule 243 + rule 113).

        A PROPOSAL never comes through this door any more -- it is one model
        call and one filing (`_propose`) -- so the only draft this loop opens
        is a PLAN's, and a plan is opened EMPTY on purpose: the bench answers
        with the form (`next: "form"`), or on an older bench with the steps
        already filed beside the person's answers (`next: "outline"`). Either
        way what comes next is the door's to name, and `_follow` follows it.
        """
        return self._put(target_id, kind, {})

    def _follow(
        self,
        target_id: str,
        kind: str,
        answer: dict[str, Any],
        want: Any,
        strategy: Any,
        brief: Any = None,
    ) -> tuple[dict[str, Any], str]:
        """WHAT THE DOOR NAMES NEXT IS WHAT HAPPENS NEXT.

        Two names today. `next: "form"` is the plan door since rule 244: the
        bench has put the agent's own proposal, the person's answers, the
        stance line, one finished example and the BLANK FORM in front of us
        and wants the form back filled. `next: "outline"` is the older plan
        door, which wants the steps back before it builds a template. An
        answer with no `next` -- and a `form` answer where a form already
        stands -- is handed back untouched, because the rest of the loop is
        what comes next.

        Returns (answer, what was asked for and never came back).
        """
        if not isinstance(answer, dict):
            return answer, ""
        named = str(answer.get("next") or "")
        if named == "form":
            if not the_form_is_blank(answer):
                return answer, ""
            return self._fill_the_form(target_id, kind, answer, want, strategy, brief)
        if named != "outline":
            return answer, ""
        bid_steps = [s for s in (answer.get("steps_you_bid") or []) if isinstance(s, dict)]
        # AN AGENT'S OWN WINS ARE ITS SHELF. A want that needs the tools of a
        # job this agent already won is that job again in other words, so
        # where the door asks for an outline the ask starts from the shape
        # that worked and only asks what changes. (This is the older plan
        # door. The current one hands over a form and one finished example of
        # its own, which is the same idea in the bench's hands.)
        seed, _score = self._seed(brief)
        payload = self._outline_payload(want, strategy, brief, seed)
        payload["steps_you_bid"] = bid_steps
        payload["the_person_answered"] = (
            answer.get("the_person_answered") or answer.get("selection_answers") or []
        )
        outline = read_outline(
            self._ask(
                SEEDED_OUTLINE_INSTRUCTION if seed else PLAN_OUTLINE_INSTRUCTION,
                payload,
                "plan outline seeded" if seed else "plan outline",
                head=self._head(answer),
            )
        )
        if not outline.get("steps") and seed:
            # The model was shown a shape that worked and answered with
            # nothing. The shape is still the best thing anyone has for this
            # want, so it goes in as it stands rather than costing the plan.
            self.log.warning(
                "draft loop: the adjust call answered with no steps; sending "
                "the plan that was accepted before, unchanged"
            )
            outline = seed
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
            return answer, "outline"
        self.log.info(
            "draft loop %s target=%s: outline round -- %d step(s) bid, %d "
            "answer(s) from the person, %d step(s) sent back",
            kind,
            target_id,
            len(bid_steps),
            len(payload["the_person_answered"]),
            len(outline["steps"]),
        )
        return self._put(target_id, kind, outline), ""

    # -- the pieces --------------------------------------------------------
    # THE FORM'S OWN WORDS, VERBATIM (2026-09-11). The plan door no longer
    # hands back a bare path and a note: a blank is a QUESTION in plain words
    # ("Step 1, what do you do?") and, where the answer is a pick, the
    # CHOICES it may be answered with ("finds, prepares, does, posts, buys,
    # books, checks, emails, calls, meeting, waits, confirms, reviews"). Both
    # are the bench's, both go in front of the model exactly as they came,
    # and this module neither rewords them nor invents a choice list of its
    # own -- a runtime that paraphrased the form would be answering a
    # different question than the one the door will mark.
    _BLANK_KEYS = (
        ("question", "question"),
        ("choices", "choices"),
        ("note", "what_belongs_there"),
        ("example", "example"),
        ("cap", "up_to_characters"),
        ("kind", "kind"),
    )

    def _blank_row(self, row: Any) -> dict[str, Any]:
        row = row if isinstance(row, dict) else {}
        out: dict[str, Any] = {"path": row.get("path")}
        for name, label in self._BLANK_KEYS:
            value = row.get(name)
            if value not in (None, "", [], {}):
                out[label] = value
        # THE SAME SENTENCE TWICE IS NOT TWO SENTENCES (2026-09-11). The plan
        # door sends `note` as a copy of `question` (draft_door.form_answer
        # builds the blanks that way), so every blank arrived carrying its own
        # words twice -- seven questions a step, doubled, on the one ask that
        # is meant to fit the whole plan.
        if out.get("what_belongs_there") == out.get("question"):
            out.pop("what_belongs_there", None)
        out["required"] = bool(row.get("required"))
        return out

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
                "step": _fit(self._step_for(answer, index)),
                "blanks": [self._blank_row(row) for row in rows],
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
                    head=self._head(answer),
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

        Advancing the draft is useful work. Being named for the same problem
        over and over is not: the THIRD consecutive round on the same
        (path, code) stops the draft and lets the watcher move to another
        target. There is no total step/round cap.

        THE GUARD IS THE PROBLEM'S NAME, NOT THE DRAFT'S TEXT (2026-09-11).
        It used to hash the whole draft with the next fix and the remaining
        count, so a model that REWORDED the same bad field looked like
        progress and the guard never tripped: the document changed every
        round while the bench named the same field every round. On 2026-09-11
        GPT-6 Astra died that way on `research_links[0].plan_use` -- two
        identical asks, two rewordings, no end -- and Jan, Alice and Bobby
        each burned the full 200-round ceiling on one want. Now the count is
        keyed on the (path, code) the bench names, and a reworded answer to
        the same named problem counts as the repeat it is.

        WHAT THE FIRST REPEAT GETS IS A BETTER PROMPT, NOT A LIMIT. When the
        bench names the same path with the same code twice running, the second
        ask carries what was sent last round and what the bench has for that
        path now. Live on 2026-09-09, Greg spent rounds 123-132 on one REJ-15
        on `finalist_questions.0.0`, asked the same question in the same words
        every time, and answered it the same way every time. Only the THIRD
        naming of that same pair stops the draft.
        """
        last_named: tuple[str, str] | None = None
        named_before: dict[tuple[str, str], int] = {}
        while (
            not answer.get("ready")
            and not answer.get("closed")
            and not _spent(answer)
            and isinstance(answer.get("next_fix"), dict)
        ):
            fix = answer["next_fix"]
            path = str(fix.get("path") or "")
            code = str(fix.get("code") or "")
            named_before[(path, code)] = named_before.get((path, code), 0) + 1
            if named_before[(path, code)] >= self.SAME_PROBLEM_ROUNDS:
                return dict(
                    answer,
                    ok=False,
                    error="draft_stalled",
                    message=(
                        f"The bench named {path or 'the same path'} "
                        f"({code or 'no code'}) three times and the patches "
                        "did not clear it; paused to avoid repeated model "
                        "spending."
                    ),
                )
            index = step_of(path)
            # THE WHOLE STEP, OR ONE LINE OF IT (0.38.3). `form.steps.2` with
            # no field after it is the STEP being named, and rewording a line
            # of it cannot clear that.
            whole = whole_step_path(path)
            repeated = (path, code) == last_named and bool(path)
            # A PROBLEM COMES BACK AS A QUESTION, NOT A CODE (2026-09-11).
            # Where the door asks one -- "Step 1, what do you do? Choose one:
            # finds, prepares, ..." -- the question and its choices ride in
            # front of the model exactly as the door wrote them. `code` and
            # `fix` still ride for a door that speaks the old way; neither is
            # reworded here.
            fix_this: dict[str, Any] = {
                "path": fix.get("path"),
                "current_value": fix.get("current"),
                "code": fix.get("code"),
                "fix": fix.get("fix"),
                "detail": fix.get("detail"),
            }
            for name in ("question", "choices", "cap", "kind"):
                value = fix.get(name)
                if value not in (None, "", [], {}):
                    fix_this[name] = value
            payload: dict[str, Any] = {
                "want": want,
                "fix_this": fix_this,
                "step_number": None if index is None else index + 1,
                # The plan in one line per step, and the ONE step being
                # changed. Never the draft: a thirty-step document in front of
                # a one-field fix is the cost this loop exists to avoid.
                "plan": outline_summary(answer.get("draft")),
                "step": _fit(self._step_for(answer, index)),
            }
            # THE TWO EXITS, and the whole step to choose between them with.
            # A step-level problem is answered by replacing the step or
            # dropping it, so the ask carries EVERY field of it -- `_fit`
            # sheds keys, and a field the model cannot see is a field it
            # cannot fill in when it sends the step back.
            if whole is not None or (repeated and index is not None):
                payload["step"] = self._whole_step_for(answer, index)
                payload["steps_path"] = _step_path(path, index)
                payload["step_number"] = index + 1 if index is not None else None
            # SAME PATH, SAME CODE AS LAST ROUND: say so, and hand back what
            # was sent beside what the bench has now. No strike rule and no
            # round limit -- a model that is told its last answer did not land,
            # and shown it, can send something else; a model that is asked the
            # same question in the same words answers it the same way.
            instruction = FIX_INSTRUCTION
            if whole is not None:
                instruction = WHOLE_STEP_FIX_INSTRUCTION
            if code == RESTATES_THE_PICK and (whole is not None or index is not None):
                instruction = RESTATING_STEP_INSTRUCTION + instruction
            if repeated:
                # REWORDING DID NOT WORK. Say it plainly and name both exits,
                # whichever path the bench used: the prod loop on 2026-09-11
                # was named `form.steps.0.do_line` three times and patched
                # that one line three times ("picks" -> "selects") while the
                # verb and the hand-over line kept the check true.
                head = REPEATED_FIX_INSTRUCTION
                if index is not None:
                    head = REPEATED_STEP_EXITS_INSTRUCTION
                    if code == RESTATES_THE_PICK:
                        head = RESTATING_STEP_INSTRUCTION + head
                instruction = head + instruction
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
            reply = self._ask(
                instruction, payload, f"fix {path or '?'}", head=self._head()
            )
            patches = read_patches(reply)
            # THE STEP COMES OUT (0.38.3). A drop is not a patch: it is the
            # door's own instruction, and it is the second exit on every
            # step-level problem.
            dropping = read_drop(reply, index)
            if dropping is None and (whole is not None or repeated) and index is not None:
                # A WHOLE-STEP QUESTION ANSWERED WITH ONE FIELD is the wrong
                # shape: ask once more with the step in front of it, and take
                # the field patch only if the second answer is the same shape.
                if whole is not None and _is_a_field_patch(patches, path):
                    self.log.info(
                        "draft loop %s target=%s: %s was answered with one "
                        "field of the step; asking once more for the whole "
                        "step or a drop",
                        kind,
                        target_id,
                        path,
                    )
                    reply = self._ask(
                        WHOLE_STEP_FIX_INSTRUCTION + instruction,
                        payload,
                        f"fix {path} (whole step)",
                        head=self._head(),
                    )
                    patches = read_patches(reply)
                    dropping = read_drop(reply, index)
            if dropping is not None:
                answer = self._drop_step(target_id, kind, dropping, path, code)
                continue
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
                              f"fix {path or '?'} (again)", head=self._head())
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

    def _whole_step_for(self, answer: Any, index: int | None) -> Any:
        """EVERY field of one step, un-shed.

        `_fit` drops the heavy keys to keep a one-field ask small, which is
        right for a one-field ask and wrong here: a step being replaced whole
        has to be seen whole, or the fields the model cannot see come back
        empty.
        """
        return self._step_for(answer, index)

    def _drop_step(
        self, target_id: str, kind: str, index: int, path: str, code: str
    ) -> dict[str, Any]:
        """THE SECOND EXIT: take the step out through the door's own door.

        Not a patch -- `{"kind": "plan", "drop": {"step": N}}` on the same
        PATCH call -- so the bench renumbers what is left and answers with its
        next problem, exactly as it does after a patch.
        """
        self.log.info(
            "draft loop %s target=%s round=%d drop step %d (%s, %s)",
            kind,
            target_id,
            self.rounds + 1,
            index + 1,
            path or "?",
            code or "no code",
        )
        self._sent.append((path, {"drop": {"step": index}}))
        answer = self.provider.drop_draft_step(target_id, index, kind=kind)
        self.rounds += 1
        self._record(target_id, kind, answer, f"drop step {index + 1}")
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
        """ONE WANT, ONE ANSWER, and which road depends on the stage.

        `kind="bid"` is a PROPOSAL: one model call, one filing, no draft door
        (rule 243). `kind="plan"` is the form: read what stands, open an empty
        draft if nothing does, do whatever the door names next -- the form, or
        an outline on an older bench -- then the blanks it left open, then the
        content it named, then file.

        `file` false runs the whole thing and stops at the door: the document
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
        self.stance = stance_line(brief)
        # TWO STAGES (rule 243, 2026-09-11). A PROPOSAL is the agent's short
        # answer to a want and it is ONE CALL. A PLAN is the form, and only
        # the agent the person picked is ever asked for one.
        if kind == "bid":
            return self._propose(target_id, brief, idempotency_key, file)
        # READ FIRST, EVERY CYCLE. This is the loop's first step and the only
        # thing that decides whether an outline goes out at all.
        state, answer = self._read_first(target_id, kind)
        if state == self.OVER:
            return self._gave_up(
                target_id, kind,
                closed_error(answer) if answer.get("closed") else
                str(answer.get("error") or "draft_unreadable"),
                str(
                    answer.get("closed")
                    or answer.get("message")
                    or "The draft on this want could not be read, so nothing was sent."
                ),
                answer,
            )
        if state == self.NOTHING_STANDING:
            answer = self._open(target_id, kind)
        # THE DOOR SAYS WHAT COMES NEXT. Read it before assuming a template.
        answer, unanswered = self._follow(
            target_id, kind, answer, want, strategy, brief
        )
        if unanswered:
            return self._gave_up(
                target_id, kind, f"no_{unanswered}",
                f"The door asked for the {unanswered} and the model gave none.",
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
                closed_error(answer) if answer.get("closed") else "draft_not_ready",
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
