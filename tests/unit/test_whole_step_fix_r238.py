"""A STEP-LEVEL PROBLEM HAS TWO EXITS: replace the step, or drop it.

WHAT FORCED THIS FILE (production, 2026-09-11, one fleet unit on want
14db651d). The bench refused a form step three rounds running. Each round the
fix ask handed the model ONE LINE of the step and the model reworded that one
line: "picks" became "selects". The verb never moved and the hand-over line
stayed, so the bench's check stayed true. Three namings, `draft_stalled`, then
`plan_failed`.

Rewording one line of a step cannot clear a problem that is about the step. So:
the whole step goes in front of the model, both exits are named, and a drop is
sent as the door's own drop instruction instead of a patch.

REWRITTEN FOR CONTRACT 4.0 (2026-09-17). There is ONE row shape now, and every
row below is a REAL one captured off the bench (`tests/fixtures/`). What tells
a step-level problem from a one-thing problem is no longer a list of codes: it
is the row's own `path`. A path that stops at `form.steps.2` is the step; a
path that goes further is one thing on it.
"""
from __future__ import annotations

import json
import logging

from tests.unit.plan_door import Door, holes_written, only, reply, row
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import DraftLoop, read_drop, whole_step_path


def _says(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[]
    )


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [_says(json.dumps(a) if not isinstance(a, str) else a) for a in answers]
    )


def _said(model, index=0):
    """The whole ask: the instruction and the payload ride the MESSAGE; the
    system is the run's stable prefix and never changes per round."""
    return model.invocations[index]["messages"][0].content[0]["text"]


# Two REAL rows off one captured answer, on the same step of the same plan.
# One stops at the step and names no field of it -- that is the step being
# named, and there is no line on it to reword. The other goes on to name one
# thing on it.
#
# 0.55.3 MOVED THESE OFF THE `unknown_fields` CAPTURE. Its step-level row was
# the falling-odds row (REJ-29), which names `declared_odds` on the step's
# path: that row is about ONE NUMBER, and the whole-step road it used to take
# is what told Sam (prod, 2026-09-25) to replace or drop a step whose only
# fault was a number. It is narrowed to the number now (the test at the end of
# this file), so the step-level row here is the email capture's REJ-40 row,
# which names no field at all. The holes on that step are marked written and a
# who step is marked present, so neither road runs ahead of the one under test.
PLAN = holes_written(reply("email_step"))
PLAN["form_steps"][0]["person_slot"] = "who"
STEP_ROW = row(PLAN, path="form.steps.1", problem="no_source", codes=["REJ-40"])
FIELD_ROW = row(PLAN, path="form.steps.1.tool.args.subject")

# The second step of that plan, every field of it as the agent sent it.
SEND_STEP = PLAN["form"]["steps"][1]


def _answer(*rows):
    return only(PLAN, *rows)


# ---------------------------------------------------------------------------
# 1. The path reader
# ---------------------------------------------------------------------------
def test_a_whole_step_path_is_told_from_a_field_on_one():
    assert whole_step_path("form.steps.0") == 0
    assert whole_step_path("steps.12") == 12
    assert whole_step_path("form.steps.0.do_line") is None
    assert whole_step_path("form.span_days") is None
    assert whole_step_path("") is None


def test_the_captured_rows_are_the_two_kinds():
    # The step-level row stops at the step; the other names a field on it.
    assert STEP_ROW["path"] == "form.steps.1"
    assert whole_step_path(STEP_ROW["path"]) == 1
    assert FIELD_ROW["path"] == "form.steps.1.tool.args.subject"
    assert whole_step_path(FIELD_ROW["path"]) is None


# ---------------------------------------------------------------------------
# 2. The whole-step ask carries the whole step, and both exits
# ---------------------------------------------------------------------------
def test_a_whole_step_question_hands_the_model_every_field():
    door = Door({"ok": True, "ready": True})
    replacement = dict(SEND_STEP, verb="reviews",
                       do_line="reviews the note you approved")
    model = _model({"patches": [{"path": "form.steps.1", "value": replacement}]})
    loop = DraftLoop(model, door)

    # Only the step-level row stands, so nothing answers it before the model.
    loop._answer_the_fixes("t-1", "plan", _answer(STEP_ROW), "Write up the replies")

    ask = _said(model)
    # EVERY field of the step, not the shed version a one-line ask gets.
    for field in SEND_STEP:
        assert field in ask, field
    assert SEND_STEP["hand_over_line"] in ask
    # And both exits, named.
    assert "TWO EXITS" in ask
    assert "REPLACE IT" in ask and "DROP IT" in ask
    assert '"drop": {"step"' in ask
    # The replacement went in as a patch on the whole step.
    assert door.patches == [[{"path": "form.steps.1", "value": replacement}]]
    assert door.drops == []


def test_the_bench_sentence_rides_in_front_of_the_model_unchanged():
    door = Door({"ok": True, "ready": True})
    model = _model({"drop": {"step": 2}})
    DraftLoop(model, door)._answer_the_fixes(
        "t-1", "plan", _answer(STEP_ROW), "Write up the replies"
    )

    ask = _said(model)
    assert STEP_ROW["say"] in ask
    # And no legacy code anywhere near the model.
    assert "REJ-" not in ask


