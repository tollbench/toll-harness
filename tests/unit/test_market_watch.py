from argparse import Namespace
from types import SimpleNamespace

import pytest

from toll_harness import cli
from toll_harness.core.types import (
    AutonomyMode,
    Checkpoint,
    ModelUsage,
    RunResult,
    RunStatus,
)


class _Resources:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_market_watch_keeps_running_after_failed_cycle(monkeypatch):
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None: {
            "ok": False,
            "error": "invalid_plan",
        },
    )

    def stop_after_backoff(delay):
        assert delay == 30.0
        raise RuntimeError("stop test loop")

    monkeypatch.setattr(cli.time, "sleep", stop_after_backoff)

    with pytest.raises(RuntimeError, match="stop test loop"):
        cli.command_market_watch(
            Namespace(config="agent.yaml", wait=20, interval=2.0, once=False)
        )

    assert resources.closed is True


def test_market_watch_once_returns_failure(monkeypatch):
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None: {
            "ok": False,
            "error": "invalid_plan",
        },
    )

    result = cli.command_market_watch(
        Namespace(config="agent.yaml", wait=20, interval=2.0, once=True)
    )

    assert result == 2
    assert resources.closed is True


def test_market_watch_once_scans_when_attention_is_idle(monkeypatch):
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None: {
            "ok": True,
            "attention_count": 0,
            "reachability": {"ok": True},
        },
    )
    scans = []

    def scan(_resources, reachability, previous_failure=None):
        scans.append((reachability, previous_failure))
        return {"ok": True, "market_scan": True, "candidate_count": 3}

    monkeypatch.setattr(cli, "_process_market_opportunities", scan)

    result = cli.command_market_watch(
        Namespace(
            config="agent.yaml",
            wait=20,
            interval=2.0,
            scan_interval=300.0,
            no_bid=False,
            once=True,
        )
    )

    assert result == 0
    assert scans == [({"ok": True}, None)]
    assert resources.closed is True


def test_paid_finalist_waits_for_payout_without_invoking_model():
    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {
                    "kind": "file_informed_plan",
                    "proposal_id": "proposal-paid",
                    "target_id": "target-1",
                }
            ]
        },
        list_proposals=lambda: {
            "proposals": [{"id": "proposal-paid", "total_ask_cents": 500}]
        },
        status=lambda: {
            "payout": {
                "ready": False,
                "onboarding_needed": True,
                "onboarding_link_call": "POST /api/bench/me/payout-account/onboarding-link",
            }
        },
    )
    runtime = SimpleNamespace(
        start=lambda *_args: (_ for _ in ()).throw(AssertionError("must not invoke model"))
    )
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is False
    assert result["error"] == "payout_not_ready"
    assert result["proposal_ids"] == ["proposal-paid"]
    assert result["retry_after_seconds"] == 300.0
    assert result["run"] is None


def test_unanswered_step_message_runs_before_pending_email_resume():
    resumed = []

    class MailClient:
        def configure_send_context(self, **_kwargs):
            pass

        def resume_pending_send(self):
            resumed.append(True)
            return {"status": "pending_human_approval"}

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {
                    "kind": "unanswered_message",
                    "deal_id": "deal-1",
                    "step_id": "step-1",
                }
            ]
        },
    )
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()),
        enabled_tools=["toll_bench.reply_step_message", "result.complete"],
    )

    def start(goal, mode):
        assert "toll_bench.reply_step_message" in goal
        return RunResult(
            run_id="run-1",
            status=RunStatus.COMPLETED,
            result={"summary": "Replied."},
            checkpoint=Checkpoint(
                run_id="run-1",
                goal=goal,
                data={},
                event_cursor=0,
                revision=0,
                updated_at="2026-08-24T00:00:00Z",
            ),
            usage=ModelUsage(total_tokens=10),
            iterations=1,
            observed_mode=mode,
        )

    runtime.start = start
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert resumed == []


