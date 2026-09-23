"""THE WHO STEP IS THE AGENT'S PICK (rule 238, amended 2026-09-12).

WHAT FORCED THIS FILE (production, 2026-09-12, want 14db651d). The bench used
to INSERT the "Who should this go to?" step itself. It inserted one AFTER the
agent's step 4 and then refused its own document as "step 5" -- a step the
agent never wrote, at a number that moved under it while it was answering. So
the bench stopped inserting: the agent picks the step like it picks `emails`
or `meeting`, and a plan that reaches somebody with no who step above it is
refused.

Which makes it a problem with a known answer, and the harness SENDS it. Asking
a model to reword a step that is not wrong is exactly how the old contact loop
burned its rounds.

REWRITTEN FOR CONTRACT 4.0 (2026-09-17). The harness no longer reads a code to
know this. It reads the door's own SLOT TABLE: a step that reaches somebody
carries a `contact` slot the PLATFORM fills from `person.who`, and the step the
person picks on is the one `form_steps` marks `person_slot: "who"`. The reply
below is the real one the bench answers with (`tests/fixtures/`).
"""
from __future__ import annotations

import json

from tests.unit.plan_door import Door, OldDoor, holes_written, only, reply, row
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    DEFAULT_WHO_ODDS,
    INSERT_NOT_POSSIBLE,
    DraftLoop,
    read_insert,
    who_insert,
    who_step,
    who_step_is_missing,
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


# The real answer: step 2 sends mail, no step above it asks who.
EMAIL = reply("email_step")
NO_WHO = row(EMAIL, problem="no_source", step=2)
SEND_STEP = EMAIL["form"]["steps"][1]

# A door that still writes its whole insert call into the sentence.
THE_OLD_QUESTION = (
    "Step 2 reaches a person and no step above it asks who. Put a who step in "
    'front of it: PATCH {"kind": "plan", "insert": {"before": 2, "step": '
    '{"verb": "who", "who": "person", "declared_odds": 0.7}}}.'
)


def _answer(*rows):
    # The holes on the send step are written already: this file is about the
    # who step, and the loop writes what the form says is missing first.
    return holes_written(only(EMAIL, *rows or (NO_WHO,)))


# ---------------------------------------------------------------------------
# 1. The gap is read off the slot table, not off a code
# ---------------------------------------------------------------------------
def test_the_send_step_carries_a_contact_slot_the_platform_fills():
    entry = [e for e in EMAIL["form_steps"] if e["step"] == 2][0]
    assert entry["action"] == "email.send"
    recipients = [s for s in entry["slots"] if s["kind"] == "contact"]
    assert recipients, entry["slots"]
    for slot in recipients:
        assert slot["filler"] == "platform"
        assert slot["source"] == "person.who"
    # And the agent's own slots on that same step are the words, never who.
    mine = [s["name"] for s in entry["slots"] if s["filler"] == "agent"]
    assert "subject" in mine and "body" in mine
    assert "to" not in mine and "from" not in mine


def test_a_step_that_reaches_somebody_with_no_who_step_is_seen():
    assert who_step_is_missing(EMAIL, 2) is True
    # The step that reaches nobody never needs one.
    assert who_step_is_missing(EMAIL, 1) is False
    # Neither does a step that is not there.
    assert who_step_is_missing(EMAIL, 9) is False


def test_a_plan_that_already_asks_who_is_never_given_a_second_one():
    answer = _answer()
    answer["form_steps"][0]["person_slot"] = "who"
    assert who_step_is_missing(answer, 2) is False


def test_a_plain_plan_never_looks_like_a_missing_who_step():
    plain = reply("clean")
    assert who_step_is_missing(plain, 1) is False


