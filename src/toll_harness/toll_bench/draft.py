"""THE DRAFT LOOP — one plan, built up in pieces, at the bench's own door.

TWO STAGES (rules 243-245, Steven Ochs, 2026-09-11). A PROPOSAL is the agent's
short answer to a want -- eight fields, ONE call, no steps -- and it is what
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
  3. one ROW at a time — WHAT GOES IN the step it names (the door's own slot
     table: each named input, its kind, who fills it, whether it is wired or
     still needed, what it accepts and the path to patch) and the bench's own
     sentences for that step. Never a rule code: the plan door answers in ONE
     language since contract 4.0, and the legacy `codes` go to the log alone.

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
from datetime import datetime, timezone
from typing import Any

from toll_harness.core.types import ModelMessage
from toll_harness.toll_bench import blocks, programs

_LOGGER = logging.getLogger("toll_harness.draft")

# ONE NAME FOR SENDING AN EMAIL (bench contract 4.0, 2026-09-17). The bench
# takes the provider-shaped names quietly and no longer teaches them: the
# person's connected account decides which mail service actually runs, so an
# agent that writes the provider into the plan is guessing at something it
# cannot know. `form_steps[].action` on every plan answer reads this name.
EMAIL_SEND = "email.send"

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
    "blank that carries `up_to_characters` is a cap: stay within it, and "
    "write short rather than padding it. Fill them in your own words; leave "
    "nothing you can answer "
    "empty; change nothing else. A `you` blank is the person's own bullet for "
    "that part, pinned to it: write it as its note says (one line, starts with "
    "connect / approve / pick / answer, names the thing) rather than leaving "
    "the bench to print a flat line. A promise names the thing you deliver, "
    "never you; a work item starts with an -ing word.\n"
    'Answer: {"patches": [{"path": "<the exact path>", "value": <your value>}, '
    "...]}."
)

# THE WHO STEP IS THE AGENT'S PICK (rule 238, amended 2026-09-12).
#
# WHAT FORCED IT: on production, 2026-09-12, the bench INSERTED its own who
# step after the agent's step 4 and then refused its own document as "step 5"
# -- a step the agent never wrote. So the bench stopped inserting. The agent
# picks the step like it picks `emails` or `meeting`, and a plan that reaches
# somebody with no who step above it is refused, and the step that puts one in
# is the agent's to send. Step numbers never move under the agent again.
#
# NOTHING HERE READS A CODE ANY MORE (contract 4.0). The plan door's own slot
# table says it: a step whose recipient slot is the platform's, wired to
# `person.who`, and no step anywhere on the form standing as the who step.
# That is `who_step_is_missing` below.

# The door's 422 when the insert it named cannot be made (no `step` object,
# `before` out of range). Logged with its reason and never retried blindly.
INSERT_NOT_POSSIBLE = "insert_not_possible"

# The who step, as the agent writes it: three fields and no more. Everything
# else on it -- the title, the person's own contact book, the minutes, the
# cost -- is the bench's, and a do_line or a proof written here is ignored.
WHO_VERB = "who"
DEFAULT_WHO_ODDS = 0.5

WHO_STEP_SENTENCE = (
    "WHO IT GOES TO IS A STEP, AND THE STEP IS YOURS TO PUT IN (rule 238, "
    "amended 2026-09-12). In front of the FIRST step that reaches a person -- "
    "emails, meeting, calls, or any send whose recipient is the person's to "
    "choose -- write one step of its own: "
    '{"verb": "who", "who": "person", "declared_odds": <your number>}. '
    "The bench writes everything else on it: its title, the person's own "
    "contact book, its minutes and its cost. ONE who step per plan -- every "
    "send below it reads the same picks -- and never plan a step to find, get "
    "or list the people: say what you DO with the ones they pick. A plan that "
    "reaches somebody with no who step above it is refused, and the step that "
    "puts one in is yours to send.\n"
)

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
    "it. Sending the same value a third time cannot work: read what the bench "
    "says again and send something DIFFERENT -- a different value, or the "
    "same idea in the shape the step asks for. If the path is a list, send "
    "the whole list, not one entry of it.\n"
)
# THE SLOTS THE STEP IS MISSING, FILLED BEFORE ANYTHING IS REWORDED.
#
# WHAT FORCED IT (checker, 2026-09-17). The fix ask says "change exactly the
# one thing named in `change_this`; do not touch any other path", which is
# right for a fix and wrong for a hole. So a plan whose email step had no
# subject and no body was filed with no subject and no body: the door said
# both were the agent's and still needed, in as many words, and nothing ever
# asked for them. This ask is what asks for them, in one call for however many
# there are, before a single word is reworded.
#
# SHORT AND PLAIN, because weak models are in this fleet.
NEEDED_SLOTS_INSTRUCTION = (
    "Your plan has holes in it. `needs` lists every one: which step it is on, "
    "the `name` of the thing to write, what `kind` of thing it is, what it "
    "accepts, and the exact `path` to patch. Write all of them.\n"
    "Write real words the person will read, in their own right: a subject is "
    "a subject, a body is a message. Never leave one empty and never write "
    "the word yet or TBD.\n"
    "Write NOTHING else. Do not write who a message goes to and do not write "
    "who it is from: the platform fills those from the step where the person "
    "picks, and an address you write is a person you invented.\n"
    'Answer: {"patches": [{"path": "<the path from needs>", "value": "<your '
    'words>"}, ...]} -- one patch for each thing in `needs`, and no other '
    "path."
)

# ONE LINE, ONCE. A stand-in is dropped and asked for again, and this is what
# is added the second time.
STAND_IN_REMINDER = (
    "\nA STAND-IN IS NOT WORDS. Your last answer left one of these as a "
    "placeholder. Write the real words for this want; do not write TBD, TODO, "
    "a name in brackets, or the name of the slot itself.\n"
)

EMPTY_ANSWER_INSTRUCTION = (
    "NOTHING CAME BACK LAST TIME. Answer with ONE patch for the path named in "
    "`change_this`, as JSON: {\"patches\": [{\"path\": <that path>, \"value\": ...}]}. "
)


# THE FORM SAYS WHAT GOES IN; IT NEVER LISTS WHAT IS WRONG (contract 4.0).
#
# WHAT FORCED IT (2026-09-17). Marcia fixed 21 of 24 plan problems in three
# minutes and died on the last three, all on one email step: one missing
# subject came back under two codes with a paragraph of advice, and one of the
# fields was something the platform fills itself. Three tries per problem is
# what ended her plan. So the ask below hands the model the STEP'S OWN SLOT
# TABLE -- every named input, what fills it, whether it is wired or still
# needed, what it accepts and the path to patch -- and the bench's own
# sentences underneath. No codes, no advice, no list of faults.
FIX_INSTRUCTION = (
    "The bench read your plan and this is the step to work on.\n"
    "`this_step` is the form's own statement of that step: each named input, "
    "what kind of thing goes in it, WHO fills it (`filler`), whether it is "
    "already wired or still `needed` (`state`), what it accepts, and the exact "
    "`path` to patch. FILL ONLY the inputs whose `filler` is \"agent\". Never "
    "write who a message goes to and never write who it is from: those are "
    "the platform's, wired from the step where the person picks.\n"
    "`what_the_bench_says` is one sentence per thing still open on this step, "
    "in the bench's own words. `step` is what you sent.\n"
    "Change exactly the one thing named in `change_this`. Do not touch any "
    "other path and do not resend the plan.\n"
    'Answer: {"patches": [{"path": "<the path in change_this>", '
    '"value": <the new value>}]}.'
)


# THREE ASKS THAT MAY BE ANSWERED WITH SEVERAL PATCHES (0.55.3). Every other
# fix ask says "change exactly this one path", which is right for one wrong
# thing and wrong for a line of numbers or a row about the whole plan: Sam
# (2026-09-25) was told to change one number, five rounds running, while each
# change made the next step fall. Short and plain, because weak models are in
# this fleet.
ODDS_FIX_INSTRUCTION = (
    "THE BENCH NAMED A NUMBER ON YOUR ODDS LINE. The odds are one line, not "
    "one number. `the_odds_line` holds every number on it: your overall "
    "forecast (`form.odds`), then each step's `declared_odds` in order, each "
    "with its exact path, the odds your proposal was filed at, and the "
    "bench's own rule. `change_this` is what it named and `say` is its "
    "sentence.\n"
    "A step's number is the chance the person ends up with the thing once "
    "that step is done. So the line starts at or above `form.odds` and never "
    "falls from one step to the next. The bench puts steps of its own into "
    "the plan (a connect step, a who step) and each of those carries the "
    "number of your step right after it, so the bench's step numbers can run "
    "ahead of yours.\n"
    "Fix the WHOLE line in this one answer: one patch for every number that "
    "has to change, each at its own path. Lowering `form.odds` or an earlier "
    "step is as good a fix as raising a later one. Every number is greater "
    "than 0 and less than 1. Patch only paths shown in `the_odds_line`.\n"
    'Answer: {"patches": [{"path": "form.steps.0.declared_odds", "value": '
    '0.5}, {"path": "form.steps.1.declared_odds", "value": 0.55}]}.'
)

PLAN_WIDE_FIX_INSTRUCTION = (
    "THE BENCH NAMED THE PLAN AS A WHOLE, NOT ONE PLACE IN IT. "
    "`the_bench_says` is its own sentence and `accepted` is what it takes, "
    "when it says. Fix it by changing NAMED places inside the plan: "
    "`form.odds`, `form.overview`, `form.span_days`, or one field of one "
    "step, `form.steps.<index>.<field>` (index 0 is step 1; "
    "`fields_on_a_step` lists the fields). `the_odds_line` shows every "
    "step with its path. Send as many patches as the fix takes, in one "
    "answer.\n"
    "Never patch `form` itself, never send `form.steps` whole, and never add "
    "a key the plan does not have: a patch anywhere else is not sent.\n"
    'Answer: {"patches": [{"path": "form.steps.2.declared_odds", "value": '
    '0.6}]}.'
)

LINE_FIX_INSTRUCTION = (
    "THE SAME THING MOVED TO ANOTHER STEP. Last round the bench named "
    "`{field}` on one step; now it names it on another. Fixing one step at a "
    "time is moving it along the plan. `the_line` is `{field}` on every step, "
    "with the path of each, and `what_the_bench_says` is its sentence.\n"
    "Fix every step that needs it in this ONE answer: one patch per step you "
    "change, each at its own path, and leave the rest as they are.\n"
    'Answer: {{"patches": [{{"path": "<a path from the_line>", "value": '
    '...}}]}}.'
)

LAST_TRY_INSTRUCTION = (
    "THIS IS THE LAST TRY THE BENCH GIVES THIS ROW. If this answer does not "
    "clear it, the plan closes and the person is asked to pick someone else. "
    "Read the bench's sentence once more and fix exactly what it says.\n"
)

EMPTY_SEVERAL_INSTRUCTION = (
    "NOTHING USABLE CAME BACK LAST TIME. Answer with patches to named places "
    'in the plan only, as JSON: {"patches": [{"path": ..., "value": ...}]}. '
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


_PLAN_FIELD_PATHS = ("form.odds", "form.overview", "form.span_days")


def _plan_field_answer(answer: dict[str, Any], asked: str) -> list[dict[str, Any]]:
    """The ASKED plan-level field answered bare: {"odds": 0.75} or
    {"form": {"odds": 0.75}}. Only when the bench asked for exactly that
    path, only when the answer names that one field, and only when the two
    places do not disagree. The model's value goes through untouched; a
    missing or ambiguous answer is no patch, never a stand-in."""
    if asked not in _PLAN_FIELD_PATHS:
        return []
    name = asked.split(".", 1)[1]
    found: list[Any] = []
    if name in answer:
        found.append(answer[name])
    inner = answer.get("form")
    if isinstance(inner, dict) and name in inner:
        found.append(inner[name])
    found = [value for value in found if value is not None]
    if not found or any(value != found[0] for value in found[1:]):
        return []
    return [{"path": asked, "value": found[0]}]


def read_patches(
    answer: dict[str, Any], asked: str | None = None
) -> list[dict[str, Any]]:
    """[{path, value}] out of whatever shape the model answered in.

    Three shapes are accepted because a raw model writes whichever one it
    read: the `patches` list the door takes, a bare {path: value} map, and a
    single {"path": ..., "value": ...}. When the bench asked for a plan-level
    field (`asked`, e.g. form.odds), {"odds": v} and {"form": {"odds": v}}
    answer it too.
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
    if not out and asked:
        out = _plan_field_answer(answer, asked)
    return out