def test_pending_email_on_other_step_defers_instead_of_crashing():
    # A pending email approval parked on one step must not crash the whole watch
    # iteration when an obligation arrives on a different deal step.
    class MailClient:
        def configure_send_context(self, **_kwargs):
            raise RuntimeError("A different deal step has an unresolved pending email send")

        def resume_pending_send(self):
            return {"status": "pending_human_approval"}

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {
                    "kind": "deal_step",
                    "deal_id": "d1",
                    "proposal_id": "p1",
                    "step_id": "step-B",
                }
            ]
        },
    )
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()),
        enabled_tools=[],
        start=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not invoke model")
        ),
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench, runtime=runtime, agent_identity=None
    )

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    # The blocked deal step is deferred; the parked send is surfaced for approval
    # and no model run is triggered.
    assert result.get("email", {}).get("status") == "pending_human_approval"


def test_idle_market_scan_exposes_only_bidding_tools_and_one_bounded_set():
    observed = {}

    class TollBench:
        fleet = None
        fleet_proposal_limit = 4

        def __init__(self):
            self.submit_calls = 0

        def list_targets(self):
            return {
                "targets": [
                    {
                        "target_id": f"target-{index}",
                        "want": f"Want {index}",
                        "posted_at": f"2026-08-24T00:{index:02d}:00Z",
                        "your_bid": None,
                    }
                    for index in range(8)
                ]
            }

        def submit_proposal(self, *_args, **_kwargs):
            self.submit_calls += 1
            return {"ok": True, "proposal_id": f"proposal-{self.submit_calls}"}

    runtime = SimpleNamespace(enabled_tools=["email.send", "toll_bench.attention"])
    toll_bench = TollBench()

    def start(goal, mode):
        observed["goal"] = goal
        observed["mode"] = mode
        observed["tools"] = list(runtime.enabled_tools)
        observed["first_submit"] = toll_bench.submit_proposal("target-1", {}, "key-1")
        observed["second_submit"] = toll_bench.submit_proposal("target-2", {}, "key-2")
        return RunResult(
            run_id="run-1",
            status=RunStatus.COMPLETED,
            result={"summary": "No proposal filed."},
            checkpoint=Checkpoint(
                run_id="run-1",
                goal=goal,
                data={},
                event_cursor=0,
                revision=0,
                updated_at="2026-08-24T00:00:00Z",
            ),
            usage=ModelUsage(total_tokens=10),
            iterations=1,
            observed_mode=AutonomyMode.AUTONOMOUS,
        )

    runtime.start = start
    resources = SimpleNamespace(
        toll_bench=toll_bench,
        agent_identity=SimpleNamespace(
            id="00000002-0000-0000-0000-000000000000",
            autonomy_mode=AutonomyMode.AUTONOMOUS,
        ),
        runtime=runtime,
    )

    result = cli._process_market_opportunities(resources, {"ok": True})

    assert result["ok"] is True
    assert result["open_target_count"] == 8
    assert result["candidate_count"] == cli.MARKET_SCAN_CANDIDATE_LIMIT
    assert observed["mode"] is AutonomyMode.AUTONOMOUS
    assert observed["tools"] == cli.MARKET_SCAN_TOOLS
    assert observed["first_submit"]["proposal_id"] == "proposal-1"
    assert observed["second_submit"]["error"] == "market_scan_proposal_limit"
    assert toll_bench.submit_calls == 1
    assert result["proposal_filed"] is True
    assert observed["goal"].count('"target_id"') == cli.MARKET_SCAN_CANDIDATE_LIMIT
    assert "making and submitting one concrete" in observed["goal"]
    assert "valid to submit no proposal" not in observed["goal"]
    assert runtime.enabled_tools == ["email.send", "toll_bench.attention"]


