from argparse import Namespace
from types import SimpleNamespace

import pytest

from tests.unit.plan_door import a_row
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


class _Stop(Exception):
    pass


def _scan_resources(enabled_tools, *, model=None):
    calls = []
    toll_bench = SimpleNamespace(
        list_targets=lambda: calls.append("list_targets")
        or {"targets": [{"target_id": "target-1", "want": "a want", "round": 1}]},
        submit_proposal=lambda *a, **k: calls.append("submit_proposal")
        or {"ok": True, "proposal_id": "proposal-1"},
        validate_proposal=lambda *a, **k: calls.append("validate_proposal") or {"ok": True},
        read_brief=lambda _target_id: {"brief": {}},
        list_act_kinds=lambda: {},
    )
    runtime = SimpleNamespace(enabled_tools=list(enabled_tools), model=model)
    resources = SimpleNamespace(
        toll_bench=toll_bench, runtime=runtime, agent_identity=None, store=None
    )
    return resources, calls


class _NoDraftLoop:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("the draft loop must not run")


def test_scan_files_nothing_when_submit_proposal_is_not_enabled(monkeypatch):
    monkeypatch.setattr(cli, "DraftLoop", _NoDraftLoop)
    resources, calls = _scan_resources(["state.save", "toll_bench.read_brief"], model=object())

    result = cli._process_market_opportunities(resources, {"ok": True})

    assert result["proposal_tool_disabled"] is True
    assert result["proposal_filed"] is False
    assert calls == []


def test_scan_draft_road_still_files_when_submit_proposal_is_enabled(monkeypatch):
    seen = {}

    class _DraftLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, target_id, **kwargs):
            seen["target_id"], seen["file"] = target_id, kwargs.get("file")
            return {"ok": True, "filed": True}

    monkeypatch.setattr(cli, "DraftLoop", _DraftLoop)
    resources, _calls = _scan_resources(["toll_bench.submit_proposal"], model=object())

    result = cli._process_market_opportunities(resources, {"ok": True})

    assert result["proposal_filed"] is True
    assert seen == {"target_id": "target-1", "file": True}


@pytest.mark.parametrize(
    ("configured", "dry_run", "expect_submit"),
    [
        (["state.save", "result.complete", "toll_bench.read_brief"], True, False),
        (["state.save", "result.complete", "toll_bench.submit_proposal"], False, True),
    ],
)
def test_scan_old_road_never_widens_the_configured_tools(configured, dry_run, expect_submit):
    resources, calls = _scan_resources(configured)
    original_submit = resources.toll_bench.submit_proposal
    seen = {}

    def start(_goal, _mode):
        seen["tools"] = list(resources.runtime.enabled_tools)
        raise _Stop

    resources.runtime.start = start

    with pytest.raises(_Stop):
        cli._process_market_opportunities(resources, {"ok": True}, dry_run=dry_run)

    assert set(seen["tools"]) <= set(configured)
    assert ("toll_bench.submit_proposal" in seen["tools"]) is expect_submit
    assert resources.runtime.enabled_tools == configured
    assert resources.toll_bench.submit_proposal is original_submit
    assert "submit_proposal" not in calls


def test_plan_draft_loop_does_not_run_without_submit_informed_plan(monkeypatch):
    monkeypatch.setattr(cli, "DraftLoop", _NoDraftLoop)
    resources = SimpleNamespace(
        runtime=SimpleNamespace(enabled_tools=["state.save"], model=object()),
        toll_bench=SimpleNamespace(),
    )
    obligation = {"kind": "file_informed_plan", "target_id": "t-1", "proposal_id": "p-1"}

    planned = cli._file_the_informed_plan_from_draft(resources, obligation, {"ok": True}, 1, 5)

    assert planned is None


def _step_ask_resources(enabled_tools):
    calls = []
    model_calls = []

    def invoke(**_kwargs):
        model_calls.append(1)
        return SimpleNamespace(
            tool_calls=[SimpleNamespace(name="reply_step_message", arguments={"reply": "hello"})],
            text="",
            usage=None,
        )

    toll_bench = SimpleNamespace(
        reply_step_message=lambda *args: calls.append(("reply_step_message", args))
        or {"ok": True},
        file_outcome=lambda *args: calls.append(("file_outcome", args)) or {"ok": True},
        list_act_kinds=lambda: {},
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench,
        runtime=SimpleNamespace(
            enabled_tools=list(enabled_tools), model=SimpleNamespace(invoke=invoke)
        ),
        store=None,
    )
    return resources, calls, model_calls


