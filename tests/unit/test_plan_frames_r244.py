"""THE FRAME (Steven, 2026-09-10) -- the agent writes the person's bullet,
pinned to the part it stands for.

The bench's template now carries a `you` blank on every step, act and account
row, each with the bench's own note saying what belongs there ("What the
person does here, one line: starts with connect / approve / pick / answer,
names the thing"). The blank rides the draft door's `blanks` list like any
other, so the harness asks the model to write it in the same call it already
makes, with the bench's note as the instruction. The bench prints a flat line
only when the slot is left empty.

The bench used to refuse (REJ-44) a promise that starts in the first person or
a work item not starting with an -ing word. Contract 4.0 RETIRED that code to
a scoreboard nobody is shown: it no longer appears in `problems` and blocks
nothing, so the harness holds no workaround for it. A bench that names it
anyway is answered like every other row -- its sentence, its path, and no code
in front of the model.
"""
from __future__ import annotations

from toll_harness import cli
from toll_harness.toll_bench import blocks

_OUTLINE = {"steps": [{"ask": "APPROVE", "title": "Draft the introduction email",
                       "tool": "gmail.message.send", "on": "google-gmail"}]}


def test_the_two_retired_codes_are_not_anticipated_anywhere():
    """RETIRED (bench contract 4.0, 2026-09-17). The line-wording code and the
    step-count code go to a scoreboard the agent is never shown: they no
    longer appear in `problems` and they block nothing. So the harness holds
    no workaround for either -- no re-wording a line to fit a frame, no
    copy of the band minimum -- and the names stay only where the FILE door
    still uses them.
    """
    import inspect

    from toll_harness.toll_bench import draft

    # Nothing in the draft loop reads either code.
    source = inspect.getsource(draft)
    assert "REJ-44" not in source and "REJ-12" not in source
    assert not hasattr(blocks, "band_floor")


def test_the_harness_own_words_obey_the_frame():
    assert "you will file" not in cli._FILE_INFORMED_PLAN_INSTRUCTION