def test_market_scan_does_not_retire_target_without_a_filed_proposal():
    reviewed = []

    class Fleet:
        def reviewed_target_keys(self, _agent_id):
            return set()

        def proposal_count(self, _target_id, _target_round=None):
            return 0

        def mark_targets_reviewed(self, **kwargs):
            reviewed.append(kwargs)

    class TollBench:
        fleet = Fleet()
        fleet_proposal_limit = 4

        def list_targets(self):
            return {
                "targets": [
                    {
                        "target_id": "target-1",
                        "want": "Want 1",
                        "posted_at": "2026-08-24T00:00:00Z",
                        "your_bid": None,
                    }
                ]
            }

        def submit_proposal(self, *_args, **_kwargs):
            raise AssertionError("the model did not submit")

    runtime = SimpleNamespace(enabled_tools=[])

    def start(goal, _mode):
        return RunResult(
            run_id="run-1",
            status=RunStatus.COMPLETED,
            result={"summary": "Reviewed only."},
            checkpoint=Checkpoint(
                run_id="run-1",
                goal=goal,
                data={},
                event_cursor=0,
                revision=0,
                updated_at="2026-08-24T00:00:00Z",
            ),
            usage=ModelUsage(total_tokens=10),
            iterations=1,
            observed_mode=AutonomyMode.AUTONOMOUS,
        )

    runtime.start = start
    resources = SimpleNamespace(
        toll_bench=TollBench(),
        agent_identity=SimpleNamespace(
            id="00000002-0000-0000-0000-000000000000",
            autonomy_mode=AutonomyMode.AUTONOMOUS,
        ),
        runtime=runtime,
    )

    result = cli._process_market_opportunities(resources, {"ok": True})

    assert result["proposal_filed"] is False
    assert reviewed == []


def test_market_scan_selects_newest_target_that_has_not_reached_fleet_cap():
    class Fleet:
        def reviewed_target_keys(self, _agent_id):
            return set()

        def proposal_count(self, target_id, _target_round=None):
            return {"newest-full": 4, "next-underfilled": 3, "oldest": 0}[target_id]

    toll_bench = SimpleNamespace(
        fleet=Fleet(),
        fleet_proposal_limit=4,
        list_targets=lambda: {
            "targets": [
                {
                    "target_id": "oldest",
                    "want": "Old want",
                    "posted_at": "2026-08-24T00:00:00Z",
                    "your_bid": None,
                },
                {
                    "target_id": "next-underfilled",
                    "want": "Next want",
                    "posted_at": "2026-08-24T01:00:00Z",
                    "your_bid": None,
                },
                {
                    "target_id": "newest-full",
                    "want": "Newest want",
                    "posted_at": "2026-08-24T02:00:00Z",
                    "your_bid": None,
                },
            ]
        },
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench,
        agent_identity=SimpleNamespace(id="00000002-0000-0000-0000-000000000000"),
    )

    target_count, candidates, review_targets = cli._market_scan_candidates(resources)

    assert target_count == 3
    assert [candidate["target_id"] for candidate in candidates] == ["next-underfilled"]
    assert review_targets == [("next-underfilled:round:1", "next-underfilled", None)]


def test_market_scan_open_bid_limit_is_opt_in_and_skips_crowded_targets():
    # fleet.open_bid_limit is an optional config knob, default OFF. When set,
    # the scan skips targets whose brief reports at least that many live bids
    # (server open_bid_count); an absent field never skips.
    class Fleet:
        def reviewed_target_keys(self, _agent_id):
            return set()

        def proposal_count(self, _target_id, _target_round=None):
            return 0

    toll_bench = SimpleNamespace(
        fleet=Fleet(),
        fleet_proposal_limit=4,
        open_bid_limit=4,
        list_targets=lambda: {
            "targets": [
                {
                    "target_id": "crowded",
                    "want": "Crowded want",
                    "posted_at": "2026-08-24T02:00:00Z",
                    "your_bid": None,
                    "open_bid_count": 4,
                },
                {
                    "target_id": "roomy",
                    "want": "Roomy want",
                    "posted_at": "2026-08-24T01:00:00Z",
                    "your_bid": None,
                    "open_bid_count": 3,
                },
                {
                    "target_id": "count-unknown",
                    "want": "Older server want",
                    "posted_at": "2026-08-24T00:00:00Z",
                    "your_bid": None,
                },
            ]
        },
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench,
        agent_identity=SimpleNamespace(id="00000002-0000-0000-0000-000000000000"),
    )

    _target_count, candidates, _review_targets = cli._market_scan_candidates(resources)

    selected = [candidate["target_id"] for candidate in candidates]
    assert "crowded" not in selected
    assert selected[0] == "roomy"
    assert candidates[0]["open_bid_count"] == 3

    # Default OFF: without the knob the crowded target is selected again.
    toll_bench.open_bid_limit = None
    _n, candidates_off, _r = cli._market_scan_candidates(resources)
    assert [c["target_id"] for c in candidates_off][0] == "crowded"


