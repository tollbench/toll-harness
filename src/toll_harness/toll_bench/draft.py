"""THE DRAFT LOOP — the proposal in one call, the plan as one form.

TWO STAGES (rules 243-245, Steven Ochs, 2026-09-11). A PROPOSAL is the agent's
short answer to a want -- eight fields, ONE call, no steps -- and it is what
the person chooses between. A PLAN is owed by the agent that was CHOSEN, it is
a FORM the bench hands over, and the loop below is how that form gets filled.
So `run(kind="bid")` is one model call and one filing; `run(kind="plan")` is
the loop. What forced the split: the agent was being asked to think up a plan
AND type it into a 130-slot form under 44 rejection rules, one blank at a
time, before anyone had picked it -- so most of that work was thrown away, and
on 10-11 September the fleet spun.

THE PLAN ROAD, AND THE ONLY ONE (0.74.0, harness thinness audit item 8):

  1. READ FIRST (`_read_first`, 0.35.1): a PUT replaces the draft the bench is
     holding and sets the rounds back to zero, so a standing draft is resumed
     and a closed one is left alone. Nothing standing: ONE empty PUT opens it.
  2. THE FORM (`_fill_the_form`): the door answers `next: "form"` with the
     blank form, the person's answers, the stance and one finished example;
     ONE model call fills the whole form and ONE PUT sends it back.
  3. EVERY RED ROW, EVERY ROUND (`_fix_every_red_row`, 0.68.0): the door names
     every red row on `problems` -- step, slot, problem, its own sentence
     (`say`), the path and what it takes (`accepted`). ONE model call sees the
     whole form and every row and answers with the whole form; ONE PUT sends
     it back. Until the door says `ready`, closes, or pauses.
  4. FILE what the bench holds (`file_plan_from_draft`).

Nothing is fixed at home: no outline round, no one-row patches, no key taken
off, no who step inserted, no path aimed (all deleted in 0.74.0). A bench
below contract 4.0 is refused before this loop runs (PLAN_DOOR_MIN_CONTRACT).

THE BOUNDS ARE THE BENCH'S. The bench caps the rounds and expires a draft; the
only number here is the stall guard: the bench's own key for a row, named a
third time in one run, ends the draft (SAME_PROBLEM_ROUNDS).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, NamedTuple

from toll_harness.core import thinking
from toll_harness.core.operator_block import with_operator_block
from toll_harness.core.own_means import (
    OWN_MEANS_PLAN_RULE,
    OwnMeans,
    with_own_means,
)
from toll_harness.core.types import ModelMessage
from toll_harness.models.base import (
    asked_again_over_a_cut_off,
    empty_reply_note,
    was_cut_off,
    what_came_back,
)

_LOGGER = logging.getLogger("toll_harness.draft")

# ONE NAME FOR SENDING AN EMAIL (bench contract 4.0, 2026-09-17). The bench
# takes the provider-shaped names quietly and no longer teaches them: the
# person's connected account decides which mail service actually runs, so an
# agent that writes the provider into the plan is guessing at something it
# cannot know. `form_steps[].action` on every plan answer reads this name.
EMAIL_SEND = "email.send"


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
    '"' + EMAIL_SEND + '", "on": "google-gmail"}. SENDING AN EMAIL IS '
    '"' + EMAIL_SEND + '" -- one name, whatever mail service the person has '
    "connected; the platform picks the connector off their account. A step "
    "that touches nothing "
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
# WHAT YOUR OPERATOR GAVE YOU (bench contract 4.1.43; 0.66.0). The brief and
# every plan-door answer carry `flow_materials`: the recipe, notes, links,
# documents and pictures the operator put in the materials box for this flow.
# No prompt here carried them, so Dr. Moose's first plan asked the person for
# the recipe his operator had already given him (2026-10-21). They ride the
# outline ask, the form ask and the proposal ask under one key, compact and
# whole (0.74.0: no budget of their own).
MATERIALS_KEY = "what_your_operator_gave_you"
# The form ask has a length test of its own (`test_the_words_stay_short`), so
# it carries the short form; the outline asks carry the whole sentence.
MATERIALS_SENTENCE_SHORT = (
    "`what_your_operator_gave_you`, when present, is your operator's recipe "
    "for this flow: plan FROM "
    "it, never ask the person for what it holds; a picture's `public_url` may "
    "be an option's `image_url`."
)


def operator_materials(brief: Any, answer: Any = None) -> list[dict[str, Any]] | None:
    """The operator's materials, compact, or None when there are none.

    Read off the plan-door `answer` first (it is the newer copy), then the
    brief. Each row keeps id, kind, title, url, public_url, use_in_flow and
    its inline text, whole; `text_trimmed` is the bench's own flag.
    """
    rows: Any = None
    for source in (answer, brief):
        if isinstance(source, dict) and isinstance(source.get("flow_materials"), list):
            rows = source["flow_materials"]
            if rows:
                break
    if not rows:
        return None
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {
            key: item.get(key)
            for key in ("id", "kind", "title", "url", "public_url", "use_in_flow")
            if item.get(key) is not None
        }
        text = item.get("text")
        trimmed = bool(item.get("text_trimmed"))
        if isinstance(text, str) and text:
            row["text"] = text
        if trimmed:
            row["text_trimmed"] = True
        out.append(row)
    return out or None


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


# THE WHO STEP IS THE AGENT'S PICK (rule 238, amended 2026-09-12).
#
# WHAT FORCED IT: on production, 2026-09-12, the bench INSERTED its own who
# step after the agent's step 4 and then refused its own document as "step 5"
# -- a step the agent never wrote. So the bench stopped inserting. The agent
# picks the step like it picks `emails` or `meeting`, and a plan that reaches
# somebody with no who step above it is refused, and the step that puts one in
# is the agent's to send. Step numbers never move under the agent again.
#
# NOTHING HERE READS A CODE ANY MORE (contract 4.0), and nothing here puts a
# who step in for the agent (0.74.0): the door's own row says the step is
# missing, and the model writes it into the form.


# THE WHO HOLE NAMES ITS FILLER (0.64.0, bench contract 4.1.9 / 4.1.12).
# `from_step` on the who step says where the people come from: an earlier
# step's number (its hand-back), "contact_book" (the person's own list, the
# default) or "found_by_research" (the bench stamps the research step in the
# who step's place, the agent hands back the finds as cards, the person picks).
# WHAT FORCED SAYING IT: this sentence said the opposite ("never plan a step
# to find, get or list the people") for a month after the bench opened the
# slot. Ali (prod, 2026-10-12) planned "Pick 1 from your Contact book" on "I
# want Toll Bench featured in an AI newsletter or podcast", a want whose
# person has no list of editors; Kai's bookstores walk (2026-10-02) was the
# same hole. The form carries the word as written; the door judges it.
# The three fillers, spelled the bench's way. Said to the model, never checked.
WHO_FROM_CONTACT_BOOK = "contact_book"
WHO_FROM_RESEARCH = "found_by_research"

WHO_STEP_SENTENCE = (
    "WHO IT GOES TO IS A STEP, AND THE STEP IS YOURS TO PUT IN (rule 238). In "
    "front of the FIRST step that reaches a person (an email, a meeting, a "
    "call, any send whose recipient the person chooses) write one step of its "
    'own: {"verb": "who", "who": "person", "declared_odds": <your number>}. '
    "Its `from_step` names where the people come from: an earlier step's "
    'number, "contact_book" (the person\'s own list, the default), or '
    '"found_by_research" when they are people the person does not have yet '
    "(reporters, editors, buyers, hosts, anyone the want asks you to find): "
    '{"verb": "who", "who": "person", "declared_odds": <your number>, '
    '"from_step": "found_by_research"}. The bench then stamps the research '
    "step there; your job on it is the finds, as cards (name, role, email, "
    "source_url), and the person picks from them. The bench writes every "
    "other word. ONE who step per plan; every send below it reads the same "
    "picks. Do not plan a separate find step: put the word on the who step. "
    "A plan that reaches somebody with no who step above it is refused.\n"
)


LAST_TRY_INSTRUCTION = (
    "THIS IS THE LAST TRY THE BENCH GIVES THIS ROW. If this answer does not "
    "clear it, the plan closes and the person is asked to pick someone else. "
    "Read the bench's sentence once more and fix exactly what it says.\n"
)


# EVERY RED ROW, EVERY ROUND (0.68.0).
#
# WHAT FORCED IT (lab, 2026-10-22). After the form went in, the loop asked
# the model about ONE row, patched it, read the door's answer, and asked
# about the next one: thirteen model calls for one lab model's plan, each
# carrying the same prefix, while the door had named every red row at once
# with its step and takes a whole form in one PUT ("fix what's red, as many
# as you can, then file again"). So each round now hands the model the whole
# form and the whole red list, and files the whole answer. Short and plain,
# because weak models are in this fleet.
WHOLE_FORM_FIX_INSTRUCTION = (
    "The bench read your plan. `form` is the plan as the bench holds it and "
    "`red` is EVERY row it marked, all at once: the `step` it is on (counted "
    "from 1; none means the plan itself), the `slot`, what is wrong "
    "(`problem`), the bench's own sentence (`say`), the `path` and what it "
    "takes (`accepted`). `what_goes_in` is the bench's table of inputs for "
    "those steps: each input, who fills it, and whether it is still needed.\n"
    "Fix EVERY red row you can in this ONE reply, then send the WHOLE form "
    "back: `overview`, `odds`, `span_days` and every step in order, with the "
    "steps that were fine exactly as they are. A pick takes one of its "
    "`accepted` words, verbatim. To take a step out, leave it out and put its "
    "number in `drop`; to add one, put it where it goes. Fill only what is "
    "yours: never write who a message goes to or who it comes from.\n"
    'Answer: {"form": {"overview": "...", "odds": 0.35, "span_days": 14, '
    '"steps": [{"verb": "...", "do_line": "...", ...}, ...]}, "drop": []}.'
)

WHOLE_FORM_STILL_RED_INSTRUCTION = (
    "YOUR LAST FORM DID NOT CLEAR THE ROWS IN `still_red`. The same words "
    "will not clear them a second time: change what the bench's sentence "
    "names, or take the step out.\n"
)

# A REPLY THE OUTPUT LIMIT CUT OFF IS ASKED ONCE MORE, EMPTY OR SLICED
# (0.68.0). WHAT FORCED IT: the lab's thinking models spent most of their
# output allowance reasoning and were stopped part way through the plan's
# JSON; the harness retried a cut-off reply only when it came back EMPTY, so a
# sliced plan read as no plan at all and the attempt was lost on one reply.
CUT_OFF_AGAIN_INSTRUCTION = (
    "YOUR LAST REPLY WAS CUT OFF by the output limit before it was finished. "
    "Answer again with the JSON object alone: no words around it, no fence, "
    "and keep every line short.\n"
)

# A PLAN REPLY THAT NEVER BECAME JSON NEVER REACHES THE DOOR, SO NOTHING ELSE
# WILL EVER SAY WHAT WAS WRONG WITH IT (0.68.0). The parser's own words go
# back to the model ONCE, ahead of the same question; a second miss stops and
# the log keeps an excerpt of what the model wrote. WHAT FORCED IT: DeepSeek
# and GLM on prod heat batches (2026-10-19) answered the plan ask with
# something that was not one JSON object, every ~75 seconds, and nothing in
# that path told the next call why; the lab runner patched it in from
# outside (`plan_feedback_message`), and the runner is being retired.
UNREADABLE_REPLY_INSTRUCTION = (
    "YOUR LAST REPLY ON THIS PLAN COULD NOT BE READ: {why}. Answer with ONE "
    "JSON object and nothing around it (no words before or after it), in the "
    "shape below.\n"
)

# ONE MORE ASK, SAYING WHAT WAS WRONG WITH THE LAST ONE. Asked once a round.
WHOLE_FORM_AGAIN = {
    "empty": (
        "NOTHING USABLE CAME BACK LAST TIME. Answer with the JSON object "
        "alone: the whole form under `form`.\n"
    ),
    "partial": (
        "YOUR LAST FORM WAS MISSING STEPS. Send EVERY step of the plan, the "
        "ones that were fine too; a step you mean to take out goes in `drop` "
        "by its number.\n"
    ),
    "unchanged": (
        "YOUR LAST FORM CAME BACK EXACTLY AS IT WAS, and every row in `red` "
        "is still red. Change what each row's sentence names.\n"
    ),
}


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


def answer_shape(answer: Any) -> str:
    """What a reply looked like, by key NAMES only (never values), for logs."""
    if not isinstance(answer, dict) or not answer:
        return "no object"
    return "keys=" + ",".join(sorted(str(key) for key in answer)[:12])


# ---------------------------------------------------------------------------
# THE PLAN DOOR SPEAKS IN SLOTS (bench contract 4.0, 2026-09-17)
# ---------------------------------------------------------------------------
# ONE LANGUAGE, NOT TWO. The plan door's reply was REPLACED, not extended: the
# old `{code, path, detail, question, fix, step_index, field}` rows are gone
# and nothing here reads them. A row now says what step, what slot, what is
# wrong with it, WHO FIXES IT, one sentence, the path to patch and what that
# slot accepts. `codes` carries the legacy names for the LOG ONLY -- branching
# on them is what let one fault cost three tries.
#
# Beside the rows, `form_steps` says what GOES IN each step: every named
# input, its kind, its filler (agent / person / platform), its state (wired /
# needed / filled), where its value comes from, what it accepts and its path.
# That table is what the model is shown, because a form that says what goes in
# is answerable and a list of faults is not.
#
# The PROPOSAL door is untouched and still answers the old way; nothing in
# this section is used on it.
AGENT = "agent"
PERSON = "person"
PLATFORM = "platform"

STATE_NEEDED = "needed"

# A SLOT WRITTEN LATER IS NOT A HOLE NOW. The bench marks a slot on a step that
# walks a list or a schedule `source: "at_the_step"`: its words are written one
# item at a time, when the step runs, and approved there. Filling those at
# filing time writes one sentence over a hundred different ones.
SOURCE_AT_THE_STEP = "at_the_step"

# NEVER WRITTEN BY THE AGENT, UNDER ANY NAME. Who a message goes to and who it
# comes from are the platform's, wired from the step where the person picks
# out of their own contact book. An agent that writes an address has invented a
# person, and that is the one mistake this market cannot allow.
NEVER_WRITTEN = frozenset({"to", "from", "cc", "bcc"})


def problems_of(answer: Any) -> list[dict[str, Any]]:
    """Every row the plan door named. ALWAYS A LIST, including []."""
    rows = answer.get("problems") if isinstance(answer, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def next_fix_of(answer: Any) -> dict[str, Any] | None:
    """The row the door says to start on, or None."""
    row = answer.get("next_fix") if isinstance(answer, dict) else None
    return row if isinstance(row, dict) else None


def form_steps_of(answer: Any) -> list[dict[str, Any]]:
    """What goes in each step, in form order. ALWAYS A LIST, including []."""
    rows = answer.get("form_steps") if isinstance(answer, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def step_entry(answer: Any, number: Any) -> dict[str, Any] | None:
    """The `form_steps` entry for one 1-based step number, or None."""
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    for entry in form_steps_of(answer):
        if entry.get("step") == number:
            return entry
    return None


def slots_of(entry: Any) -> list[dict[str, Any]]:
    """One step's named inputs. ALWAYS A LIST: a plain step has none."""
    slots = (entry or {}).get("slots") if isinstance(entry, dict) else None
    return [s for s in slots if isinstance(s, dict)] if isinstance(slots, list) else []


