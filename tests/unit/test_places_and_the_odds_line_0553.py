"""WHERE A PATCH CAN LAND, AND THE ODDS LINE ASKED WHOLE (0.55.3).

WHAT FORCED THIS FILE (Sam, prod want 355b9b91, 2026-09-25). The plan door
named a falling odds line one step at a time. Each fix ask said "change
exactly this one number", so Sam raised step 1, which made step 2 fall, and so
on for five rounds. Then the door named a row with no step and the path
`form`; the loop asked for a patch AT `form`, the model sent
`form = {"step": 6, "declared_odds": 0.84}`, the door stored it as a key named
`form` inside the form, and two rounds went on `form.form = null`. Every one of
those was charged against the row and the plan closed `plan_failed`.

Pinned here, one rule per shape and none per code:
  * a path that is not a place in the plan is never sent, on any road;
  * a row about the whole plan is asked for leaf patches, with the bench's own
    sentence and every number on the odds line;
  * a row about a number on the line shows the whole line and takes every
    patch it needs in one answer;
  * `not_allowed` with nothing accepted takes a key off, but never a key the
    step cannot stand without;
  * "your last patch did not clear this" is said only after a real patch.
"""
from __future__ import annotations

import copy
import json

from tests.unit.plan_door import Door, SpyDoor, a_row, holes_written, only, reply, row
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    DraftLoop,
    accepted_of,
    can_come_off,
    is_a_leaf_place,
    names_a_place,
    odds_line,
)


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [
            ModelResponse(
                message=ModelMessage.text("assistant", json.dumps(a)),
                text=json.dumps(a),
                tool_calls=[],
            )
            for a in answers
        ]
    )


