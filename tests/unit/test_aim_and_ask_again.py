"""0.35.6: the asked path is the path, and an empty answer gets one more ask."""

from __future__ import annotations

import importlib.util
import pathlib

from toll_harness.toll_bench.draft import DraftLoop

_spec = importlib.util.spec_from_file_location(
    "draft_loop_helpers", pathlib.Path(__file__).with_name("test_draft_loop_r241.py")
)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_OUTLINE, FakeDraftBench, _model = _helpers._OUTLINE, _helpers.FakeDraftBench, _helpers._model

# The blanks the fake bench asks for first, a step at a time, before any fix.
_BLANKS = [
    {"patches": [{"path": "steps.0.outcome_promise", "value": "Three places, with sources."}]},
    {"patches": [{"path": "steps.1.outcome_promise", "value": "A booked table."}]},
    {"patches": [{"path": "pitch_title", "value": "A table for four"}]},
]


def test_a_single_patch_for_the_wrong_path_is_filed_at_the_asked_path():
    """Cindy, 2026-09-09: asked for steps.1.outcome_promise sixty rounds
    running, answered steps.2.outcome_promise every time. (Here on a title,
    because the fake bench's blanks would fill an outcome_promise first.)"""
    bench = FakeDraftBench(cap=9)
    bench.pending_fixes = [
        {"path": "steps.1.title", "current": "", "code": "REJ-34",
         "fix": "Say how the introduction is made.", "detail": None}
    ]
    model = _model(
        _OUTLINE,
        *_BLANKS,
        {"patches": [{"path": "steps.2.title", "value": "Send the introduction email"}]},
    )
    outcome = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9",
        brief={"want": "Connect two people"}, idempotency_key="k"
    )
    assert outcome["ok"] is True, outcome
    # One patch, and it landed where the bench asked, not where the model aimed.
    assert bench.patch_calls[-1] == [
        {"path": "steps.1.title", "value": "Send the introduction email"}
    ]
    assert bench.filed is not None


def test_two_patches_are_left_where_the_model_put_them():
    bench = FakeDraftBench(cap=6)
    bench.pending_fixes = [
        {"path": "steps.0.title", "current": "", "code": "REJ-34",
         "fix": "Say how.", "detail": None}
    ]
    wide = {"patches": [
        {"path": "steps.0.outcome_promise", "value": "A."},
        {"path": "steps.1.outcome_promise", "value": "B."},
    ]}
    model = _model(_OUTLINE, *_BLANKS, *[wide for _ in range(8)])
    outcome = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Book a table"}, idempotency_key="k"
    )
    # The bench names `steps.0.title` every round and the model never
    # answers it; the third naming of that (path, code) ends the draft.
    assert outcome["ok"] is False and outcome["error"] == "draft_stalled"
    # After the three blanks, every round went out as the model wrote it.
    assert all(len(call) == 2 for call in bench.patch_calls[3:])
    assert len(bench.patch_calls) > 3
    assert not any(
        entry["path"] == "steps.0.title" for call in bench.patch_calls for entry in call
    )


def test_an_empty_answer_is_asked_once_more_before_the_draft_is_given_up():
    bench = FakeDraftBench(cap=9)
    bench.pending_fixes = [
        {"path": "steps.0.title", "current": "", "code": "REJ-34",
         "fix": "Say how.", "detail": None}
    ]
    model = _model(
        _OUTLINE,
        *_BLANKS,
        "",  # nothing came back
        {"patches": [{"path": "steps.0.title", "value": "Email the venue and book it"}]},
    )
    outcome = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Book a table"}, idempotency_key="k"
    )
    assert outcome["ok"] is True, outcome
    again = model.invocations[-1]["messages"][0].content[0]["text"]
    assert "NOTHING CAME BACK LAST TIME" in again
    assert bench.patch_calls[-1][0]["path"] == "steps.0.title"


def test_two_empty_answers_end_the_draft():
    bench = FakeDraftBench(cap=9)
    bench.pending_fixes = [
        {"path": "steps.0.title", "current": "", "code": "REJ-34",
         "fix": "Say how.", "detail": None}
    ]
    model = _model(_OUTLINE, *_BLANKS, "", "")
    outcome = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Book a table"}, idempotency_key="k"
    )
    assert outcome["ok"] is False
    assert bench.filed is None
    assert len(model.invocations) == 6  # outline, three blanks, ask, ask again


def test_a_parent_path_or_another_field_is_left_where_the_model_put_it():
    """`finalist_questions` for `finalist_questions.0.0` is the whole list
    coming back, which is right; `steps.0.title` for `steps.1.outcome_promise`
    is another change altogether. Neither moves."""
    bench = FakeDraftBench(cap=9)
    loop = DraftLoop(_model(), bench)
    whole = [{"path": "finalist_questions", "value": ["a", "b"]}]
    assert loop._aim(whole, "finalist_questions.0.0", "bid", "t-1") == whole
    other = [{"path": "steps.0.title", "value": "x"}]
    assert loop._aim(other, "steps.1.outcome_promise", "bid", "t-1") == other
    same_field = [{"path": "steps.2.outcome_promise", "value": "x"}]
    assert loop._aim(same_field, "steps.1.outcome_promise", "bid", "t-1") == [
        {"path": "steps.1.outcome_promise", "value": "x"}
    ]