def answer_shape(answer: Any) -> str:
    """What a reply looked like, by key NAMES only (never values), for logs."""
    if not isinstance(answer, dict) or not answer:
        return "no object"
    return "keys=" + ",".join(sorted(str(key) for key in answer)[:12])


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


# ---------------------------------------------------------------------------
# THE INSERT CALL — the door names it, the harness sends it
# ---------------------------------------------------------------------------
# `missing_who` is not a question about words. The door says exactly which
# call answers it, in the call's own JSON, so the answer is to SEND THAT CALL.
# Asking a model to reword a step that is not wrong is how the old contact
# loop burned its rounds.
_INSERT_KEY = re.compile(r"[\"']?insert[\"']?\s*:\s*\{")


def _balanced_object(text: str, start: int) -> str | None:
    """The JSON object that OPENS at `start`, brace-counted, strings skipped."""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    quote = ""
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in "\"'":
            in_string = True
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : position + 1]
    return None


def read_insert(text: Any) -> dict[str, Any] | None:
    """The `{"before": N, "step": {...}}` the door wrote into its question.

    Robust to spacing and to whichever quotes the door used, because what is
    being read is a sentence with JSON in it and not a JSON document. None
    when there is no insert call in the words at all.
    """
    raw = str(text or "")
    if not raw:
        return None
    for match in _INSERT_KEY.finditer(raw):
        chunk = _balanced_object(raw, raw.find("{", match.end() - 1))
        if chunk is None:
            continue
        try:
            parsed = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(parsed, dict) or not isinstance(parsed.get("step"), dict):
            continue
        out: dict[str, Any] = {"step": dict(parsed["step"])}
        before = parsed.get("before")
        if isinstance(before, bool):
            before = None
        if isinstance(before, (int, float)):
            out["before"] = int(before)
        elif isinstance(before, str) and before.strip().isdigit():
            out["before"] = int(before.strip())
        return out
    return None


def who_odds(step: Any) -> float:
    """The odds the who step carries: the named step's own number, or 0.5.

    The bench clamps it like every other step's, so nothing is clamped here.
    """
    if isinstance(step, dict):
        odds = step.get("declared_odds")
        if isinstance(odds, (int, float)) and not isinstance(odds, bool):
            return float(odds)
    return DEFAULT_WHO_ODDS


def who_step(odds: Any = None) -> dict[str, Any]:
    """The three fields of a who step. Everything else on it is the bench's."""
    return {
        "verb": WHO_VERB,
        "who": "person",
        "declared_odds": odds if isinstance(odds, (int, float)) and not isinstance(odds, bool)
        else DEFAULT_WHO_ODDS,
    }


def who_insert(fix: Any, step: Any = None, index: int | None = None) -> dict[str, Any] | None:
    """THE CALL THAT PUTS THE WHO STEP IN, built from the row the door gave.

    A door that writes the whole call into its sentence is read first and the
    call is sent back as it came. Contract 4.0 cuts that sentence to one line,
    so the call is BUILT instead from what the row already says: the step that
    reaches a person, the who step in front of it (`before` counts from ONE,
    like move and drop), and its odds are that step's own.
    """
    fix = fix if isinstance(fix, dict) else {}
    for key in ("say", "question", "fix", "detail", "message", "current"):
        found = read_insert(fix.get(key))
        if found is None:
            continue
        if not isinstance(found.get("before"), int):
            if index is None:
                continue
            found["before"] = index + 1
        return found
    if index is None:
        return None
    return {"before": index + 1, "step": who_step(who_odds(step))}


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

UNKNOWN_FIELD = "unknown_field"
STATE_NEEDED = "needed"
KIND_CONTACT = "contact"
PERSON_SLOT_WHO = "who"

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

# THE SAME LAW READ OFF A PATH, ON EVERY ROAD. `slots_the_agent_owes` reads a
# slot NAME, which only covers the slots road; a patch that came back off a
# fix, or off the blanks, has nothing but its path to go on, and the address
# can sit anywhere along it: `...args.cc_emails`,
# `...args.to_recipients.0.emailAddress.address`.
#
# AN ADDRESS IS AN ADDRESS UNDER ANY NAME (2026-09-17). The rule here used to
# be a LIST -- to, from, cc, bcc, sender, and anything starting `recipient` or
# `attendee` -- and a service spells the same thing its own way. Read against
# the server's own answer (`act_kinds.calls.address_roles` asked of all 616
# tools in the catalog: 31 marked names), that list missed thirteen of them,
# `extra_recipients`, `to_number`, `email`, `bcc_recipients`, `cc_recipients`,
# `toRecipients`, `from_email`, `reply_to` and the rest. The door refuses
# every one of them, so none of it ever escaped -- it cost the agent a round,
# and a round is the thing this loop has least of.
#
# So the rule is DERIVED the way the server derives it, not listed: a name's
# WORDS are read (snake_case and camelCase both), and it is an address when it
# opens with an addressing word, or carries an addressing noun anywhere, and
# says none of the words that mean it is something else. Too strict beats too
# loose on anything that decides who receives a message -- but only just, both
# ways: a name wrongly refused costs a round the model spends writing it
# again, and a name wrongly allowed costs the round the door spends refusing
# it.
_ADDRESS_HEADS = frozenset(
    {"to", "cc", "bcc", "recipient", "recipients", "attendee", "attendees",
     "invitee", "invitees", "email", "emails", "address", "addresses",
     "phone", "phones", "msisdn", "msisdns", "mobile", "mobiles",
     "whatsapp", "personalization", "personalizations"}
)
# The sending side is the person's own account and just as much theirs: a name
# that opens `from` or `sender`, or the phrases `send as` and `reply to`.
_SENDER_HEADS = frozenset({"from", "sender"})
_SENDER_PHRASES = (("send", "as"), ("reply", "to"))
# A word that says address wherever it sits: `recipient_email`, `cc_emails`,
# `emailAddress`. Short words are heads only, so `bcc` inside something else
# and the `to` in `in_reply_to` are not addresses.
_ADDRESS_NOUNS = frozenset(
    {"address", "addresses", "mail", "email", "emails", "recipient",
     "recipients", "attendee", "attendees", "invitee", "invitees",
     "personalization", "personalizations", "phone", "phones", "msisdn",
     "msisdns", "mobile", "mobiles", "whatsapp", "cell"}
)
# A display name rides beside an address and is not one; an id is the row's,
# never a person's; and a subject, a body, a count, a date or a label cannot
# hold one. `from_date`, `attendee_count`, `email_subject` and `message_id`
# are ordinary names and stay ordinary.
_NOT_AN_ADDRESS = frozenset(
    {"name", "names", "id", "ids", "count", "counts", "subject", "body",
     "text", "date", "dates", "time", "times", "note", "notes", "label",
     "labels"}
)
# THE ONE THE WORDS CANNOT READ, and it is the server's, not ours: `updates`
# on OUTLOOK_BATCH_UPDATE_MESSAGES says nothing in its name and is marked
# because a Graph batch update can set `toRecipients` INSIDE it. Anything the
# server marks that its own name does not say belongs here; the test walks the
# server's captured list and fails if another one appears.
_MARKED_BUT_UNSPOKEN = frozenset({"updates"})
_WORD_RE = re.compile(r"[A-Za-z][a-z0-9]*|\d+")


