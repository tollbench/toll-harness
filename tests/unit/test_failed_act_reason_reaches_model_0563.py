"""A FAILED ACT'S REASON REACHES THE MODEL (0.56.3).

WHAT FORCED THIS FILE. `agent_view_for_step` in
The bench puts a person's send-back/denial
reason in `note`, but a FAILED act (no_evidence, recipient_bounced, ...) has
no person behind it: the bench puts that reason in `error` (and, off the
email kind, the act's own detail lines in `words`; a family act's `progress`
says how far it got). `_act_row` in step.py kept only
(act_id, kind, state, note, next), so a failed act's row reached the model
with none of those three keys, and the `refile_act` instruction told the
model to read a `note` that was never there. Agent Kai reported "the failure
reason is missing" three times on a failed act (error=no_evidence) and was
parked with the reason sitting unread on the very payload it was handed.

Fixed: `_act_row` keeps `error`, `words` and `progress` when present, and the
`refile_act` instruction now says a FAILED act's reason is in `error`
(`words` for the act's own detail), a person's send-back/denial reason stays
in `note`.
"""
from __future__ import annotations

from toll_harness.toll_bench.step import (
    MOVE_INSTRUCTIONS,
    RETURNED_ACT_STATES,
    StepAsk,
    _act_row,
    step_tail,
    the_move,
)

from tests.unit.test_step_ask import FakeBench, OBLIGATION, _model, _payload


# ---------------------------------------------------------------------------
# 1. `_act_row` keeps error/words/progress when the bench sends them
# ---------------------------------------------------------------------------
def test_a_failed_acts_error_and_words_survive_the_row():
    act = {
        "act_id": "a1",
        "kind": "meeting",
        "state": "failed",
        "note": None,
        "error": "no_evidence",
        "words": "the run turned up nothing in the folder",
        "progress": {"offered": 2, "picked": 0},
    }
    row = _act_row(act)
    assert row["error"] == "no_evidence"
    assert row["words"] == "the run turned up nothing in the folder"
    assert row["progress"] == {"offered": 2, "picked": 0}
    assert "note" not in row  # ALWAYS PRESENT law is about the payload, not a null key here


def test_a_sent_back_acts_note_still_reaches_the_row_and_error_stays_absent():
    act = {"act_id": "a2", "kind": "email", "state": "sent_back", "note": "make it 11-11:30"}
    row = _act_row(act)
    assert row["note"] == "make it 11-11:30"
    assert "error" not in row and "words" not in row and "progress" not in row


# ---------------------------------------------------------------------------
# 2. The failed act's reason reaches the tail the model actually reads
# ---------------------------------------------------------------------------
def test_a_failed_act_carries_error_and_words_into_the_tail():
    payload = _payload(acts=[{
        "act_id": "a1",
        "kind": "meeting",
        "state": "failed",
        "error": "no_evidence",
        "words": "the run turned up nothing in the folder",
    }])
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    row = tail["acts"][0]
    assert row["error"] == "no_evidence"
    assert row["words"] == "the run turned up nothing in the folder"


def test_a_failed_act_is_still_the_refile_act_move():
    assert "failed" in RETURNED_ACT_STATES
    payload = _payload(acts=[{"act_id": "a1", "kind": "meeting", "state": "failed",
                              "error": "no_evidence"}])
    assert the_move(payload)["move"] == "refile_act"


# ---------------------------------------------------------------------------
# 3. The instruction itself points at the right key for each cause
# ---------------------------------------------------------------------------
def test_refile_act_instruction_names_error_for_a_failure_and_note_for_a_person():
    text = MOVE_INSTRUCTIONS["refile_act"]
    assert "`error`" in text
    assert "`note`" in text
    assert "FAILED" in text


# ---------------------------------------------------------------------------
# 4. End to end: a failed act with `error` reaches the model and gets refiled
# ---------------------------------------------------------------------------
def test_kais_case_a_failed_act_is_refiled_from_the_error_not_a_missing_note():
    bench = FakeBench()
    failed = _payload(acts=[{
        "act_id": "a1",
        "kind": "email",
        "state": "failed",
        "error": "no_evidence",
        "words": "the run turned up nothing in the folder",
    }])
    tail = step_tail(failed, OBLIGATION, the_move(failed), [])
    # The word the model was missing is now on the payload it is handed.
    assert tail["acts"][0].get("note") is None
    assert tail["acts"][0]["error"] == "no_evidence"

    act = {
        "kind": "email",
        "to": "ruby@x",
        "subject": "Trying again",
        "body_text": "Sending this once more with the missing evidence.",
        "purpose": "reschedule",
    }
    out = StepAsk(_model(dict(act, call="propose_act")), bench).run(OBLIGATION, failed)
    assert out["ok"] and out["move"] == "refile_act"
    assert bench.acts[0][2] == act
