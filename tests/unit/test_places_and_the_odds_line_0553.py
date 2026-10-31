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

import json

from tests.unit.plan_door import Door, reply
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    DraftLoop,
    accepted_of,
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


def test_accepted_keeps_the_shape_the_door_sent():
    assert accepted_of({"accepted": None}) == []
    assert accepted_of({"accepted": ["a", "b"]}) == ["a", "b"]
    assert accepted_of({"accepted": {"at_least": 0.82}}) == {"at_least": 0.82}
    assert accepted_of({"accepted": 0.82}) == [0.82]


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


