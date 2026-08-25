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

        def proposal_count(self, _target_id):
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

        def proposal_count(self, target_id):
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