def slots_the_agent_owes(answer: Any, number: Any) -> list[dict[str, Any]]:
    """The slots on ONE step that the agent must write, and has not.

    SLOTS BEFORE WORDS. The door says what goes in every step and who fills
    it. Four things have to be true before a slot is the agent's to write now:
    the agent fills it (`filler`), it is still empty (`state: needed`), the
    step cannot stand without it (`required`), and it is written at FILING and
    not per item at the step (`source`). Everything else is somebody else's or
    later.

    Nothing named `to`, `from`, `cc` or `bcc` is ever in this list, whatever
    the door says about it: those are people, and people come from the
    person's own picks.
    """
    out: list[dict[str, Any]] = []
    for slot in slots_of(step_entry(answer, number)):
        name = str(slot.get("name") or "").strip()
        if not name or name.lower() in NEVER_WRITTEN:
            continue
        if slot.get("filler") != AGENT or slot.get("state") != STATE_NEEDED:
            continue
        if not slot.get("required"):
            continue
        if str(slot.get("source") or "") == SOURCE_AT_THE_STEP:
            continue
        out.append(slot)
    return out


def fixed_by(rows: Any, who: str) -> list[dict[str, Any]]:
    """The rows one party has to fix, in the order the door listed them."""
    return [row for row in (rows or []) if row.get("who_fixes") == who]


def fix_key(row: Any) -> str:
    """ONE FAULT, ONE KEY -- and the bench's own key when it sends one.

    THE BENCH NAMES ITS OWN ROWS NOW (`key`, 2026-09-17). It is what
    `plan_form.count_tries` counts tries under, so reading it is the only way
    the two sides can agree on what "the same problem" is; a key this package
    works out for itself is a second opinion, and a second opinion is how one
    side stops a draft the other thinks is still moving.

    The old key -- the step, the slot and what is wrong with it, plus the
    codes when there is no slot -- is the fallback for a bench that sends none,
    and for a row this package builds in a test. Never the legacy codes alone:
    the same missing subject came back as three of them and cost three of the
    three tries.

    What is NOT in either key: the sentence, the accepted list, and anything
    the bench adds later. A reworded row is the same row.
    """
    row = row if isinstance(row, dict) else {}
    named = str(row.get("key") or "").strip()
    if named:
        return named
    key = "{}|{}|{}".format(row.get("step"), row.get("slot"), row.get("problem"))
    if row.get("slot"):
        return key
    return "{}|{}".format(key, ",".join(sorted(str(c) for c in (row.get("codes") or []))))


# The numbers the plan's odds line is made of: the overall forecast, then one
# per step. A row about either is a row about the line.
ODDS_FIELDS = frozenset({"odds", "declared_odds"})
_BRACKET_INDEX = re.compile(r"\[(\d+)\]")


def path_parts(path: Any) -> list[str]:
    """A dotted path as its parts; `steps[2]` reads as `steps.2`."""
    text = _BRACKET_INDEX.sub(r".\1", str(path or "").strip())
    return [part for part in text.split(".") if part]


def accepted_of(row: Any) -> Any:
    """What the row says the place takes, in the shape the door sent it.

    A list stays a list; a floor or a range sent as an object stays an object
    (a `list()` of a dict would keep only its key names). Nothing is [].
    """
    value = (row or {}).get("accepted") if isinstance(row, dict) else None
    if value is None or value == "":
        return []
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def field_of(row: Any, path: Any) -> str:
    """The name of the thing a row is about: its slot, or the path's last key."""
    slot = str((row or {}).get("slot") or "").strip() if isinstance(row, dict) else ""
    if slot and slot != "step":
        return slot.split(".")[-1]
    for part in reversed(path_parts(path)):
        if not part.isdigit() and part not in ("form", "steps"):
            return part
    return ""


def odds_line(answer: Any, proposal_odds: Any = None) -> dict[str, Any]:
    """EVERY NUMBER ON THE ODDS LINE, with the path of each (0.55.3).

    The overall forecast and each step's declared_odds, in form order, beside
    the bench's own rule for them (`forecast_context`) and the odds this
    agent's proposal was filed at. A number judged against its neighbours is
    never asked about without its neighbours: asked for one number at a time,
    a model raises step 1, and step 2 is the next row, and so on down the plan.

    ALWAYS PRESENT, INCLUDING EMPTY: `your_proposal_odds` is null when this
    agent does not know what it filed at, never left out.
    """
    form = form_of(answer) or {}
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(form.get("steps") or []):
        if not isinstance(step, dict):
            continue
        do = " ".join(str(step.get("do_line") or "").split())
        steps.append(
            {
                "path": f"form.steps.{index}.declared_odds",
                "declared_odds": step.get("declared_odds"),
                "verb": step.get("verb"),
                "do": do if len(do) <= 60 else do[:59] + "…",
            }
        )
    out: dict[str, Any] = {
        "overall": {"path": "form.odds", "odds": form.get("odds")},
        "steps": steps,
        "your_proposal_odds": proposal_odds,
    }
    # The bench's own words about the numbers, as it sent them, minus the
    # want itself (already on every ask). Whatever it adds here rides along.
    forecast = answer.get("forecast_context") if isinstance(answer, dict) else None
    if isinstance(forecast, dict):
        out["the_bench_says"] = {
            key: value for key, value in forecast.items()
            if key != "goal" and value is not None
        }
    return out


def attempts_on(answer: Any, row: Any) -> tuple[int, int | None]:
    """(used, left) the door reports for THIS row, or (0, None).

    `correction_attempts` counts the one row the door pointed at
    (`next_fix`), so it is read only when that is the row being worked.
    """
    pointed = next_fix_of(answer)
    if pointed is None or not isinstance(row, dict) or fix_key(pointed) != fix_key(row):
        return 0, None
    counts = answer.get("correction_attempts") if isinstance(answer, dict) else None
    if not isinstance(counts, dict):
        return 0, None

    def whole(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return whole(counts.get("used")) or 0, whole(counts.get("left"))


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


def tools_index(brief: Any) -> list[dict[str, str]]:
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

    The index goes whole (0.74.0): no budget cuts rows off it. Its last row
    is the wildcard -- composio:<service>/<TOOL>, the door to ~1,500 other
    services.

    AN EMPTY LIST IS AN ANSWER (2026-09-11). `tools: []` is the bench saying
    this want offers NO tools; only a MISSING key (or a null) means "this
    bench publishes no index". Until this fix an empty list fell through to
    a platform fallback, so every plan on a want with no tools was offered a
    hard-coded tool list and put a connection row it could never use on the
    front of a step. That one line is why Jan, Alice and Bobby each burned the
    full 200-round ceiling on the workshop want and filed nothing.

    A bench that publishes no index gets an empty list (0.74.0): the harness
    keeps no list of the bench's tools of its own.
    """
    published = (brief or {}).get("tools") if isinstance(brief, dict) else None
    if isinstance(published, list):
        return [row for row in (_tool_row(entry) for entry in published) if row]
    return []


# What the person said, in the order it matters to an outline.
_STRATEGY_KEYS = (
    "want_in_own_words",
    "feedback_first",
    "person_context",
    "required_blocks",
    "person_already_connected",
    "timeline_days",
    "public_answers",
    "person_facts",
)


def stable_prefix(
    brief: Any = None,
    act_kinds: Any = None,
    with_tools: bool = True,
) -> str:
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
    publishes for it, in priority order, whole (0.74.0), and null where the
    person said nothing.
    """
    if not isinstance(brief, dict):
        return None
    strategy = {key: brief[key] for key in _STRATEGY_KEYS if brief.get(key)}
    return strategy or None


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
    and it rides every ask whole (0.74.0).
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
# THE PARAGRAPH CAP IS KEPT HERE, BEFORE THE DOOR (0.50.0). The bench used to
# trim a long paragraph and say so on `bench_fixed`; since rule 243 was amended
# (2026-09-15) it SENDS IT BACK, NEVER CUT: "pitch_body is 617 characters and
# the cap is 600: cut at least 17 and send the proposal again. Nothing was
# filed." WHAT FORCED THIS: a live agent (Rick, 2026-09-23) filed a 617-char
# paragraph, the free door named REJ-21, the fix round had nothing for a length
# problem, and the filing door refused it -- the want lost for that cycle.
# So the draft loop counts the way the door counts (`pitch_length`), asks the
# model once to cut to the cap, and cuts at a sentence or word boundary itself
# if the model cannot (`trim_to_cap`).
#
# THE TITLE HAS NO LENGTH RULE AT THE DOOR (Steven 2026-09-11, "we don't have
# to trim titles"; bid_validator._check_pitch never counts it). The 120 below
# is said in the ask as guidance only and is never enforced or cut here.
PROPOSAL_TITLE_MAX = 120
PROPOSAL_BODY_MAX = 600
# What the bench says the paragraph takes since contract 4.1.55: up to 400
# characters, longer TRIMMED at a word (never refused). The 600 above stays as
# the cut for an older bench that still refuses past it.
PROPOSAL_BODY_SAID = 400
# A PASS IS AN ANSWER (contract 4.1.55): {"decline": "<one line>"} on the
# proposal door answers 200, and the want leaves this agent's open list. The
# line is trimmed at a word by the bench past this.
DECLINE_MAX = 280
# THE MISSING TOOL IS A SLOT ON THE PASS (contract 4.1.65, Steven 2026-11-05).
# WHAT FORCED IT: Milo passed on "Call my friend and wish him happy birthday"
# because no tool on the want could ring a phone, and the reason went nowhere.
# A pass now names what was missing, in plain words; the bench flags it for
# building and asks back, once, for another way with the tools the want has
# (`alternative_ask`). Up to 3, each up to 120 characters: the bench trims
# past that and says so, it never refuses, and this package cuts the same way.
MISSING_TOOLS_MAX = 3
MISSING_TOOL_CHARS = 120
MISSING_TOOLS_LINE = (
    "If the reason is that no tool on this want can do a part of it, add "
    '"missing_tools": ["<each missing capability in plain words, e.g. place a '
    f'phone call>"], up to {MISSING_TOOLS_MAX}; otherwise leave it out.'
)
# The provider's answer when the free validate door refused and nothing was
# filed (0.74.0: no "filing anyway").
REFUSED_AT_VALIDATE = "proposal_refused_at_validate"
PROPOSAL_LINKS_MIN = 1
PROPOSAL_LINKS_MAX = 3
PROPOSAL_QUESTIONS_MAX = 3

# THE HEADLINE IS A SLOT (bench contract 4.0.12, Steven 2026-09-24: "it's
# another slot"). WHAT FORCED IT: the new card designs put a five-word
# headline on every want card, and the proposal had no short slot for one.
# The agent's own quick-hit title, up to 40 characters; the person's card
# wears it at the top once this agent is picked. The bench TRIMS a longer one
# at a word and says what it cut (`trimmed`, on the validate door and on the
# filing door's 201), so a long headline is a warning here and is never cut or
# refused by this package. The ONE refusal is an empty headline: REJ-21 at the
# door, in the sentence below, which is the bench's own
# (bid_validator._check_headline, HEADLINE_MAX and HEADLINE_EXAMPLE there).
HEADLINE_MAX = 40
HEADLINE_EXAMPLE = "A little heat. A great night."
HEADLINE_EMPTY_PROBLEM = (
    "headline is empty: write the short quick-hit title the person's card "
    f'wears once you are picked, up to {HEADLINE_MAX} characters, e.g. '
    f'"{HEADLINE_EXAMPLE}"'
)


# THE DOOR'S COUNT, EXACTLY. The bench counts `len(pitch_body.strip())` --
# Python code points after stripping leading and trailing whitespace, nothing
# else normalised -- in bid_validator._check_pitch
# (the bench's own validator, cap PITCH_BODY_MAX =
# 600). Count any other way and a paragraph that passes here is
# refused there.
def pitch_length(text: Any) -> int:
    return len(text.strip()) if isinstance(text, str) else 0


_SENTENCE_ENDS = (". ", "! ", "? ")


def trim_to_cap(text: Any, cap: int = PROPOSAL_BODY_MAX) -> str:
    """Cut `text` to at most `cap` characters, by the door's count.

    At the last sentence end (". ", "! ", "? " or a newline) that fits, else
    at the last word boundary that fits. No ellipsis, and never mid-word
    unless the text is one word longer than the cap (nothing else fits).
    Text already inside the cap comes back stripped and otherwise untouched.
    """
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) <= cap:
        return text
    head = text[:cap]
    cut = -1
    for mark in _SENTENCE_ENDS:
        at = head.rfind(mark)
        if at >= 0:
            cut = max(cut, at + 1)
    # A sentence that ends exactly at the cap, with a space (or nothing) after.
    if head[-1] in ".!?" and text[cap].isspace():
        cut = cap
    newline = head.rfind("\n")
    if newline > 0:
        cut = max(cut, newline)
    if cut > 0 and head[:cut].strip():
        return head[:cut].rstrip()
    if text[cap].isspace():
        return head.rstrip()
    space = max(head.rfind(" "), head.rfind("\t"))
    if space > 0 and head[:space].strip():
        return head[:space].rstrip()
    return head


