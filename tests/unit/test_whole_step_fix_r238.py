"""A STEP-LEVEL PROBLEM HAS TWO EXITS: replace the step, or drop it (0.38.3).

WHAT FORCED THIS FILE (production, 2026-09-11, one fleet unit on want
14db651d). The bench refused a form step `restates_the_pick` -- "The person
picks who this goes to on their own step (step 1, Who should this go to?). Do
not plan a step for it. Say what you DO with the people they pick, or drop this
step." -- three rounds running. Each round the fix ask handed the model ONE
LINE of the step (`form.steps.0.do_line`) and the model reworded that one line:
"picks" became "selects". The verb `finds` never moved and the hand-over line
stayed "A list of two contacts with their names and email addresses", so the
bench's check stayed true. Three namings, `draft_stalled`, then `plan_failed`.

Rewording one line of a step cannot clear a problem that is about the step. So:
the whole step goes in front of the model, both exits are named, and a drop is
sent as the door's own drop instruction instead of a patch.

NOTE ON THE BENCH SIDE: as this was written the bench still named
`form.steps.N.do_line` and published no `drop` instruction (checked in
plan_form.py and draft_routes.py on staging). Both paths are held here -- the
field path the bench uses today, and the whole-step path it is moving to -- and
the drop call is built to the agreed shape,
`PATCH {"kind": "plan", "drop": {"step": N}}`.
"""
from __future__ import annotations

import json
import logging

from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    RESTATES_THE_PICK,
    DraftLoop,
    read_drop,
    whole_step_path,
)


def _says(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[]
    )


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [_says(json.dumps(a) if not isinstance(a, str) else a) for a in answers]
    )


# The step as the fleet unit actually filed it, every field on it.
RESTATING_STEP = {
    "verb": "finds",
    "do_line": "finds the two friends the person picks in their contact book",
    "hand_over_line": "A list of two contacts with their names and email addresses",
    "need_line": "",
    "who": "agent",
    "declared_odds": 0.9,
    "proof": "the list",
    "tool": "",
    "bid_step": 1,
}

THE_QUESTION = (
    "The person picks who this goes to on their own step (step 1, Who should "
    "this go to?). Do not plan a step for it. Say what you DO with the people "
    "they pick, or drop this step."
)


def _fix(path):
    return {
        "path": path,
        "code": RESTATES_THE_PICK,
        "question": THE_QUESTION,
        "detail": "a step whose only deliverable is the person's own contact pick",
        "current": RESTATING_STEP if whole_step_path(path) is not None else
        RESTATING_STEP["do_line"],
    }


class FormBench:
    """The plan door, scripted: one answer per call, and the calls recorded."""

    fleet = None

    def __init__(self, *answers):
        self.answers = list(answers)
        self.patches: list[list[dict]] = []
        self.drops: list[int] = []

    def _next(self):
        return self.answers.pop(0) if self.answers else {"ok": True, "ready": True}

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.patches.append(list(patches))
        return self._next()

    def drop_draft_step(self, target_id, step, *, kind="plan"):
        self.drops.append(int(step))
        return self._next()


def _answer(path, form_steps=(RESTATING_STEP,)):
    return {
        "ok": True,
        "ready": False,
        "closed": None,
        "next_fix": _fix(path),
        "draft": {"form": {"steps": [dict(step) for step in form_steps]},
                  "steps": [{"title": "Send the notes"}]},
        "form": {"steps": [dict(step) for step in form_steps]},
        "rounds": {"used": 3, "left": 17, "cap": 20},
    }


def _ask_text(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


def _said(model, index=0):
    """The whole ask: the instruction and the payload ride the MESSAGE; the
    system is the run's stable prefix and never changes per round."""
    return model.invocations[index]["messages"][0].content[0]["text"]


# ---------------------------------------------------------------------------
# 1. The path reader
# ---------------------------------------------------------------------------
def test_a_whole_step_path_is_told_from_a_field_on_one():
    assert whole_step_path("form.steps.0") == 0
    assert whole_step_path("steps.12") == 12
    assert whole_step_path("form.steps.0.do_line") is None
    assert whole_step_path("form.span_days") is None
    assert whole_step_path("") is None


# ---------------------------------------------------------------------------
# 2. The whole-step ask carries the whole step, and both exits
# ---------------------------------------------------------------------------
def test_a_whole_step_question_hands_the_model_every_field():
    bench = FormBench({"ok": True, "ready": True})
    replacement = {
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
    model = _model({"patches": [{"path": "form.steps.0", "value": replacement}]})
    loop = DraftLoop(model, bench)

    loop._answer_the_fixes("t-1", "plan", _answer("form.steps.0"), "Send thank-you notes")

    ask = _ask_text(model)
    # EVERY field of the step, not the shed version a one-line ask gets.
    for field in RESTATING_STEP:
        assert field in ask, field
    assert RESTATING_STEP["hand_over_line"] in ask
    # And both exits, named.
    said = _said(model)
    assert "TWO EXITS" in said
    assert "REPLACE IT" in said and "DROP IT" in said
    assert '"drop": {"step"' in said
    # The replacement went in as a patch on the whole step.
    assert bench.patches == [[{"path": "form.steps.0", "value": replacement}]]
    assert bench.drops == []


def test_a_restating_step_is_told_what_would_make_it_a_step():
    bench = FormBench({"ok": True, "ready": True})
    model = _model({"drop": {"step": 1}})
    DraftLoop(model, bench)._answer_the_fixes(
        "t-1", "plan", _answer("form.steps.0"), "Send thank-you notes"
    )

    said = _said(model)
    assert "handing back people the person already picked" in said
    assert "email them, meet them, call them" in said
    assert "DROP IT" in said
    # The bench's own question rides in front of the model unchanged.
    assert THE_QUESTION in _ask_text(model)


# ---------------------------------------------------------------------------
# 3. A drop answer becomes the door's drop call, not a patch
# ---------------------------------------------------------------------------
def test_a_drop_answer_becomes_the_doors_drop_call():
    bench = FormBench({"ok": True, "ready": True})
    model = _model({"drop": {"step": 0}})

    DraftLoop(model, bench)._answer_the_fixes(
        "t-1", "plan", _answer("form.steps.0"), "Send thank-you notes"
    )

    assert bench.drops == [0]
    assert bench.patches == []


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
    bench = FormBench({"ok": True, "ready": True})
    model = _model({"drop": {"step": 0}})
    loop = DraftLoop(model, bench)

    loop._answer_the_fixes("t-1", "plan", _answer("form.steps.0"), "x")

    assert loop.rounds == 1
    assert loop._sent == [("form.steps.0", {"drop": {"step": 0}})]


# ---------------------------------------------------------------------------
# 4. A field patch to a whole-step question is the wrong shape
# ---------------------------------------------------------------------------
def test_a_reworded_line_on_a_whole_step_path_is_asked_again():
    # This is the prod failure in one test: the model answers a step-level
    # question by rewording one line of the step.
    bench = FormBench({"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": "form.steps.0.do_line",
                      "value": "finds the two friends the person selects"}]},
        {"drop": {"step": 0}},
    )

    DraftLoop(model, bench)._answer_the_fixes(
        "t-1", "plan", _answer("form.steps.0"), "x"
    )

    # Asked twice, the second time with the whole-step instruction in front.
    assert len(model.invocations) == 2
    assert _said(model, 1).count("TWO EXITS") >= 1
    # The reworded line never reached the door.
    assert bench.patches == []
    assert bench.drops == [0]