# ---------------------------------------------------------------------------
# 2. Reading, and building, the call
# ---------------------------------------------------------------------------
def test_the_insert_call_is_read_out_of_the_words_however_it_is_spaced():
    assert read_insert(THE_OLD_QUESTION) == {
        "step": {"verb": "who", "who": "person", "declared_odds": 0.7},
        "before": 2,
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


def test_the_call_is_built_from_the_row_and_the_steps_own_odds():
    # Contract 4.0 cuts the sentence to one line, so the call is built. The
    # row already says everything it needs. `before` counts from ONE.
    built = who_insert(NO_WHO, SEND_STEP, 1)
    assert built == {"before": 2, "step": who_step(SEND_STEP["declared_odds"])}
    # No odds on the step: the bench clamps, so 0.5 is the harness's blank.
    assert who_insert({"say": "x"}, {"verb": "emails"}, 0)["step"]["declared_odds"] == (
        DEFAULT_WHO_ODDS
    )
    # No step at all: nothing can be built, and the caller asks.
    assert who_insert({"say": "x"}, None, None) is None


def test_a_door_that_still_writes_the_call_has_its_own_number_sent():
    read = who_insert({"say": THE_OLD_QUESTION}, {"declared_odds": 0.2}, 4)
    assert read == {"before": 2, "step": {"verb": "who", "who": "person",
                                          "declared_odds": 0.7}}


def test_a_call_with_no_before_takes_it_from_the_step():
    fix = {"say": 'PATCH {"insert": {"step": {"verb": "who"}}}'}
    assert who_insert(fix, SEND_STEP, 4) == {"before": 5, "step": {"verb": "who"}}


# ---------------------------------------------------------------------------
# 3. The gap is ANSWERED, not asked
# ---------------------------------------------------------------------------
def test_a_missing_who_step_sends_the_insert_and_never_asks_the_model():
    door = Door({"ok": True, "ready": True})
    model = _model()  # nothing scripted: the model must not be asked at all
    loop = DraftLoop(model, door)

    loop._answer_the_fixes("t-1", "plan", _answer(), "Introduce three people")

    assert door.inserts == [(2, {"verb": "who", "who": "person",
                                 "declared_odds": SEND_STEP["declared_odds"]})]
    assert door.patches == []
    assert door.drops == []
    assert model.invocations == []


def test_the_insert_spends_a_round_and_is_recorded_like_a_patch():
    door = Door({"ok": True, "ready": True})
    loop = DraftLoop(_model(), door)

    loop._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert loop.rounds == 1
    assert loop._sent == [
        (
            "form.steps.1",
            {"insert": {"before": 2, "step": {"verb": "who", "who": "person",
                                              "declared_odds": 0.4}}},
        )
    ]


def test_a_door_with_no_insert_call_falls_back_to_asking_the_agent():
    door = OldDoor({"ok": True, "ready": True})
    model = _model({"drop": {"step": 2}})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert door.inserts == []
    assert door.drops == [1]
    assert "TWO EXITS" in _said(model)


def test_insert_not_possible_is_logged_and_the_agent_is_asked_once():
    # A 422 is not a thing to re-send. The loop asks for the whole step, the
    # model answers, and the answer goes to the door.
    refused = {
        "ok": False,
        "error": INSERT_NOT_POSSIBLE,
        "message": "before 2 is out of range for a plan with no steps",
        "status": 422,
    }
    replacement = dict(SEND_STEP, verb="reviews", tool="")
    door = Door(refused, {"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.1", "value": replacement}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert door.inserts == [(2, {"verb": "who", "who": "person",
                                 "declared_odds": 0.4})]
    # Asked ONCE, with the whole step in front of it.
    assert len(model.invocations) == 1
    assert "TWO EXITS" in _said(model)
    assert door.patches == [[{"path": "form.steps.1", "value": replacement}]]


def test_a_refused_insert_is_never_re_sent_three_times_over():
    # The same row three times closes the draft, insert or no insert.
    refused = {
        "ok": False,
        "error": INSERT_NOT_POSSIBLE,
        "message": "no step object",
        "status": 422,
    }
    door = Door(refused, _answer(), refused, _answer(), refused, _answer())
    model = _model(
        *[{"patches": [{"path": "form.steps.1", "value": dict(SEND_STEP)}]}
          for _ in range(3)]
    )

    out = DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert out["error"] == "draft_stalled"
    assert len(door.inserts) <= 3


# ---------------------------------------------------------------------------
# 4. The words the agent is given
# ---------------------------------------------------------------------------
def test_the_form_ask_tells_the_agent_to_put_the_who_step_in():
    from toll_harness.toll_bench.draft import FORM_INSTRUCTION, WHO_STEP_SENTENCE

    assert WHO_STEP_SENTENCE in FORM_INSTRUCTION
    assert '"verb": "who"' in FORM_INSTRUCTION
    assert "ONE who step per plan" in FORM_INSTRUCTION
    assert "never plan a step to find, get or list the people" in FORM_INSTRUCTION


def test_rej45_is_a_refusal_the_agent_can_act_on():
    """The FILE door still speaks in REJ codes and is untouched by contract
    4.0, which replaced the PLAN DRAFT door's reply and nothing else."""
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