TRIM_INSTRUCTION = (
    "Your proposal's `pitch_body` is {have} characters and the bench's cap is "
    "{cap}: cut at least {excess} characters. Keep what they get and roughly "
    "how; drop padding, not substance. The bench refuses a paragraph over the "
    "cap, it does not trim it. Answer with nothing else: "
    '{{"pitch_body": "..."}}'
)

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
# RULE 238 (Steven, 2026-09-11; amended 2026-09-12): THE CONTACT BOOK IS NOT A
# QUESTION. Who this goes to is a STEP of the plan -- one the AGENT puts in
# (verb `who`) and the bench writes the person's own book onto, after they
# have chosen this agent -- so a
# `contact_picker` on a PROPOSAL is refused REJ-15 at the door. The slug is
# kept here for one reason: to recognise it and DROP it if a model writes one.
CONTACT_PICKER_FORMAT = "contact_picker"

PROPOSAL_FIELDS = (
    "pitch_title",
    "headline",
    "pitch_body",
    "odds",
    "total_ask_cents",
    "research_links",
    "finalist_questions",
    "tools_needed",
)

# THE THREE ROW SLOTS (bench contract 4.1.3, Steven 2026-09-29) ride beside
# the eight fields: `approach`, `capabilities`, `step_estimate`. WHAT FORCED
# THEM: the person now chooses between proposals on one row each (the "A1
# Essential" row), and the row prints the approach across the top, the
# agent's capability lines and "Your part: N steps". Steven: "we need to get
# them to choose a strategy then design it based on that." So the ask below
# puts the APPROACH FIRST and asks for the rest to follow it. All three are
# OPTIONAL at the door: a proposal without them still files. They go to the
# door as the model wrote them; the bench reads its own words in any case and
# trims or refuses in its own words (0.74.0).
ROW_SLOT_FIELDS: tuple[str, ...] = ("approach", "capabilities", "step_estimate")
# The dials' words, as the bench's proposal schema publishes them. Said to the
# model; never used to judge an answer.
APPROACH_DIALS: dict[str, tuple[str, str, str]] = {
    "risk": ("Careful", "Middle risk", "Aggressive"),
    "finish": ("Scrappy", "Middle finish", "Polished"),
    "path": ("Proven path", "Middle path", "Creative"),
}
CAPABILITY_LINE_MAX = 60
APPROACH_WORDS = "; ".join(
    f"{dial}: {', '.join(words[:-1])} or {words[-1]}"
    for dial, words in APPROACH_DIALS.items()
)

PROPOSAL_INSTRUCTION = (
    "Answer the want below with a PROPOSAL, in ONE reply and nothing else. A "
    "proposal is your short answer to what this person wants; it is what they "
    "choose between, one row per agent, side by side. You do NOT write a plan "
    "here -- no steps, no blocks, no account rows, no deliverables, no grant "
    "requests, no finish line, no wins, no skill research, no separate "
    "strategy block. If they pick you, the bench hands you a form and you "
    "write the plan then.\n"
    "CHOOSE YOUR APPROACH FIRST, THEN DESIGN THE PROPOSAL AROUND IT.\n"
    "`approach` -- decide it BEFORE you write anything else: one stop on each "
    f"of three dials, in exactly these words. {APPROACH_WORDS}. Pick what "
    "fits THIS want and what the person said. `your_default_approach`, when "
    "it is there, is your usual style: start from it, and move a dial when "
    "this want calls for it. The person's row prints the three words across "
    "the top.\n"
    "Then write the eight fields below so they clearly FOLLOW that approach: "
    "the headline, the title, the paragraph, the price and the odds of an "
    "Aggressive, Scrappy, Creative proposal read differently from a Careful, "
    "Polished, Proven path one.\n"
    f"`pitch_title` -- what you are offering, up to {PROPOSAL_TITLE_MAX} "
    "characters.\n"
    f"`headline` -- REQUIRED. A short quick-hit title in your own words, up to "
    f"{HEADLINE_MAX} characters (about five words), that the person's card "
    "wears at the top once you are picked, e.g. "
    f'"{HEADLINE_EXAMPLE}" for a hot-sauce tasting night, or "Five emails. '
    'One surprise." for a five-email countdown. It is not `pitch_title` said '
    "again: the title says what you offer, the headline is the few words on "
    "their card. Longer is trimmed at a word and the answer says what was "
    "cut; an empty one is refused.\n"
    f"`pitch_body` -- ONE paragraph, up to {PROPOSAL_BODY_SAID} characters: "
    "what they get and roughly how, the way your approach does it. THIS IS "
    "YOUR STRATEGY; there is no other place for it.\n"
    "`odds` -- your honest chance this person ends up with the thing, a "
    "fraction strictly between 0 and 1 (0.35, never 35, never 0).\n"
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
    "`capabilities` -- one or two short lines, up to "
    f"{CAPABILITY_LINE_MAX} characters each, in your own words: what "
    "YOU bring to THIS want that another agent may not, e.g. \"Calendar and "
    "Gmail audit\" or \"Hires and manages human assistants\". Draw on "
    "`your_profile` when it is there. Not a generic skill list.\n"
    "`step_estimate` -- your first guess at the plan you would write if "
    'picked: {"total": 7, "you": 2}, how many steps in all and how many of '
    "them need the person (0 up to total). It follows your approach too.\n"
    f"A paragraph over {PROPOSAL_BODY_SAID} characters is trimmed at a word by "
    "the bench; do not pad it.\n"
    "A PASS IS AN ANSWER: if this want is not one you can deliver or should "
    'bid on, answer only {"decline": "<one line: why>"} -- no penalty. '
    + MISSING_TOOLS_LINE + "\n"
    'Answer, approach first: {"approach": {"risk": "...", "finish": "...", '
    '"path": "..."}, "pitch_title": "...", "headline": "...", '
    '"pitch_body": "...", "odds": <your honest chance, more than 0 and less '
    'than 1>, '
    '"total_ask_cents": 0, "research_links": [...], "finalist_questions": '
    '[...], "tools_needed": [...], "capabilities": ["..."], '
    '"step_estimate": {"total": 0, "you": 0}}.'
)

LINKS_INSTRUCTION = (
    "Your proposal is written, but it carries no research links and the bench "
    f"refuses a proposal without them (REJ-31). Give {PROPOSAL_LINKS_MIN} to "
    f"{PROPOSAL_LINKS_MAX} links you went and found for THIS want, each "
    '{"url": "https://...", "note": "one line: what this link told you about '
    'this want"}. Real pages you read, not a search box and not this site. '
    'Answer with nothing else: {"research_links": [...]}'
)

# The bench's own sentence for an empty headline, and one small ask. The door
# refuses an empty headline (REJ-21) and a refused bid is the round, so the
# field is asked for once more before anything is filed -- the same bargain as
# the links above.
HEADLINE_INSTRUCTION = (
    "Your proposal is written, but the bench says: " + HEADLINE_EMPTY_PROBLEM + ". "
    f"About five words, up to {HEADLINE_MAX} characters, in your own words, "
    "for THIS want. It is not your pitch_title said again: the title says what "
    "you offer, the headline is the few words on their card. "
    'Answer with nothing else: {"headline": "..."}'
)


UNREADABLE_AGAIN = (
    "Your last reply could not be read: {why}. Answer with ONE JSON object "
    "and nothing else.\n"
)

PROPOSAL_REPAIR_INSTRUCTION = (
    "The bench's free validate door read your proposal and filed nothing. "
    "`the_bench_refused` is its own words, row by row. Answer with the whole "
    "proposal again, the same fields as JSON, changing only what those rows "
    "name. If this want is not one you should bid on after all, answer only "
    '{"decline": "<one line: why>"}. ' + MISSING_TOOLS_LINE
)

# THE ALTERNATIVE, ONCE (contract 4.1.65). A pass that named a missing tool
# comes back from the bench with its own question: is there another way with
# the tools this want has? It rides on top of the proposal ask itself, so the
# answer is a whole proposal, the same shape, through the same doors.
ALTERNATIVE_INSTRUCTION = (
    "You passed on this want; `your_pass` is what you said. The bench has "
    "flagged the missing tool for building, and `the_bench_asks` is its own "
    "question back: `ask`, the tools this want does have (`tool_slugs`) and "
    "`how` to bid instead. Is there a reasonable other set of actions that "
    "gets this person what they want with only those tools? If there is, "
    "answer with a whole proposal as set out below, every entry in "
    "`tools_needed` copied from `tools_on_this_want`. If there is not, answer "
    'only {"decline": "<one line: why>"} and your pass stands.'
)
# The keys of a refusal row the repair ask carries: the door's own words.
REFUSAL_ROW_KEYS = ("code", "rej", "field", "path", "detail", "message", "fix",
                    "say", "accepted", "example")


def decline_of(answer: Any) -> str | None:
    """The one line of a pass, or None. `{"decline": "why"}`, or the older
    `{"decline": true, "reason": "why"}` / `{"no_bid": ...}` shapes."""
    if not isinstance(answer, dict):
        return None
    value = answer.get("decline", answer.get("no_bid"))
    if value in (None, False, "", [], {}):
        return None
    reason = value if isinstance(value, str) else answer.get("reason")
    line = " ".join(str(reason or "").split())
    return line[:DECLINE_MAX] or "No reason given."


def missing_tools_of(answer: Any) -> list[str]:
    """What a pass names as missing (contract 4.1.65): strings only, each
    stripped and cut at MISSING_TOOL_CHARS, the first MISSING_TOOLS_MAX. A
    lone string counts as one; anything else is []."""
    if not isinstance(answer, dict):
        return []
    value = answer.get("missing_tools")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    lines = [" ".join(item.split())[:MISSING_TOOL_CHARS].strip()
             for item in value if isinstance(item, str)]
    return [line for line in lines if line][:MISSING_TOOLS_MAX]


def alternative_ask_of(answer: Any) -> dict[str, Any] | None:
    """The bench's ask back on a pass (contract 4.1.65), as it sent it, or
    None: null, absent (an older bench) or empty is none."""
    ask = answer.get("alternative_ask") if isinstance(answer, dict) else None
    return dict(ask) if isinstance(ask, dict) and ask else None


class _Pass(NamedTuple):
    """A pass the repair ask came back with: the line and what was missing."""

    reason: str
    missing_tools: tuple[str, ...] = ()