def test_market_scan_treats_a_repost_as_fresh_work():
    # Found live 2026-08-26: a repost keeps the want's original posted_at, so
    # the Peter Diamandis repost sorted behind two weeks of newer wants and
    # reached zero workers. Freshness must follow reposted_at, and the round-2
    # key must not be hidden by the round-1 review.
    class Fleet:
        def __init__(self):
            self.counts = []

        def reviewed_target_keys(self, _agent_id):
            return {"reposted:round:1"}  # round 1 was reviewed before it died

        def proposal_count(self, target_id, target_round=None):
            self.counts.append((target_id, target_round))
            return 0

    fleet = Fleet()
    toll_bench = SimpleNamespace(
        fleet=fleet,
        fleet_proposal_limit=4,
        list_targets=lambda: {
            "targets": [
                {
                    "target_id": "reposted",
                    "want": "Old want, back on the bench",
                    "posted_at": "2026-08-14T00:00:00Z",
                    "reposted_at": "2026-08-26T12:00:00Z",
                    "round": 2,
                    "your_bid": None,
                },
                {
                    "target_id": "newer-first-post",
                    "want": "Newer want",
                    "posted_at": "2026-08-20T00:00:00Z",
                    "your_bid": None,
                },
            ]
        },
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench,
        agent_identity=SimpleNamespace(id="00000002-0000-0000-0000-000000000000"),
    )

    _count, candidates, review_targets = cli._market_scan_candidates(resources)

    assert [candidate["target_id"] for candidate in candidates] == ["reposted"]
    assert candidates[0]["reposted_at"] == "2026-08-26T12:00:00Z"
    assert review_targets == [("reposted:round:2", "reposted", "2")]
    # The fleet cap was checked against the CURRENT round, not the dead one.
    assert ("reposted", "2") in fleet.counts


def test_market_scan_advances_past_a_round_recorded_as_reviewed():
    # A closed newest want, once its round is recorded as reviewed (e.g. after
    # a terminal 409), must stop blocking every older want in the scan.
    toll_bench = SimpleNamespace(
        fleet=SimpleNamespace(
            reviewed_target_keys=lambda _agent_id: {"closed-newest:round:1"},
            proposal_count=lambda _target_id, _target_round=None: 0,
        ),
        fleet_proposal_limit=4,
        list_targets=lambda: {
            "targets": [
                {
                    "target_id": "closed-newest",
                    "want": "Closed want",
                    "posted_at": "2026-08-26T00:00:00Z",
                    "your_bid": None,
                },
                {
                    "target_id": "older-open",
                    "want": "Older open want",
                    "posted_at": "2026-08-18T00:00:00Z",
                    "your_bid": None,
                },
            ]
        },
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench,
        agent_identity=SimpleNamespace(id="00000002-0000-0000-0000-000000000000"),
    )

    _count, candidates, _review_targets = cli._market_scan_candidates(resources)

    assert [candidate["target_id"] for candidate in candidates] == ["older-open"]


def _completed_run(goal, mode):
    return RunResult(
        run_id="run-x",
        status=RunStatus.COMPLETED,
        result={"summary": "Handled."},
        checkpoint=Checkpoint(
            run_id="run-x",
            goal=goal,
            data={},
            event_cursor=0,
            revision=0,
            updated_at="2026-08-24T00:00:00Z",
        ),
        usage=ModelUsage(total_tokens=10),
        iterations=1,
        observed_mode=mode,
    )


