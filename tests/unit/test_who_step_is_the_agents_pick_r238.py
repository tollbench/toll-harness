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

from tests.unit.plan_door import OldDoor, holes_written, only, reply, row
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import DraftLoop


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


def test_a_door_with_no_insert_call_falls_back_to_asking_the_agent():
    door = OldDoor({"ok": True, "ready": True})
    model = _model({"drop": {"step": 2}})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _answer(), "x")

    assert door.inserts == []
    assert door.drops == [2]  # the door counts from ONE (0.56.2)
    assert "TWO EXITS" in _said(model)


# ---------------------------------------------------------------------------
# 4. The words the agent is given
# ---------------------------------------------------------------------------
def test_the_form_ask_tells_the_agent_to_put_the_who_step_in():
    from toll_harness.toll_bench.draft import FORM_INSTRUCTION, WHO_STEP_SENTENCE

    assert WHO_STEP_SENTENCE in FORM_INSTRUCTION
    assert '"verb": "who"' in FORM_INSTRUCTION
    assert "ONE who step per plan" in FORM_INSTRUCTION
    # 0.64.0: the slot, not a wall. The old clause told the agent never to
    # plan the finding; the bench has taken `from_step: "found_by_research"`
    # since contract 4.1.12.
    assert "never plan a step to find, get or list the people" not in FORM_INSTRUCTION
    assert "Do not plan a separate find step: put the word on the who step" in FORM_INSTRUCTION


def test_rej45_is_a_refusal_the_agent_can_act_on():
    """The FILE door still speaks in REJ codes and is untouched by contract
    4.0, which replaced the PLAN DRAFT door's reply and nothing else. Since
    0.74.0 the refusal comes back in the door's own words and nothing else."""
    from toll_harness.email.book_of_houses import BookOfHousesApiError
    from toll_harness.toll_bench.book_of_houses import (
        FILE_DOOR_REFUSALS,
        BookOfHousesTollBenchProvider,
    )

    assert "who_step_missing" in FILE_DOOR_REFUSALS
    error = BookOfHousesApiError(
        422, "who_step_missing", "step 2 reaches a person and no who step stands above it",
        body={"rej": "REJ-45", "fix": "put the who step in front of step 2"},
    )
    refusal = BookOfHousesTollBenchProvider._door_refused(error)
    assert refusal["ok"] is False
    assert refusal["terminal"] is False
    assert refusal["error"] == "who_step_missing"
    assert refusal["detail"] == error.message
    assert refusal["fix"] == "put the who step in front of step 2"
