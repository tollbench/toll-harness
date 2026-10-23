"""THE PLAN'S OWN FIELDS SURVIVE (0.42.1).

WHAT FORCED THESE TESTS. On 0.42.0 `read_form` rebuilt the form from `steps`
and `span_days` alone, so the overall `odds` and the `overview` a model wrote
were dropped before the PUT, and the door asked for `form.odds` again. Kari
answered that fix with {"odds": ...} and {"form": {"odds": ...}}; `read_patches`
read neither, so every round "said nothing" until the draft stopped. The step
whitelist was also older than the door's (no work_line, words, room, wait).
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.unit.plan_door import a_row
from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_two_stages_r243_r245 import FILLED_STEP, FormBench
from toll_harness.toll_bench.draft import (
    FORM_INSTRUCTION,
    FORM_STEP_FIELDS,
    DraftLoop,
    read_form,
    read_patches,
)

# The door's published list: the PlanFormStep snapshot the parity test
# (test_form_step_parity.py) keeps in line with the bench's openapi.
SERVER_STEP_FIELDS = tuple(json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "bench_plan_form_step.json")
    .read_text())["properties"])


def test_file_kind_rides_a_file_step_to_the_bench():
    """0.57.2: bench contract 4.1.11 lets a `proof: file` step say what kind
    of file it hands back; read_form used to drop the key, so a song step was
    frozen as a PDF and its mp3 could never close it."""
    step = dict(FILLED_STEP, proof="file", file_kind="audio")
    read = read_form({"overview": "A song.", "odds": 0.5, "span_days": 7,
                      "steps": [step]})
    assert read["steps"][0]["file_kind"] == "audio"


class PlanFieldBench(FormBench):
    """FormBench that also asks the plan's own questions, like plan_form."""

    def _form_answer(self):
        out = super()._form_answer()
        form = self.form or {}
        odds = form.get("odds")
        legal = (isinstance(odds, (int, float)) and not isinstance(odds, bool)
                 and 0 < odds < 1)
        missing = []
        if self.form is not None and not legal:
            missing.append("form.odds")
        if self.form is not None and not str(form.get("overview") or "").strip():
            missing.append("form.overview")
        if missing and self.closed is None:
            out["next_fix"] = a_row(missing[0], slot=missing[0].rsplit(".", 1)[-1],
                                    say="What is your chance?", codes=["blank"])
            out["remaining"] = out["remaining"] + len(missing)
            out["ready"] = False
        return out


def _form(count=3, **plan):
    return dict({"overview": "I find three shows and book one.", "odds": 0.35,
                 "span_days": 14,
                 "steps": [dict(FILLED_STEP) for _ in range(count)]}, **plan)


def test_the_step_fields_are_the_doors_published_list():
    assert set(FORM_STEP_FIELDS) == set(SERVER_STEP_FIELDS)


def test_a_complete_form_keeps_odds_overview_and_newer_step_fields():
    step = dict(FILLED_STEP, verb="posts", work_line="Drafting the post",
                words="Hello world", room="#general", wait=3)
    read = read_form({"overview": "Scope.", "odds": 0.75, "span_days": 10,
                      "steps": [step]})
    assert read["odds"] == 0.75 and read["overview"] == "Scope."
    for name in ("work_line", "words", "room", "wait"):
        assert read["steps"][0][name] == step[name]
    # Unchanged, not coerced.
    assert read_form({"odds": "0.75", "steps": [FILLED_STEP]})["odds"] == "0.75"
    # Absent stays absent: nothing invented.
    bare = read_form({"steps": [FILLED_STEP]})
    assert "odds" not in bare and "overview" not in bare


def test_the_put_carries_odds_and_overview_and_files():
    bench = PlanFieldBench()
    model = _model(_form(3))
    out = DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                      brief={"want": "Podcasts"}, idempotency_key="k")
    assert out["ok"] is True and out["filed"] is True, out
    sent = bench.puts[1]["form"]
    assert sent["odds"] == 0.35 and sent["overview"].startswith("I find")
    assert len(model.invocations) == 1


def test_explicit_and_the_two_bare_shapes_answer_the_asked_odds():
    want = [{"path": "form.odds", "value": 0.75}]
    assert read_patches({"patches": want}, "form.odds") == want
    assert read_patches({"odds": 0.75}, "form.odds") == want
    assert read_patches({"form": {"odds": 0.75}}, "form.odds") == want
    # Type preserved.
    assert read_patches({"odds": "75%"}, "form.odds") == [{"path": "form.odds", "value": "75%"}]


def test_missing_or_ambiguous_answers_invent_nothing():
    assert read_patches({"odds": 0.75}) == []                       # nothing asked
    assert read_patches({"odds": 0.75}, "form.overview") == []      # other field
    assert read_patches({"odds": 0.75}, "form.steps.0.do_line") == []
    assert read_patches({"odds": None}, "form.odds") == []
    assert read_patches({"sorry": "no"}, "form.odds") == []
    assert read_patches({}, "form.odds") == []
    assert read_patches({"odds": 0.5, "form": {"odds": 0.9}}, "form.odds") == []


def test_a_multi_step_plan_is_corrected_then_filed():
    bench = PlanFieldBench()
    model = _model(_form(4, odds=None, overview=None),
                   {"odds": 0.6},
                   {"form": {"overview": "Four steps to a booked show."}})
    out = DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                      brief={"want": "Podcasts"}, idempotency_key="k")
    assert out["ok"] is True and out["filed"] is True, out
    assert bench.form["odds"] == 0.6
    assert bench.form["overview"] == "Four steps to a booked show."
    assert len(bench.form["steps"]) == 4
    # 0.68.0: each answer goes back as the whole form, in one PUT, with every
    # step kept: the open, the form, then one refile per answer.
    assert bench.patches == []
    assert len(bench.puts) == 4
    assert [len(put["form"]["steps"]) for put in bench.puts[1:]] == [4, 4, 4]
    # The plan-level fix saw the plan-level fields and the row it was asked.
    ask = model.invocations[1]["messages"][0].content[0]["text"]
    assert '"path":"form.odds"' in ask and "span_days" in ask


def test_no_answer_files_nothing_and_stops_after_one_more_ask():
    bench = PlanFieldBench()
    model = _model(_form(2, odds=None), "", "not json at all")
    out = DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                      brief={"want": "Podcasts"}, idempotency_key="k")
    assert bench.plan_filed is None
    assert bench.patches == []
    assert "odds" not in (bench.form or {})
    assert len(model.invocations) == 3  # the form, the fix, once more
    assert out.get("filed") is not True


def test_an_answer_the_door_keeps_refusing_still_trips_the_guard():
    bench = PlanFieldBench()
    model = _model(_form(2, odds=None), *([{"odds": 1.5}] * 10))
    out = DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                      brief={"want": "Podcasts"}, idempotency_key="k")
    assert bench.plan_filed is None
    assert len(model.invocations) <= 1 + DraftLoop.SAME_PROBLEM_ROUNDS
    assert out.get("filed") is not True


def test_the_form_prompt_shows_odds_and_overview_and_promises_no_trim():
    assert '"odds"' in FORM_INSTRUCTION and '"overview"' in FORM_INSTRUCTION
    assert "TRIMS" not in FORM_INSTRUCTION and "never refuses" not in FORM_INSTRUCTION