_UNREAD_STEP = {
    "current_step": {"id": "step-1", "number": 2, "state": "agent_working"},
    "step_thread": {"unread_from_person": 1, "messages": [{"id": "m-1", "who": "person"}]},
    "submission": {"actions": [{"action": "post_step_message", "schema": {"type": "object"}}]},
    "deal": {"id": "deal-1"},
}


def test_step_ask_makes_no_disabled_call_and_takes_the_old_road(monkeypatch):
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})
    monkeypatch.setattr(cli, "_STEP_REFUSALS", {})
    resources, calls, model_calls = _step_ask_resources(["toll_bench.current_step"])
    obligation = {"kind": "unanswered_message", "deal_id": "deal-1", "step_id": "step-1"}

    asked = cli._the_step_ask(resources, obligation, _UNREAD_STEP, {"ok": True}, 1, 5)

    assert asked is None
    assert calls == []
    assert model_calls == []


def test_step_ask_still_answers_when_the_reply_tool_is_enabled(monkeypatch):
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})
    monkeypatch.setattr(cli, "_STEP_REFUSALS", {})
    resources, calls, _model_calls = _step_ask_resources(["toll_bench.reply_step_message"])
    obligation = {"kind": "unanswered_message", "deal_id": "deal-1", "step_id": "step-1"}

    asked = cli._the_step_ask(resources, obligation, _UNREAD_STEP, {"ok": True}, 1, 5)

    assert asked["ok"] is True
    assert [name for name, _args in calls] == ["reply_step_message"]


def test_outcome_form_needs_both_outcome_and_check_in_tools():
    resources = SimpleNamespace(
        runtime=SimpleNamespace(enabled_tools=["toll_bench.file_outcome"])
    )
    state = {
        "submission": {
            "actions": [{"action": "submit_step_outcome"}, {"action": "post_work_pulse"}],
            "worker_status": {"schema": {}},
        }
    }

    narrowed = cli._permitted_step_state(resources, state)

    assert narrowed["submission"]["actions"] == []
    assert "worker_status" not in narrowed["submission"]
    assert len(state["submission"]["actions"]) == 2


@pytest.mark.parametrize(("enabled", "posted"), [([], False), (["toll_bench.post_check_in"], True)])
def test_brake_blocker_obeys_post_check_in(enabled, posted):
    posts = []
    resources = SimpleNamespace(
        runtime=SimpleNamespace(enabled_tools=enabled),
        toll_bench=SimpleNamespace(post_check_in=lambda *args: posts.append(args) or {"ok": True}),
    )
    record = {"code": "stand_in", "road": 3, "sentence": "Refused."}

    cli._post_the_blocker(resources, {"deal_id": "deal-1", "step_id": "step-1"}, {}, record)

    assert bool(posts) is posted
    assert record["blocker_posted"] is posted