# ---------------------------------------------------------------------------
# 3. A drop answer becomes the door's drop call, not a patch
# ---------------------------------------------------------------------------
def test_a_drop_answer_becomes_the_doors_drop_call():
    door = Door({"ok": True, "ready": True})
    model = _model({"drop": {"step": 1}})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(STEP_ROW), "x")

    assert door.drops == [1]
    assert door.patches == []


def test_the_drop_is_read_however_the_model_wrote_it():
    # The door's shape, a bare number, the 1-based number the prompt showed,
    # the action spelling, and the word with no number at all.
    assert read_drop({"drop": {"step": 2}}, 2) == 2
    assert read_drop({"drop": 3}, None) == 3
    assert read_drop({"drop": {"step": 1}}, 0) == 0
    assert read_drop({"action": "drop", "step": 4}, None) == 4
    assert read_drop({"drop": True}, 5) == 5
    assert read_drop({"drop": "drop this step"}, 6) == 6
    assert read_drop({"patches": [{"path": "form.steps.0", "value": {}}]}, 0) is None
    assert read_drop({}, 0) is None
    assert read_drop("not a dict", 0) is None


def test_a_drop_spends_a_round_and_is_recorded_like_a_patch():
    door = Door({"ok": True, "ready": True})
    model = _model({"drop": {"step": 2}})
    loop = DraftLoop(model, door)

    loop._answer_the_fixes("t-1", "plan", _answer(STEP_ROW), "x")

    assert loop.rounds == 1
    assert loop._sent == [("form.steps.1", {"drop": {"step": 1}})]


# ---------------------------------------------------------------------------
# 4. A field patch to a whole-step question is the wrong shape
# ---------------------------------------------------------------------------
def test_a_reworded_line_on_a_whole_step_path_is_asked_again():
    # This is the prod failure in one test: the model answers a step-level
    # question by rewording one line of the step.
    door = Door({"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": "form.steps.1.do_line",
                      "value": "writes up what came back"}]},
        {"drop": {"step": 2}},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(STEP_ROW), "x")

    # Asked twice, the second time with the whole-step instruction in front.
    assert len(model.invocations) == 2
    assert _said(model, 1).count("TWO EXITS") >= 1
    # The reworded line never reached the door.
    assert door.patches == []
    assert door.drops == [1]


def test_the_second_answer_is_taken_whatever_shape_it_is():
    # Asked once more, answered the same way: the field patch goes, because a
    # third ask on one question is the loop this brake exists to stop.
    reworded = {"path": "form.steps.1.do_line", "value": "writes up the replies"}
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [reworded]}, {"patches": [reworded]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(STEP_ROW), "x")

    assert len(model.invocations) == 2
    assert door.patches == [[reworded]]


# ---------------------------------------------------------------------------
# 5. The repeated branch names the exits -- on a FIELD path too
# ---------------------------------------------------------------------------
def test_the_repeated_branch_names_both_exits_on_a_field_path():
    """A field named TWICE means rewording is not the answer, and the ask
    has to say so."""
    field = FIELD_ROW["path"]
    door = Door(_answer(FIELD_ROW), {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": field, "value": 0.45}]},
        {"drop": {"step": 2}},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(FIELD_ROW), "x")

    second = _said(model, 1)
    assert "REWORDING DID NOT WORK" in second
    assert "REPLACE THE WHOLE STEP" in second
    assert "DROP THE STEP" in second
    # The second ask carries the whole step and the path to send it to.
    assert '"steps_path":"form.steps.1"' in second
    assert SEND_STEP["hand_over_line"] in second
    assert '"verb":"emails"' in second
    # It took the drop.
    assert door.drops == [1]


def test_the_repeated_branch_still_shows_what_was_sent_last_round():
    field = FIELD_ROW["path"]
    door = Door(_answer(FIELD_ROW), {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": field, "value": 0.45}]},
        {"drop": {"step": 2}},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(FIELD_ROW), "x")

    ask = _said(model, 1)
    assert "your_last_patch_did_not_clear_this" in ask
    assert "0.45" in ask


def test_a_whole_step_replacement_on_a_field_path_is_not_aimed_back_at_the_field():
    """`_aim` files a stray patch at the path that was asked for. A step sent
    back WHOLE against a field question is the right answer to the repeated
    ask, and re-aiming it at `...subject` would write a dict into a
    subject line."""
    field = FIELD_ROW["path"]
    replacement = dict(SEND_STEP, declared_odds=0.8)
    door = Door(_answer(FIELD_ROW), {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": field, "value": 0.45}]},
        {"patches": [{"path": "form.steps.1", "value": replacement}]},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(FIELD_ROW), "x")

    assert door.patches[-1] == [{"path": "form.steps.1", "value": replacement}]
    assert door.drops == []


# ---------------------------------------------------------------------------
# 6. The three-tries brake is untouched
# ---------------------------------------------------------------------------
def test_three_namings_of_one_problem_still_stop_the_draft(caplog):
    field = FIELD_ROW["path"]
    door = Door(*[_answer(FIELD_ROW) for _ in range(3)])
    model = _model(
        *[{"patches": [{"path": field, "value": 0.4 + n / 100}]} for n in range(4)]
    )

    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        out = DraftLoop(model, door)._answer_the_fixes(
            "t-1", "plan", _answer(FIELD_ROW), "x"
        )

    assert out["error"] == "draft_stalled"
    assert "three times" in out["message"]
    # The key the brake counts on is the bench's own -- the `key` it stamped on
    # the row, not one this package worked out for itself.
    assert FIELD_ROW["key"] in out["message"]
