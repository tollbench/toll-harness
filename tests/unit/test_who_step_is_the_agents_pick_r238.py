"""THE WHO STEP IS THE AGENT'S PICK (rule 238, amended 2026-09-12).

WHAT FORCED THIS FILE (production, 2026-09-12, want 14db651d). The bench used
to INSERT the "Who should this go to?" step itself. It inserted one AFTER the
agent's step 4 and then refused its own document as "step 5" -- a step the
agent never wrote, at a number that moved under it while it was answering. So
the bench stopped inserting: the agent picks the step like it picks `emails`
or `meeting`, and a plan that reaches somebody with no who step above it is
REFUSED WITH THE BLOCK and the exact insert call.

Which makes `missing_who` a problem with a known answer. The door names the
call; the harness SENDS it. Asking a model to reword a step that is not wrong
is exactly how the old contact loop burned its rounds.
"""
from __future__ import annotations

import json

from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    DEFAULT_WHO_ODDS,
    INSERT_NOT_POSSIBLE,
    MISSING_WHO,
    ONE_WHO_PER_PLAN,
    WHO_REACHES_NOBODY,
    WHOLE_STEP_CODES,
    DraftLoop,
    read_insert,
    who_insert,
    who_step,
)


def _says(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[]
    )


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [_says(json.dumps(a) if not isinstance(a, str) else a) for a in answers]
    )