def test_market_watch_keeps_running_after_failed_cycle(monkeypatch):
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None, **_options: {
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
        lambda _resources, _wait, previous_failure=None, **_options: {
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
        lambda _resources, _wait, previous_failure=None, **_options: {
            "ok": True,
            "attention_count": 0,
            "reachability": {"ok": True},
        },
    )
    scans = []

    def scan(_resources, reachability, previous_failure=None, **_kwargs):
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


_BROAD_PATHS = (
    "_process_wakes",
    "_process_market_attention",
    "_report_worker_status",
    "_new_inbound_email_marker",
    "_earliest_wake_at",
)


def _forbid_broad_paths(monkeypatch):
    entered = []

    def spy(name):
        def forbidden(*_args, **_kwargs):
            entered.append(name)
            raise AssertionError(f"{name} must not run in proposal-only mode")

        return forbidden

    for name in _BROAD_PATHS:
        monkeypatch.setattr(cli, name, spy(name))
    return entered


def _proposal_only_resources(status=None, enabled_tools=("toll_bench.submit_proposal",)):
    calls = []

    def forbidden(name):
        def call(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called in proposal-only mode")

        return call

    def read_status():
        calls.append("status")
        if isinstance(status, Exception):
            raise status
        return {"ok": True} if status is None else status

    toll_bench = SimpleNamespace(
        status=read_status,
        ensure_reachable=forbidden("ensure_reachable"),
        attention=forbidden("attention"),
        acknowledge_ping=forbidden("acknowledge_ping"),
        report_worker_status=forbidden("report_worker_status"),
        current_step=forbidden("current_step"),
        list_targets=lambda: calls.append("list_targets") or {"targets": []},
        submit_proposal=forbidden("submit_proposal"),
    )
    email_provider = SimpleNamespace(
        list=forbidden("email.list"),
        client=SimpleNamespace(resume_pending_send=forbidden("resume_pending_send")),
    )
    resources = _Resources()
    resources.toll_bench = toll_bench
    resources.runtime = SimpleNamespace(
        enabled_tools=list(enabled_tools),
        email_provider=email_provider,
        resume=forbidden("runtime.resume"),
        start=forbidden("runtime.start"),
        model=None,
    )
    resources.agent_identity = None
    resources.store = None
    return resources, calls


def _proposal_only_arguments(**overrides):
    values = dict(
        config="agent.yaml",
        wait=20,
        interval=2.0,
        scan_interval=300.0,
        no_bid=False,
        dry_run=False,
        once=True,
        proposals_only=True,
    )
    values.update(overrides)
    return Namespace(**values)


def test_default_watch_still_wakes_and_processes_attention_before_scanning(monkeypatch):
    resources = _Resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    order = []
    monkeypatch.setattr(cli, "_process_wakes", lambda _resources: order.append("wakes") or [])
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None, **_options: order.append("attention")
        or {"ok": True, "attention_count": 0, "reachability": {"ok": True}, "run": None},
    )
    monkeypatch.setattr(
        cli,
        "_process_market_opportunities",
        lambda *_args, **_kwargs: order.append("scan") or {"ok": True, "market_scan": True},
    )

    result = cli.command_market_watch(_proposal_only_arguments(proposals_only=False))

    assert result == 0
    assert order == ["wakes", "attention", "scan"]


def test_proposals_only_scans_without_any_broad_path(monkeypatch):
    entered = _forbid_broad_paths(monkeypatch)
    resources, calls = _proposal_only_resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    scans = []

    def scan(_resources, reachability, previous_failure=None, dry_run=False):
        scans.append((reachability, previous_failure, dry_run))
        return {"ok": True, "market_scan": True, "candidate_count": 1, "proposal_filed": True}

    monkeypatch.setattr(cli, "_process_market_opportunities", scan)

    result = cli.command_market_watch(_proposal_only_arguments())

    assert result == 0
    assert scans == [({"ok": True, "source": "status"}, None, False)]
    assert calls == ["status"]
    assert entered == []
    assert resources.closed is True


def test_proposals_only_runs_allowed_fake_proposal_road(monkeypatch):
    entered = _forbid_broad_paths(monkeypatch)
    resources, calls = _scan_resources(["toll_bench.submit_proposal"], model=object())
    resources.toll_bench.status = lambda: calls.append("status") or {"ok": True}
    resources.toll_bench.ensure_reachable = lambda: pytest.fail("ping path entered")
    resources.close = lambda: calls.append("close")
    filed = []

    class _DraftLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, target_id, **kwargs):
            filed.append((target_id, kwargs["file"]))
            resources.toll_bench.submit_proposal(target_id, {}, "fake-key")
            return {"ok": True, "filed": True}

    monkeypatch.setattr(cli, "DraftLoop", _DraftLoop)
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)

    assert cli.command_market_watch(_proposal_only_arguments()) == 0
    assert filed == [("target-1", True)]
    assert calls == ["status", "list_targets", "submit_proposal", "close"]
    assert entered == []


@pytest.mark.parametrize(
    "status",
    [RuntimeError("bench unreachable"), {"ok": False, "error": "unauthorized"}],
)
def test_proposals_only_failed_status_read_is_a_failed_cycle_and_nothing_else(
    monkeypatch, status
):
    entered = _forbid_broad_paths(monkeypatch)
    resources, calls = _proposal_only_resources(status=status)
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_opportunities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    result = cli.command_market_watch(_proposal_only_arguments())

    assert result == 2
    assert calls == ["status"]
    assert entered == []
    assert resources.closed is True


def test_proposals_only_scan_failure_falls_into_no_broad_path(monkeypatch):
    entered = _forbid_broad_paths(monkeypatch)
    resources, calls = _proposal_only_resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)

    def scan(*_args, **_kwargs):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(cli, "_process_market_opportunities", scan)

    result = cli.command_market_watch(_proposal_only_arguments())

    assert result == 2
    assert calls == ["status"]
    assert entered == []


