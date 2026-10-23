"""A PLAN REPLY THAT NEVER BECAME JSON IS HANDED BACK ONCE, THEN LOGGED (0.68.0).

WHAT FORCED THESE TESTS. A reply that was not one JSON object never reached
the door, so nothing ever said what was wrong with it: the form ask gave up
`no_form` on the first such reply, and the lab runner patched the feedback in
from outside (DeepSeek and GLM on prod heat batches, 2026-10-19, one call
every ~75 seconds, nothing filed). Now the parser's own words go back to the
model ONCE ahead of the same question; a second miss stops, and the run log
keeps one line with the step it was about and the first 600 characters of
what the model wrote (Steven asked to see it).
"""
from __future__ import annotations

import json
import logging

from tests.unit.plan_door import a_row, only, reply
from tests.unit.test_one_read_before_any_write_0680 import FORM, NO_DRAFT, Door
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    UNREADABLE_REPLY_INSTRUCTION,
    WHOLE_FORM_AGAIN,
    DraftLoop,
)

PROSE = "Here is my plan. First I will find three places, then I will book one. " * 12


def _model(*texts: str) -> ScriptedModelAdapter:
    return ScriptedModelAdapter([
        ModelResponse(message=ModelMessage.text("assistant", text), text=text,
                      tool_calls=[], stop_reason="end_turn")
        for text in texts
    ])


def _asked(model, n):
    return model.invocations[n]["messages"][0].content[0]["text"]


def _plan(model, door, already_read=None):
    return DraftLoop(model, door).run(
        "t-1", kind="plan", proposal_id="p-1", idempotency_key="k",
        brief={"want": "A table for four"}, already_read=already_read or dict(NO_DRAFT))


def test_an_unreadable_form_reply_is_handed_back_once_with_the_parsers_words():
    model = _model(PROSE, json.dumps(FORM))
    out = _plan(model, Door())
    assert out["filed"] is True, out
    assert len(model.invocations) == 2
    again = _asked(model, 1)
    head = UNREADABLE_REPLY_INSTRUCTION.split(":")[0]
    assert head in again and head not in _asked(model, 0)
    assert f"it was not one JSON object ({len(PROSE)} characters" in again
    # The parser's words, never the model's own words back.
    assert "Here is my plan" not in again


def test_unreadable_twice_stops_and_the_log_shows_what_the_model_wrote(caplog):
    model = _model(PROSE, PROSE)
    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        out = _plan(model, Door())
    assert out["filed"] is False and out["error"] == "no_form"
    assert len(model.invocations) == 2  # once more, never a third time
    failed = [r.getMessage() for r in caplog.records
              if "could not be used" in r.getMessage()]
    assert len(failed) == 1
    line = failed[0]
    assert "form (every step)" in line and "it was not one JSON object" in line
    excerpt = line.split("it wrote: ", 1)[1]
    assert excerpt == " ".join(PROSE.split())[:600] and len(excerpt) == 600


def test_a_red_round_hands_an_unreadable_answer_back_once_naming_its_steps(caplog):
    row = a_row("form.steps.0.hand_over_line", step=1, slot="hand_over_line",
                say="Step 1 needs the thing you hand over.")
    model = _model("I fixed it, trust me.", "Still prose, sorry.")
    door = Door()
    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        out = _plan(model, door, already_read=only(reply("clean"), row))
    assert out["filed"] is False and door.calls == []
    again = _asked(model, 1)
    assert UNREADABLE_REPLY_INSTRUCTION.split(":")[0] in again
    assert WHOLE_FORM_AGAIN["empty"].split(".")[0] in again
    failed = [r.getMessage() for r in caplog.records
              if "could not be used" in r.getMessage()]
    assert len(failed) == 1 and "steps 1:" in failed[0]
    assert failed[0].endswith("it wrote: Still prose, sorry.")