def _said(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


def _sent_paths(door):
    return [entry["path"] for call in door.patches for entry in call]


# Sam's own line at round 0: an overall forecast of 0.72 above every step
# before the last two.
SAM_LINE = [0.40, 0.45, 0.50, 0.53, 0.58, 0.66, 0.68, 0.72]


def _plan(values=SAM_LINE, overall=0.72, **extra):
    """A captured answer carrying a form with this odds line."""
    answer = reply("clean")
    base = answer["form"]["steps"][0]
    steps = []
    for index, value in enumerate(values):
        step = dict(base, declared_odds=value, do_line=f"Step {index + 1} work")
        step.update(extra)
        steps.append(step)
    answer["form"] = {"overview": "A day at the summit.", "odds": overall,
                      "span_days": 30, "steps": steps}
    return answer


# The two rows Sam met, in the one shape the door sends.
FIRST_ROW = a_row(
    "form.steps.0.declared_odds", step=1, slot=None, problem="not_allowed",
    say="After step 1 succeeds, your forecast is 40.0%, below your overall "
        "forecast (72.0%). Forecast the same goal and deadline; correct this "
        "number or your earlier forecast.",
    codes=["forecast"], key="s1|-|not_allowed|agent|form.steps.0.declared_odds|forecast|4687b649",
)
PLAN_ROW = a_row(
    "form", step=None, slot="declared_odds", problem="not_allowed",
    say="step 6 declares 66% but step 5 declared 82%: every declared_odds is "
        "your chance the PERSON ends up with the thing, judged from that step.",
    codes=["REJ-29"],
)


# ---------------------------------------------------------------------------
# 1. What a place is
# ---------------------------------------------------------------------------
def test_the_form_has_four_places_at_the_top_and_no_others():
    for nowhere in ("", "form", "form.steps", "form.form", "form.form.step",
                    "form.odds.value", "form.steps.x", None):
        assert names_a_place(nowhere) is False, nowhere
    for somewhere in ("form.odds", "form.overview", "form.span_days",
                      "form.steps.2", "form.steps.2.declared_odds",
                      "form.steps[2].do_line", "form.steps.1.tool.args.subject",
                      "steps.2.outcome_promise", "finalist_questions"):
        assert names_a_place(somewhere) is True, somewhere


def test_a_leaf_is_one_value_and_a_whole_step_is_not_one():
    assert is_a_leaf_place("form.steps.2.declared_odds")
    assert is_a_leaf_place("form.odds")
    assert not is_a_leaf_place("form.steps.2")
    assert not is_a_leaf_place("form")


def test_accepted_keeps_the_shape_the_door_sent():
    assert accepted_of({"accepted": None}) == []
    assert accepted_of({"accepted": ["a", "b"]}) == ["a", "b"]
    assert accepted_of({"accepted": {"at_least": 0.82}}) == {"at_least": 0.82}
    assert accepted_of({"accepted": 0.82}) == [0.82]


def test_no_road_ever_sends_a_path_that_is_not_a_place():
    door = SpyDoor({"ok": True, "ready": True})
    loop = DraftLoop(None, door)
    loop._patch("t-1", "plan", [
        {"path": "form", "value": {"step": 6, "declared_odds": 0.84}},
        {"path": "form.form", "value": None},
        {"path": "form.steps", "value": []},
        {"path": "form.steps.5.declared_odds", "value": 0.82},
    ], "fix", standing=_plan())
    assert door.patches == [[{"path": "form.steps.5.declared_odds", "value": 0.82}]]


# ---------------------------------------------------------------------------
# 2. A row about the whole plan (Sam, rounds 9-11)
# ---------------------------------------------------------------------------
def test_a_row_at_form_is_asked_for_leaf_patches_and_form_is_never_sent():
    answer = only(_plan([0.75, 0.76, 0.78, 0.80, 0.82, 0.66, 0.68, 0.72]), PLAN_ROW)
    door = Door({"ok": True, "ready": True})
    model = _model(
        # What Sam sent: a patch AT `form`. Not sent; asked once more.
        {"patches": [{"path": "form", "value": {"step": 6, "declared_odds": 0.84}}]},
        {"patches": [{"path": "form.steps.5.declared_odds", "value": 0.82},
                     {"path": "form.steps.6.declared_odds", "value": 0.82},
                     {"path": "form.steps.7.declared_odds", "value": 0.82}]},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    first = _said(model)
    assert "THE BENCH NAMED THE PLAN AS A WHOLE" in first
    assert PLAN_ROW["say"] in first
    # Every step's number, with its path.
    for index in range(8):
        assert f"form.steps.{index}.declared_odds" in first
    # Nothing to copy a path of `form` from.
    assert '"change_this"' not in first
    assert "form" not in _sent_paths(door) and "form.form" not in _sent_paths(door)
    assert door.patches == [[
        {"path": "form.steps.5.declared_odds", "value": 0.82},
        {"path": "form.steps.6.declared_odds", "value": 0.82},
        {"path": "form.steps.7.declared_odds", "value": 0.82},
    ]]


def test_a_plan_wide_answer_with_no_leaf_in_it_sends_nothing_and_stops():
    answer = only(_plan(), PLAN_ROW)
    door = Door()
    model = _model({"patches": [{"path": "form", "value": {}}]},
                   {"patches": [{"path": "form.steps.1", "value": {}}]})

    out = DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert door.patches == []
    assert len(model.invocations) == 2
    assert out.get("closed") is None


def test_a_stray_key_is_not_taken_off_over_a_row_that_names_no_place():
    """The door charges its tries to the row it points at, and against a row
    at `form` every patch reads as an answer to it."""
    stray = row(reply("unknown_fields"), problem="unknown_field", path="form.steps.0")
    answer = only(_plan(), PLAN_ROW, stray)
    answer["next_fix"] = PLAN_ROW
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.0.declared_odds", "value": 0.72}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert door.patches == [[{"path": "form.steps.0.declared_odds", "value": 0.72}]]


# ---------------------------------------------------------------------------
# 3. A number on the line is asked about with the whole line (Sam, rounds 0-8)
# ---------------------------------------------------------------------------
def test_one_falling_number_is_answered_with_the_whole_line_in_one_round():
    answer = only(_plan(), FIRST_ROW)
    answer["forecast_context"] = reply("clean")["forecast_context"]
    door = Door({"ok": True, "ready": True})
    fix = [{"path": "form.odds", "value": 0.38}]
    model = _model({"patches": fix})

    loop = DraftLoop(model, door)
    loop.proposal_odds = 0.6
    loop._answer_the_fixes("t-1", "plan", answer, "x")

    ask = _said(model)
    assert "THE BENCH NAMED A NUMBER ON YOUR ODDS LINE" in ask
    assert "Change exactly the one thing" not in ask
    for index in range(len(SAM_LINE)):
        assert f"form.steps.{index}.declared_odds" in ask
    # The bench's own rule and what the proposal was filed at ride it.
    assert "keep it equal to or above your starting forecast" in ask
    assert '"your_proposal_odds":0.6' in ask
    # Lowering the overall forecast is a fix, and it is not re-aimed at the
    # step the row named.
    assert door.patches == [fix]


def test_several_numbers_go_in_one_round_each_where_it_was_put():
    answer = only(_plan(), FIRST_ROW)
    door = Door({"ok": True, "ready": True})
    fix = [{"path": f"form.steps.{i}.declared_odds", "value": 0.72}
           for i in range(7)]
    model = _model({"patches": fix})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert door.patches == [fix]
    assert len(model.invocations) == 1


def test_the_whole_step_odds_row_is_narrowed_to_the_number():
    """The REJ-29 row names `declared_odds` on the step's path. It is one
    number, never the replace-or-drop question."""
    captured = row(reply("unknown_fields"), codes=["REJ-29"])
    assert captured["path"] == "form.steps.1" and captured["slot"] == "declared_odds"
    answer = only(_plan([0.6, 0.4], overall=0.5), captured)
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.1.declared_odds", "value": 0.6}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    ask = _said(model)
    assert "TWO EXITS" not in ask and "DROP" not in ask
    assert "THE BENCH NAMED A NUMBER ON YOUR ODDS LINE" in ask
    assert door.drops == []
    assert door.patches == [[{"path": "form.steps.1.declared_odds", "value": 0.6}]]


def test_the_odds_line_says_null_for_an_odds_it_does_not_know():
    line = odds_line(_plan([0.5, 0.6]))
    assert line["your_proposal_odds"] is None
    assert line["overall"] == {"path": "form.odds", "odds": 0.72}
    assert [s["path"] for s in line["steps"]] == [
        "form.steps.0.declared_odds", "form.steps.1.declared_odds"]


def test_the_form_ask_carries_the_benchs_rule_for_the_numbers():
    answer = reply("outline_round")
    payload = DraftLoop(None, Door())._the_form_ask(answer, "x", None, {})
    assert "keep it equal to or above" in payload["forecast_context"]["instruction"]
    assert "goal" not in payload["forecast_context"]


# ---------------------------------------------------------------------------
# 4. `not_allowed` with nothing accepted takes a key off
# ---------------------------------------------------------------------------
def _room_row(**extra):
    return a_row("form.steps.0.room", step=1, slot="room", problem="not_allowed",
                 say="Step 1: a room is not allowed on this step.", **extra)


def test_a_key_that_may_not_be_there_comes_off_with_no_model_call():
    answer = only(_plan([0.8], room="#general"), _room_row())
    door = Door({"ok": True, "ready": True})
    model = _model()

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert model.invocations == []
    assert door.patches == [[{"path": "form.steps.0.room", "value": None}]]


def test_a_key_with_something_accepted_is_asked_about_not_taken_off():
    answer = only(_plan([0.8], room="#general"), _room_row(accepted=["#summit"]))
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.0.room", "value": "#summit"}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert "#summit" in _said(model)
    assert door.patches == [[{"path": "form.steps.0.room", "value": "#summit"}]]


def test_a_key_the_step_cannot_stand_without_is_never_taken_off():
    """Taking it off buys the same row back as `empty` and spends a try."""
    answer = _plan()
    for held in (FIRST_ROW, a_row("form.steps.0.do_line", step=1, problem="not_allowed")):
        assert can_come_off(held, held["path"], answer) is False
    assert can_come_off(_room_row(), "form.steps.0.room", _plan([0.8], room="#general"))
    # Already blank: not blanked twice, the model is asked.
    assert not can_come_off(_room_row(), "form.steps.0.room", _plan([0.8], room=None))


def test_a_later_ask_about_a_key_taken_off_says_why():
    answer = only(_plan([0.8], room="#general"), _room_row())
    empty = only(_plan([0.8], room=None),
                 a_row("form.steps.0.room", step=1, slot="room", problem="wrong_kind",
                       say="Step 1: the room holds the wrong kind of thing."))
    door = Door(empty, {"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.0.room", "value": "#summit"}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    ask = _said(model)
    assert "the_bench_had_this_taken_off" in ask
    assert "a room is not allowed on this step" in ask


# ---------------------------------------------------------------------------
# 5. "Again" only when it is true; the strategy switches early
# ---------------------------------------------------------------------------
def test_again_is_not_said_after_a_round_that_only_wrote_holes():
    """Sam, round 4: told REWORDING DID NOT WORK with nothing sent last round,
    because the round before had written a slot while the row stood."""
    answer = reply("email_step")
    answer["form_steps"][0]["person_slot"] = "who"
    odds_row = row(answer, path="form.steps.1.declared_odds")
    answer = only(answer, odds_row)
    after = holes_written(copy.deepcopy(answer))
    door = Door(after, {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": "form.steps.1.tool.args.subject", "value": "Hello"},
                     {"path": "form.steps.1.tool.args.body", "value": "A note."}]},
        {"patches": [{"path": "form.steps.1.declared_odds", "value": 0.6}]},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    second = _said(model, 1)
    assert "REWORDING DID NOT WORK" not in second
    assert "NAMING THE SAME THING AGAIN" not in second
    assert "your_last_patch_did_not_clear_this" not in second


def test_a_field_that_moves_to_the_next_step_is_asked_as_a_line():
    first = a_row("form.steps.0.do_line", step=1, slot="do_line", problem="wrong_kind",
                  say="Step 1: the do_line promises a send.")
    second = a_row("form.steps.1.do_line", step=2, slot="do_line", problem="wrong_kind",
                   say="Step 2: the do_line promises a send.")
    door = Door(only(_plan([0.8, 0.8, 0.8]), second), {"ok": True, "ready": True})
    fix = [{"path": "form.steps.1.do_line", "value": "Drafts the note"},
           {"path": "form.steps.2.do_line", "value": "Drafts the reply"}]
    model = _model({"patches": [{"path": "form.steps.0.do_line", "value": "Drafts it"}]},
                   {"patches": fix})

    DraftLoop(model, door)._answer_the_fixes(
        "t-1", "plan", only(_plan([0.8, 0.8, 0.8]), first), "x")

    ask = _said(model, 1)
    assert "THE SAME THING MOVED TO ANOTHER STEP" in ask
    assert '"the_line"' in ask
    assert "form.steps.2.do_line" in ask
    # Both patches in one round, each where the model put it.
    assert door.patches[-1] == fix


def test_the_doors_last_try_is_said_out_loud_on_a_fresh_run():
    """A new cycle starts the loop's own count at zero; the door's count is
    the one that closes the plan."""
    target = a_row("form.steps.0.do_line", step=1, slot="do_line", problem="wrong_kind",
                   say="Step 1: the do_line promises a send.", key="k-do-line")
    answer = only(_plan([0.8]), target)
    answer["correction_attempts"] = {"limit": 3, "used": 2, "left": 1}
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.0.do_line", "value": "Drafts it"}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert "THIS IS THE LAST TRY THE BENCH GIVES THIS ROW" in _said(model)
