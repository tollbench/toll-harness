"""THE FRAME (Steven, 2026-09-10) -- the agent writes the person's bullet,
pinned to the part it stands for.

The bench's template now carries a `you` blank on every step, act and account
row, each with the bench's own note saying what belongs there ("What the
person does here, one line: starts with connect / approve / pick / answer,
names the thing"). The blank rides the draft door's `blanks` list like any
other, so the harness asks the model to write it in the same call it already
makes, with the bench's note as the instruction. The bench prints a flat line
only when the slot is left empty.

The bench also refuses (REJ-44) a promise that starts in the first person, a
work item not starting with an -ing word, or a `you` line that is a status
phrase; the refusal quotes the frame. It takes the generic fix path: nothing
is repaired at home.
"""
from __future__ import annotations

from tests.unit.test_draft_loop_r241 import FakeDraftBench, _model
from toll_harness import cli
from toll_harness.toll_bench import blocks, book_of_houses, programs
from toll_harness.toll_bench.draft import BLANKS_INSTRUCTION, DraftLoop

# The bench's own notes, verbatim (app/services/plan_frames.py on the bench).
YOU_NOTE = (
    "What the person does here, one line: starts with connect / approve / "
    "pick / answer, names the thing. Leave it empty and the platform prints a "
    "flat line."
)
WORK_FRAME = "1-3 lines, each starting with an -ing word and naming what it produces."


class BenchWithYou(FakeDraftBench):
    """The draft door as it now expands a step: a `you` blank on the step and
    on its act, listed beside the promise with the bench's note."""

    def put_draft(self, target_id, outline, *, kind="bid"):
        super().put_draft(target_id, outline, kind=kind)
        for step in self.document["steps"]:
            step["you"] = ""
            step["acts"] = [{"kind": "email", "purpose": "the intro", "you": ""}]
        return self.answer()

    def blanks(self):
        rows = super().blanks()
        for index, step in enumerate(self.document.get("steps") or []):
            if not step.get("you"):
                rows.append({"path": f"steps.{index}.you", "note": YOU_NOTE,
                             "example": "Approve the email to Grant", "required": False})
            for ai, act in enumerate(step.get("acts") or []):
                if not act.get("you"):
                    rows.append({"path": f"steps.{index}.acts.{ai}.you", "note": YOU_NOTE,
                                 "example": "Approve the email to Grant", "required": False})
        return rows


_OUTLINE = {"steps": [{"ask": "APPROVE", "title": "Draft the introduction email",
                       "tool": "gmail.message.send", "on": "google-gmail"}]}
_PROMISE = "An email draft ready for review."


def _asks(model):
    return [inv["messages"][0].content[0]["text"] for inv in model.invocations]


def test_the_you_blank_is_asked_for_with_the_benchs_note_and_filed():
    bench = BenchWithYou()
    model = _model(
        _OUTLINE,
        {"patches": [
            {"path": "steps.0.outcome_promise", "value": _PROMISE},
            {"path": "steps.0.you", "value": "Approve the email to Grant"},
            {"path": "steps.0.acts.0.you", "value": "Approve the email to Grant"},
        ]},
        {"patches": [{"path": "pitch_title", "value": "An introduction, done right"}]},
        {"patches": []},
    )

    DraftLoop(model, bench).run("t-1", brief={"want": "Introduce me to Grant"},
                                idempotency_key="k")

    asked = _asks(model)[1]
    assert "steps.0.you" in asked and "steps.0.acts.0.you" in asked
    assert "starts with connect / approve / pick / answer" in asked, "the bench's note rides"
    assert "pinned to it" in asked, "and the instruction says to write it, not leave it"
    filed = bench.filed[2]
    assert filed["steps"][0]["you"] == "Approve the email to Grant"
    assert filed["steps"][0]["acts"][0]["you"] == "Approve the email to Grant"


def test_the_instruction_carries_the_frame_in_the_benchs_words():
    assert "`you` blank" in BLANKS_INSTRUCTION
    assert "starts with connect / approve / pick / answer" in BLANKS_INSTRUCTION
    assert "names the thing you deliver, never you" in BLANKS_INSTRUCTION
    assert "-ing word" in BLANKS_INSTRUCTION


def test_rej_44_is_named_and_takes_the_generic_fix_path():
    assert blocks.REJ_FRAME == "REJ-44" == book_of_houses.REJ_FRAME
    refusal = ("step 1 minor_detail line 1 does not start with an -ing word: "
               '"Book of Houses runs the invitation." Write it to its frame -- ' + WORK_FRAME)
    bench = FakeDraftBench(fixes=[{
        "path": "steps.0.minor_detail",
        "current": "Book of Houses runs the invitation.",
        "code": "REJ-44",
        "fix": refusal,
        "detail": None,
    }])
    model = _model(
        _OUTLINE,
        {"patches": [{"path": "steps.0.outcome_promise", "value": _PROMISE}]},
        {"patches": [{"path": "pitch_title", "value": "The intro"}]},
        {"patches": [{"path": "steps.0.minor_detail",
                      "value": "Offering the invitee three times from your calendar"}]},
        {"patches": []},
    )

    DraftLoop(model, bench).run("t-1", brief={"want": "Introduce me to Grant"},
                                idempotency_key="k")

    texts = _asks(model)
    assert any("REJ-44" in t and "-ing word" in t for t in texts), texts
    assert bench.filed[2]["steps"][0]["minor_detail"].startswith("Offering ")


def test_the_harness_own_words_obey_the_frame():
    assert "you will file" not in cli._FILE_INFORMED_PLAN_INSTRUCTION
    assert "An email draft ready for your approval." in cli._FILE_INFORMED_PLAN_INSTRUCTION
    assert "you" in programs.EXPECTED_LEAVES, "a `you` the model wrote is its words"