def test_the_second_answer_is_taken_whatever_shape_it_is():
    # Asked once more, answered the same way: the field patch goes, because a
    # third ask on one question is the loop this brake exists to stop.
    reworded = {"path": "form.steps.0.do_line",
                "value": "finds the two friends the person selects"}
    bench = FormBench({"ok": True, "ready": True})
    model = _model({"patches": [reworded]}, {"patches": [reworded]})

    DraftLoop(model, bench)._answer_the_fixes(
        "t-1", "plan", _answer("form.steps.0"), "x"
    )

    assert len(model.invocations) == 2
    assert bench.patches == [[reworded]]


# ---------------------------------------------------------------------------
# 5. The repeated branch names the exits -- on a FIELD path too
# ---------------------------------------------------------------------------
def test_the_repeated_branch_names_both_exits_on_a_field_path():
    """The bench today names `form.steps.N.do_line`. When it names it TWICE,
    rewording is not the answer and the ask must say so."""
    field = "form.steps.0.do_line"
    bench = FormBench(_answer(field), {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": field, "value": "finds the two friends they select"}]},
        {"drop": {"step": 0}},
    )

    DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(field), "x")

    second = _said(model, 1)
    assert "REWORDING DID NOT WORK" in second
    assert "REPLACE THE WHOLE STEP" in second
    assert "DROP THE STEP" in second
    # And the restating sentence, because that is the code.
    assert "handing back people the person already picked" in second
    # The second ask carries the whole step and the path to send it to.
    assert '"steps_path":"form.steps.0"' in second
    assert RESTATING_STEP["hand_over_line"] in second
    assert '"verb":"finds"' in second
    # It took the drop.
    assert bench.drops == [0]


def test_the_repeated_branch_still_shows_what_was_sent_last_round():
    field = "form.steps.0.do_line"
    bench = FormBench(_answer(field), {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": field, "value": "finds the two friends they select"}]},
        {"drop": {"step": 0}},
    )

    DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(field), "x")

    ask = _ask_text(model, 1)
    assert "your_last_patch_did_not_clear_this" in ask
    assert "finds the two friends they select" in ask


def test_a_whole_step_replacement_on_a_field_path_is_not_aimed_back_at_the_field():
    """`_aim` files a stray patch at the path that was asked for. A step sent
    back WHOLE against a field question is the right answer to the repeated
    ask, and re-aiming it at `...do_line` would write a dict into a line."""
    field = "form.steps.0.do_line"
    replacement = dict(RESTATING_STEP, verb="emails",
                       do_line="emails each person the note you approved",
                       hand_over_line="A sent receipt for every note")
    bench = FormBench(_answer(field), {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": field, "value": "finds the two friends they select"}]},
        {"patches": [{"path": "form.steps.0", "value": replacement}]},
    )

    DraftLoop(model, bench)._answer_the_fixes("t-1", "plan", _answer(field), "x")

    assert bench.patches[-1] == [{"path": "form.steps.0", "value": replacement}]
    assert bench.drops == []


# ---------------------------------------------------------------------------
# 6. The three-tries brake is untouched
# ---------------------------------------------------------------------------
def test_three_namings_of_one_problem_still_stop_the_draft(caplog):
    field = "form.steps.0.do_line"
    bench = FormBench(_answer(field), _answer(field), _answer(field))
    model = _model(
        *[{"patches": [{"path": field, "value": f"finds them, take {n}"}]} for n in range(4)]
    )

    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        out = DraftLoop(model, bench)._answer_the_fixes(
            "t-1", "plan", _answer(field), "x"
        )

    assert out["error"] == "draft_stalled"
    assert "three times" in out["message"]