def refusal_rows(refusal: Any) -> list[dict[str, Any]]:
    """The door's rows on a refused proposal, each in its own words."""
    rows: list[dict[str, Any]] = []
    problems = refusal.get("problems") if isinstance(refusal, dict) else None
    for row in problems or []:
        if isinstance(row, dict):
            kept = {key: row[key] for key in REFUSAL_ROW_KEYS if row.get(key) not in (None, "")}
            if kept:
                rows.append(kept)
    return rows


def headline_length(proposal: Any) -> int:
    """The headline's length the way the door counts it: stripped, in code
    points (bid_validator.proposal_trims, `len(head.strip())`)."""
    return pitch_length(proposal.get("headline")) if isinstance(proposal, dict) else 0


def headline_problems(proposal: Any) -> list[dict[str, str]]:
    """THE ONE REFUSAL, IN THE BENCH'S WORDS: an empty headline (REJ-21).

    `[]` when there is a headline. Its LENGTH is never a problem here: the
    bench trims a long one at a word and reports it (`headline_warnings`).
    """
    if headline_length(proposal) > 0:
        return []
    return [
        {
            "path": "headline",
            "code": "REJ-21",
            "message": HEADLINE_EMPTY_PROBLEM,
        }
    ]


def headline_warnings(proposal: Any) -> list[str]:
    """A headline over the cap, said once and never refused.

    The bench cuts it at a word on the way in and says what it cut on
    `trimmed`, so the words it will store may be shorter than the ones sent.
    Nothing here cuts it: the cut is the door's, and the door reports it.
    """
    have = headline_length(proposal)
    if have <= HEADLINE_MAX:
        return []
    return [
        f"headline is {have} characters and the card holds {HEADLINE_MAX}: the "
        "bench will trim it at a word and say what it cut (not refused)"
    ]


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
        # Not ours to ask HERE. Who it goes to is a step of the plan, and
        # since 2026-09-12 it is the agent's own step (verb `who`).
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


def catalog_match(slug: Any, catalog: set[str]) -> str | None:
    """The row of this want's tool list that `slug` names, or None.

    ONE reading of "the want offers this", for the proposal's
    `tools_needed` (REJ-01). An exact match, the
    same name in another case, or the tail after a provider prefix. Nothing
    here guesses what a made-up name meant.
    """
    name = str(slug or "").strip()
    if not name or not catalog:
        return None
    by_lower = {row.lower(): row for row in catalog}
    match = name if name in catalog else by_lower.get(name.lower())
    if match is not None:
        return match
    for separator in ("/", ":"):
        tail = name.rsplit(separator, 1)[-1].strip()
        if tail and tail != name:
            found = by_lower.get(tail.lower())
            if found:
                return found
    return None


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
    kept: list[str] = []
    dropped: list[str] = []
    for slug in rows:
        match = catalog_match(slug, catalog)
        if match is None:
            dropped.append(slug)
        elif match not in kept:
            kept.append(match)
    return kept, dropped


def read_proposal(answer: dict[str, Any], notes: list[str] | None = None) -> dict[str, Any]:
    """The eight fields and the three row slots out of the model's answer,
    and nothing else.

    FIELD NAMES ARE A CONTRACT. A field the door does not name is a field the
    door will not read, so anything else the model volunteered is dropped here
    rather than filed. Nothing is trimmed, coerced or invented: `odds` goes
    to the door exactly as written (0.74.0), and an `odds` outside 0..1 is
    the door's to refuse in its own words.

    THE ROW SLOTS ARE OPTIONAL (contract 4.1.3). `approach`, `capabilities`
    and `step_estimate` are read when the answer carries them, exactly as
    written, and simply left off when it does not -- the old eight-field
    answer still reads whole. `notes` is accepted for older callers and left
    untouched: nothing is settled here (0.74.0).
    """
    if not isinstance(answer, dict):
        return {}
    holder = answer
    wanted = PROPOSAL_FIELDS + ROW_SLOT_FIELDS
    for key in ("proposal", "bid"):
        inner = answer.get(key)
        if isinstance(inner, dict) and any(field in inner for field in wanted):
            holder = inner
            break
    out: dict[str, Any] = {}
    # The headline is read like the title: the model's words, stripped, never
    # cut here. A long one is the door's to trim, and the door says so.
    for field in ("pitch_title", "headline", "pitch_body"):
        value = holder.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()
    odds = holder.get("odds")
    if isinstance(odds, (int, float)) and not isinstance(odds, bool):
        out["odds"] = odds
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
    for slot in ROW_SLOT_FIELDS:
        # "Approach first" can put the approach BESIDE a wrapped proposal:
        # {"approach": {...}, "proposal": {...}}. A slot the wrapper lacks is
        # read off the outside.
        value = holder.get(slot)
        if value in (None, "", [], {}) and holder is not answer:
            value = answer.get(slot)
        if value not in (None, "", [], {}):
            out[slot] = value
    return out


def mend_the_small_proposal(
    proposal: Any, brief: Any = None
) -> tuple[dict[str, Any], list[str]]:
    """Every fix this package can make to an eight-field proposal, with no
    model call. Returns the (possibly unchanged) proposal and what was mended.

    Three things, and only the three the door refuses this package for:

      * THE QUESTION SHAPES (REJ-15). `read_questions` is idempotent, so
        running it again over a proposal built by hand, carried over from an
        older contract, or handed back by another caller puts every question
        in the shape the door takes: the whole question in `title`, one of the
        three formats, options where it is a choice, and no `fill`.
      * THE TOOL LIST (REJ-01). A name the want's list does not carry comes
        off; a service that is not on the list belongs on the step that uses
        it, as an outside act.
      * THE PARAGRAPH CAP (REJ-21). A `pitch_body` over the cap, by the
        door's own count, is cut at a sentence or word boundary.

    And a fourth that is never a refusal:

    It does not invent a research link, a title, a headline or a price. A
    field that is simply not there is not something this package can mend.
    And it never cuts a headline: the bench trims a long one at a word and
    says what it cut, so its length is the door's, not a mend.
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
    # THE PARAGRAPH CAP (REJ-21). The door refuses a paragraph over the cap
    # and never cuts it, so a length problem IS one this package can change.
    body = out.get("pitch_body")
    if pitch_length(body) > PROPOSAL_BODY_MAX:
        cut = trim_to_cap(body, PROPOSAL_BODY_MAX)
        out["pitch_body"] = cut
        mended.append(
            f"pitch_body: cut from {pitch_length(body)} to {len(cut)} characters "
            f"(the cap is {PROPOSAL_BODY_MAX})"
        )
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
    """True when this is the rule-243 proposal: eight fields and no steps.

    A proposal that carries steps is the old plan-shaped one, and since
    0.74.0 it is filed exactly as written, with no repair and no free-door
    round. An eight-field proposal takes the small road.
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
    "work_line",
    "hand_over_line",
    "need_line",
    "declared_odds",
    "proof",
    "who",
    "only_if",
    "do_ask",
    "tool",
    "repeats",
    "words",
    "room",
    "bid_step",
    "wait",
    # 0.57.2 (bench contract 4.1.11): on a `proof: file` step, what kind of
    # file comes back -- document (the default, a PDF), audio, image, video or
    # code. Carried as written; the bench validates it.
    "file_kind",
    # 0.57.2: the rest of the bench's PlanFormStep (contract 4.1.11). These
    # were on the door for weeks and this list dropped them without a word,
    # so a harness agent could never send a step's own ask, a who step's
    # count or from_step, a text step's fields, a paid step's cost or
    # line_item_amount, an account row's fallback, or a calendar entry's
    # starts_on / ends_on / with. Carried as written; the bench validates.
    # tests/unit/test_form_step_parity.py fails if the published schema
    # lists a step property this tuple does not carry.
    "ask",
    "count",
    "from_step",
    "fields",
    "cost",
    "line_item_amount",
    "fallback",
    "starts_on",
    "ends_on",
    "with",
)

# THE PLAN'S OWN FIELDS (contract: blank_form is overview, odds, steps,
# span_days). 0.42.0 read only steps and span_days, so a model that answered
# the overall odds and the overview had both dropped here and the door asked
# for form.odds again and again. They are carried exactly as the model wrote
# them; the door validates them.
FORM_PLAN_FIELDS = ("overview", "odds")


FORM_INSTRUCTION = (
    "The person PICKED you. Now write the plan, and the plan is a FORM you "
    "fill in ONE reply.\n"
    "`form` is the blank form, one entry per step. "
    "`answer_these_on_every_step` is the bench's own question for each blank "
    "of a step -- written for step 1, asked of every step you write -- and "
    "`and_for_the_plan` is what the plan itself asks. A question with `choices` "
    "is a PICK: answer with one of them, verbatim. "
    "One that names a number of characters is a CAP: stay "
    "within it.\n"
    "The plan itself needs `overview` (its blank in `and_for_the_plan` says "
    "what it is) and `odds` (YOUR chance of the whole goal within "
    "`span_days`, more than 0 and less than 1). Each step's `declared_odds` "
    "is that chance once the step is done: it starts at or above `odds` and "
    "never falls step to step (`forecast_context` is the bench's rule for "
    "the numbers).\n"
    "`you_may_also_use` are extra picks a step MAY carry; leave out what "
    "you do not need.\n"
    "WRITE AS MANY STEPS AS THE PLAN NEEDS, two or twenty: add entries to "
    "the blank form or drop them freely. You never write "
    "a connect row, a grant request, a title, a schedule or a pointer: the "
    "bench writes those from your picks.\n"
    + WHO_STEP_SENTENCE
    + MATERIALS_SENTENCE_SHORT + "\n"
    + 'Answer: {"overview": "...", "odds": 0.35, "span_days": 14, "steps": '
    '[{"verb": "finds", "do_line": "...", "hand_over_line": "...", '
    '"need_line": "", "declared_odds": 0.4, "proof": "text", "who": "agent"}, '
    '...]}.'
)


def form_instruction(own_means: Sequence[OwnMeans] | None = None) -> str:
    """FORM_INSTRUCTION, with the agent's own means as one line under it
    (0.58.0). Exactly FORM_INSTRUCTION when it declared none."""
    return with_own_means(FORM_INSTRUCTION, own_means, OWN_MEANS_PLAN_RULE)


def form_of(answer: Any) -> dict[str, Any] | None:
    """The form the door is carrying on this answer, or None."""
    if not isinstance(answer, dict):
        return None
    form = answer.get("form")
    return form if isinstance(form, dict) else None


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
    """The FORM out of the model's answer: overview, odds, steps, span_days.

    FIELD NAMES ARE A CONTRACT, the same law `read_proposal` keeps: the form
    carries the fields the door publishes and no others, so anything else the model
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
    for name in FORM_PLAN_FIELDS:
        # Unchanged: never rounded, coerced or defaulted. Absent stays absent
        # so the door asks for it rather than reading a stand-in.
        value = holder.get(name, answer.get(name))
        if value is not None:
            form[name] = value
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
# A PAUSED DOOR (contract 4.0, 2026-09-17)
# ---------------------------------------------------------------------------
# A PAUSE IS NOT A CLOSE. The bench shuts a plan for an hour when the same plan
# comes back five times, and for a quarter of an hour when the thing in the way
# is the bench's OWN bug. Both open again by themselves and the selection
# stands the whole time, so a harness that read a pause as a close threw away a
# plan the person is still waiting on.
#
# EVERY PLAN-DOOR BODY CARRIES IT, including the 409s: `paused_until` is the
# instant it opens again and `paused_reason` is the word for why. While it
# stands there is nothing to send -- every call is read and refused -- so a
# paused draft must cost NO model call and NO door call at all.
DRAFT_PAUSED = "draft_paused"
# The bench's word for a pause the agent did not cause. It is our bug, it is
# logged like one, and the agent is never charged for it.
BENCH_FAULT_PAUSE = "platform_fault"


def paused_until(answer: Any) -> str:
    """When this door opens again, as the bench wrote it, or ""."""
    value = answer.get("paused_until") if isinstance(answer, dict) else None
    return str(value or "").strip()


def pause_reason(answer: Any) -> str:
    """The bench's word for why the door is shut, or ""."""
    value = answer.get("paused_reason") if isinstance(answer, dict) else None
    return str(value or "").strip()


def is_paused(answer: Any, now: datetime | None = None) -> bool:
    """True while the bench says this door is shut and will open by itself.

    An instant that has already passed is not a pause any more. An instant
    that cannot be read is treated AS a pause: the bench only writes the field
    while one stands, and spending rounds against a shut door is the failure
    this reader exists to stop.
    """
    stamp = paused_until(answer)
    if not stamp:
        return False
    try:
        until = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > (now or datetime.now(timezone.utc))


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


def heat_filings_of(answer: Any) -> dict[str, Any] | None:
    """`heat_filings` {used, left, cap} on a heat copy's plan door answer
    (contract 4.1.50), else None."""
    value = answer.get("heat_filings") if isinstance(answer, dict) else None
    return value if isinstance(value, dict) else None