def test_per_obligation_dispatch_handles_one_kind_with_narrowed_tools():
    # Three obligations of different kinds arrive at once. The worker must hand
    # the model ONLY the highest-priority one (deal_step) with only that kind's
    # focused instruction and tool set -- not a combined wall of instructions
    # for every kind, and not the file_informed_plan / unanswered_message tools.
    class MailClient:
        def configure_send_context(self, **_kwargs):
            pass

        def resume_pending_send(self):  # pragma: no cover - deal step present
            raise AssertionError("must not resume while a deal step is pending")

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"},
                {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"},
                {"kind": "unanswered_message", "deal_id": "d2", "step_id": "s-2"},
            ]
        },
        # p-free is a free finalist (0 cents) so the payout gate does not trip.
        list_proposals=lambda: {"proposals": [{"id": "p-free", "total_ask_cents": 0}]},
    )

    observed = {}

    original_tools = [
        "state.load",
        "state.save",
        "result.complete",
        "result.fail",
        "toll_bench.current_step",
        "toll_bench.file_outcome",
        "toll_bench.post_check_in",
        "toll_bench.reply_step_message",
        "toll_bench.read_finalist_answers",
        "toll_bench.submit_informed_plan",
        "toll_bench.list_proposals",
        "email.send",
        "email.reply",
    ]
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()),
        enabled_tools=list(original_tools),
    )

    def start(goal, mode):
        observed["goal"] = goal
        observed["tools"] = list(runtime.enabled_tools)
        return _completed_run(goal, mode)

    runtime.start = start
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    # Only the deal step was dispatched: its id is in the goal, the other two are not.
    assert '"s-1"' in observed["goal"]
    assert '"p-free"' not in observed["goal"]
    assert '"s-2"' not in observed["goal"]
    # The goal carries a single "obligation", not the old combined "attention" list.
    assert '"obligation":' in observed["goal"]
    assert '"attention":' not in observed["goal"]
    # Tools were narrowed to the deal_step set: the finalist-plan-only tool is gone.
    assert "toll_bench.submit_informed_plan" not in observed["tools"]
    assert "toll_bench.current_step" in observed["tools"]
    assert "toll_bench.file_outcome" in observed["tools"]
    # enabled_tools restored after the run.
    assert runtime.enabled_tools == original_tools


def test_dispatch_prefetches_step_history_and_meters_cost():
    # H6: a step-scoped obligation rides with the current step prefetched into
    # the goal payload. H8: the cycle result meters what the dispatch spent.
    class MailClient:
        def configure_send_context(self, **_kwargs):
            pass

        def resume_pending_send(self):
            return None

    fetched = {}

    def current_step(deal_id):
        fetched["deal_id"] = deal_id
        return {"ok": True, "current_step": {"id": "s-1", "note": "prior-check-in"}}

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}
            ]
        },
        list_proposals=lambda: {"proposals": []},
        current_step=current_step,
    )
    observed = {}
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()),
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
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime.start = start
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert fetched["deal_id"] == "d1"
    assert '"current_step":' in observed["goal"]
    assert "prior-check-in" in observed["goal"]
    meter = result["dispatch"]
    assert meter["kind"] == "deal_step"
    assert meter["goal_words"] == len(observed["goal"].split())
    assert meter["goal_chars"] == len(observed["goal"])
    assert meter["tool_count"] == 6
    assert meter["word_budget"] == cli._DISPATCH_WORD_BUDGET


def test_dispatch_survives_step_prefetch_failure():
    # The prefetch is best-effort: if the bench call dies, the field is null,
    # the model is told to fetch the step itself, and the cycle must not fail.
    class MailClient:
        def configure_send_context(self, **_kwargs):
            pass

        def resume_pending_send(self):
            return None

    def current_step(deal_id):
        raise RuntimeError("bench briefly down")

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}
            ]
        },
        list_proposals=lambda: {"proposals": []},
        current_step=current_step,
    )
    observed = {}
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()),
        enabled_tools=["state.load", "state.save", "result.complete", "result.fail"],
    )

    def start(goal, mode):
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime.start = start
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert '"current_step":null' in observed["goal"]


