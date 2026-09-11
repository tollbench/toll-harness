"""THE REFUSAL BRAKE -- three refusals on one state and the old road stops.

WHAT FORCED THESE TESTS (production, 2026-09-11). One fleet unit sat on a deal
step no filing could ever satisfy: the bench refused every outcome 422
`stand_in`, because the step only restated the person's own Contact-book pick
and a typed name is a stand-in, not a value. The small step ask gave up after
two refused asks and handed the step to the OLD ROAD -- the whole agentic run,
60-76k input tokens of tools brief -- and the old road had no stop rule at
all. From the third cycle on it ran again every ~40 seconds forever: same
state, same filing, same refusal.

`_IDLE_STEP_MEMO` cannot catch that. A run whose filings are all refused ends
FAILED (the runtime stops a run after three refused protected writes), and a
failed run deliberately records no memo, because an adapter error must retry
at full cadence. So the old road needed a brake of its own, and the number is
THREE (Steven: "two seems odd, how about 3, in case of a mistake"). Each test
below holds one line of that rule.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.core.types import Checkpoint, ModelUsage, RunResult, RunStatus

STAND_IN = {
    "ok": False,
    "error": "stand_in",
    "status": 422,
    "message": (
        "card 1 field email reads a made-up address: a stand-in is not a "
        "value. Use what the person said (`the_person_said`) or leave it blank."
    ),
}

OBLIGATION = {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}


def _future_iso(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (
        (datetime.now(timezone.utc) + timedelta(minutes=minutes))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _payload(*, message_ids=(), unread=0):
    return {
        "ok": True,
        "current_step": {
            "id": "s-1",
            "number": 3,
            "state": "agent_working",
            "outcome_filed_at": None,
        },
        "step_thread": {
            "messages": [{"id": mid, "who": "person"} for mid in message_ids],
            "unread_from_person": unread,
            "unanswered_elsewhere": [],
        },
        "latest_work_pulse": {
            "overdue": False,
            "progress_percent": 50,
            "next_due_at": _future_iso(25),
        },
        "released_materials_count": 0,
        "access": {"grants": []},
        "deal": {"id": "d1", "status": "signed", "target_goal_id": "t1"},
    }


def _run(goal, mode, status, result):
    return RunResult(
        run_id="run-x",
        status=status,
        result=result,
        checkpoint=Checkpoint(
            run_id="run-x",
            goal=goal,
            data={},
            event_cursor=0,
            revision=0,
            updated_at="2026-09-11T00:00:00Z",
        ),
        usage=ModelUsage(total_tokens=10),
        iterations=1,
        observed_mode=mode,
    )


class _MailClient:
    def configure_send_context(self, **_kwargs):
        pass

    def resume_pending_send(self):
        return None


class FakeBench:
    """A bench that refuses the filing the model makes inside the run."""

    def __init__(self, payload, refusal=STAND_IN):
        self.payload = payload
        self.refusal = refusal
        self.filings = []
        self.pulses = []

    # -- the doors the dispatch uses ---------------------------------------
    def ensure_reachable(self):
        return {"ok": True}

    def attention(self, wait):
        return {"attention": [dict(OBLIGATION)]}

    def list_proposals(self):
        return {"proposals": []}

    def current_step(self, deal_id):
        return self.payload

    def platform_owned_block(self, step_id):
        return None

    # -- the doors a step move knocks on -----------------------------------
    def file_outcome(self, target_id, outcome, key):
        self.filings.append((target_id, outcome, key))
        return dict(self.refusal)

    def post_check_in(self, deal_id, pulse, key):
        self.pulses.append((deal_id, pulse, key))
        return {"ok": True, "work_pulse": {"progress_percent": pulse.get("progress_percent")}}


def _resources(bench, calls):
    """The OLD ROAD: no `runtime.model`, so the small ask is not available and
    every cycle is the full agentic dispatch -- exactly the road that looped.

    The fake run does what the real one did: it files, the bench refuses, and
    the run ends FAILED on the runtime's protected-write limit, so no idle
    memo is written and nothing but the brake can stop the next cycle.
    """
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=_MailClient()),
        enabled_tools=[
            "state.load",
            "state.save",
            "result.complete",
            "result.fail",
            "toll_bench.current_step",
            "toll_bench.file_outcome",
        ],
    )

    def start(goal, mode):
        calls.append(goal)
        answer = bench.file_outcome("t1", {"note": "here it is"}, f"k-{len(calls)}")
        if answer.get("ok"):
            return _run(goal, mode, RunStatus.COMPLETED, {"summary": "Filed."})
        return _run(
            goal,
            mode,
            RunStatus.FAILED,
            {"reason": "Protected write attempt limit reached", "tool": "toll_bench.file_outcome"},
        )

    runtime.start = start
    return SimpleNamespace(toll_bench=bench, runtime=runtime, agent_identity=None)


def _clean():
    cli._IDLE_STEP_MEMO.clear()
    cli._STEP_REFUSALS.clear()
    cli._OBLIGATION_FAILURES.clear()


# ---------------------------------------------------------------------------
# 1. Three runs on one state, and then no more
# ---------------------------------------------------------------------------
def test_three_refusals_on_one_state_stop_the_old_road():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)

    for _ in range(6):
        cli._process_market_attention(resources, wait=20)

    assert len(calls) == 3
    assert len(bench.filings) == 3
    _clean()


def test_the_braked_step_reports_itself_and_asks_for_no_model_call():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    for _ in range(3):
        cli._process_market_attention(resources, wait=20)

    held = cli._process_market_attention(resources, wait=20)

    assert held["run"] is None
    assert held["braked_steps"] == 1
    assert len(calls) == 3
    _clean()


def test_the_brake_logs_one_clear_line(caplog):
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    with caplog.at_level(logging.WARNING, logger="toll_harness.cli"):
        for _ in range(3):
            cli._process_market_attention(resources, wait=20)

    lines = [
        record.getMessage()
        for record in caplog.records
        if "refused 3 times on one state" in record.getMessage()
    ]
    assert lines == [
        "step 3: refused 3 times on one state (code stand_in); waiting for the "
        "step to change"
    ]
    _clean()


# ---------------------------------------------------------------------------
# 2. The person sees why nothing is moving
# ---------------------------------------------------------------------------
def test_the_brake_posts_the_benchs_own_sentence_as_the_blocker():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    for _ in range(4):
        cli._process_market_attention(resources, wait=20)

    assert len(bench.pulses) == 1
    deal_id, pulse, _key = bench.pulses[0]
    assert deal_id == "d1"
    assert pulse["blocker"].startswith(
        "The bench refused this filing 3 times, the same way (stand_in)."
    )
    assert "a stand-in is not a value" in pulse["blocker"]
    assert len(pulse["blocker"]) <= 280
    # Never 100: a refused filing did not finish the step.
    assert pulse["progress_percent"] == 50
    _clean()


def test_the_blocker_carries_nothing_the_check_in_door_would_refuse():
    # A `stand_in` refusal QUOTES the stand-in, and the check-in door runs the
    # same stand-in check on `blocker`. Echoing it verbatim earns the same 422
    # in a new place, so addresses, bracket blanks and bare URLs come out.
    _clean()
    quoted = dict(
        STAND_IN,
        message=(
            "card 1 field email reads john.doe@example.com: a stand-in is not "
            "a value. See https://example.com/help or [Person Name]."
        ),
    )
    bench = FakeBench(_payload(), refusal=quoted)
    calls = []
    resources = _resources(bench, calls)
    for _ in range(4):
        cli._process_market_attention(resources, wait=20)

    blocker = bench.pulses[0][1]["blocker"]
    assert "@" not in blocker
    assert "http" not in blocker
    assert "[" not in blocker
    _clean()


def test_a_refused_blocker_falls_back_to_plain_words():
    _clean()
    bench = FakeBench(_payload())
    seen = []

    def picky(deal_id, pulse, key):
        seen.append(pulse["blocker"])
        if len(seen) == 1:
            return {"ok": False, "error": "stand_in"}
        bench.pulses.append((deal_id, pulse, key))
        return {"ok": True}

    bench.post_check_in = picky
    calls = []
    resources = _resources(bench, calls)
    for _ in range(4):
        cli._process_market_attention(resources, wait=20)

    assert len(seen) == 2
    assert seen[1] == cli._BRAKE_BLOCKER_PLAIN
    _clean()


# ---------------------------------------------------------------------------
# 3. It resets on change -- a road choice, not a strike
# ---------------------------------------------------------------------------
def test_a_change_on_the_step_gives_it_one_more_try():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    for _ in range(4):
        cli._process_market_attention(resources, wait=20)
    assert len(calls) == 3

    # The person writes: the fingerprint changes and the brake lets go.
    bench.payload = _payload(message_ids=("m-1",), unread=1)
    cli._process_market_attention(resources, wait=20)

    assert len(calls) == 4
    _clean()


def test_a_different_refusal_code_restarts_the_count():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    for _ in range(2):
        cli._process_market_attention(resources, wait=20)
    assert cli._STEP_REFUSALS["s-1"]["road"] == 2

    bench.refusal = {"ok": False, "error": "deliverable_missing", "message": "no file"}
    cli._process_market_attention(resources, wait=20)

    record = cli._STEP_REFUSALS["s-1"]
    assert record["code"] == "deliverable_missing"
    assert record["road"] == 1
    # And the brake does not hold yet: two more tries on the new code.
    cli._process_market_attention(resources, wait=20)
    assert len(calls) == 4
    _clean()


def test_a_filing_that_lands_clears_the_memo():
    # The model fixes it on try two: the filing lands, the journal is
    # forgotten, and nothing is braked.
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    cli._process_market_attention(resources, wait=20)
    assert cli._STEP_REFUSALS["s-1"]["road"] == 1

    bench.refusal = {"ok": True, "outcome_id": "o-1"}
    cli._process_market_attention(resources, wait=20)

    assert "s-1" not in cli._STEP_REFUSALS
    assert bench.pulses == []
    _clean()


def test_the_journal_is_forgotten_when_the_step_leaves_attention():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    cli._process_market_attention(resources, wait=20)
    assert "s-1" in cli._STEP_REFUSALS

    # An empty attention list returns before the filter, so prune through a
    # cycle that still carries one unrelated obligation.
    bench.attention = lambda wait: {
        "attention": [
            {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"}
        ]
    }
    cli._process_market_attention(resources, wait=20)

    assert "s-1" not in cli._STEP_REFUSALS
    _clean()


# ---------------------------------------------------------------------------
# 4. The bench's words ride the next try
# ---------------------------------------------------------------------------
def test_the_refusal_rides_the_next_dispatch():
    _clean()
    bench = FakeBench(_payload())
    calls = []
    resources = _resources(bench, calls)
    cli._process_market_attention(resources, wait=20)
    cli._process_market_attention(resources, wait=20)

    assert '"the_bench_refused":null' in calls[0]
    assert '"the_bench_refused"' in calls[1]
    assert "a stand-in is not a value" in calls[1]
    assert "`the_bench_refused`" in calls[1]
    _clean()