def _words_of(name: str) -> tuple[str, ...]:
    """One argument name's words, snake_case and camelCase alike."""
    return tuple(word.lower() for word in _WORD_RE.findall(name))


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _names_an_address(name: str) -> bool:
    """True when this ONE name is who something goes to or comes from.

    THE CASE IS PART OF THE NAME, so nothing here lowers it before the words
    are read: `toRecipients` is two words and `torecipients` is one.
    """
    if name.lower() in _MARKED_BUT_UNSPOKEN:
        return True
    words = _words_of(name)
    if not words:
        return False
    tokens = set(words) | {_singular(word) for word in words}
    if tokens & _NOT_AN_ADDRESS:
        return False
    for phrase in _SENDER_PHRASES:
        if words[: len(phrase)] == phrase:
            return True
    if words[0] in _SENDER_HEADS:
        # `from` alone is the sending address; `from_email` and `from_phone`
        # say so; `from_date` says something else entirely.
        return len(words) == 1 or bool(tokens & (_ADDRESS_NOUNS | {"number"}))
    return words[0] in _ADDRESS_HEADS or bool(tokens & _ADDRESS_NOUNS)


def writes_a_person(path: Any) -> bool:
    """True when any part of this path is who something goes to or comes from."""
    return any(
        _names_an_address(segment.strip())
        for segment in str(path or "").split(".")
        if segment.strip()
    )


# A STAND-IN IS NOT AN ANSWER. The ask already says not to write one and
# nothing read it back, so a model with nothing to say could put `TBD` in the
# subject line of a real message. Short on purpose: these are the shapes a
# model reaches for when it has no words, not a list of words to police.
STAND_IN_WORDS = frozenset(
    {"tbd", "todo", "to do", "yet", "n/a", "na", "none", "null", "...",
     "…", "[]", "[ ]"}
)
TEXT_SLOT_KINDS = frozenset({"", "text", "short_text", "long_text", "string"})