def _said(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


# The steps of the plan that forced it: the agent wrote the send and no who
# step above it.
SEND_STEP = {
    "verb": "emails",
    "do_line": "emails each person the note you approved",
    "hand_over_line": "A sent receipt for every note",
    "need_line": "",
    "who": "agent",
    "declared_odds": 0.7,
    "proof": "the receipts",
    "tool": "gmail.message.send",
    "bid_step": 1,
}

# The door's own question, with its own call in it.
THE_QUESTION = (
    "Step 1 reaches a person and no step above it asks who. The person picks "
    "who this goes to from their own contact book, on a step of their own; you "
    "never find or list them. Put a who step in front of it: PATCH "
    '{"kind": "plan", "insert": {"before": 1, "step": {"verb": "who", "who": '
    '"person", "declared_odds": 0.7}}}. Or take the send out of step 1: PATCH '
    '{"kind": "plan", "patches": [{"path": "form.steps.0", "value": '
    '{"verb": "reviews"}}]}.'
)


class FormBench:
    """The plan door, scripted: one answer per call, and the calls recorded."""

    fleet = None

    def __init__(self, *answers):
        self.answers = list(answers)
        self.patches: list[list[dict]] = []
        self.drops: list[int] = []
        self.inserts: list[tuple[int, dict]] = []

    def _next(self):
        return self.answers.pop(0) if self.answers else {"ok": True, "ready": True}

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.patches.append(list(patches))
        return self._next()

    def drop_draft_step(self, target_id, step, *, kind="plan"):
        self.drops.append(int(step))
        return self._next()

    def insert_draft_step(self, target_id, before, step, *, kind="plan"):
        self.inserts.append((int(before), dict(step)))
        return self._next()


class OldDoor(FormBench):
    """A bench that publishes no insert call at all."""

    insert_draft_step = None


def _fix(path=".steps.0", code=MISSING_WHO, question=THE_QUESTION):
    return {
        "path": f"form{path}" if path.startswith(".") else path,
        "code": code,
        "question": question,
        "detail": "a step that reaches a person with no who step above it",
        "current": dict(SEND_STEP),
    }


def _answer(fix=None, form_steps=(SEND_STEP,)):
    return {
        "ok": True,
        "ready": False,
        "closed": None,
        "next_fix": fix or _fix(),
        "draft": {"form": {"steps": [dict(step) for step in form_steps]},
                  "steps": [{"title": "Send the notes"}]},
        "form": {"steps": [dict(step) for step in form_steps]},
        "rounds": {"used": 3, "left": 17, "cap": 20},
    }


# ---------------------------------------------------------------------------
# 1. Reading the call out of the door's words
# ---------------------------------------------------------------------------
def test_the_insert_call_is_read_out_of_the_question_however_it_is_spaced():
    assert read_insert(THE_QUESTION) == {
        "step": {"verb": "who", "who": "person", "declared_odds": 0.7},
        "before": 1,
    }
    # Spacing is the door's business, not a contract.
    assert read_insert('{ "insert" :  { "before" : 12 , "step" : { "verb" : "who" } } }') == {
        "step": {"verb": "who"},
        "before": 12,
    }
    # A `before` the door wrote as a string is still a number.
    assert read_insert('"insert": {"before": "3", "step": {"verb": "who"}}')["before"] == 3
    # Words with no call in them, and a call with no step object, are nothing.
    assert read_insert("Drop step 2 instead.") is None
    assert read_insert('"insert": {"before": 2}') is None
    assert read_insert("") is None
    assert read_insert(None) is None


def test_a_question_naming_a_drop_is_not_read_as_an_insert():
    drop = 'Drop step 3: PATCH {"kind": "plan", "drop": {"step": 3}}.'
    assert read_insert(drop) is None


# ---------------------------------------------------------------------------
# 2. Building the call when the words carry none
# ---------------------------------------------------------------------------
def test_the_call_is_built_from_the_path_and_the_steps_own_odds():
    # An older door, or one that reworded its question: the problem already
    # says everything the call needs. `before` counts from ONE.
    built = who_insert({"question": "Step 3 reaches a person."}, SEND_STEP, 2)
    assert built == {"before": 3, "step": who_step(0.7)}
    # No odds on the step: the bench clamps, so 0.5 is the harness's blank.
    assert who_insert({"question": "x"}, {"verb": "emails"}, 0)["step"]["declared_odds"] == (
        DEFAULT_WHO_ODDS
    )
    # No step in the path at all: nothing can be built, and the caller asks.
    assert who_insert({"question": "x"}, None, None) is None


def test_the_doors_own_call_wins_over_the_built_one():
    # The door said `before` 1 with odds 0.7; a builder reading the same
    # problem would agree, but the door's number is the one that is sent.
    read = who_insert(_fix(), {"declared_odds": 0.2}, 0)
    assert read == {"before": 1, "step": {"verb": "who", "who": "person",
                                          "declared_odds": 0.7}}


def test_a_call_with_no_before_takes_it_from_the_path():
    fix = _fix(question='PATCH {"insert": {"step": {"verb": "who"}}}')
    assert who_insert(fix, SEND_STEP, 4) == {"before": 5, "step": {"verb": "who"}}


# ---------------------------------------------------------------------------
# 3. `missing_who` is ANSWERED, not asked
# ---------------------------------------------------------------------------
def test_missing_who_sends_the_doors_insert_call_and_never_asks_the_model():
    bench = FormBench({"ok": True, "ready": True})
    model = _model()  # nothing scripted: the model must not be asked at all
    loop = DraftLoop(model, bench)

    loop._answer_the_fixes("t-1", "plan", _answer(), "Send thank-you notes")

    assert bench.inserts == [(1, {"verb": "who", "who": "person", "declared_odds": 0.7})]
    assert bench.patches == []
    assert bench.drops == []
    assert model.invocations == []


def test_the_insert_spends_a_round_and_is_recorded_like_a_patch():
    bench = FormBench({"ok": True, "ready": True})
    loop = DraftLoop(_model(), bench)

    loop._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert loop.rounds == 1
    assert loop._sent == [
        (
            "form.steps.0",
            {"insert": {"before": 1, "step": {"verb": "who", "who": "person",
                                              "declared_odds": 0.7}}},
        )
    ]


def test_a_door_with_no_insert_call_falls_back_to_asking_the_agent():
    bench = OldDoor({"ok": True, "ready": True})
    model = _model({"drop": {"step": 1}})

    DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert bench.inserts == []
    assert bench.drops == [0]
    assert "TWO EXITS" in _said(model)


def test_insert_not_possible_is_logged_and_the_agent_is_asked_once():
    # A 422 is not a thing to re-send. The loop asks for the whole step, the
    # model answers, and the answer goes to the door.
    refused = {
        "ok": False,
        "error": INSERT_NOT_POSSIBLE,
        "message": "before 1 is out of range for a plan with no steps",
        "status": 422,
    }
    replacement = dict(SEND_STEP, verb="reviews", do_line="reviews the notes you wrote")
    bench = FormBench(refused, {"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.0", "value": replacement}]})

    DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert bench.inserts == [(1, {"verb": "who", "who": "person", "declared_odds": 0.7})]
    # Asked ONCE, with the whole step in front of it.
    assert len(model.invocations) == 1
    assert "TWO EXITS" in _said(model)
    assert bench.patches == [[{"path": "form.steps.0", "value": replacement}]]