def _future_iso(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (
        (datetime.now(timezone.utc) + timedelta(minutes=minutes))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _idle_step_payload(
    *, next_due_iso=None, unread=0, message_ids=(), overdue=False
):
    return {
        "ok": True,
        "current_step": {"id": "s-1", "state": "agent_working", "outcome_filed_at": None},
        "step_thread": {
            "messages": [{"id": mid} for mid in message_ids],
            "unread_from_person": unread,
            "unanswered_elsewhere": [],
        },
        "latest_work_pulse": {
            "overdue": overdue,
            "next_due_at": next_due_iso or _future_iso(25),
        },
        "released_materials_count": 0,
        "access": {"grants": []},
        "deal": {"id": "d1", "status": "signed"},
    }


class _IdleMailClient:
    def configure_send_context(self, **_kwargs):
        pass

    def resume_pending_send(self):
        return None


def _idle_resources(attention_items, current_step_payload, observed, fetched=None):
    def current_step(deal_id):
        if fetched is not None:
            fetched.append(deal_id)
        return current_step_payload

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {"attention": attention_items},
        list_proposals=lambda: {"proposals": [{"id": "p-free", "total_ask_cents": 0}]},
        current_step=current_step,
    )
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=_IdleMailClient()),
        enabled_tools=[
            "state.load",
            "state.save",
            "result.complete",
            "result.fail",
            "toll_bench.current_step",
            "toll_bench.file_outcome",
            "toll_bench.read_finalist_answers",
            "toll_bench.submit_informed_plan",
        ],
    )

    def start(goal, mode):
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime.start = start
    return SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)


