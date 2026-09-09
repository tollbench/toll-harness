"""AN AGENT'S OWN WINS ARE ITS SHELF (Steven, 2026-09-09).

The bench stopped pushing worked programs onto briefs, and the right shelf was
never a stranger's plan anyway: it is the plans THIS agent already got picked
for. A want that needs the same tools as a job it already won is that job again
in other words, so the outline starts as that plan's shape and the one model
call asks only what changes.
"""
from __future__ import annotations

import json

from tests.unit.test_draft_loop_r241 import FakeDraftBench, _model
from toll_harness.toll_bench.draft import (
    DraftLoop,
    families_the_want_needs,
    nearest_win,
    outline_of,
    own_wins,
)

TOOLS = [
    {"family": "mail", "provider": "google-gmail", "tool": "gmail.message.send",
     "one_line": "Send an email from their mailbox."},
    {"family": "calendar", "provider": "google-calendar",
     "tool": "calendar.events.create", "one_line": "Put an event on their calendar."},
    {"family": "sheet", "provider": "google-sheets", "tool": "sheet.rows.append",
     "one_line": "Add rows to a spreadsheet."},
]

BRIEF = {
    "want": "Email twelve florists and get a calendar hold for the visits",
    "tools": TOOLS,
}

WIN = {
    "id": "p-win",
    "status": "accepted",
    "want": "Email eight caterers and book the tastings",
    "pitch_title": "Twelve emails, three tastings",
    "steps": [
        {"ask": "APPROVE", "title": "Find the caterers",
         "acts": [{"kind": "calls", "runs": [
             {"name": "find", "tool": "platform.research"}]}]},
        {"ask": "APPROVE", "title": "Email them and hold the times",
         "acts": [{"kind": "calls", "runs": [
             {"name": "send", "tool": "gmail.message.send", "on": "google-gmail"},
             {"name": "hold", "tool": "calendar.events.create",
              "on": "google-calendar"}]}]},
    ],
}

UNRELATED = {
    "id": "p-other",
    "status": "accepted",
    "want": "Keep a spreadsheet of prices up to date",
    "steps": [
        {"ask": "APPROVE", "title": "Update the sheet",
         "acts": [{"kind": "calls", "runs": [
             {"name": "rows", "tool": "sheet.rows.append", "on": "google-sheets"}]}]}
    ],
}


def test_only_the_plans_this_agent_was_picked_for_are_a_shelf():
    rows = [
        WIN,
        {"id": "p-filed", "status": "filed", "steps": [{"title": "x"}]},
        {"id": "p-withdrawn", "status": "withdrawn", "steps": [{"title": "x"}]},
        {"id": "p-selected", "status": "filed", "finalist_ordinal": 1,
         "steps": [{"title": "x"}]},
        {"id": "p-walked", "status": "filed", "deal": {"deal_id": "d-9"},
         "steps": [{"title": "x"}]},
        {"id": "p-empty", "status": "accepted", "steps": []},
    ]

    kept = {
        row["id"]
        for row in own_wins(rows, {"your_finished_walks": [{"deal_id": "d-9"}]})
    }

    assert kept == {"p-win", "p-selected", "p-walked"}


def test_the_families_a_want_needs_are_read_off_the_bench_s_own_index():
    assert families_the_want_needs(BRIEF) == {"mail", "calendar"}
    # A want that names none of them gets none: no guessing.
    assert families_the_want_needs({"want": "Write me a poem", "tools": TOOLS}) == set()


def test_the_nearest_win_is_the_one_that_ran_the_same_tools():
    win, score = nearest_win([UNRELATED, WIN], BRIEF)

    assert win is WIN
    assert score > 0


def test_nothing_seeds_when_no_win_shares_a_tool_family():
    win, score = nearest_win([UNRELATED], BRIEF)

    assert win is None
    assert score == 0


def test_a_plan_reads_back_as_an_outline():
    outline = outline_of(WIN)

    assert outline == {
        "steps": [
            {"ask": "APPROVE", "title": "Find the caterers", "tool": "platform.research",
             "on": ""},
            {"ask": "APPROVE", "title": "Email them and hold the times",
             "tool": "gmail.message.send", "on": "google-gmail",
             "tools": [{"tool": "gmail.message.send", "on": "google-gmail"},
                       {"tool": "calendar.events.create", "on": "google-calendar"}]},
        ]
    }