def test_proposals_only_keeps_scanning_on_the_scan_cadence(monkeypatch):
    entered = _forbid_broad_paths(monkeypatch)
    resources, _calls = _proposal_only_resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    scans = []

    def scan(_resources, _reachability, previous_failure=None, dry_run=False):
        scans.append(previous_failure)
        if len(scans) == 1:
            return {"ok": False, "error": "bench_refused", "run": None}
        return {"ok": True, "market_scan": True}

    monkeypatch.setattr(cli, "_process_market_opportunities", scan)
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise _Stop

    monkeypatch.setattr(cli.time, "sleep", sleep)

    with pytest.raises(_Stop):
        cli.command_market_watch(_proposal_only_arguments(once=False, scan_interval=120.0))

    assert scans == [None, {"error": "bench_refused", "error_type": None}]
    assert sleeps == [120.0, 120.0]
    assert entered == []
    assert resources.closed is True


def test_proposals_only_disabled_submit_proposal_still_files_nothing(monkeypatch):
    entered = _forbid_broad_paths(monkeypatch)
    monkeypatch.setattr(cli, "DraftLoop", _NoDraftLoop)
    resources, calls = _proposal_only_resources(enabled_tools=["state.save"])
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)

    result = cli.command_market_watch(_proposal_only_arguments())

    assert result == 0
    assert calls == ["status"]
    assert entered == []


def test_proposals_only_dry_run_reaches_the_scan_as_validation_only(monkeypatch):
    _forbid_broad_paths(monkeypatch)
    resources, _calls = _proposal_only_resources()
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    seen = []
    monkeypatch.setattr(
        cli,
        "_process_market_opportunities",
        lambda _resources, _reachability, previous_failure=None, dry_run=False: seen.append(
            dry_run
        )
        or {"ok": True, "market_scan": True},
    )

    assert cli.command_market_watch(_proposal_only_arguments(dry_run=True)) == 0
    assert seen == [True]


def test_proposals_only_refuses_no_bid_before_building_the_runtime(monkeypatch):
    monkeypatch.setattr(
        cli,
        "build_runtime",
        lambda _config: (_ for _ in ()).throw(AssertionError("must not build")),
    )

    with pytest.raises(ValueError, match="--no-bid"):
        cli.command_market_watch(_proposal_only_arguments(no_bid=True))


def test_parser_accepts_proposals_only_and_defaults_it_off():
    parser = cli.build_parser()

    on = parser.parse_args(["market", "watch", "agent.yaml", "--proposals-only"])
    off = parser.parse_args(["market", "watch", "agent.yaml"])

    assert on.proposals_only is True
    assert off.proposals_only is False


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