def _spent(answer: dict[str, Any]) -> bool:
    """No more writes: the bench's rounds are used up, or on a heat copy its
    plan filings are (0.74.0)."""
    heat = heat_filings_of(answer)
    if heat is not None and isinstance(heat.get("left"), (int, float)) and heat["left"] <= 0:
        return True
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
        # What the last model reply was (empty / malformed_json / object /
        # api_error), for the fix loop's logs.
        self.last_reply = ""
        self.trail: list[dict[str, Any]] = []
        # The stable prefix for this run, built once in `run` and unchanged
        # after. Until then, the front door alone.
        self.prefix: str = FRONT_DOOR
        # The lab lead's standing direction (focus.md), riding the proposal
        # call when set; empty means the call is unchanged.
        self.standing_direction: str = ""
        # THE AGENT'S DEFAULT APPROACH (agent.yaml `strategy.approach`, 0.57.0)
        # and what it is (name, brand, company, what it can do), both riding
        # the proposal call when set. The default is the model's starting
        # point, never its answer; it also fills a dial the model left out.
        self.approach_default: dict[str, str] | None = None
        self.agent_profile: dict[str, Any] = {}
        # What the agent does by its own means (agent.yaml `agent.own_means`,
        # 0.58.0): one line under the proposal ask and under the plan form's
        # no-tool sentence when set; empty means both asks are unchanged.
        self.own_means: tuple[OwnMeans, ...] = ()
        # The operator's standing instructions (agent.yaml
        # `agent.operator_instructions`, 0.59.0): one labeled block, the whole
        # text, beside the system text of every ask of the run. Empty means
        # every ask is unchanged.
        self.operator_instructions: str = ""
        self.prompt_chars = 0
        self.cached_tokens = 0
        # A provider that caches nothing gets the tools index in the ask
        # itself, instead of in a prefix it would pay for in full each time.
        self._tools_ride_the_outline = False
        self._act_kinds: Any = None
        # The person's own sentence about HOW, read once off the brief in
        # `run` and printed at the top of every ask that writes words.
        self.stance: str = ""
        # What the door said it corrected on the way in, over the whole run.
        # A trim is never retried; it is logged and carried.
        self.bench_fixed: list[Any] = []
        # The odds this agent's proposal on this want was filed at, when the
        # caller knows it; the odds line carries it, null when unknown.
        self.proposal_odds: Any = None
        # WHICH WANT, on every model-call log line (0.68.0). `run` sets both;
        # a caller that asks before `run` (the operator dialogue) sets the id.
        self.target_id: str = ""
        self.kind: str = ""
        # This loop's own run id on the want's log (0.75.0).
        self.run_id: str = uuid.uuid4().hex
        # True when the LAST ask already spent its one retry on a reply the
        # output limit cut off, so the caller does not ask a third time.
        self.cut_off_retried = False
        # The last reply's own text, for the failure excerpt only (0.68.0).
        self.last_text = ""
        # Whether this proposal already had its one alternative ask after a
        # pass (contract 4.1.65); never a second.
        self._alternative_asked = False

    # -- the model ---------------------------------------------------------
    def _tools(self, brief: Any) -> list[dict[str, str]]:
        """This want's tools index, whole."""
        return tools_index(brief)

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
    ) -> dict[str, Any]:
        """One question: the stable prefix as the system, the variable tail as
        the message. Every call is measured, and what the cache did is logged
        where the provider reports it. The tail goes whole (0.74.0).

        A reply the output limit cut off -- empty, or sliced part way through
        its JSON -- is asked ONCE more (0.68.0), unless the adapter already
        asked again over it; `cut_off_retried` says so to the caller."""
        head = list(head)
        tail = self._tail(instruction, payload, head)
        self.cut_off_retried = False
        response, parsed = self._call(tail, what)
        if was_cut_off(response) and asked_again_over_a_cut_off(response):
            # The adapter already asked once more over this cut-off (the
            # reasoning turned down): that was the one retry.
            self.cut_off_retried = True
        elif not parsed and was_cut_off(response):
            # A CUT-OFF REPLY GETS ONE RETRY, EMPTY OR SLICED (0.68.0). The
            # output limit ended it before the answer was whole; the same
            # question once more, asking for the object alone and short, and
            # never a third time.
            self.cut_off_retried = True
            self.log.info(
                "draft loop ask %s: the output limit cut the reply off (%s); "
                "asking once more",
                what or "?",
                self.last_reply,
            )
            again = self._tail(CUT_OFF_AGAIN_INSTRUCTION + instruction, payload, head)
            response, parsed = self._call(again, f"{what or '?'} (cut off, again)")
        return parsed

    def _call(self, tail: str, what: str) -> tuple[Any, dict[str, Any]]:
        """ONE model call on this tail: counted, measured, logged and read."""
        self.calls += 1
        self.prompt_chars += len(tail)
        self.last_text = ""
        try:
            # 0.75.0: the turn, live, on the want's log; with no stream this
            # is the plain call.
            response = thinking.invoke(
                self.model,
                system=self.prefix,
                messages=[ModelMessage.text("user", tail)],
                tools=[],
                place=thinking.here(target_id=self.target_id, run_id=self.run_id),
            )
        except Exception as exc:
            # The exception CLASS only: provider messages can echo headers.
            self.last_reply = f"api_error:{type(exc).__name__}"
            self.log.warning(
                "draft loop model call %d want=%s kind=%s ask=%s: prompt %d chars "
                "(prefix %d, tail %d), no answer: api_error %s",
                self.calls, self.target_id or "?", self.kind or "?", what or "?",
                len(self.prefix) + len(tail), len(self.prefix), len(tail),
                type(exc).__name__,
            )
            raise
        usage = getattr(response, "usage", None)
        cached = cached_input_tokens(usage)
        if cached:
            self.cached_tokens += cached
        text = str(getattr(response, "text", "") or "")
        self.last_text = text
        parsed = read_json_object(text)
        # WHAT CAME BACK, told apart: empty, not JSON, or an object (whose
        # shape the caller judges). Lengths and key names only, never text.
        if not text.strip():
            # 0.64.3: a reply the budget cut off says so, never just "empty".
            note = empty_reply_note(response)
            self.last_reply = f"empty ({note})" if note else "empty"
        elif not parsed:
            cut = ", cut off" if was_cut_off(response) else ""
            self.last_reply = f"malformed_json({len(text)} chars{cut})"
        else:
            self.last_reply = f"object({len(text)} chars)"
        # ONE LINE PER MODEL CALL (0.68.0): which want, how big the prompt
        # was, how big the answer was and what stopped it, and what it read
        # as. Sizes, counts and key names only, never the text.
        prompt_in = getattr(usage, "input_tokens", None)
        self.log.info(
            "draft loop model call %d want=%s kind=%s ask=%s: prompt %d chars "
            "(prefix %d cacheable, tail %d, ~%d tokens; in %s, cached %s); %s; "
            "reply %s %s",
            self.calls, self.target_id or "?", self.kind or "?", what or "?",
            len(self.prefix) + len(tail), len(self.prefix), len(tail),
            (len(self.prefix) + len(tail)) // 4,
            prompt_in if isinstance(prompt_in, int) and prompt_in else "unreported",
            "unreported" if cached is None else cached,
            what_came_back(response), self.last_reply, answer_shape(parsed),
        )
        return response, parsed

    # WHAT A FAILING MODEL WROTE, kept for the log (0.68.0): Steven asked to SEE
    # it. One line, this long, and only when the reply could not be used.
    EXCERPT_CHARS = 600

    def _excerpt(self) -> str:
        return " ".join(str(self.last_text or "").split())[: self.EXCERPT_CHARS]

    def _what_was_wrong(self, parsed: Any, needs: str) -> str:
        """The parser's own words about the last reply: for the model's one
        more try and for the log. Never the model's words back."""
        text = str(self.last_text or "")
        if not text.strip():
            return f"nothing came back ({self.last_reply})"
        if not parsed:
            return (f"it was not one JSON object ({len(text)} characters, no "
                    "readable {...} in it)")
        return f"the object had no {needs} (it carried {answer_shape(parsed)})"

    def _failed_reply(self, kind: str, target_id: str, where: str, why: str) -> None:
        """ONE LINE: which step, what was wrong, and what the model wrote."""
        self.log.warning(
            "draft loop %s target=%s %s: the model's reply could not be used (%s); "
            "it wrote: %s",
            kind, target_id, where, why, self._excerpt() or "(nothing)",
        )

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
        fix = next_fix_of(answer) or {}
        rounds = answer.get("rounds") or {}
        rows = problems_of(answer)
        line = {
            "target": target_id,
            "kind": kind,
            "round": self.rounds,
            "on": what,
            "remaining": answer.get("remaining"),
            "ready": bool(answer.get("ready")),
            "used": rounds.get("used"),
            "left": rounds.get("left"),
            "heat_filings": heat_filings_of(answer),
            "next_fix": fix.get("path"),
            # THE LEGACY CODES LIVE HERE AND NOWHERE ELSE (contract 4.0): a
            # log line is where a person looks a fault up, and a prompt is not.
            "problem": fix_key(fix) if fix else None,
            "codes": list(fix.get("codes") or []),
            "rows": {who: len(fixed_by(rows, who))
                     for who in (AGENT, PERSON, PLATFORM)},
            "closed": answer.get("closed"),
        }
        fixed = self._note_bench_fixed(target_id, kind, answer)
        if fixed:
            line["bench_fixed"] = fixed
        self.trail.append(line)
        heat = line["heat_filings"]
        # On a heat copy the budget that matters is the plan filings, not the
        # rounds (the board's "rounds=1/63" told nobody anything).
        budget = (f"heat filings {heat.get('used')}/{heat.get('cap')} ({heat.get('left')} left)"
                  if heat else f"rounds={line['used']}/{rounds.get('cap')}")
        self.log.info(
            "draft loop %s target=%s round=%d on=%s remaining=%s %s "
            "next_fix=%s (%s)%s",
            kind,
            target_id,
            self.rounds,
            what,
            line["remaining"],
            budget,
            line["next_fix"],
            line["problem"],
            " CLOSED" if line["closed"] else (" READY" if line["ready"] else ""),
        )

    # -- the three doors ---------------------------------------------------
    def _put(self, target_id: str, kind: str, outline: dict[str, Any]) -> dict[str, Any]:
        answer = self.provider.put_draft(target_id, outline, kind=kind)
        self.rounds = 0
        self._record(target_id, kind, answer, "open")
        return answer

    PREVIEW = 120
    # The third naming of one key in a run ends the draft -- the bench's own
    # `fix_key`, so both sides count one fault once. Cumulative, not
    # consecutive: two problems taking turns never repeat in a row and used to
    # run for ever. Two is the better prompt (`still_red`); three is an answer.
    SAME_PROBLEM_ROUNDS = 3

    def _preview(self, value: Any) -> str:
        try:
            text = value if isinstance(value, str) else json.dumps(value, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            text = str(value)
        text = " ".join(str(text).split())
        return text if len(text) <= self.PREVIEW else text[: self.PREVIEW - 1] + "\u2026"

    # What the first read of a cycle can find. Only ONE of these ends in a PUT.
    NOTHING_STANDING = "nothing_standing"   # 404 no_draft: open one
    STANDING = "standing"                   # carry it on, never PUT over it
    OVER = "over"                           # closed, or a door that will not
                                            # answer: leave the want this cycle

    def _read_first(
        self, target_id: str, kind: str, already_read: Any = None
    ) -> tuple[str, dict[str, Any]]:
        """THE FIRST STEP OF EVERY CYCLE. `GET .../proposals/draft` (and
        `?kind=plan` for a plan), which costs no round.

        ONE READ BEFORE ANY WRITE (0.68.0). `already_read` is the answer this
        cycle already got off that same GET -- the market watch's loop-guard
        probe reads the plan draft to fingerprint the work before it reserves
        a try -- and when it is given, the door is not asked again. A plan
        nobody has opened was read twice (the probe, then here, both 404)
        before the loop opened it; now the probe's 404 goes straight to the
        PUT that creates the draft, and everything after that is read off the
        door's own answers.

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
        if isinstance(already_read, dict):
            answer = already_read
            self.log.info(
                "draft loop %s target=%s: this cycle already read the draft (%s); "
                "not reading it again",
                kind,
                target_id,
                str(already_read.get("error") or already_read.get("status") or "read"),
            )
        else:
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

    # -- stage two: the form -----------------------------------------------
    # THE FORM'S OWN WORDS, VERBATIM (2026-09-11). The plan door no longer
    # hands back a bare path and a note: a blank is a QUESTION in plain words
    # ("Step 1, what do you do?") and, where the answer is a pick, the
    # CHOICES it may be answered with ("finds, prepares, does, posts, buys,
    # books, checks, emails, calls, meeting, who, waits, confirms, reviews").
    # `who` is the step the person picks on, and since 2026-09-12 it is the
    # AGENT's to put in front of the first step that reaches somebody. Both
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
        if answer.get("change_request"):
            payload["change_request"] = answer["change_request"]
        if isinstance(brief, dict) and brief.get("operator_dialogue"):
            payload["operator_conversation"] = brief["operator_dialogue"].get("messages") or []
        materials = operator_materials(brief, answer)
        if materials:
            payload[MATERIALS_KEY] = materials
        send = answer.get("send") or ""
        if send:
            payload["how_to_send_it"] = send
        # THE BENCH'S OWN RULE FOR THE NUMBERS (0.55.3). The door sends it on
        # the form answer and this ask used to leave it behind, so a model
        # wrote an overall forecast above its first steps and the door then
        # named the line one step at a time.
        forecast = answer.get("forecast_context")
        if isinstance(forecast, dict) and forecast:
            payload["forecast_context"] = {
                key: value for key, value in forecast.items() if key != "goal"
            }
        if self._tools_ride_the_outline:
            # The `tool` pick comes off this want's list, and on a provider
            # that caches nothing the list is not in the prefix.
            payload["tools_on_this_want"] = self._tools(brief)
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
        # LAW A: what the person said rides this ask too. The agent's own
        # means (0.58.0) stand under the no-tool sentence when it has any.
        instruction = with_the_person_said(
            form_instruction(self.own_means), payload, answer
        )
        if answer.get("change_request"):
            instruction += (
                "\nThe person requested a revision in change_request. Read the existing "
                "form and return the COMPLETE revised form. Add, remove, reorder or "
                "change steps and approval/delivery blocks as their request requires. "
                "Preserve unaffected scope and agreed terms. Do not just refile the "
                "old form or acknowledge the request; edit the actual plan. Do not "
                "claim abilities or deliverables you cannot perform."
            )
        reply = self._ask(
            instruction,
            payload,
            "form",
            head=self._head(answer),
        )
        form = read_form(reply)
        if not form.get("steps") and not self.cut_off_retried:
            why = self._what_was_wrong(reply, "`steps` list")
            self.log.info(
                "draft loop %s target=%s: the form reply could not be read (%s); "
                "handing that back once",
                kind, target_id, why,
            )
            reply = self._ask(
                UNREADABLE_REPLY_INSTRUCTION.format(why=why) + instruction,
                payload,
                "form (unreadable, again)",
                head=self._head(answer),
            )
            form = read_form(reply)
        if not form.get("steps"):
            self._failed_reply(kind, target_id, "form (every step)",
                               self._what_was_wrong(reply, "`steps` list"))
            self.log.warning(
                "draft loop %s target=%s: the door asked for the form and the "
                "model answered with no steps",
                kind,
                target_id,
            )
            return answer, "form"
        if answer.get("change_request") and read_form(form) == read_form(form_of(answer) or {}):
            return answer, "changed_form"
        self.log.info(
            "draft loop %s target=%s: the form came back with %d step(s) and "
            "span_days=%s",
            kind,
            target_id,
            len(form["steps"]),
            form.get("span_days"),
        )
        return self._put(target_id, kind, {"form": form}), ""

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
        the tools this want offers go in; eight fields come back; the bench's
        own proposal door takes them. No outline, no blanks, no fixes: there
        is no plan here to fix. A paragraph over the cap is cut to it first
        (`_fit_the_body`), because the door refuses it rather than trimming;
        an empty headline gets one more small ask (`_headline_for`), because
        the door refuses that too; a long headline is only logged, because the
        door trims it. Anything the door still corrects comes back on
        `bench_fixed` or `trimmed` and is logged, never retried.

        A pass goes to the bench with what was missing (`missing_tools`), and
        the bench's ask back gets ONE more turn (`_after_the_pass`).
        """
        self._alternative_asked = False
        payload = self._proposal_payload(brief)
        slot_notes: list[str] = []
        instruction = self._proposal_instruction()
        answer = self._ask(instruction, payload, "proposal", head=self._head())
        proposal = read_proposal(answer, slot_notes)
        if (decline_of(answer) is None and not proposal.get("pitch_title")
                and not proposal.get("pitch_body")):
            # ONE MORE TRY WITH THE PARSER'S WORDS (0.74.0): an unreadable
            # reply is asked once more, saying what could not be read.
            why = self._what_was_wrong(answer, "pitch_title or pitch_body")
            answer = self._ask(UNREADABLE_AGAIN.format(why=why) + instruction, payload,
                               "proposal (unreadable, again)", head=self._head())
            proposal = read_proposal(answer, slot_notes)
        declined = decline_of(answer)
        if declined is not None:
            out = self._decline(target_id, declined, file, missing_tools_of(answer))
            return self._after_the_pass(target_id, brief, out, idempotency_key, file)
        if not proposal.get("pitch_title") and not proposal.get("pitch_body"):
            return self._gave_up(
                target_id,
                "bid",
                "no_proposal",
                "The model was asked for a proposal and answered with no "
                "title and no paragraph.",
            )
        return self._file_the_proposal(
            target_id, brief, proposal, slot_notes, idempotency_key, file)

    def _proposal_payload(self, brief: Any) -> dict[str, Any]:
        """What the proposal ask carries: the want, what the person said, the
        budget, this want's tools, and the agent's own standing pieces."""
        payload: dict[str, Any] = {
            "want": (brief or {}).get("want") if isinstance(brief, dict) else None,
            "what_the_person_said": person_strategy(brief),
            "budget_cents": budget_of(brief),
            "tools_on_this_want": self._tools(brief),
        }
        materials = operator_materials(brief)
        if materials:
            payload[MATERIALS_KEY] = materials
        if self.standing_direction:
            payload["lab_lead_standing_direction"] = self.standing_direction
        if self.approach_default:
            payload["your_default_approach"] = dict(self.approach_default)
        if self.agent_profile:
            payload["your_profile"] = dict(self.agent_profile)
        if self._tools_ride_the_outline:
            # Nothing caches here, so the rules ride the one call that makes
            # the choice rather than a prefix nobody is charged less for.
            payload["the_rules"] = SHORT_FRONT_DOOR
        return payload

    def _proposal_instruction(self) -> str:
        return with_own_means(PROPOSAL_INSTRUCTION, self.own_means)

    def _file_the_proposal(
        self,
        target_id: str,
        brief: Any,
        proposal: dict[str, Any],
        slot_notes: list[str],
        idempotency_key: str,
        file: bool,
        standing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A READ PROPOSAL, THROUGH THE SAME DOORS EVERY BID TAKES: the row
        slots, the want's own tool list, the links and headline asks, the cap,
        the free validate door, one repair, then the filing door.

        `standing` is the pass this proposal is the alternative to: a repair
        that passes again lets that pass stand and files nothing more."""
        proposal = self._the_row_slots(target_id, proposal, slot_notes)
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
        # THE HEADLINE IS A SLOT (contract 4.0.12). Empty is the door's one
        # refusal for it (REJ-21), so it gets one more small ask, like the
        # links; long is only a warning, because the door trims it at a word
        # and says what it cut.
        if headline_problems(proposal):
            proposal = self._headline_for(target_id, brief, proposal)
        if headline_problems(proposal):
            return self._gave_up(
                target_id,
                "bid",
                "no_headline",
                HEADLINE_EMPTY_PROBLEM + ". The model gave none, twice. Nothing "
                "was filed: the door refuses an empty headline (REJ-21), and a "
                "bid spent on a refusal is the round.",
                proposal,
            )
        for warning in headline_warnings(proposal):
            self.log.warning("proposal for target=%s: %s", target_id, warning)
        proposal = self._fit_the_body(target_id, proposal)
        self.log.info(
            "proposal for target=%s: %d of %d fields, %d link(s), %d "
            "question(s), %d tool(s); approach=%s capabilities=%d "
            "step_estimate=%s",
            target_id,
            len([key for key in proposal if key in PROPOSAL_FIELDS]),
            len(PROPOSAL_FIELDS),
            len(proposal.get("research_links") or []),
            len(proposal.get("finalist_questions") or []),
            len(proposal.get("tools_needed") or []),
            " / ".join((proposal.get("approach") or {}).values()) or "none",
            len(proposal.get("capabilities") or []),
            proposal.get("step_estimate") or "none",
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
        if not filed.get("ok") and filed.get("error") == REFUSED_AT_VALIDATE:
            repaired = self._repair(target_id, brief, proposal, filed)
            if isinstance(repaired, _Pass):
                if standing is not None:
                    return self._the_pass_stands(target_id, standing, repaired.reason)
                out = self._decline(target_id, repaired.reason, file,
                                    list(repaired.missing_tools))
                return self._after_the_pass(target_id, brief, out, idempotency_key, file)
            if repaired is not None:
                proposal = repaired
                filed = self.provider.submit_proposal(target_id, proposal, idempotency_key)
                filed = filed if isinstance(filed, dict) else {}
                if not filed.get("ok") and filed.get("error") == REFUSED_AT_VALIDATE:
                    self.log.warning(
                        "proposal for target=%s: the door refused the repaired proposal "
                        "too; nothing filed, recorded here (%s)",
                        target_id,
                        "; ".join(str(row.get("code") or row.get("rej") or "?")
                                  for row in refusal_rows(filed)),
                    )
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
        # CONTRACT 4.0.12: the filing door's 201 carries `trimmed`, every cut
        # it made ({path, from, to, from_chars, to_chars}, `to` is what was
        # stored). A trim is not a refusal: logged, carried, never retried.
        trims = [row for row in (filed.get("trimmed") or []) if row]
        if trims:
            out["trimmed"] = trims
            self.log.info(
                "proposal for target=%s: the bench trimmed %s on the way in",
                target_id,
                "; ".join(
                    self._preview(
                        f"{row.get('path')}: {row.get('to')!r}"
                        if isinstance(row, dict)
                        else str(row)
                    )
                    for row in trims
                ),
            )
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

    def _repair(
        self, target_id: str, brief: Any, proposal: dict[str, Any], refusal: dict[str, Any]
    ) -> dict[str, Any] | _Pass | None:
        """ONE small repair ask carrying the door's own rows (H3, 0.74.0).

        The validate door is free and files nothing, so its words ride one
        more ask and the answer goes back to it; only what passes is filed.
        A `_Pass` back is a pass (the model declined, with what was missing);
        None is no usable answer, and the refusal stands as the record."""
        rows = refusal_rows(refusal)
        if not rows:
            return None
        payload: dict[str, Any] = {
            "want": (brief or {}).get("want") if isinstance(brief, dict) else None,
            "your_proposal": proposal,
            "the_bench_refused": rows,
        }
        if refusal.get("bench_fixed"):
            payload["bench_fixed"] = refusal["bench_fixed"]
        answer = self._ask(PROPOSAL_REPAIR_INSTRUCTION, payload, "proposal repair",
                           head=self._head())
        declined = decline_of(answer)
        if declined is not None:
            return _Pass(declined, tuple(missing_tools_of(answer)))
        fixed = read_proposal(answer)
        if not fixed.get("pitch_title") and not fixed.get("pitch_body"):
            self.log.warning("proposal for target=%s: the repair ask came back unreadable "
                             "(%s); nothing filed", target_id, self.last_reply)
            return None
        merged = {**proposal, **fixed}
        self.log.info("proposal for target=%s: one repair ask on the door's %d row(s)",
                      target_id, len(rows))
        return self._fit_the_body(target_id, merged)

    def _decline(
        self, target_id: str, reason: str, file: bool, missing_tools: Sequence[str] = ()
    ) -> dict[str, Any]:
        """A PASS IS AN ANSWER (contract 4.1.55). The bench is told, with what
        was missing when the model named it (contract 4.1.65), and the caller
        marks the want reviewed so nothing more is asked on it. A bench
        without the decline door still gets the local record."""
        missing = list(missing_tools or [])
        out: dict[str, Any] = {
            "ok": True,
            "filed": False,
            "declined": True,
            "reason": reason,
            "kind": "bid",
            "rounds": 0,
            "model_calls": self.calls,
            "trail": self.trail,
        }
        if missing:
            out["missing_tools"] = missing
        sender = getattr(self.provider, "decline_target", None)
        if file and callable(sender):
            answer = (sender(target_id, reason, missing_tools=missing) if missing
                      else sender(target_id, reason))
            out["response"] = answer
            if not (isinstance(answer, dict) and answer.get("declined")):
                out["declined_locally"] = True
        else:
            out["declined_locally"] = True
        self.log.info("proposal for target=%s: declined (%s)%s%s", target_id, reason,
                      f"; missing: {'; '.join(missing)}" if missing else "",
                      " -- recorded here only" if out.get("declined_locally") else "")
        return out

    def _after_the_pass(
        self,
        target_id: str,
        brief: Any,
        out: dict[str, Any],
        idempotency_key: str,
        file: bool,
    ) -> dict[str, Any]:
        """THE ALTERNATIVE, ONCE (contract 4.1.65, Steven 2026-11-05).

        WHAT FORCED IT: Milo passed on a birthday call because no tool on the
        want could ring a phone, and offered nothing else. When the bench's
        answer to a pass carries `alternative_ask`, the model is asked ONE
        more time, on the same prefix, stance and payload it drafted on, with
        the bench's own words on top: a whole proposal with the tools this
        want has goes through the same doors as any bid (and lifts the pass
        at the bench); a pass back lets the first one stand and files nothing
        more. Never a second alternative in one run. An older bench answers
        without the key, and nothing changes.
        """
        ask = alternative_ask_of(out.get("response"))
        if ask is None or not file or self._alternative_asked:
            return out
        self._alternative_asked = True
        reason = str(out.get("reason") or "")
        missing = list(out.get("missing_tools") or [])
        payload = self._proposal_payload(brief)
        payload["your_pass"] = {"decline": reason, "missing_tools": missing}
        payload["the_bench_asks"] = ask
        slugs = ask.get("tool_slugs") if isinstance(ask.get("tool_slugs"), list) else []
        said = (f"the bench flagged {'; '.join(missing) or 'a missing tool'} for "
                f"building and asked for another way with {len(slugs)} tool(s) "
                "this want has; asking the model once")
        self.log.info("proposal for target=%s: %s", target_id, said)
        thinking.note(self.model, f"after the pass: {said}",
                      place=thinking.here(target_id=self.target_id, run_id=self.run_id))
        instruction = ALTERNATIVE_INSTRUCTION + "\n\n" + self._proposal_instruction()
        answer = self._ask(instruction, payload, "proposal (alternative)", head=self._head())
        again = decline_of(answer)
        if again is not None:
            return self._the_pass_stands(target_id, out, again)
        slot_notes: list[str] = []
        proposal = read_proposal(answer, slot_notes)
        if not proposal.get("pitch_title") and not proposal.get("pitch_body"):
            self.log.warning(
                "proposal for target=%s: the alternative came back unreadable (%s); "
                "the pass stands, nothing more filed", target_id, self.last_reply)
            out["alternative"] = {"asked": True, "answer": "unreadable"}
            out["model_calls"] = self.calls
            return out
        self.log.info("proposal for target=%s: the alternative is a proposal; filing it "
                      "the way every bid is filed", target_id)
        filed = self._file_the_proposal(
            target_id, brief, proposal, slot_notes, idempotency_key, file, standing=out)
        if filed is out:
            return out
        filed["declined_first"] = {"reason": reason, "missing_tools": missing,
                                   "response": out.get("response")}
        filed["alternative"] = {"asked": True, "answer": "proposal", "ask": ask}
        return filed

    def _the_pass_stands(
        self, target_id: str, out: dict[str, Any], again: str
    ) -> dict[str, Any]:
        """The model passed again on the alternative: the first pass stands
        and nothing more is filed."""
        self.log.info("proposal for target=%s: the alternative is a pass too (%s); "
                      "the first pass stands, nothing more filed", target_id, again)
        out["alternative"] = {"asked": True, "answer": "declined", "reason": again}
        out["model_calls"] = self.calls
        return out

    def _the_row_slots(
        self, target_id: str, proposal: dict[str, Any], notes: list[str]
    ) -> dict[str, Any]:
        """THE APPROACH FIRST (0.57.0), as the model wrote it (0.74.0).

        A proposal with no approach carries the agent's own default from
        agent.yaml, when there is one; an approach the model wrote goes as
        written, and the bench judges its words. Nothing here fails a
        proposal or settles a spelling.
        """
        approach = proposal.get("approach")
        if not approach and self.approach_default:
            approach = dict(self.approach_default)
            self.log.info(
                "proposal for target=%s: approach from the agent's default", target_id
            )
        out = {key: value for key, value in proposal.items() if key != "approach"}
        if approach:
            # The approach goes FIRST on the wire too: it is what the rest
            # was written to follow.
            out = {"approach": approach, **out}
        for note in notes:
            self.log.info("proposal for target=%s: %s", target_id, note)
        return out

    def _fit_the_body(
        self, target_id: str, proposal: dict[str, Any]
    ) -> dict[str, Any]:
        """THE PARAGRAPH INSIDE THE CAP BEFORE ANY DOOR SEES IT (REJ-21).

        One model round that names the exact cap and the exact excess; if the
        reply is still over the cap, or empty, the cut is made here at a
        sentence or word boundary (`trim_to_cap`). Counted the door's way.
        """
        body = proposal.get("pitch_body")
        have = pitch_length(body)
        if have <= PROPOSAL_BODY_MAX:
            return proposal
        excess = have - PROPOSAL_BODY_MAX
        answer = self._ask(
            TRIM_INSTRUCTION.format(have=have, cap=PROPOSAL_BODY_MAX, excess=excess),
            {"pitch_title": proposal.get("pitch_title"), "pitch_body": body},
            "pitch trim",
        )
        again = answer.get("pitch_body") if isinstance(answer, dict) else None
        if isinstance(again, str) and 0 < pitch_length(again) <= PROPOSAL_BODY_MAX:
            self.log.info(
                "proposal for target=%s: pitch_body was %d characters (cap %d); "
                "the model cut it to %d",
                target_id,
                have,
                PROPOSAL_BODY_MAX,
                pitch_length(again),
            )
            return {**proposal, "pitch_body": again.strip()}
        cut = trim_to_cap(body, PROPOSAL_BODY_MAX)
        self.log.warning(
            "proposal for target=%s: pitch_body was %d characters (cap %d) and "
            "the trim round came back %s; cut here at a boundary to %d",
            target_id,
            have,
            PROPOSAL_BODY_MAX,
            f"at {pitch_length(again)}" if isinstance(again, str) else "empty",
            len(cut),
        )
        return {**proposal, "pitch_body": cut}

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

    def _headline_for(
        self, target_id: str, brief: Any, proposal: dict[str, Any]
    ) -> dict[str, Any]:
        """ONE more small ask, for the headline and nothing else.

        The bench refuses an empty headline (REJ-21, contract 4.0.12) in the
        sentence `HEADLINE_EMPTY_PROBLEM` carries, and that sentence is what
        the model is asked. A field the harness can get by asking for it is
        worth one more ask; the alternative is spending the want's one bid on
        a refusal.
        """
        answer = self._ask(
            HEADLINE_INSTRUCTION,
            {
                "want": (brief or {}).get("want") if isinstance(brief, dict) else None,
                "your_proposal": {
                    key: proposal.get(key)
                    for key in ("approach", "pitch_title", "pitch_body")
                    if proposal.get(key)
                },
            },
            "headline",
            head=self._head(),
        )
        headline = read_proposal(answer).get("headline")
        if headline:
            self.log.info(
                "proposal for target=%s carried no headline; one more ask "
                "found one (%d characters)",
                target_id,
                pitch_length(headline),
            )
            return {**proposal, "headline": headline}
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

        One name today: `next: "form"`, the plan door since rule 244 -- the
        bench has put the agent's own proposal, the person's answers, the
        stance line, one finished example and the BLANK FORM in front of us
        and wants the form back filled. Anything else -- including a `form`
        answer where a form already stands -- is handed back untouched,
        because the whole-form road is what comes next. The older outline
        road (a bench below contract 4.0) is gone (0.74.0); such a bench is
        refused before this loop runs (PLAN_DOOR_MIN_CONTRACT).

        Returns (answer, what was asked for and never came back).
        """
        if not isinstance(answer, dict):
            return answer, ""
        if str(answer.get("next") or "") == "form" and the_form_is_blank(answer):
            return self._fill_the_form(target_id, kind, answer, want, strategy, brief)
        return answer, ""

    # THE TWO ANSWERS THAT ARE NOT THE AGENT'S (contract 4.0).
    BENCH_MUST_FIX = "bench_must_fix"
    WAITING_ON_THE_PERSON = "waiting_on_the_person"

    def _paused(self, target_id: str, kind: str, answer: Any) -> dict[str, Any] | None:
        """Stop the moment the bench says this door is shut for now.

        THE DOOR IS READ OFF EVERY BODY, INCLUDING THE 409s. While a pause
        stands nothing the agent sends is read, so one more round is one more
        model call and one more door call spent on a refusal we were told
        about. The want is not lost: the pause lifts by itself and the
        selection stands the whole time.

        A pause the bench blames on ITSELF is our bug, and it shouts here the
        way a platform row does, because nobody finds it otherwise.
        """
        if not is_paused(answer):
            return None
        reason = pause_reason(answer) or "no reason given"
        when = paused_until(answer)
        if reason == BENCH_FAULT_PAUSE:
            self.log.error(
                "draft loop %s target=%s: THE BENCH MUST FIX THIS -- the door "
                "is paused on our own fault until %s; nothing the agent sends "
                "is read until then",
                kind,
                target_id,
                when,
            )
        else:
            self.log.info(
                "draft loop %s target=%s: the door is paused until %s (%s); "
                "nothing sent, and the want is not given up",
                kind,
                target_id,
                when,
                reason,
            )
        return dict(
            answer,
            ok=False,
            error=DRAFT_PAUSED,
            message=(
                f"The plan door is paused until {when} ({reason}). Nothing was "
                "sent: while a pause stands the bench reads nothing, and it "
                "opens again by itself."
            ),
        )

    def _not_the_agents_rows(
        self, target_id: str, kind: str, answer: dict[str, Any], rows: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Stop when every row still standing belongs to somebody else.

        A `platform` row is the BENCH'S OWN BUG. The agent is told "we are
        fixing this"; it is never charged a try and there is nothing it can
        send that clears it, so a loop that keeps asking a model about one is
        spending the owner's money on our mistake. It shouts here, with
        everything a person needs to find it, and the draft is left alone
        until the next round.

        A `person` row is a WAIT, not a failure: the plan is fine and somebody
        has to answer before it can move.

        None means there is still work of the agent's own to do.
        """
        ours = fixed_by(rows, PLATFORM)
        theirs = fixed_by(rows, PERSON)
        if ours:
            for row in ours:
                self.log.error(
                    "draft loop %s target=%s: THE BENCH MUST FIX THIS -- step=%s "
                    "slot=%s problem=%s path=%s codes=%s say=%s",
                    kind,
                    target_id,
                    row.get("step"),
                    row.get("slot"),
                    row.get("problem"),
                    row.get("path"),
                    row.get("codes"),
                    self._preview(row.get("say")),
                )
            return dict(
                answer,
                ok=False,
                error=self.BENCH_MUST_FIX,
                message=(
                    f"{len(ours)} row(s) left on this plan are the bench's own to "
                    "fix and none of them is the agent's. Nothing was sent; the "
                    "rows are in the run log with their step, slot and codes."
                ),
            )
        if theirs:
            self.log.info(
                "draft loop %s target=%s: %d row(s) left are waiting on the "
                "person (%s); nothing for the agent to do this round",
                kind,
                target_id,
                len(theirs),
                "; ".join(self._preview(row.get("say")) for row in theirs),
            )
            return dict(
                answer,
                ok=False,
                error=self.WAITING_ON_THE_PERSON,
                message=(
                    f"{len(theirs)} row(s) left on this plan wait on the person. "
                    "That is not a failure and nothing was sent."
                ),
            )
        return None

    # -- every red row, every round (0.68.0) ---------------------------------
    @staticmethod
    def takes_the_whole_form(kind: str, answer: Any) -> bool:
        """True when this answer is a plan form the door already holds, in
        the one language (contract 4.0: every red row on `problems`).

        That door takes the whole form back in one PUT, so every red row is
        answered in one model call a round. Anything else is not a plan this
        loop can work (0.74.0: there is no other road), and the run stops at
        `draft_not_ready`.
        """
        return (
            kind == "plan"
            and form_of(answer) is not None
            and not the_form_is_blank(answer)
            and isinstance((answer or {}).get("problems"), list)
        )

    def _refile(
        self, target_id: str, kind: str, form: dict[str, Any], what: str
    ) -> dict[str, Any]:
        """THE WHOLE FORM BACK IN ONE PUT, over the form the door is holding.

        Not the opening PUT: the door carries its rounds forward over a
        standing form (a full form answers the row it asked about), so the
        round count is read back off the answer rather than set to zero.
        """
        answer = self.provider.put_draft(target_id, {"form": form}, kind=kind)
        used = ((answer or {}).get("rounds") or {}).get("used")
        if isinstance(used, int) and not isinstance(used, bool):
            self.rounds = used
        else:
            self.rounds += 1
        self._record(target_id, kind, answer, what)
        return answer

    def _red_rows(self, answer: Any) -> list[dict[str, Any]]:
        """EVERY ROW THE AGENT OWES, as the model reads it: step, slot,
        problem, the bench's sentence, the path and what it takes. The legacy
        codes stay in the log and never reach the model. One fault said twice
        is one row. A required blank the door still holds open, and a
        `next_fix` that is the agent's and not on `problems`, are rows too.
        """
        rows: list[dict[str, Any]] = list(fixed_by(problems_of(answer), AGENT))
        pointed = next_fix_of(answer)
        if (
            isinstance(pointed, dict)
            and pointed.get("who_fixes", AGENT) == AGENT
            and str(pointed.get("path") or "") not in ("", "outline")
            and all(fix_key(pointed) != fix_key(row) for row in rows)
        ):
            rows.append(pointed)
        named_paths = {str(row.get("path") or "") for row in rows}
        for blank in (answer or {}).get("blanks") or []:
            if not isinstance(blank, dict) or not blank.get("required"):
                continue
            path = str(blank.get("path") or "")
            if not path or path in named_paths:
                continue
            rows.append(
                {
                    "step": (step_of(path) + 1) if step_of(path) is not None else None,
                    "slot": path.rsplit(".", 1)[-1],
                    "problem": "empty",
                    "say": blank.get("question") or blank.get("note") or "",
                    "path": path,
                    "accepted": list(blank.get("choices") or []),
                }
            )
        out: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            path = str(row.get("path") or "")
            entry: dict[str, Any] = {
                "step": row.get("step"),
                "slot": row.get("slot"),
                "problem": row.get("problem"),
                "say": row.get("say"),
                "path": path or None,
                "accepted": accepted_of(row),
            }
            if row.get("keys"):
                entry["keys"] = list(row["keys"])
            mark = (entry["step"], entry["slot"], entry["problem"], entry["path"],
                    str(entry["say"] or ""))
            if mark in seen:
                continue
            seen.add(mark)
            entry["_key"] = fix_key(row)
            out.append(entry)
        return out

    def _stalled(self, answer: dict[str, Any], key: str, row: Any) -> dict[str, Any]:
        return dict(
            answer,
            ok=False,
            error="draft_stalled",
            message=(
                f"The bench named {key} three times and the patches did "
                "not clear it; paused to avoid repeated model spending. "
                f"It said: {self._preview((row or {}).get('say'))}"
            ),
        )

    @staticmethod
    def _whole_form_from(
        reply: Any, standing: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, str]:
        """(the form to file, "") or (None, why): empty, partial, unchanged.

        A form with fewer steps than the plan holds is a PART of a form unless
        the reply names the missing ones in `drop`: filing it would take out
        steps the model never meant to touch.

        THE PLAN'S OWN FIELDS ALONE ARE AN ANSWER (the 0.42.1 law, Kari): a
        reply that carries only `odds`, `overview` or `span_days`, bare or
        under `form`, changes those on the form the door holds and keeps
        every step as it stands.
        """
        reply = reply if isinstance(reply, dict) else {}
        holder = reply.get("form") if isinstance(reply.get("form"), dict) else reply
        form = read_form(reply)
        if not form.get("steps"):
            held = read_form(standing or {})
            fields = {
                name: holder.get(name, reply.get(name))
                for name in (*FORM_PLAN_FIELDS, "span_days")
            }
            fields = {name: value for name, value in fields.items() if value is not None}
            span = fields.pop("span_days", None)
            if isinstance(span, (int, float)) and not isinstance(span, bool):
                fields["span_days"] = int(span)
            if not fields or not held.get("steps"):
                return None, "empty"
            form = dict(held, **fields)
        drops = holder.get("drop", reply.get("drop"))
        drops = drops if isinstance(drops, list) else [drops] if drops is not None else []
        dropped = {
            value for value in drops if isinstance(value, int) and not isinstance(value, bool)
        }
        held = (standing or {}).get("steps")
        held_steps = len(held) if isinstance(held, list) else 0
        if len(form["steps"]) + len(dropped) < held_steps:
            return None, "partial"
        if standing is not None and form == read_form(standing):
            return None, "unchanged"
        return form, ""

    def _fix_every_red_row(
        self,
        target_id: str,
        kind: str,
        answer: dict[str, Any],
        want: Any,
        brief: Any = None,
    ) -> dict[str, Any]:
        """EVERY RED ROW, EVERY ROUND (0.68.0), for a form the door holds.

        Each round: ONE model call is shown the whole form and every red row
        the door named -- step, slot, problem, the bench's sentence (`say`),
        path and what it takes (`accepted`), plus the input table of each of
        those steps -- and answers with the whole form, which goes back in
        ONE PUT. The door then names what is still red, and the next round
        starts from that. Nothing is worked without a model (0.74.0).

        A pause stops it; rows that are all the bench's or all the person's
        stop it; and the brake is the
        bench's own key, counted across the whole run -- a row the model has
        been shown twice that is still red the third time ends the draft.
        One more ask a round, saying what was wrong, when the answer was
        empty, a part of the form, or the form unchanged -- and none when
        that ask already spent its retry on a reply the output limit cut off.
        """
        named_before: dict[str, int] = {}
        asked_last: set[str] = set()
        while (
            not answer.get("ready")
            and not answer.get("closed")
            and not _spent(answer)
        ):
            stopped = self._paused(target_id, kind, answer)
            if stopped is not None:
                return stopped
            red = self._red_rows(answer)
            if not red:
                stopped = self._not_the_agents_rows(
                    target_id, kind, answer, problems_of(answer)
                )
                if stopped is not None:
                    return stopped
                break
            for row in red:
                key = row["_key"]
                named_before[key] = named_before.get(key, 0) + 1
                if named_before[key] >= self.SAME_PROBLEM_ROUNDS:
                    return self._stalled(answer, key, row)
            standing = form_of(answer) or {}
            shown = [{k: v for k, v in row.items() if k != "_key"} for row in red]
            still = [row for row, full in zip(shown, red, strict=True)
                     if full["_key"] in asked_last]
            numbers = sorted(
                {row["step"] for row in red
                 if isinstance(row.get("step"), int) and not isinstance(row["step"], bool)}
                | {entry.get("step") for entry in form_steps_of(answer)
                   if isinstance(entry.get("step"), int)
                   and slots_the_agent_owes(answer, entry.get("step"))}
            )
            payload: dict[str, Any] = {
                "want": want,
                "form": {
                    name: value for name, value in standing.items()
                    if not str(name).startswith("_")
                },
                "red": shown,
            }
            tables = [step_entry(answer, number) for number in numbers]
            tables = [entry for entry in tables if entry]
            if tables:
                payload["what_goes_in"] = tables
            if still:
                payload["still_red"] = still
            if any(field_of(row, str(row.get("path") or "")) in ODDS_FIELDS
                   or str(row.get("path") or "") == "form.odds" for row in red):
                payload["the_odds_line"] = odds_line(answer, self.proposal_odds)
            forecast = answer.get("forecast_context")
            if isinstance(forecast, dict) and forecast:
                payload["forecast_context"] = {
                    key: value for key, value in forecast.items() if key != "goal"
                }
            if answer.get("optional_picks"):
                payload["you_may_also_use"] = answer["optional_picks"]
            if self._tools_ride_the_outline:
                payload["tools_on_this_want"] = self._tools(brief)
            instruction = WHOLE_FORM_FIX_INSTRUCTION
            if still:
                instruction = WHOLE_FORM_STILL_RED_INSTRUCTION + instruction
            pointed = next_fix_of(answer)
            used, left = attempts_on(answer, pointed)
            if pointed is not None and used >= 1 and left == 1:
                instruction = LAST_TRY_INSTRUCTION + instruction
                payload["the_row_on_its_last_try"] = {
                    "step": pointed.get("step"), "slot": pointed.get("slot"),
                    "say": pointed.get("say"),
                }
            instruction = with_the_person_said(instruction, payload, answer)
            what = f"fix {len(red)} red row(s)"
            self.log.info(
                "draft loop %s target=%s round=%d: %d red row(s) on %d step(s), "
                "asked in one call: %s",
                kind, target_id, self.rounds + 1, len(red),
                len({row.get('step') for row in red}),
                "; ".join(row["_key"] for row in red),
            )
            reply = self._ask(instruction, payload, what, head=self._head(answer))
            form, why = self._whole_form_from(reply, standing)
            if form is None and not self.cut_off_retried:
                self.log.info(
                    "draft loop %s target=%s: the whole-form answer was %s "
                    "(reply=%s); asking once more",
                    kind, target_id, why, self.last_reply,
                )
                again = WHOLE_FORM_AGAIN[why]
                if why == "empty":
                    again = UNREADABLE_REPLY_INSTRUCTION.format(
                        why=self._what_was_wrong(reply, "form")) + again
                reply = self._ask(again + instruction, payload,
                                  f"{what} (again)", head=self._head(answer))
                form, why = self._whole_form_from(reply, standing)
            if form is None:
                self._failed_reply(
                    kind, target_id,
                    "steps " + ", ".join(str(n) for n in numbers) if numbers else "the plan",
                    why if why != "empty" else self._what_was_wrong(reply, "form"),
                )
                self.log.warning(
                    "draft loop %s target=%s: no whole form came back for %d red "
                    "row(s) (%s, reply=%s); stopping this draft",
                    kind, target_id, len(red), why, self.last_reply,
                )
                break
            asked_last = {row["_key"] for row in red}
            answer = self._refile(target_id, kind, form, what)
        return self._paused(target_id, kind, answer) or answer

    # -- the whole thing ---------------------------------------------------
    def run(self, target_id: str, **options: Any) -> dict[str, Any]:
        """One want (see `_run`). Whatever it ends with, what the thinking
        stream holds for it goes now (0.75.0)."""
        try:
            return self._run(target_id, **options)
        finally:
            thinking.flush(self.model, self.run_id)

    def _run(
        self,
        target_id: str,
        *,
        kind: str = "bid",
        brief: Any = None,
        act_kinds: Any = None,
        proposal_id: str | None = None,
        idempotency_key: str = "",
        file: bool = True,
        revision: dict[str, Any] | None = None,
        already_read: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """ONE WANT, ONE ANSWER, and which road depends on the stage.

        `kind="bid"` is a PROPOSAL: one model call, one filing, no draft door
        (rule 243). `kind="plan"` is the form: read what stands, open an empty
        draft if nothing does, do whatever the door names next -- the form, or
        an outline on an older bench -- then the blanks it left open, then the
        content it named, then file.

        `file` false runs the whole thing and stops at the door: the document
        the bench holds comes back on `draft` and nothing is filed.

        `already_read` is this cycle's own read of the plan draft, when the
        caller made one (0.68.0): the loop starts from it instead of reading
        the door a second time before it writes anything.
        """
        self.target_id = str(target_id or "")
        self.kind = str(kind or "")
        want = (brief or {}).get("want") if isinstance(brief, dict) else None
        strategy = person_strategy(brief)
        # ONCE, and byte-identical from here to the end of the run: the front
        # door, the block index and the tools index. Everything after this is
        # a small tail on top of a prefix the provider has already seen.
        self._act_kinds = act_kinds
        caches = bool(getattr(self.model, "caches_a_stable_prefix", lambda: False)())
        # 0.59.0: the operator's standing instructions ride beside it, WHOLE,
        # the same bytes on every ask of the run. They sit outside the tail
        # each ask measures, so they never make an ask shed or warn.
        self.prefix = with_operator_block(
            stable_prefix(brief, act_kinds, with_tools=caches),
            self.operator_instructions,
        )
        self._tools_ride_the_outline = not caches
        self.stance = stance_line(brief)
        # TWO STAGES (rule 243, 2026-09-11). A PROPOSAL is the agent's short
        # answer to a want and it is ONE CALL. A PLAN is the form, and only
        # the agent the person picked is ever asked for one.
        if kind == "bid":
            return self._propose(target_id, brief, idempotency_key, file)
        # READ FIRST, EVERY CYCLE. This is the loop's first step and the only
        # thing that decides whether an outline goes out at all.
        state, answer = self._read_first(target_id, kind, already_read)
        # A PAUSED DOOR IS READ BEFORE ANYTHING IS SENT. The read itself costs
        # no round; everything after it would be spent on a refusal the body in
        # hand already announced.
        stopped = self._paused(target_id, kind, answer)
        if stopped is not None:
            return self._gave_up(
                target_id, kind, DRAFT_PAUSED, stopped["message"], answer
            )
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
        # A human revision is new work, not a resume of the ready saved form.
        # Only an explicit request authorizes replacing that form's structure.
        if revision:
            if answer.get("next") != "form":
                return self._gave_up(
                    target_id, kind, "revision_form_unavailable",
                    "The plan door has no editable form for this revision.", answer,
                )
            answer = dict(answer, change_request=revision)
            answer, unanswered = self._fill_the_form(
                target_id, kind, answer, want, strategy, brief
            )
        else:
            # Ordinary recovery still preserves the standing form.
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
        if not answer.get("closed") and self.takes_the_whole_form(kind, answer):
            # EVERY RED ROW, EVERY ROUND (0.68.0): the whole form and the whole
            # red list in one call, the whole answer back in one PUT. The ONLY
            # plan road (0.74.0).
            answer = self._fix_every_red_row(target_id, kind, answer, want, brief)
        stopped = str(answer.get("error") or "")
        if stopped in ("draft_stalled", DRAFT_PAUSED, self.BENCH_MUST_FIX,
                       self.WAITING_ON_THE_PERSON):
            return self._gave_up(
                target_id, kind,
                stopped,
                str(answer.get("message") or answer.get("closed") or stopped),
                answer,
            )
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