class _WithWins(FakeDraftBench):
    def __init__(self, wins, **kwargs):
        super().__init__(**kwargs)
        self.wins = wins
        self.proposal_reads = 0

    def _owned_proposals(self):
        self.proposal_reads += 1
        return list(self.wins)


def test_a_seeded_outline_reproduces_the_tools_and_the_step_count_of_the_win():
    bench = _WithWins([UNRELATED, WIN])
    # The model is asked ONE small question -- adjust this -- and answers with
    # the same shape in this want's words.
    adjusted = {
        "steps": [
            {"ask": "APPROVE", "title": "Find the florists", "tool": "platform.research"},
            {"ask": "APPROVE", "title": "Email them and hold the visits",
             "tool": "gmail.message.send", "on": "google-gmail"},
        ]
    }
    model = _model(
        adjusted,
        {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
        {"patches": [{"path": "steps.1.outcome_promise", "value": "B."}]},
        {"patches": [{"path": "pitch_title", "value": "Florists"}]},
    )

    loop = DraftLoop(model, bench)
    loop.run("t-1", brief=BRIEF, idempotency_key="k")

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    assert "outline_you_ran" in ask
    assert "You have done a job like this one before" in ask
    assert "gmail.message.send" in ask
    assert loop.seeded_by == "p-win"
    # The outline that went in has the win's step count and its tools.
    sent = bench.puts[0][1]
    assert len(sent["steps"]) == len(WIN["steps"])
    assert [step.get("tool") for step in sent["steps"]] == [
        "platform.research", "gmail.message.send"
    ]


def test_no_seed_when_nothing_overlaps_and_the_outline_is_written_as_before():
    bench = _WithWins([UNRELATED])
    model = _model(
        {"steps": [{"ask": "APPROVE", "title": "Write it from scratch"}]},
        {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
        {"patches": [{"path": "pitch_title", "value": "T"}]},
    )

    loop = DraftLoop(model, bench)
    loop.run("t-1", brief=BRIEF, idempotency_key="k")

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    assert "outline_you_ran" not in ask
    assert "Write the OUTLINE" in ask
    assert loop.seeded_by is None


def test_the_shelf_costs_one_bench_call_a_run():
    bench = _WithWins([WIN])
    model = _model(
        {"steps": [{"ask": "APPROVE", "title": "One step"}]},
        {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
        {"patches": [{"path": "pitch_title", "value": "T"}]},
    )

    DraftLoop(model, bench).run("t-1", brief=BRIEF, idempotency_key="k")

    assert bench.proposal_reads == 1


def test_a_seed_the_model_will_not_adjust_still_goes_in():
    """The shape that was accepted is the best thing anyone has for this want;
    a model that answers the adjust call with nothing must not cost the want."""
    bench = _WithWins([WIN])
    model = _model(
        {"nothing": "here"},
        {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
        {"patches": [{"path": "steps.1.outcome_promise", "value": "B."}]},
        {"patches": [{"path": "pitch_title", "value": "T"}]},
    )

    outcome = DraftLoop(model, bench).run("t-1", brief=BRIEF, idempotency_key="k")

    assert outcome["ok"] is True
    assert len(bench.puts[0][1]["steps"]) == len(WIN["steps"])


def test_the_seeded_ask_stays_small():
    bench = _WithWins([WIN])
    model = _model(
        {"steps": [{"ask": "APPROVE", "title": "One step"}]},
        {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
        {"patches": [{"path": "pitch_title", "value": "T"}]},
    )

    DraftLoop(model, bench).run("t-1", brief=BRIEF, idempotency_key="k")

    call = model.invocations[0]
    tail = call["messages"][0].content[0]["text"]
    # The outline it ran, not the plan it filed: no promises, no blocks, no
    # money, no questions.
    assert "outcome_promise" not in tail
    assert len(tail) // 4 < 1_000
    assert json.loads(tail[tail.rindex("\n\n") + 2 :])["outline_you_ran"]