@pytest.mark.parametrize(("enabled", "expect_resume"), [([], False), (["email.send"], True)])
def test_pending_email_on_other_step_defers_instead_of_crashing(enabled, expect_resume):
    # A pending email approval parked on another step does not crash this cycle.
    # It may resume only while email.send is configured.
    resumed = []

    class MailClient:
        def configure_send_context(self, **_kwargs):
            raise RuntimeError("A different deal step has an unresolved pending email send")

        def resume_pending_send(self):
            resumed.append(True)
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
        enabled_tools=enabled,
        start=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not invoke model")
        ),
    )
    resources = SimpleNamespace(
        toll_bench=toll_bench, runtime=runtime, agent_identity=None
    )

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    # The blocked deal step is deferred and no model run is triggered.
    assert resumed == ([True] if expect_resume else [])
    assert (result.get("email", {}).get("status") == "pending_human_approval") is expect_resume


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

    runtime = SimpleNamespace(
        enabled_tools=["email.send", "toll_bench.attention", *cli.MARKET_SCAN_TOOLS]
    )
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
    # RULE 243: the old road asks for the same SEVEN FIELDS now.
    assert "ONE PROPOSAL" in observed["goal"]
    assert "valid to submit no proposal" not in observed["goal"]
    assert runtime.enabled_tools == ["email.send", "toll_bench.attention", *cli.MARKET_SCAN_TOOLS]


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
        "email.list",
        "email.read",
        "web.search",
        "web.fetch",
        "http.request",
        "browser.open",
        "browser.observe",
        "browser.click",
        "browser.type",
        "browser.type_secret",
        "browser.wait",
        "secret.generate",
        "files.list",
        "files.read",
        "files.write",
        "wake.set_timer",
        "human.request",
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
    assert {
        "web.search",
        "http.request",
        "browser.open",
        "browser.type_secret",
        "secret.generate",
        "files.write",
        "wake.set_timer",
        "email.list",
    } <= set(observed["tools"])
    # Person-owned access may arrive only as a signed GRANT, never a mid-deal ask.
    assert "human.request" not in observed["tools"]
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
    *, next_due_iso=None, unread=0, message_ids=(), agent_message_ids=(), overdue=False
):
    return {
        "ok": True,
        "current_step": {"id": "s-1", "state": "agent_working", "outcome_filed_at": None},
        "step_thread": {
            "messages": [{"id": mid, "who": "person"} for mid in message_ids]
            + [{"id": mid, "who": "agent"} for mid in agent_message_ids],
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
    # starving (the 2026-08-29 Porsche plan sat ~55 minutes behind one).
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
    attention = iter(
        [
            {
                "attention": [
                    {
                        "kind": "deal_step",
                        "deal_id": "d1",
                        "proposal_id": "p1",
                        "step_id": "s-1",
                    },
                    {
                        "kind": "file_informed_plan",
                        "proposal_id": "p-free",
                        "target_id": "t-1",
                    },
                ]
            },
            {
                "attention": [
                    {
                        "kind": "deal_step",
                        "deal_id": "d1",
                        "proposal_id": "p1",
                        "step_id": "s-1",
                    }
                ]
            },
        ]
    )
    resources.toll_bench.attention = lambda wait: next(attention)

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


def test_agent_reposting_its_own_ask_does_not_defeat_the_idle_skip():
    # A model that re-posts the same question to the person every run (one
    # posted the identical ask 20 times on 2026-08-29) must still read as
    # idle: its own messages are output, not actionable input, so the
    # fingerprint ignores them and the spam loop is capped at the pulse
    # cadence instead of the poll interval.
    cli._IDLE_STEP_MEMO.clear()
    before = _idle_step_payload(agent_message_ids=("a-1",))
    cli._IDLE_STEP_MEMO["s-1"] = cli._deal_step_fingerprint(before)
    after = _idle_step_payload(agent_message_ids=("a-1", "a-2"))
    observed = {}
    resources = _idle_resources(
        [{"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}],
        after,
        observed,
    )

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert result["run"] is None
    assert "goal" not in observed

    # But a PERSON message with the same shape wakes the step immediately.
    spoken = _idle_step_payload(message_ids=("m-1",), agent_message_ids=("a-1", "a-2"))
    resources2 = _idle_resources(
        [{"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}],
        spoken,
        observed,
    )
    result2 = cli._process_market_attention(resources2, wait=20)
    assert result2["ok"] is True
    assert '"s-1"' in observed["goal"]
    cli._IDLE_STEP_MEMO.clear()


def test_limit_reached_run_still_records_the_idle_memo():
    # A run that exhausts its iteration budget spent a full run over exactly
    # this state; re-running identical input wanders identically at full
    # price. limit_reached therefore records the memo like a completed run.
    # (A FAILED run still records nothing -- adapter errors are transient.)
    cli._IDLE_STEP_MEMO.clear()
    payload = _idle_step_payload()
    observed = {}
    resources = _idle_resources(
        [{"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}],
        payload,
        observed,
    )

    def start_limited(goal, mode):
        import dataclasses

        observed["goal"] = goal
        return dataclasses.replace(
            _completed_run(goal, mode), status=RunStatus.LIMIT_REACHED
        )

    resources.runtime.start = start_limited

    cli._process_market_attention(resources, wait=20)

    assert cli._IDLE_STEP_MEMO.get("s-1") == cli._deal_step_fingerprint(payload)
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
        lambda _resources, _wait, previous_failure=None, **_options: {
            "ok": False,
            "error": "payout_not_ready",
            "reachability": {"ok": True},
            "attention_count": 1,
            "retry_after_seconds": 300.0,
            "run": None,
        },
    )
    scans = []

    def scan(_resources, reachability, previous_failure=None, **_kwargs):
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
        lambda _resources, _wait, previous_failure=None, **_options: {
            "ok": True,
            "reachability": {"ok": True},
            "attention_count": 1,
            "run": {"status": "completed"},
        },
    )
    scans = []

    def scan(_resources, reachability, previous_failure=None, **_kwargs):
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


def _breaker_run_result(goal, mode, status, result):
    return RunResult(
        run_id="run-breaker",
        status=status,
        result=result,
        checkpoint=Checkpoint(
            run_id="run-breaker",
            goal=goal,
            data={},
            event_cursor=0,
            revision=0,
            updated_at="2026-09-02T00:00:00Z",
        ),
        usage=ModelUsage(total_tokens=10),
        iterations=1,
        observed_mode=mode,
    )


def _breaker_resources(
    obligation, *, failure=None, withdrawals=None, goals=None, draft=None
):
    """A connected agent whose single obligation always fails the same way.

    `draft` is what the bench is HOLDING for a plan: a dict, or a callable
    answering one, so a test can change it server-side mid-run the way the
    bench did on 2026-09-11.
    """

    def withdraw_proposal(proposal_id, *, reason, cause="other", idempotency_key=""):
        if withdrawals is not None:
            withdrawals.append({
                "proposal_id": proposal_id,
                "reason": reason,
                "cause": cause,
                "idempotency_key": idempotency_key,
            })
        return {"ok": True, "returned_count": 4}

    def read_draft(target_id, *, kind="bid"):
        held = draft() if callable(draft) else draft
        return held if isinstance(held, dict) else {"ok": True, "problems": []}

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {"attention": [dict(obligation)]},
        list_proposals=lambda: {"proposals": [{"id": "proposal-1", "total_ask_cents": 0}]},
        status=lambda: {"payout": {"ready": True}},
        withdraw_proposal=withdraw_proposal,
        read_draft=read_draft,
    )
    runtime = SimpleNamespace(
        email_provider=None,
        enabled_tools=[
            "toll_bench.list_proposals",
            "toll_bench.submit_informed_plan",
            "toll_bench.withdraw_proposal",
            "toll_bench.submit_proposal",
            "toll_bench.read_brief",
            "toll_bench.validate_proposal",
            "result.complete",
        ],
    )

    def start(goal, mode):
        if goals is not None:
            goals.append((goal, list(runtime.enabled_tools)))
        if failure is None:
            return _breaker_run_result(goal, mode, RunStatus.COMPLETED, {"summary": "Filed."})
        return _breaker_run_result(goal, mode, RunStatus.FAILED, failure)

    runtime.start = start
    return SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)


def _plan_obligation(**extra):
    return {
        "kind": "file_informed_plan",
        "proposal_id": "proposal-1",
        "target_id": "target-1",
        **extra,
    }


def test_identical_failures_back_off_and_stall_the_obligation(monkeypatch):
    # One selected agent failed this same dispatch 663 times in 11 hours on a
    # flat 65-second delay. The delay now doubles and the key stops dispatching.
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    withdrawals = []
    resources = _breaker_resources(
        _plan_obligation(),
        failure={"reason": "Model invocation failed", "error": "ValidationException: toolUse"},
        withdrawals=withdrawals,
    )

    first = cli._process_market_attention(resources, wait=0, stall_threshold=3)
    second = cli._process_market_attention(resources, wait=0, stall_threshold=3)
    third = cli._process_market_attention(resources, wait=0, stall_threshold=3)

    assert [step["breaker"]["consecutive_failures"] for step in (first, second, third)] == [1, 2, 3]
    assert [step["retry_after_seconds"] for step in (first, second, third)] == [120.0, 240.0, 480.0]
    assert first["breaker"]["stalled"] is False
    assert third["breaker"]["stalled"] is True
    assert third["breaker"]["error"] == "ValidationException: toolUse"

    # Stalled: the fourth cycle never reaches the model at all.
    fourth = cli._process_market_attention(resources, wait=0, stall_threshold=3)

    assert fourth["ok"] is True
    assert fourth["run"] is None
    assert fourth["stalled_obligations"] == 1
    # AND NOTHING IS WITHDRAWN (0.38.4). See the test below for the night that
    # forced it.
    assert withdrawals == []


def test_a_stalled_plan_request_never_withdraws_the_persons_chosen_agent(monkeypatch):
    """WHAT FORCED IT (production, 2026-09-11). The bench refused a selected
    agent's plan five times on an UNCHANGED state for a bench-side bug -- a
    refusal on a step the bench had stamped itself -- and this package went
    straight to withdrawing the bid with cause `cannot_deliver`. It only failed
    because the call carried no idempotency key. Had it worked, the person
    would have watched their chosen agent withdraw over a mistake that was not
    the agent's, and every held bid on the want would have returned to the
    table for nothing.

    A refusal the agent cannot clear is not proof the agent cannot deliver."""
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    withdrawals = []
    resources = _breaker_resources(
        _plan_obligation(),
        failure={"error": "REJ-16 on a step the bench stamped"},
        withdrawals=withdrawals,
    )

    for _ in range(5):
        result = cli._process_market_attention(resources, wait=0, stall_threshold=5)

    assert withdrawals == []
    assert result["breaker"]["stalled"] is True
    blocked = result["breaker"]["blocked"]
    assert blocked["withdrawn"] is False
    assert blocked["blocked"] is True
    assert blocked["attempts"] == 5
    assert "REJ-16" in blocked["refusal"]
    assert blocked["waiting_seconds"] == cli._PLAN_STALL_WAIT_SECONDS


def test_the_stall_lifts_when_the_bench_is_holding_a_different_draft(monkeypatch):
    """Tonight the bench FIXED the document server-side and the unit sat
    stalled anyway until someone restarted it. The draft read costs no round,
    so a stalled plan is re-tested every cycle."""
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    named = a_row("form.steps.0", step=1, slot="hand_over_line",
                  say="Step 1: the hand-over line is empty.", codes=["REJ-16"])
    held = {"ok": True, "problems": [named], "next_fix": named,
            "draft": {"steps": [{"title": "one"}]}}
    goals = []
    resources = _breaker_resources(
        _plan_obligation(),
        failure={"error": "REJ-16 on a step the bench stamped"},
        goals=goals,
        draft=lambda: held,
    )

    for _ in range(2):
        cli._process_market_attention(resources, wait=0, stall_threshold=2)
    stalled = cli._process_market_attention(resources, wait=0, stall_threshold=2)
    assert stalled["run"] is None
    assert stalled["stalled_obligations"] == 1
    dispatched = len(goals)

    # The bench fixes its own step. Nothing about the obligation changed.
    held = {"ok": True, "problems": [], "next_fix": None, "ready": True,
            "draft": {"steps": [{"title": "one"}], "_bench_fixed": ["step 1 rebuilt"]}}

    after = cli._process_market_attention(resources, wait=0, stall_threshold=2)

    assert after["run"] is not None
    assert len(goals) == dispatched + 1


def test_a_stalled_plan_tries_again_after_the_bounded_wait(monkeypatch):
    """Not forever, and not until a restart: ten minutes is the ceiling on a
    stall somebody is waiting behind."""
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    goals = []
    resources = _breaker_resources(
        _plan_obligation(),
        failure={"error": "REJ-16 on a step the bench stamped"},
        goals=goals,
        draft={"ok": True, "problems": [
            a_row("form.steps.0", step=1, slot="hand_over_line",
                  say="Step 1: the hand-over line is empty.", codes=["REJ-16"])]},
    )

    for _ in range(2):
        cli._process_market_attention(resources, wait=0, stall_threshold=2)
    assert cli._process_market_attention(
        resources, wait=0, stall_threshold=2
    )["run"] is None
    dispatched = len(goals)

    # Ten minutes later, with the same draft and the same obligation.
    key = cli._obligation_key(_plan_obligation())
    cli._OBLIGATION_FAILURES[key]["stalled_at"] -= cli._PLAN_STALL_WAIT_SECONDS + 1

    after = cli._process_market_attention(resources, wait=0, stall_threshold=2)

    assert after["run"] is not None
    assert len(goals) == dispatched + 1


def test_a_plan_blocked_on_a_deal_puts_the_refusal_on_the_check_in(monkeypatch):
    """Where the plan road HAS a blocker to post, the person reads why nothing
    is moving. Where it has none -- a plan the person is still waiting on, with
    no deal yet -- the log is the record and nothing is invented."""
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    pulses = []
    resources = _breaker_resources(
        _plan_obligation(deal_id="deal-9"),
        failure={"error": "REJ-16 on a step the bench stamped"},
    )
    resources.toll_bench.post_check_in = lambda deal_id, pulse, key: (
        pulses.append((deal_id, pulse, key)) or {"ok": True}
    )
    resources.runtime.enabled_tools.append("toll_bench.post_check_in")

    for _ in range(2):
        result = cli._process_market_attention(resources, wait=0, stall_threshold=2)

    assert result["breaker"]["blocked"]["blocker_posted"] is True
    assert pulses[0][0] == "deal-9"
    assert "refusing this plan" in pulses[0][1]["blocker"]
    assert len(pulses[0][1]["blocker"]) <= 280


def test_a_plan_with_no_deal_posts_nothing_and_says_nothing_it_cannot(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    resources = _breaker_resources(
        _plan_obligation(),
        failure={"error": "REJ-16 on a step the bench stamped"},
    )

    for _ in range(2):
        result = cli._process_market_attention(resources, wait=0, stall_threshold=2)

    assert "blocker_posted" not in result["breaker"]["blocked"]


def test_a_stalled_deal_step_stalls_without_withdrawing(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})
    withdrawals = []
    obligation = {"kind": "deal_step", "deal_id": "deal-1", "step_id": "step-1"}
    resources = _breaker_resources(
        obligation, failure={"error": "boom"}, withdrawals=withdrawals
    )
    resources.toll_bench.current_step = lambda deal_id: {"current_step": {"id": "step-1"}}

    for _ in range(2):
        result = cli._process_market_attention(resources, wait=0, stall_threshold=2)

    assert result["breaker"]["stalled"] is True
    assert withdrawals == []


def test_a_different_failure_starts_the_count_over(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    errors = ["first failure", "first failure", "a different failure"]
    obligation = _plan_obligation()

    def next_resources():
        return _breaker_resources(obligation, failure={"error": errors.pop(0)})

    first = cli._process_market_attention(next_resources(), wait=0, stall_threshold=5)
    second = cli._process_market_attention(next_resources(), wait=0, stall_threshold=5)
    third = cli._process_market_attention(next_resources(), wait=0, stall_threshold=5)

    assert [step["breaker"]["consecutive_failures"] for step in (first, second, third)] == [1, 2, 1]


def test_a_success_clears_the_breaker(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    obligation = _plan_obligation()
    failing = _breaker_resources(obligation, failure={"error": "boom"})

    cli._process_market_attention(failing, wait=0, stall_threshold=5)
    successful = _breaker_resources(obligation)
    attention = iter(
        [
            {"attention": [dict(obligation)]},
            {"attention": []},
        ]
    )
    successful.toll_bench.attention = lambda wait: next(attention)
    cli._process_market_attention(successful, wait=0, stall_threshold=5)
    after = cli._process_market_attention(failing, wait=0, stall_threshold=5)

    assert after["breaker"]["consecutive_failures"] == 1
    assert cli._OBLIGATION_FAILURES[cli._obligation_key(obligation)]["count"] == 1


def test_completed_plan_run_is_failure_while_filing_obligation_remains(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    obligation = _plan_obligation()
    resources = _breaker_resources(obligation)

    result = cli._process_market_attention(resources, wait=0, stall_threshold=5)

    assert result["ok"] is False
    assert result["plan_filing_verified"] is False
    assert result["breaker"]["consecutive_failures"] == 1
    assert "without filing the informed plan" in result["breaker"]["error"]


def test_completed_plan_run_succeeds_after_server_clears_obligation(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    obligation = _plan_obligation()
    resources = _breaker_resources(obligation)
    attention = iter(
        [
            {"attention": [dict(obligation)]},
            {"attention": []},
        ]
    )
    resources.toll_bench.attention = lambda wait: next(attention)

    result = cli._process_market_attention(resources, wait=0, stall_threshold=5)

    assert result["ok"] is True
    assert result["plan_filing_verified"] is True
    assert cli._obligation_key(obligation) not in cli._OBLIGATION_FAILURES


def test_a_changed_obligation_payload_lifts_the_stall(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    goals = []
    stalled = _breaker_resources(_plan_obligation(), failure={"error": "boom"})

    for _ in range(2):
        cli._process_market_attention(stalled, wait=0, stall_threshold=2)
    skipped = cli._process_market_attention(stalled, wait=0, stall_threshold=2)

    changed = _breaker_resources(
        _plan_obligation(why="the person answered your question"),
        failure={"error": "boom"},
        goals=goals,
    )
    result = cli._process_market_attention(changed, wait=0, stall_threshold=2)

    assert skipped["run"] is None
    assert result["run"] is not None
    assert len(goals) == 1


def test_an_unknown_kind_still_dispatches_without_the_retired_feedback_tools(monkeypatch):
    """The unknown-kind fallback keeps every live instruction and tool set;
    the retired feedback_returned set is no longer folded in."""
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    goals = []
    obligation = {
        "kind": "some_future_kind",
        "target_id": "target-1",
        "proposal_id": "proposal-1",
        "why": "a kind this build has not special-cased",
    }
    resources = _breaker_resources(obligation, goals=goals)

    result = cli._process_market_attention(resources, wait=0)

    assert result["ok"] is True
    goal, tools = goals[0]
    assert "re-file ONCE" not in goal
    assert "back on the table" not in goal
    assert "toll_bench.submit_proposal" not in set(tools)
    assert "toll_bench.validate_proposal" not in set(tools)


def test_stall_threshold_comes_from_the_agent_configuration(tmp_path):
    config = tmp_path / "agent.yaml"
    config.write_text("version: 1\nfleet:\n  stall_threshold: 9\n")

    assert cli._configured_stall_threshold(config) == 9
    assert cli._configured_stall_threshold(tmp_path / "missing.yaml") == 5
