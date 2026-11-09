"""A CLOSED STEP COSTS NO MODEL CALL (0.75.0, bench d6d7bebb2).

WHAT FORCED IT: from 2026-11-08 the bench answers an outcome filed on a step
that already closed with 422 `not_the_current_step`, naming the step's state
and the current step. The step ask treated it like any refusal and asked the
model once more; Sam-prod spent a model call that way at 20:50 UTC the same
day. Nothing the model writes changes a closed step, so the ask ends there and
the next attention cycle reads the current step whole.
"""
from __future__ import annotations

import logging

from tests.unit.test_step_ask import CAFES, OBLIGATION, FakeBench, _model, _payload
from toll_harness.toll_bench.api import BookOfHousesApiError
from toll_harness.toll_bench.step import NOT_THE_CURRENT_STEP, StepAsk

BODY = {"ok": False, "error": NOT_THE_CURRENT_STEP,
        "message": "Step 2 is already approved; the deal is on step 3.",
        "step_id": "s-1", "step_number": 2, "step_state": "approved",
        "current_step": {"id": "s-2", "number": 3}}


class ClosedBench(FakeBench):
    def file_outcome(self, target_id, outcome, key):
        self.outcomes.append((target_id, outcome, key))
        raise BookOfHousesApiError(422, NOT_THE_CURRENT_STEP, BODY["message"], body=BODY)


def test_a_closed_step_ends_the_ask_with_one_model_call(caplog):
    bench = ClosedBench()
    model = _model(CAFES, CAFES)
    with caplog.at_level(logging.INFO, logger="toll_harness.step"):
        out = StepAsk(model, bench).run(OBLIGATION, _payload())

    assert out["model_calls"] == 1 and len(model.invocations) == 1
    assert len(bench.outcomes) == 1
    assert out["ok"] is False and out["error"] == NOT_THE_CURRENT_STEP
    assert out["result"]["detail"]["current_step"] == {"id": "s-2", "number": 3}
    lines = [r.getMessage() for r in caplog.records]
    assert ("step ask step 2: the bench says step 2 is approved, not the current step "
            "(current: step 3); not asking the model again") in lines
    assert not any("asking once more" in line for line in lines)


def test_a_closed_step_with_no_current_step_says_none(caplog):
    class Ended(FakeBench):
        def file_outcome(self, target_id, outcome, key):
            self.outcomes.append(1)
            return {**BODY, "current_step": None}

    model = _model(CAFES, CAFES)
    with caplog.at_level(logging.INFO, logger="toll_harness.step"):
        out = StepAsk(model, Ended()).run(OBLIGATION, _payload())
    assert out["model_calls"] == 1
    assert any("(current: step none)" in r.getMessage() for r in caplog.records)


def test_any_other_refusal_still_gets_one_more_ask():
    refusal = {"ok": False, "error": "deliverable_count_short", "message": "3 promised, 2 filed"}
    model = _model(CAFES, CAFES)
    out = StepAsk(model, FakeBench(refuse_first=refusal)).run(OBLIGATION, _payload())
    assert out["ok"] is True and out["model_calls"] == 2