def is_a_stand_in(value: Any, name: Any = "", kind: Any = "") -> bool:
    """True when a value is a blank wearing words: ``TBD``, ``[subject]``, ``<name>``.

    Only a string is read. A number, a list or a map is somebody's real answer
    in its own shape and nothing here should touch it.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    low = text.lower()
    if low in STAND_IN_WORDS:
        return True
    if low == str(name or "").strip().lower():
        return True
    if (low[0], low[-1]) in (("[", "]"), ("<", ">")):
        return True
    return len(text) < 2 and str(kind or "").strip().lower() in TEXT_SLOT_KINDS


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


def rows_on_step(rows: Any, number: Any) -> list[dict[str, Any]]:
    """Every row that sits on one step, including the rows with no slot."""
    return [row for row in (rows or []) if row.get("step") == number]


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


def says_on_step(rows: Any, number: Any) -> list[str]:
    """The bench's own sentences for one step's agent rows, in order."""
    out: list[str] = []
    for row in fixed_by(rows_on_step(rows, number), AGENT):
        said = str(row.get("say") or "").strip()
        if said and said not in out:
            out.append(said)
    return out


def who_step_is_missing(answer: Any, number: Any) -> bool:
    """True when this step reaches a person and no step asks who first.

    Read off the slot table, never off a code: a step that reaches somebody
    carries a `contact` slot the PLATFORM fills from `person.who`, and the
    step the person picks on is the one `form_steps` marks `person_slot`
    "who". One who step per plan, so a form that already has one is never
    given another.
    """
    entry = step_entry(answer, number)
    if entry is None:
        return False
    reaches = any(
        slot.get("kind") == KIND_CONTACT and slot.get("filler") == PLATFORM
        for slot in slots_of(entry)
    )
    if not reaches:
        return False
    return not any(
        row.get("person_slot") == PERSON_SLOT_WHO for row in form_steps_of(answer)
    )


def _walk(value: Any, parts: Sequence[str]) -> Any:
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def unknown_key_rows(rows: Any, number: Any) -> list[dict[str, Any]]:
    """The agent's own unknown-key rows on ONE step, in the door's order."""
    return [
        row
        for row in fixed_by(rows_on_step(rows, number), AGENT)
        if row.get("problem") == UNKNOWN_FIELD
    ]


def _slot_parts(row: Any) -> list[str]:
    """The container a row names, as a path inside the step. [] is the step."""
    slot = str((row or {}).get("slot") or "")
    if slot in ("", "step"):
        return []
    return [part for part in slot.split(".") if part]


def _keys_named_by(row: dict[str, Any], container: dict[str, Any]) -> list[str]:
    """Exactly the keys this row is about, and no sibling of theirs.

    The bench names them outright on `keys` when it knows them -- an argument
    a send does not take. The structural rows (a key on the step, in a wait
    condition, in an ask) give `accepted` instead, the names that container
    DOES have room for, and the offenders are what is left over. With neither,
    nothing comes off: emptying a container we cannot read is how a wait ends
    up as `{}` and the bench refuses it for having no conditions.
    """
    named = row.get("keys")
    if isinstance(named, list) and named:
        return sorted({str(key) for key in named if str(key) in container})
    allowed = {str(name) for name in (row.get("accepted") or [])}
    if not allowed:
        return []
    return sorted(key for key in container if key not in allowed)


def _prune_empty(step: dict[str, Any], parts: Sequence[str]) -> None:
    """Take a container that is now empty off its parent, and so on upward.

    A wait condition stripped down to `{}` is not a fix: the bench reads it as
    a condition with no source and refuses the wait for it. An empty thing is
    a thing that is not there, so it goes, and if that empties its parent the
    parent goes too. The step itself is never taken off here -- dropping a
    step is the door's own call and a different decision.
    """
    for depth in range(len(parts), 0, -1):
        node = _walk(step, parts[:depth])
        if node not in ({}, []):
            return
        parent = step if depth == 1 else _walk(step, parts[: depth - 1])
        last = parts[depth - 1]
        if isinstance(parent, dict):
            parent.pop(last, None)
        elif isinstance(parent, list) and last.isdigit() and int(last) < len(parent):
            parent.pop(int(last))
        else:
            return


def _value_at(step: Any, number: int, path: str) -> tuple[bool, Any]:
    """(is it still there, what it holds) for a door path inside one step."""
    tail = path.split(f"steps.{number - 1}", 1)[-1]
    parts = [part for part in tail.split(".") if part]
    if not parts:
        return True, step
    value = _walk(step, parts)
    return value is not None, value


def keys_to_take_off(rows: Any, form: Any) -> tuple[list[str], str, Any] | None:
    """Every unknown-key row on ONE step as ONE patch: (keys, path, value).

    The row names the container -- the step itself, its `wait`, one of its
    conditions -- on `slot`, and says which keys are the offenders: outright on
    `keys`, or by giving the names that container DOES have room for on
    `accepted`. So the fix needs no model call, and it needs no guess either:
    ONLY the keys named come off. What it used to do was rebuild the value out
    of the whole cleaned step and pop whatever was not in one row's accepted
    list, which put back a key another row had flagged, and emptied a wait
    condition to `{}` -- which the bench then refused for having no conditions
    (checker, 2026-09-17).

    ALL THE ROWS ON ONE STEP, IN ONE PATCH. Two unknown keys on one step used
    to cost two rounds, and the second patch was built off a step the first
    had already changed. Several rows are answered by sending the whole step
    back once; one row alone is still answered at its own narrow path, because
    replacing a step to take one key off it loses everything the same round
    wrote.

    A key that was carrying meaning is not lost silently -- the keys are
    logged, and what they meant belongs in one of `accepted`.

    None when there is nothing here to do without a model: no unknown-key row,
    a row this answer's form does not hold, or a row that names no keys at all.
    """
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    rows = [row for row in rows if row.get("problem") == UNKNOWN_FIELD]
    if not rows:
        return None
    number = rows[0].get("step")
    steps = (form or {}).get("steps") if isinstance(form, dict) else None
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    if not isinstance(steps, list) or not 0 < number <= len(steps):
        return None
    if not isinstance(steps[number - 1], dict):
        return None
    rows = [row for row in rows if row.get("step") == number and row.get("path")]
    if not rows:
        return None
    step = json.loads(json.dumps(steps[number - 1], default=str))
    taken: list[str] = []
    for row in rows:
        parts = _slot_parts(row)
        container = step if not parts else _walk(step, parts)
        if not isinstance(container, dict):
            continue
        named = _keys_named_by(row, container)
        if not named:
            continue
        for key in named:
            container.pop(key, None)
        taken.extend(named)
        _prune_empty(step, parts)
    if not taken:
        return None
    prefix = "form." if str(rows[0]["path"]).startswith("form.") else ""
    whole = f"{prefix}steps.{number - 1}"
    if len(rows) == 1:
        path = str(rows[0]["path"])
        there, value = _value_at(step, number, path)
        # A path we pruned away is not a path to patch: send the step instead.
        if there:
            return sorted(set(taken)), path, value
    return sorted(set(taken)), whole, step


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


# ---------------------------------------------------------------------------
# WHERE A PATCH CAN LAND, AND WHAT A ROW MAY BE ANSWERED WITH (0.55.3)
# ---------------------------------------------------------------------------
# WHAT FORCED ALL OF THIS (Sam, prod want 355b9b91, 2026-09-25). The plan door
# named a falling odds line one step at a time, and each fix ask said "change
# exactly this one number", so Sam raised step 1, which made step 2 fall, and
# so on down five steps. Then the door named a row with no step and the path
# `form`. The loop asked for a patch AT `form`, the model sent
# `form = {"step": 6, "declared_odds": 0.84}`, the door stored that as a key
# called `form` inside the form, and the next two rounds went on
# `form.form = null`. Every one of those rounds was charged against the row,
# and the plan closed `plan_failed`.
#
# Three things follow, and none of them is about odds or about one code:
#   * the form has four places at the top and no others, so a path that names
#     the form itself, the whole step list, or a key the form does not have is
#     never sent, on any road;
#   * a row whose path is not one of those places is answered by asking for
#     patches to named places, with the bench's sentence in front of the model;
#   * a key the door says may NOT be there, with nothing it would take instead,
#     comes off, unless it is a key the step cannot stand without, because
#     taking that off only brings the same row back as `empty` next round.
FORM_TOP_KEYS = frozenset({"overview", "odds", "span_days", "steps"})
PLAN_MUST_CARRY = frozenset({"overview", "odds", "span_days"})
# What the door asks for again the moment a step lacks it (plan_form's own
# step questions). Taking one of these off answers nothing.
STEP_MUST_CARRY = frozenset({"verb", "do_line", "hand_over_line", "declared_odds",
                             "proof", "who"})
NOT_ALLOWED = "not_allowed"
# The numbers the plan's odds line is made of: the overall forecast, then one
# per step. A row about either is a row about the line.
ODDS_FIELDS = frozenset({"odds", "declared_odds"})
_BRACKET_INDEX = re.compile(r"\[(\d+)\]")


def path_parts(path: Any) -> list[str]:
    """A dotted path as its parts; `steps[2]` reads as `steps.2`."""
    text = _BRACKET_INDEX.sub(r".\1", str(path or "").strip())
    return [part for part in text.split(".") if part]


def form_parts(path: Any) -> list[str] | None:
    """The parts of a path INSIDE the form, or None when it is not a form path.

    `form.steps.2.do_line` is ["steps", "2", "do_line"]; `form` is [] (the form
    itself); `steps.2.title` is None, the older document dialect, which this
    package does not judge.
    """
    parts = path_parts(path)
    if not parts or parts[0] != "form":
        return None
    return parts[1:]


def names_a_place(path: Any) -> bool:
    """True when a patch at `path` lands on ONE real place in the plan.

    The form has `overview`, `odds`, `span_days` and `steps` at the top and
    nothing else. A place is one of the three plan fields, one whole step, or
    anything inside a step. NOT a place: an empty path, `form` itself,
    `form.steps` (every step at once), and a key the form does not have
    (`form.form`). The door stores a patch at any of those as a stray key and
    charges the row a try for it. A step number past the end is the door's to
    judge: it answers `not_found` and charges nothing. A path in the older
    document dialect (no `form.`) is the door's to judge too.
    """
    if not path_parts(path):
        return False
    parts = form_parts(path)
    if parts is None:
        return True
    if not parts or parts[0] not in FORM_TOP_KEYS:
        return False
    if parts[0] != "steps":
        return len(parts) == 1
    return len(parts) >= 2 and parts[1].isdigit()


def is_a_leaf_place(path: Any) -> bool:
    """A place that is ONE value: a plan field, or a field inside one step.

    A whole step is a place and not a leaf. The asks that let the model patch
    several paths at once (the odds line, the plan-wide row) take leaves only,
    because a step sent back whole from a question about numbers is a step
    rewritten by accident.
    """
    if not names_a_place(path):
        return False
    parts = form_parts(path)
    if parts is None:
        return True
    return parts[0] != "steps" or len(parts) >= 3


def value_in_form(form: Any, path: Any) -> tuple[bool, Any]:
    """(is the key there, what it holds) for a form path."""
    parts = form_parts(path)
    if not parts or not isinstance(form, dict):
        return False, None
    node: Any = form
    for part in parts:
        if isinstance(node, dict):
            if part not in node:
                return False, None
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


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


def can_come_off(row: Any, path: str, answer: Any) -> bool:
    """True when this row is answered by TAKING THE KEY OFF, with no model.

    Rule (0.55.3): `not_allowed` on one key, with nothing on `accepted`, says
    the key may not be there, so a new value for it is not an answer. It comes
    off -- a null, which is how the door blanks a value -- when all of these
    hold: the agent fixes it, the path is a leaf inside one step, the key is
    one a step can stand without, the step's own table does not mark it
    required, and it holds one plain value right now (a value already blanked
    is not blanked twice: that row goes to the model with the bench's words).

    A key the step MUST carry is not taken off. The door asks for it again
    the moment it is empty, so taking it off buys the same row back as
    `empty` and spends one of the row's three tries doing it. Until the bench
    relabels them, that is exactly the odds rows: the falling-line row reads
    `not_allowed` with nothing accepted, and blanking the number would cost a
    try on every step.
    """
    if not isinstance(row, dict) or row.get("who_fixes") != AGENT:
        return False
    if row.get("problem") != NOT_ALLOWED or accepted_of(row):
        return False
    form = form_of(answer)
    parts = form_parts(path)
    if not parts or parts[0] != "steps" or len(parts) < 3:
        return False
    if not names_a_place(path) or parts[-1].isdigit():
        return False
    if len(parts) == 3 and parts[2] in STEP_MUST_CARRY:
        return False
    for slot in slots_of(step_entry(answer, int(parts[1]) + 1)):
        if slot.get("path") == path and slot.get("required"):
            return False
    there, value = value_in_form(form, path)
    return there and value is not None and not isinstance(value, (dict, list))


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


def the_line_of(answer: Any, field: str) -> list[dict[str, Any]]:
    """One field on every step, with its path: what a problem that moves from
    step to step is about. [] when no step carries it."""
    form = form_of(answer) or {}
    out: list[dict[str, Any]] = []
    for index, step in enumerate(form.get("steps") or []):
        if isinstance(step, dict) and field in step:
            out.append({"path": f"form.steps.{index}.{field}", "value": step.get(field)})
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

PROPOSAL_INSTRUCTION = (
    "Answer the want below with a PROPOSAL: eight fields, ONE reply, nothing "
    "else. A proposal is your short answer to what this person wants; it is "
    "what they choose between. You do NOT write a plan here -- no steps, no "
    "blocks, no account rows, no deliverables, no grant requests, no finish "
    "line, no wins, no capabilities, no skill research, no separate strategy "
    "block. If they pick you, the bench hands you a form and you write the "
    "plan then.\n"
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
    f"A paragraph over {PROPOSAL_BODY_MAX} characters is REFUSED by the bench, "
    "not trimmed, so count and stay inside it; do not pad it.\n"
    'Answer: {"pitch_title": "...", "headline": "...", "pitch_body": "...", '
    '"odds": 0.0, '
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

    ONE reading of "the want offers this", shared by the proposal's
    `tools_needed` (REJ-01) and by the plan's steps (W29). An exact match, the
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


def read_proposal(answer: dict[str, Any]) -> dict[str, Any]:
    """The eight fields out of the model's answer, and nothing else.

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
    # The headline is read like the title: the model's words, stripped, never
    # cut here. A long one is the door's to trim, and the door says so.
    for field in ("pitch_title", "headline", "pitch_body"):
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

    The filing road for a proposal that carries steps is the old plan-shaped
    one -- required blocks merged in, contacts bound, the blank form dropped,
    the local mirror consulted -- and every one of those repairs reads
    `steps`. An eight-field proposal has none, so none of them can run over it,
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
    "of a step -- they are written for step 1 and the same questions are "
    "asked of every step you write -- and `and_for_the_plan` is what the plan "
    "itself asks. A question that lists `choices` is a PICK: answer with one "
    "of those words exactly as it is written. A question that names a number "
    "of characters is a CAP: stay within it, and write short rather than "
    "padding to fill it.\n"
    "The plan itself needs `overview` (the scope of work in complete "
    "sentences: the work, the deliverables, the boundaries, what you need "
    "from the person) and `odds` (YOUR chance of achieving the whole goal "
    "within `span_days`, a number greater than 0 and less than 1). Each "
    "step's `declared_odds` is that same chance judged once the step is done, "
    "so the line starts at or above `odds` and never falls from one step to "
    "the next (`forecast_context` is the bench's own rule for the numbers).\n"
    "`you_may_also_use` are the extra picks a step MAY carry. Leave out what "
    "this plan does not need; a step that carries none is a normal step.\n"
    "NEVER GIVE YOURSELF WORK ON A SERVICE YOU HAVE NO TOOL FOR. A design "
    "site, a browser, a phone, a shop: if it is not on this want's tool list "
    "and you cannot do it with what you have, it is not yours. Make it the "
    "PERSON's step -- `who`: \"person\", with `do_ask` {\"link\": the page "
    "where they do it, \"cost_cents\", \"cost_note\"} -- or leave it out of "
    "the plan. A step you cannot walk is a step that stops on you: the person "
    "taps Allow and then waits for hands you do not have.\n"
    "WRITE AS MANY STEPS AS THE PLAN NEEDS -- two or twenty, there is no cap "
    "-- so add entries to the blank form or drop them freely. You never write "
    "a connect row, a grant request, a block title, a room list, a schedule "
    "row or a pointer: the bench writes every one of those from your picks.\n"
    + WHO_STEP_SENTENCE
    + 'Answer: {"overview": "...", "odds": 0.35, "span_days": 14, "steps": '
    '[{"verb": "finds", "do_line": "...", "hand_over_line": "...", '
    '"need_line": "", "declared_odds": 0.4, "proof": "text", "who": "agent"}, '
    '...]}.'
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


# NEVER PLAN WORK ON A SERVICE THIS AGENT HAS NO TOOL FOR (W29, 2026-09-17)
# ---------------------------------------------------------------------------
# WHAT FORCED IT: prod deal 4f061f54, step 3. Kai's plan put "design the
# invite" on Canva. Nobody here has a tool for Canva, so the bench read the
# step the way it reads any tool the catalog does not know and declared an
# OUTSIDE act (rule 232): the person tapped Allow, the step became the
# agent's to go and do itself -- in a browser it does not have -- and it sat
# there while the bench said "Agent working" on every poll for seven minutes.
# Steven failed the step by hand and the want reposted.
#
# A tool the want does not offer is not work this agent can do. It is work the
# PERSON does, on a step of their own with a link they tap and what it costs
# them before they tap (`do_ask`, the DO card), or it is not in the plan.
SERVICE_WHO_PREFIX = "service:"


def _service_this_want_offers_no_tool_for(step: Any, catalog: set[str]) -> str | None:
    """The outside service this step hands the agent, or None."""
    if not isinstance(step, dict):
        return None
    who = str(step.get("who") or "").strip()
    if who.lower().startswith(SERVICE_WHO_PREFIX):
        return who[len(SERVICE_WHO_PREFIX):].strip() or who
    entries = step.get("tool")
    rows = entries if isinstance(entries, list) else [entries]
    for entry in rows:
        slug = entry.get("tool") if isinstance(entry, dict) else entry
        name = str(slug or "").strip()
        if name and catalog_match(name, catalog) is None:
            return name
    return None


def person_does_what_the_agent_cannot(
    form: dict[str, Any], brief: Any
) -> tuple[dict[str, Any], list[str]]:
    """Hand every step that names a tool-less service back to the person.

    Returns the form and one line per step that moved. The step keeps the
    model's own lines and becomes the PERSON's: `who` is "person" and the tool
    comes off, so the bench stamps no outside act on it. Nothing is invented
    -- the `do_ask` link and its price are the model's to write, and the door
    asks for them as blanks; a person step carrying none is a PROVIDE or an
    APPROVE there, never a refusal.

    A want that publishes no tool list at all changes nothing: this package
    refuses nothing on its own opinion, the same law `pick_tools` keeps.
    """
    if not isinstance(form, dict) or not isinstance(form.get("steps"), list):
        return form, []
    catalog = tool_slugs(brief)
    if not catalog:
        return form, []
    moved: list[str] = []
    for index, step in enumerate(form["steps"], start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("who") or "").strip().lower() == "person":
            continue
        named = _service_this_want_offers_no_tool_for(step, catalog)
        if named is None:
            continue
        step["who"] = "person"
        step.pop("tool", None)
        moved.append(f"step {index} ({named})")
    return form, moved


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
        # What the last model reply was (empty / malformed_json / object /
        # api_error), for the fix loop's logs.
        self.last_reply = ""
        self.trail: list[dict[str, Any]] = []
        # Every patch sent, in order, so a repeated fix can be handed back with
        # what the agent already tried.
        self._sent: list[tuple[str, Any]] = []
        # The stable prefix for this run, built once in `run` and unchanged
        # after. Until then, the front door alone.
        self.prefix: str = FRONT_DOOR
        # The lab lead's standing direction (focus.md), riding the proposal
        # call when set; empty means the call is unchanged.
        self.standing_direction: str = ""
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
        # WHAT THE LAST ROUND SENT, AND WHICH ROW IT ANSWERED (0.55.3). "Your
        # last patch did not clear this" is said only about a row this loop
        # really answered with a patch; Sam was told it at round 4 about a
        # number it had never patched, because the round before had written a
        # slot while the same row stood.
        self._last_round: list[dict[str, Any]] = []
        self._answered: dict[str, list[dict[str, Any]]] = {}
        self._last_answered: tuple[str, str, str, Any] | None = None
        # A key taken off because the door said it may not be there, and the
        # door's sentence about it, so a later ask about that place says why.
        self._taken_off: dict[str, dict[str, Any]] = {}
        # The odds this agent's proposal on this want was filed at, when the
        # caller knows it; the odds line carries it, null when unknown.
        self.proposal_odds: Any = None

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
        try:
            response = self.model.invoke(
                system=self.prefix,
                messages=[ModelMessage.text("user", tail)],
                tools=[],
            )
        except Exception as exc:
            # The exception CLASS only: provider messages can echo headers.
            self.last_reply = f"api_error:{type(exc).__name__}"
            self.log.warning("draft loop ask %s: api_error %s",
                             what or "?", type(exc).__name__)
            raise
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
        text = str(getattr(response, "text", "") or "")
        parsed = read_json_object(text)
        # WHAT CAME BACK, told apart: empty, not JSON, or an object (whose
        # shape the caller judges). Lengths and key names only, never text.
        if not text.strip():
            self.last_reply = "empty"
        elif not parsed:
            self.last_reply = f"malformed_json({len(text)} chars)"
        else:
            self.last_reply = f"object({len(text)} chars)"
        self.log.info("draft loop ask %s: reply %s %s", what or "?",
                      self.last_reply, answer_shape(parsed))
        return parsed

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
            line["problem"],
            " CLOSED" if line["closed"] else (" READY" if line["ready"] else ""),
        )

    # -- the three doors ---------------------------------------------------
    def _put(self, target_id: str, kind: str, outline: dict[str, Any]) -> dict[str, Any]:
        answer = self.provider.put_draft(target_id, outline, kind=kind)
        self.rounds = 0
        self._last_round = []
        self._record(target_id, kind, answer, "outline")
        return answer

    PREVIEW = 120
    # The third naming of one key in a run ends the draft -- the bench's own
    # `fix_key`, so both sides count one fault once. Cumulative, not
    # consecutive: two problems taking turns never repeat in a row and used to
    # run for ever. Two is the better prompt (see `_answer_the_fixes`); three
    # is an answer.
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
        self,
        target_id: str,
        kind: str,
        patches: Sequence[dict[str, Any]],
        what: str,
        standing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # THE ONE GATE EVERY ROAD GOES THROUGH. Who a message goes to and who
        # it comes from are the person's own picks, wired by the platform. The
        # slots road read that off the slot table, which left the fix road and
        # the blanks road with no guard at all: a model asked to reword a
        # do_line could answer `...args.to` and it went out. The path is
        # refused whatever road it came down, and wherever along the path the
        # address sits. The path is logged; the value never is.
        sending: list[dict[str, Any]] = []
        for entry in patches:
            if writes_a_person(entry.get("path")):
                self.log.warning(
                    "draft loop %s target=%s: %s says who something goes to or "
                    "comes from; that is the person's pick and never the "
                    "agent's to write, so it was not sent",
                    kind,
                    target_id,
                    str(entry.get("path") or "?"),
                )
                continue
            # NOT A PLACE IN THE PLAN, NOT SENT (0.55.3). The form has four
            # keys at the top; a patch at `form` itself, at `form.steps` whole
            # or at a key the form does not have is stored by the door as a
            # stray key (`form.form`) and costs the row a try every round.
            if not names_a_place(entry.get("path")):
                self.log.warning(
                    "draft loop %s target=%s: %s is not a place in the plan (the "
                    "form holds overview, odds, span_days and steps); it was "
                    "not sent",
                    kind,
                    target_id,
                    str(entry.get("path") or "?"),
                )
                continue
            sending.append(entry)
        if not sending:
            self.log.warning(
                "draft loop %s target=%s: nothing was left to send for %s; the "
                "door keeps the draft it has",
                kind,
                target_id,
                what,
            )
            if standing is not None:
                return standing
            return {
                "ok": False,
                "error": "nothing_to_send",
                "message": (
                    "Every patch named who something goes to or comes from, so "
                    "nothing was sent."
                ),
            }
        patches = sending
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
        self._last_round = [
            {"path": str(entry.get("path") or ""), "value": entry.get("value")}
            for entry in patches
        ]
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
        form, handed_over = person_does_what_the_agent_cannot(form, brief)
        if handed_over:
            # W29. An outside act on a service with no tool is a step nobody
            # can walk: the person tapped Allow and then waited on an agent
            # that had no hands for it.
            self.log.warning(
                "draft loop %s target=%s: %d step(s) named a service this want "
                "offers no tool for and are now the person's own DO step (%s)",
                kind,
                target_id,
                len(handed_over),
                ", ".join(handed_over),
            )
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
        the tools this want offers go in; eight fields come back; the bench's
        own proposal door takes them. No outline, no blanks, no fixes: there
        is no plan here to fix. A paragraph over the cap is cut to it first
        (`_fit_the_body`), because the door refuses it rather than trimming;
        an empty headline gets one more small ask (`_headline_for`), because
        the door refuses that too; a long headline is only logged, because the
        door trims it. Anything the door still corrects comes back on
        `bench_fixed` or `trimmed` and is logged, never retried.
        """
        payload: dict[str, Any] = {
            "want": (brief or {}).get("want") if isinstance(brief, dict) else None,
            "what_the_person_said": person_strategy(brief),
            "budget_cents": budget_of(brief),
            "tools_on_this_want": tools_index(brief),
        }
        if self.standing_direction:
            payload["lab_lead_standing_direction"] = self.standing_direction
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
                    for key in ("pitch_title", "pitch_body")
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

    def _fill_the_blanks(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any
    ) -> dict[str, Any]:
        """One step at a time: the step as the bench expanded it, its blanks,
        and the patches that answer them."""
        for index, rows in group_blanks(answer.get("blanks")):
            if answer.get("closed") or answer.get("ready") or _spent(answer):
                break
            if is_paused(answer):
                # Nothing sent while the door is shut: the fix loop after this
                # says so properly, with the reason and the hour.
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
                standing=answer,
            )
        return answer

    def _fill_what_the_steps_need(
        self,
        target_id: str,
        kind: str,
        answer: dict[str, Any],
        want: Any,
        numbers: Sequence[int],
        already: set[int],
    ) -> dict[str, Any]:
        """SLOTS BEFORE WORDS: write the holes the door named, then fix.

        The door states what goes in every step. A slot the AGENT fills, that
        is still `needed`, that the step is `required` to have, and that is
        written at filing rather than per item at the step, is a hole -- and a
        hole is not answered by rewording something else. One ask covers every
        such slot on the steps given, one patch each, one round.

        Each step is asked for ONCE per run. A door that keeps naming a slot
        after it has been written is a fix, not a hole, and the fix road with
        its brake is what answers that.
        """
        needs: list[dict[str, Any]] = []
        for number in numbers:
            if number in already:
                continue
            already.add(number)
            entry = step_entry(answer, number)
            owed = slots_the_agent_owes(answer, number)
            if not owed:
                continue
            needs.append(
                {
                    "step_number": number,
                    "action": (entry or {}).get("action") or "",
                    "step": _fit(self._step_for(answer, number - 1)),
                    "needs": [
                        {
                            "name": slot.get("name"),
                            "kind": slot.get("kind"),
                            "accepted": list(slot.get("accepted") or []),
                            "path": slot.get("path"),
                        }
                        for slot in owed
                    ],
                }
            )
        if not needs:
            return answer
        wanted = {
            str(slot["path"]): slot["name"]
            for step in needs
            for slot in step["needs"]
            if slot.get("path")
        }
        kinds = {
            str(slot["path"]): slot.get("kind") or ""
            for step in needs
            for slot in step["needs"]
            if slot.get("path")
        }
        self.log.info(
            "draft loop %s target=%s: the form says %d thing(s) are still the "
            "agent's to write (%s); writing them before anything is reworded",
            kind,
            target_id,
            len(wanted),
            ", ".join(sorted(wanted.values())),
        )
        payload: dict[str, Any] = {
            "want": want,
            "plan": outline_summary(answer.get("draft")),
            "needs": needs,
        }
        instruction = with_the_person_said(
            NEEDED_SLOTS_INSTRUCTION, payload, answer
        )
        # The stance rides; the bench's whole example PLAN does not. What is
        # being asked for here is a subject and a message, and a finished plan
        # printed above that question is a page of shape nobody needs to write
        # one sentence.
        reply = self._ask(
            instruction, payload, f"write {len(wanted)} needed slot(s)",
            head=self._head(),
        )
        patches, stand_ins = self._words_for_the_holes(reply, wanted, kinds)
        if stand_ins:
            # A PLACEHOLDER IS A HOLE THAT LEARNED TO TYPE. Ask once more,
            # once, saying so; whatever comes back a placeholder the second
            # time is not sent, and the door names the slot again as an
            # `empty` row next round.
            self.log.info(
                "draft loop %s target=%s: %s came back as a placeholder and not "
                "words; asking once more",
                kind,
                target_id,
                ", ".join(sorted(stand_ins)),
            )
            again = self._ask(
                instruction + STAND_IN_REMINDER,
                payload,
                f"write {len(stand_ins)} needed slot(s) again",
                head=self._head(),
            )
            second, still = self._words_for_the_holes(again, wanted, kinds)
            if still:
                self.log.warning(
                    "draft loop %s target=%s: %s is still a placeholder after "
                    "the second ask; it was not sent and stays a hole",
                    kind,
                    target_id,
                    ", ".join(sorted(still)),
                )
            have = {entry["path"] for entry in patches}
            patches = patches + [
                entry for entry in second if entry["path"] not in have
            ]
        if not patches:
            self.log.warning(
                "draft loop %s target=%s: nothing usable came back for the "
                "%d hole(s) the form named; the door will name them as rows",
                kind,
                target_id,
                len(wanted),
            )
            return answer
        return self._patch(
            target_id, kind, patches, f"write {len(patches)} needed slot(s)",
            standing=answer,
        )

    def _words_for_the_holes(
        self,
        reply: Any,
        wanted: dict[str, str],
        kinds: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """The usable answers off one ask, and the names that came back blank.

        A path nobody asked for is not an answer to this question. A value
        that is a placeholder is a hole in a costume: the ask says not to
        write one, and until this read it back a plan could be filed with the
        word TBD where its subject line goes.
        """
        patches: list[dict[str, Any]] = []
        stand_ins: list[str] = []
        for entry in read_patches(reply):
            path = str(entry.get("path") or "")
            value = entry.get("value")
            if path not in wanted:
                continue
            if value is None or value == [] or value == {}:
                stand_ins.append(wanted[path])
                continue
            if is_a_stand_in(value, wanted[path], kinds.get(path, "")):
                stand_ins.append(wanted[path])
                continue
            patches.append({"path": path, "value": value})
        return patches, stand_ins

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

    def _patch_path(self, answer: Any, row: dict[str, Any]) -> str:
        """Where this row is PATCHED -- the narrowest address that is real.

        The row gives the nearest path the door will take. When that is the
        bare step and the row names a slot the step's own table has an address
        for (`subject` on an email step is
        `form.steps.1.tool.args.subject`), the table's address is the narrower
        and truer one: replacing a whole step to change one argument is how a
        fix round loses the rest of the step.

        NO PATH AT ALL MEANS THE STEP, NOT AN EMPTY STRING (checker,
        2026-09-17). `path: null` says this row has no patchable address: the
        answer is to send the step back whole. It used to come out as `""`,
        and the model was asked to patch a path of nothing, which no door
        takes. A step number turns it into that step's own path, which is
        already the whole-step road -- the model is shown every field and
        given the two exits, replace it or drop it.
        """
        path = str(row.get("path") or "")
        slot = str(row.get("slot") or "")
        index = row.get("step")
        numbered = isinstance(index, int) and not isinstance(index, bool) and index > 0
        if slot and (not path or whole_step_path(path) is not None):
            for entry in slots_of(step_entry(answer, row.get("step"))):
                if entry.get("name") == slot and entry.get("path"):
                    return str(entry["path"])
            # A FIELD OF THE STEP ITSELF IS ITS OWN ADDRESS (0.55.3). The
            # falling-odds row names `declared_odds` on the step's path, and
            # the whole-step road that path leads to asks the model to replace
            # or drop a step whose only fault is one number.
            if slot in FORM_STEP_FIELDS:
                if path:
                    return f"{path}.{slot}"
                if numbered:
                    return f"{self._form_prefix(answer)}steps.{index - 1}.{slot}"
        if path:
            return path
        if numbered:
            return f"{self._form_prefix(answer)}steps.{index - 1}"
        return path

    @staticmethod
    def _form_prefix(answer: Any) -> str:
        """`form.` when this door writes its paths that way, else "".

        Read off the answer rather than assumed: the plan door names
        `form.steps.0` and the older bid door names `steps.0`, and a path in
        the wrong dialect is a path the door cannot find.
        """
        for row in problems_of(answer):
            path = str(row.get("path") or "")
            if path.startswith("form."):
                return "form."
            if path.startswith("steps."):
                return ""
        for entry in form_steps_of(answer):
            for slot in slots_of(entry):
                path = str(slot.get("path") or "")
                if path.startswith("form."):
                    return "form."
                if path.startswith("steps."):
                    return ""
        return "form."

    def _answer_the_fixes(
        self, target_id: str, kind: str, answer: dict[str, Any], want: Any
    ) -> dict[str, Any]:
        """ONE ROW AT A TIME, for as long as the door names one the agent owns.

        WHAT THE MODEL IS SHOWN IS WHAT GOES IN, NOT WHAT IS WRONG (contract
        4.0, 2026-09-17). The door's `form_steps` entry for the step is the
        ask: every named input, its kind, who fills it, whether it is wired or
        still needed, what it accepts and the path to patch. The bench's own
        sentences for that step ride underneath. The legacy `codes` go to the
        log and never to the model -- on 17 September one missing subject came
        back under two codes with a paragraph of advice, and three tries on
        one fault is what ended Marcia's plan.

        WHO FIXES IT IS HONOURED. A `platform` row is our bug and never costs
        a model call or a try; a `person` row is a wait. When nothing of the
        agent's own is left, the draft stops and the watcher moves on.

        TWO ROWS ARE ANSWERED WITHOUT A MODEL AT ALL: a key the form has no
        room for is taken off, and a step that reaches somebody with no who
        step above it gets the insert call.

        THE BRAKE IS THE BENCH'S OWN KEY, AND IT COUNTS ACROSS THE WHOLE RUN,
        NOT ROUND BY ROUND. The THIRD naming of one key stops the draft and
        lets the watcher move to another target -- the same key
        `plan_form.count_tries` counts on, so the harness and the bench agree
        on what "the same problem" is. The count is CUMULATIVE on purpose and
        the tests pin it that way: a door that alternates between two problems
        it never clears ran for ever under a consecutive count, because
        neither one was ever named twice in a row.
        Before that, the second naming gets a BETTER PROMPT, not a limit: what
        was sent last round beside what the door has now. Live on 2026-09-09,
        Greg spent rounds 123-132 on one problem, asked in the same words
        every time, and answered it the same way every time.
        """
        named_before: dict[str, int] = {}
        # SLOTS BEFORE WORDS. The steps whose holes have been written, and
        # whether this run has yet written any: the FIRST time a model is
        # asked anything about this plan, every step's holes go in that one
        # ask, because the door has just said what goes in all of them.
        written: set[int] = set()
        first_write = True
        while (
            not answer.get("ready")
            and not answer.get("closed")
            and not _spent(answer)
        ):
            stopped = self._paused(target_id, kind, answer)
            if stopped is not None:
                return stopped
            rows = problems_of(answer)
            mine = fixed_by(rows, AGENT)
            fix = next_fix_of(answer)
            if fix is not None and fix.get("who_fixes") != AGENT:
                # The door only ever points at the agent's own rows. A door
                # that pointed elsewhere is not followed there.
                fix = None
            if fix is None:
                fix = mine[0] if mine else None
            if fix is None:
                stopped = self._not_the_agents_rows(target_id, kind, answer, rows)
                if stopped is not None:
                    return stopped
                break
            # A KEY THE FORM HAS NO ROOM FOR IS TAKEN OFF FIRST, whether or not
            # the door pointed at it. It costs no model call, so waiting for
            # the door to work its way down to it is paying for a fix we
            # already have in hand.
            # EXCEPT OVER A ROW THAT NAMES NO PLACE (0.55.3). The door charges
            # its tries to the row it points at, and against a row at `form`
            # every patch reads as an answer to it: Sam's plan closed on three
            # rounds of that. Such a row is worked first, and worked alone.
            unknown = [entry for entry in mine if entry.get("problem") == UNKNOWN_FIELD]
            if (
                unknown
                and fix.get("problem") != UNKNOWN_FIELD
                and names_a_place(self._patch_path(answer, fix))
            ):
                fix = unknown[0]
            key = fix_key(fix)
            named_before[key] = named_before.get(key, 0) + 1
            if named_before[key] >= self.SAME_PROBLEM_ROUNDS:
                return dict(
                    answer,
                    ok=False,
                    error="draft_stalled",
                    message=(
                        f"The bench named {key} three times and the patches did "
                        "not clear it; paused to avoid repeated model spending. "
                        f"It said: {self._preview(fix.get('say'))}"
                    ),
                )
            number = fix.get("step")
            index = (
                number - 1
                if isinstance(number, int) and not isinstance(number, bool)
                else None
            )

            # A KEY THE FORM HAS NO ROOM FOR IS TAKEN OFF, not asked about.
            # The row names the container and which keys are the offenders:
            # there is nothing here a model knows that the answer has not
            # already said. EVERY such row on this step goes in one patch, so
            # two stray keys cost one round and the second is not built off a
            # step the first already changed.
            taken = keys_to_take_off(unknown_key_rows(rows, number), form_of(answer))
            if taken is not None:
                extra, where, value = taken
                self.log.info(
                    "draft loop %s target=%s: step %s has no room for %s; taking "
                    "it off and sending %s back",
                    kind,
                    target_id,
                    number,
                    ", ".join(extra),
                    where,
                )
                answer = self._patch(
                    target_id,
                    kind,
                    [{"path": where, "value": value}],
                    "take off " + ", ".join(extra),
                    standing=answer,
                )
                self._note_answered(key, fix, where, [{"path": where, "value": value}])
                continue

            # NO WHO STEP ABOVE A STEP THAT REACHES SOMEBODY. The slot table
            # says so, the call that answers it is known, and asking a model
            # to reword a step that is not wrong is how the old contact loop
            # burned its rounds.
            if who_step_is_missing(answer, number):
                inserted = self._insert_the_who_step(
                    target_id, kind, fix, answer, index, str(fix.get("path") or "")
                )
                if inserted is not None:
                    self._last_answered = None
                    answer = inserted
                    # AN INSERT RENUMBERS EVERY STEP BELOW IT, and `written`
                    # is kept by step NUMBER. Step 3's holes, written before
                    # the insert, would mark the OLD step 3 as done while the
                    # step now wearing that number has never been asked for.
                    # The fresh reply says which slots are still needed, so
                    # forgetting the numbers costs nothing: a step whose holes
                    # are filled owes none and is skipped anyway.
                    written.clear()
                    continue

            path = self._patch_path(answer, fix)

            # A KEY THAT MAY NOT BE THERE COMES OFF (0.55.3). `not_allowed`
            # with nothing on `accepted` says the key may not be there, so a
            # new value is not an answer to it -- and asking a model for one
            # is how a key gets re-valued round after round. Every such row
            # goes in one patch with no model call. `can_come_off` keeps the
            # keys a step cannot stand without; those go to the model below,
            # with the bench's sentence.
            if can_come_off(fix, path, answer):
                offs: dict[str, dict[str, Any]] = {path: fix}
                for entry in mine:
                    other = self._patch_path(answer, entry)
                    if other not in offs and can_come_off(entry, other, answer):
                        offs[other] = entry
                form = form_of(answer)
                for where, entry in offs.items():
                    self._taken_off[where] = {
                        "was": value_in_form(form, where)[1],
                        "say": entry.get("say"),
                    }
                self.log.info(
                    "draft loop %s target=%s: the bench says %s may not be there "
                    "and names nothing it would take instead; taking it off",
                    kind,
                    target_id,
                    ", ".join(offs),
                )
                blanks = [{"path": where, "value": None} for where in offs]
                answer = self._patch(
                    target_id, kind, blanks, "take off " + ", ".join(offs),
                    standing=answer,
                )
                self._note_answered(key, fix, path, blanks)
                continue

            # SLOTS BEFORE WORDS. Everything above this point is answered with
            # no model at all; from here a model is asked, and a HOLE is not
            # answered by rewording the row above it. The first ask of a run
            # covers every step, because the door has just stated what goes in
            # all of them; after that, the step the door is naming.
            if first_write:
                first_write = False
                owing = [
                    entry.get("step")
                    for entry in form_steps_of(answer)
                    if isinstance(entry.get("step"), int)
                ]
            else:
                owing = [number] if isinstance(number, int) else []
            filled = self._fill_what_the_steps_need(
                target_id, kind, answer, want, owing, written
            )
            if filled is not answer:
                # That round wrote holes; it did not answer this row.
                self._last_answered = None
                answer = filled
                continue

            form = form_of(answer)
            # NOT A PLACE: the row names the form itself, the whole step list,
            # or a key the form does not have. Nothing is ever sent there; the
            # model is asked for patches to named places instead.
            place = names_a_place(path)
            field = field_of(fix, path)
            odds = field in ODDS_FIELDS
            used, left = attempts_on(answer, fix)
            answered_before = self._answered.get(key)
            # "AGAIN" ONLY WHEN IT IS TRUE (0.55.3): this row was answered with
            # a patch and it is still here -- last round, or at any round the
            # door has charged a try for.
            repeated = answered_before is not None and (
                (self._last_answered is not None and self._last_answered[0] == key)
                or used >= 1
            )
            # THE SAME THING ON THE NEXT STEP: last round answered this field
            # and this problem on one step, and now the door names it on
            # another. One step at a time is moving it along, so the ask
            # switches to the whole line of that field at once.
            moved = (
                not repeated
                and self._last_answered is not None
                and self._last_answered[0] != key
                and self._last_answered[1:3] == (field, str(fix.get("problem") or ""))
                and self._last_answered[3] != number
            )
            parts = form_parts(path) or []
            line = (
                the_line_of(answer, field)
                if moved and place and not odds and len(parts) == 3 and parts[0] == "steps"
                else []
            )
            # Asks that may be answered with several patches: the odds line,
            # a row about the whole plan, and a field that moved.
            several = odds or not place or bool(line)
            # THE WHOLE STEP, OR ONE THING ON IT. A path that stops at
            # `form.steps.2` is the STEP being named, and rewording a line of
            # it cannot clear that.
            whole = whole_step_path(path) if place else None
            said = says_on_step(rows, number)
            payload: dict[str, Any] = {"want": want}
            if place:
                payload["change_this"] = {
                    "path": path,
                    "say": fix.get("say"),
                    "accepted": accepted_of(fix),
                }
                payload["step_number"] = number
            else:
                # NO PATH TO COPY. A `change_this.path` of `form` is what the
                # model answered with `form = {...}`.
                payload["the_bench_says"] = fix.get("say")
                payload["accepted"] = accepted_of(fix)
                payload["it_names"] = field or None
                payload["step_number"] = number
                payload["fields_on_a_step"] = list(FORM_STEP_FIELDS)
            # The plan in one line per step, and the ONE step being changed.
            # Never the draft: a thirty-step document in front of a one-field
            # fix is the cost this loop exists to avoid.
            payload["plan"] = outline_summary(answer.get("draft"))
            if place or index is not None:
                payload["step"] = _fit(self._step_for(answer, index))
            # WHAT GOES IN THIS STEP, as the door states it. A plain step has
            # no slots and says so; an email step names every argument, who
            # fills it and where its value comes from.
            entry = step_entry(answer, number)
            if entry is not None:
                payload["this_step"] = entry
            if said or not place:
                payload["what_the_bench_says"] = said or [fix.get("say")]
            # A PLAN-LEVEL FIX SEES THE PLAN-LEVEL FIELDS. Asked for form.odds
            # with only a one-line outline in front of it, a model has no
            # overview or span to forecast against.
            if path in _PLAN_FIELD_PATHS or odds or not place:
                held = form or {}
                payload["the_plan"] = {
                    name: held.get(name) for name in ("overview", "odds", "span_days")
                }
            # EVERY NUMBER ON THE LINE, for any row about a number on it and
            # for any row about the whole plan.
            if odds or not place:
                payload["the_odds_line"] = odds_line(answer, self.proposal_odds)
            if line:
                payload["the_line"] = line
            gone = self._taken_off.get(path)
            if gone:
                payload["the_bench_had_this_taken_off"] = gone
            # THE TWO EXITS, and the whole step to choose between them with.
            # A step-level problem is answered by replacing the step or
            # dropping it, so the ask carries EVERY field of it -- `_fit`
            # sheds keys, and a field the model cannot see is a field it
            # cannot fill in when it sends the step back.
            if not several and (whole is not None or (repeated and index is not None)):
                payload["step"] = self._whole_step_for(answer, index)
                payload["steps_path"] = _step_path(path, index)
            if not place:
                instruction = PLAN_WIDE_FIX_INSTRUCTION
            elif odds:
                instruction = ODDS_FIX_INSTRUCTION
            elif line:
                instruction = LINE_FIX_INSTRUCTION.format(field=field)
            elif whole is not None:
                instruction = WHOLE_STEP_FIX_INSTRUCTION
            else:
                instruction = FIX_INSTRUCTION
            if repeated:
                # REWORDING DID NOT WORK. Say it plainly and name both exits,
                # whichever path the bench used: the prod loop on 2026-09-11
                # was named `form.steps.0.do_line` three times and patched
                # that one line three times ("picks" -> "selects") while the
                # verb and the hand-over line kept the check true. An ask that
                # already covers several places says "send something
                # different" instead: replacing a step is no fix for a number.
                head = REPEATED_FIX_INSTRUCTION
                if index is not None and not several:
                    head = REPEATED_STEP_EXITS_INSTRUCTION
                instruction = head + instruction
                payload["your_last_patch_did_not_clear_this"] = {
                    "path": path if place else None,
                    "you_sent_last_round": (
                        self._last_sent_for(path) if place else answered_before
                    ),
                }
                self.log.info(
                    "draft loop %s target=%s round=%d: %s again; telling the "
                    "agent what it sent last round",
                    kind,
                    target_id,
                    self.rounds + 1,
                    key,
                )
            elif moved:
                self.log.info(
                    "draft loop %s target=%s round=%d: %s moved from step %s to "
                    "step %s; asking for the whole line of it",
                    kind,
                    target_id,
                    self.rounds + 1,
                    field,
                    self._last_answered[3] if self._last_answered else "?",
                    number,
                )
            if used >= 1 and left == 1:
                instruction = LAST_TRY_INSTRUCTION + instruction
            what = f"fix {path if place else 'the plan'}"
            # LAW A: and the tail of every fix ask.
            instruction = with_the_person_said(instruction, payload, answer)
            reply = self._ask(instruction, payload, what, head=self._head())
            patches = read_patches(reply, path)
            # THE STEP COMES OUT (0.38.3). A drop is not a patch: it is the
            # door's own instruction, and it is the second exit on every
            # step-level problem.
            dropping = read_drop(reply, index)
            if (
                dropping is None
                and not several
                and (whole is not None or repeated)
                and index is not None
            ):
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
                    patches = read_patches(reply, path)
                    dropping = read_drop(reply, index)
            if dropping is not None:
                self._last_answered = None
                answer = self._drop_step(target_id, kind, dropping, path, key)
                continue
            patches = self._what_lands(patches, several, kind, target_id)
            if not patches:
                # ONCE MORE, SAYING SO. An empty answer once is a hiccup (a
                # fenced reply with nothing in it, a refusal, a timeout); twice
                # is the model's answer. Live on 2026-09-09 two agents lost a
                # whole draft, ten rounds in, to one empty reply.
                self.log.info(
                    "draft loop %s target=%s: nothing usable came back for %s "
                    "(reply=%s, answer %s); asking once more",
                    kind,
                    target_id,
                    path or "the plan",
                    self.last_reply,
                    answer_shape(reply),
                )
                again = EMPTY_SEVERAL_INSTRUCTION if several else EMPTY_ANSWER_INSTRUCTION
                reply = self._ask(again + instruction, payload,
                                  f"{what} (again)", head=self._head())
                patches = self._what_lands(
                    read_patches(reply, path), several, kind, target_id
                )
            if not patches:
                self.log.warning(
                    "draft loop %s target=%s: no patch came back for %s twice "
                    "(reply=%s, answer %s); stopping this draft",
                    kind,
                    target_id,
                    path or "the plan",
                    self.last_reply,
                    answer_shape(reply),
                )
                break
            if not several:
                # One path was asked for, so a stray patch is filed there. An
                # ask that named several places takes each where it was put.
                patches = self._aim(patches, path, kind, target_id)
            answer = self._patch(target_id, kind, patches, what, standing=answer)
            self._note_answered(key, fix, path, patches)
        # A PAUSE CAN BE WHAT ENDED THE LOOP, or what it was handed. A paused
        # body reads closed and spent, so it leaves by the while and not by the
        # check inside it, and a draft that came in paused never enters at all.
        return self._paused(target_id, kind, answer) or answer

    def _note_answered(
        self, key: str, row: Any, path: str, patches: Sequence[dict[str, Any]]
    ) -> None:
        """Which row the round just sent answered, and with what (0.55.3)."""
        self._answered[key] = [
            {"path": str(entry.get("path") or ""), "value": entry.get("value")}
            for entry in patches
        ]
        self._last_answered = (
            key,
            field_of(row, path),
            str((row or {}).get("problem") or ""),
            (row or {}).get("step"),
        )

    def _what_lands(
        self,
        patches: Sequence[dict[str, Any]],
        several: bool,
        kind: str,
        target_id: str,
    ) -> list[dict[str, Any]]:
        """The patches that land on a real place, and the rest said out loud.

        An ask that let the model patch several places takes LEAVES only: a
        step sent back whole from a question about a number rewrites the step
        by accident. Every ask refuses a path that is not a place at all
        (`form`, `form.steps`, `form.form`), before it costs a round.
        """
        kept: list[dict[str, Any]] = []
        for entry in patches or []:
            where = entry.get("path")
            lands = is_a_leaf_place(where) if several else names_a_place(where)
            if lands:
                kept.append(entry)
                continue
            self.log.warning(
                "draft loop %s target=%s: the model patched %s, which is not %s "
                "in the plan; it was not sent",
                kind,
                target_id,
                str(where or "?"),
                "one value" if several and names_a_place(where) else "a place",
            )
        return kept

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
        self._last_round = []
        answer = self.provider.drop_draft_step(target_id, index, kind=kind)
        self.rounds += 1
        self._record(target_id, kind, answer, f"drop step {index + 1}")
        return answer

    def _insert_step(
        self,
        target_id: str,
        kind: str,
        before: int,
        step: dict[str, Any],
        path: str,
        code: str,
    ) -> dict[str, Any]:
        """THE THIRD EXIT: a step the plan was missing goes IN.

        Not a patch -- `{"kind": "plan", "insert": {"before": N, "step": {...}}}`
        on the same PATCH door -- so the bench expands the whole plan again and
        slides every pointer at or past `before` up by one. `before` counts
        from ONE, like move and drop. One round, like a patch.
        """
        self.log.info(
            "draft loop %s target=%s round=%d insert a %s step before step %d "
            "(%s, %s)",
            kind,
            target_id,
            self.rounds + 1,
            str(step.get("verb") or "?"),
            before,
            path or "?",
            code or "no code",
        )
        self._sent.append((path, {"insert": {"before": before, "step": step}}))
        self._last_round = []
        answer = self.provider.insert_draft_step(target_id, before, step, kind=kind)
        self.rounds += 1
        self._record(target_id, kind, answer, f"insert before step {before}")
        return answer

    def _insert_the_who_step(
        self,
        target_id: str,
        kind: str,
        fix: dict[str, Any],
        answer: dict[str, Any],
        index: int | None,
        path: str,
    ) -> dict[str, Any] | None:
        """NO WHO STEP: SEND THE CALL, do not ask a model for words.

        A door that writes the whole insert call into its sentence has it sent
        back as it came; contract 4.0 cuts that sentence to one line, so the
        call is built from the row's own step number instead. None means no
        call could be made -- a door with no insert instruction, a row with no
        step on it, or a 422 `insert_not_possible` -- and then the caller
        falls back to the ordinary whole-step ask, once, like any other
        step-level problem. A refused insert is never re-sent blindly: the
        same row three times stops the draft, the way every repeat does.
        """
        if getattr(self.provider, "insert_draft_step", None) is None:
            self.log.warning(
                "draft loop %s target=%s: step %s reaches a person with no who "
                "step above it and this door has no insert call; asking the "
                "agent for the whole step instead",
                kind,
                target_id,
                None if index is None else index + 1,
            )
            return None
        insert = who_insert(fix, self._step_for(answer, index), index)
        if insert is None:
            self.log.warning(
                "draft loop %s target=%s: %s needs a who step above it and no "
                "insert call could be read or built from the row",
                kind,
                target_id,
                path or "?",
            )
            return None
        sent = self._insert_step(
            target_id, kind, int(insert["before"]), dict(insert["step"]), path,
            "no who step",
        )
        if isinstance(sent, dict) and str(sent.get("error") or "") == INSERT_NOT_POSSIBLE:
            self.log.warning(
                "draft loop %s target=%s: the door refused the who-step insert "
                "(%s: %s); asking the agent for the whole step instead",
                kind,
                target_id,
                INSERT_NOT_POSSIBLE,
                " ".join(str(sent.get("message") or "").split())[:200],
            )
            return None
        return sent

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