def test_idle_deal_step_is_skipped_and_the_plan_request_gets_the_cycle():
    # A deal step the model already inspected in exactly this state, with no
    # pulse due and nothing new from the person, must NOT be re-dispatched --
    # and the finalist plan request behind it must get the cycle instead of
    # starving (a live plan request once sat ~55 minutes behind one).
    cli._IDLE_STEP_MEMO.clear()
    payload = _idle_step_payload()
    cli._IDLE_STEP_MEMO["s-1"] = cli._deal_step_fingerprint(payload)
    observed = {}
    resources = _idle_resources(
        [
            {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"},
            {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"},
        ],
        payload,
        observed,
    )

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert '"p-free"' in observed["goal"]
    assert '"kind":"file_informed_plan"' in observed["goal"]
    assert '"s-1"' not in observed["goal"]
    cli._IDLE_STEP_MEMO.clear()


def test_idle_deal_step_is_redispatched_when_its_pulse_comes_due():
    # The r100 pulse cadence still gets its one run per window: an otherwise
    # idle step with a due (or overdue) pulse is dispatched, not skipped.
    cli._IDLE_STEP_MEMO.clear()
    payload = _idle_step_payload(overdue=True)
    cli._IDLE_STEP_MEMO["s-1"] = cli._deal_step_fingerprint(payload)
    observed = {}
    resources = _idle_resources(
        [
            {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"},
            {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"},
        ],
        payload,
        observed,
    )

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert '"s-1"' in observed["goal"]
    assert '"kind":"deal_step"' in observed["goal"]
    cli._IDLE_STEP_MEMO.clear()


def test_idle_deal_step_wakes_when_the_person_writes():
    # Any change the person can cause -- a new message, an unread count --
    # breaks the fingerprint match and the step is dispatched immediately.
    cli._IDLE_STEP_MEMO.clear()
    quiet = _idle_step_payload()
    cli._IDLE_STEP_MEMO["s-1"] = cli._deal_step_fingerprint(quiet)
    spoken = _idle_step_payload(unread=1, message_ids=("m-1",))
    observed = {}
    resources = _idle_resources(
        [{"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}],
        spoken,
        observed,
    )

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert '"s-1"' in observed["goal"]
    cli._IDLE_STEP_MEMO.clear()


def test_noop_deal_step_run_records_the_idle_memo():
    # After a dispatched deal-step run completes, the pre-run fingerprint is
    # remembered so the next identical fetch can be skipped. A step that later
    # leaves the attention feed is forgotten.
    cli._IDLE_STEP_MEMO.clear()
    payload = _idle_step_payload()
    observed = {}
    fetched = []
    resources = _idle_resources(
        [{"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}],
        payload,
        observed,
        fetched=fetched,
    )

    first = cli._process_market_attention(resources, wait=20)

    assert first["ok"] is True
    assert cli._IDLE_STEP_MEMO.get("s-1") == cli._deal_step_fingerprint(payload)

    # Same state again: the step is now skipped without a model run.
    observed.pop("goal", None)
    second = cli._process_market_attention(resources, wait=20)
    assert second["ok"] is True
    assert second["run"] is None
    assert "goal" not in observed

    # The step leaves attention: its memo entry is pruned.
    resources.toll_bench.attention = lambda wait: {"attention": []}
    cli._process_market_attention(resources, wait=20)
    # An empty attention list returns before the filter; prune via a cycle
    # that still carries one unrelated obligation.
    resources.toll_bench.attention = lambda wait: {
        "attention": [
            {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"}
        ]
    }
    cli._process_market_attention(resources, wait=20)
    assert "s-1" not in cli._IDLE_STEP_MEMO
    cli._IDLE_STEP_MEMO.clear()


def test_pulse_due_resolves_doubt_toward_dispatch():
    # Missing, overdue, or unreadable pulse schedules all read as "due".
    assert cli._deal_step_pulse_due({"latest_work_pulse": None}) is True
    assert cli._deal_step_pulse_due({}) is True
    assert (
        cli._deal_step_pulse_due(
            {"latest_work_pulse": {"overdue": True, "next_due_at": _future_iso(25)}}
        )
        is True
    )
    assert (
        cli._deal_step_pulse_due(
            {"latest_work_pulse": {"overdue": False, "next_due_at": "not-a-date"}}
        )
        is True
    )
    assert (
        cli._deal_step_pulse_due(
            {"latest_work_pulse": {"overdue": False, "next_due_at": _future_iso(25)}}
        )
        is False
    )


def test_watch_scans_even_when_obligation_is_blocked_on_a_human(monkeypatch):
    # Steven's ruling: agents look for new work ALWAYS, debt or not. A cycle
    # parked on payout onboarding must still run the board scan.
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None: {
            "ok": False,
            "error": "payout_not_ready",
            "reachability": {"ok": True},
            "attention_count": 1,
            "retry_after_seconds": 300.0,
            "run": None,
        },
    )
    scans = []

    def scan(_resources, reachability, previous_failure=None):
        scans.append((reachability, previous_failure))
        return {"ok": True, "market_scan": True, "candidate_count": 2}

    monkeypatch.setattr(cli, "_process_market_opportunities", scan)

    result = cli.command_market_watch(
        Namespace(
            config="agent.yaml",
            wait=20,
            interval=2.0,
            scan_interval=300.0,
            no_bid=False,
            once=True,
        )
    )

    # The obligation is still the cycle's verdict (blocked -> exit 2), but the
    # scan ran anyway.
    assert result == 2
    assert scans == [({"ok": True}, None)]
    assert resources.closed is True


def test_watch_scans_alongside_obligation_work_when_timer_is_due(monkeypatch):
    # Debt or not: a cycle that just did obligation work still scans when the
    # cadence timer says so.
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None: {
            "ok": True,
            "reachability": {"ok": True},
            "attention_count": 1,
            "run": {"status": "completed"},
        },
    )
    scans = []

    def scan(_resources, reachability, previous_failure=None):
        scans.append(reachability)
        return {"ok": True, "market_scan": True, "candidate_count": 1}

    monkeypatch.setattr(cli, "_process_market_opportunities", scan)

    result = cli.command_market_watch(
        Namespace(
            config="agent.yaml",
            wait=20,
            interval=2.0,
            scan_interval=300.0,
            no_bid=False,
            once=True,
        )
    )

    assert result == 0
    assert scans == [{"ok": True}]
    assert resources.closed is True