def test_a_refused_insert_is_never_re_sent_three_times_over():
    # The same (path, code) three times closes the draft, insert or no insert.
    refused = {
        "ok": False,
        "error": INSERT_NOT_POSSIBLE,
        "message": "no step object",
        "status": 422,
    }
    bench = FormBench(refused, _answer(), refused, _answer(), refused, _answer())
    model = _model(
        {"patches": [{"path": "form.steps.0", "value": dict(SEND_STEP)}]},
        {"patches": [{"path": "form.steps.0", "value": dict(SEND_STEP)}]},
        {"patches": [{"path": "form.steps.0", "value": dict(SEND_STEP)}]},
    )

    out = DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert out["error"] == "draft_stalled"
    assert len(bench.inserts) <= 3


# ---------------------------------------------------------------------------
# 4. The other two who codes are WHOLE-STEP problems, and the exit is the drop
# ---------------------------------------------------------------------------
def test_all_three_who_codes_are_whole_step_codes():
    assert MISSING_WHO in WHOLE_STEP_CODES
    assert ONE_WHO_PER_PLAN in WHOLE_STEP_CODES
    assert WHO_REACHES_NOBODY in WHOLE_STEP_CODES


def test_one_who_per_plan_is_answered_with_the_drop():
    question = (
        "This plan asks who twice (steps 1 and 3). One who step per plan. Drop "
        'step 3: PATCH {"kind": "plan", "drop": {"step": 3}}.'
    )
    bench = FormBench({"ok": True, "ready": True})
    model = _model({"drop": {"step": 3}})
    fix = _fix(".steps.2", ONE_WHO_PER_PLAN, question)

    DraftLoop(model, bench)._answer_the_fixes(
        "t-1", "plan", _answer(fix, (SEND_STEP, SEND_STEP, SEND_STEP)), "x"
    )

    assert bench.drops == [2]
    assert bench.inserts == []
    # The door's own words ride in front of the model (the JSON in them is
    # escaped by the payload dump, so the plain half is what is asserted).
    assert "One who step per plan" in _said(model)


def test_who_reaches_nobody_is_asked_as_a_whole_step_even_on_a_field_path():
    # A bench that names a LINE of the step is still naming the step: there
    # is no line on a who step to reword.
    question = 'Step 1 asks who and nothing after it reaches them. Drop it.'
    bench = FormBench({"ok": True, "ready": True})
    model = _model({"drop": {"step": 1}})
    fix = _fix(".steps.0.do_line", WHO_REACHES_NOBODY, question)

    DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(fix), "x")

    said = _said(model)
    assert "TWO EXITS" in said
    assert '"steps_path":"form.steps.0"' in said
    assert bench.drops == [0]


# ---------------------------------------------------------------------------
# 5. The words the agent is given
# ---------------------------------------------------------------------------
def test_the_form_ask_tells_the_agent_to_put_the_who_step_in():
    from toll_harness.toll_bench.draft import FORM_INSTRUCTION, WHO_STEP_SENTENCE

    assert WHO_STEP_SENTENCE in FORM_INSTRUCTION
    assert '"verb": "who"' in FORM_INSTRUCTION
    assert "ONE who step per plan" in FORM_INSTRUCTION
    assert "never plan a step to find, get or list the people" in FORM_INSTRUCTION


def test_the_restating_refusal_no_longer_says_the_bench_asks_who():
    from toll_harness.toll_bench.draft import RESTATING_STEP_INSTRUCTION

    assert "a step YOU put in the plan, verb `who`" in RESTATING_STEP_INSTRUCTION
    assert "The bench asks WHO on a step of its own" not in RESTATING_STEP_INSTRUCTION


def test_rej45_is_a_refusal_the_agent_can_act_on():
    from toll_harness.toll_bench import blocks
    from toll_harness.toll_bench.book_of_houses import (
        FILE_DOOR_REFUSALS,
        REJ_WHO_STEP_MISSING,
        BookOfHousesTollBenchProvider,
    )

    assert REJ_WHO_STEP_MISSING == "REJ-45"
    assert blocks.WHO_STEP_MISSING_CODE in FILE_DOOR_REFUSALS

    class _Error:
        rej = "REJ-45"
        message = "step 2 reaches a person and no who step stands above it"

    refusal = BookOfHousesTollBenchProvider._who_step_missing_refusal(_Error())
    assert refusal["ok"] is False
    assert refusal["terminal"] is False
    assert refusal["error"] == blocks.WHO_STEP_MISSING_CODE
    assert refusal["detail"] == _Error.message
    assert 'block_templates["who"]' in refusal["message"]
