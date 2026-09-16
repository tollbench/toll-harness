from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from toll_harness import cli
from toll_harness.loop_guard import LoopGuard, fingerprint
from toll_harness.storage.local import SQLiteStore


def test_restart_alternating_states_and_scoped_reset(tmp_path):
    path = tmp_path / "state.sqlite3"
    for _ in range(3):
        assert LoopGuard(path).reserve("work", "A")
    assert not LoopGuard(path).reserve("work", "A")
    assert LoopGuard(path).reserve("work", "B")
    assert not LoopGuard(path).reserve("work", "A")
    assert LoopGuard(path).reserve("other", "A")
    assert LoopGuard(path).reset("work") == 2
    assert LoopGuard(path).reserve("work", "A")
    assert any(row["work_key"] == "other" for row in LoopGuard(path).status())


def test_concurrent_workers_cannot_exceed_budget(tmp_path):
    guard = LoopGuard(tmp_path / "state.sqlite3")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: guard.reserve("work", "same"), range(24)))
    assert sum(results) == 3
    assert guard.held("work", "same")


def test_clock_and_own_pulse_do_not_buy_retries():
    assert fingerprint(
        {"step": {"id": "s", "updated_at": "1"}, "latest_work_pulse": "a"}
    ) == fingerprint({"step": {"id": "s", "updated_at": "2"}, "latest_work_pulse": "b"})
    assert fingerprint({"message_ids": ["a"]}) != fingerprint({"message_ids": ["a", "b"]})


@pytest.mark.parametrize("route", ["step_ask", "legacy"])
def test_dispatch_is_bounded_across_restarts_and_different_errors(tmp_path, monkeypatch, route):
    path = tmp_path / "state.sqlite3"
    obligation = {"kind": "unanswered_message", "deal_id": "deal", "step_id": "step"}
    payload = {"current_step": {"id": "step"}, "step_thread": {"messages": []}}
    calls = []

    def invoke(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("different error " + str(len(calls)))

    monkeypatch.setattr(cli, "platform_move", lambda *a: None)
    monkeypatch.setattr(cli, "_refusal_brake_holds", lambda *a: None)
    monkeypatch.setattr(cli, "_step_ask_available", lambda *a: route == "step_ask")
    monkeypatch.setattr(cli, "_the_step_ask", invoke)
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})

    def resources():
        return SimpleNamespace(
            store=SQLiteStore(path),
            agent_identity=None,
            toll_bench=SimpleNamespace(
                ensure_reachable=lambda: {"ok": True},
                attention=lambda wait: {"attention": [dict(obligation, error=str(len(calls)))]},
                current_step=lambda deal: payload,
            ),
            runtime=SimpleNamespace(enabled_tools=[], start=invoke, email_provider=None),
        )

    for _ in range(3):
        with pytest.raises(RuntimeError, match="different error"):
            cli._process_market_attention(resources(), wait=0)
    parked = cli._process_market_attention(resources(), wait=0)
    assert parked["loop_guard"]["parked"]
    assert len(calls) == 3
    payload["step_thread"]["messages"].append({"id": "new", "who": "person"})
    with pytest.raises(RuntimeError, match="different error"):
        cli._process_market_attention(resources(), wait=0)
    assert len(calls) == 4


def test_plan_rewording_is_not_progress():
    answer = {
        "next_fix": {"path": "title", "code": "invalid", "current": "a"},
        "draft": {"title": "a"},
    }
    resources = SimpleNamespace(toll_bench=SimpleNamespace(read_draft=lambda *a, **k: answer))
    obligation = {"kind": "file_informed_plan", "target_id": "t"}
    first = cli._loop_state(resources, obligation)
    answer["next_fix"]["current"] = "different wording"
    answer["draft"]["title"] = "another bad title"
    assert cli._loop_state(resources, obligation) == first
    answer["next_fix"]["path"] = "steps.0"
    assert cli._loop_state(resources, obligation) != first


def test_parked_bid_does_not_starve_other_targets(tmp_path, monkeypatch):
    resources = SimpleNamespace(store=SQLiteStore(tmp_path / "s.db"), agent_identity=None)
    candidates = [{"target_id": "stuck", "round": 1}, {"target_id": "fresh", "round": 1}]
    guard = LoopGuard(resources.store.path)
    for _ in range(3):
        guard.reserve("bid:stuck:round:1", fingerprint(candidates[0]))
    monkeypatch.setattr(cli, "_market_scan_candidates", lambda r: (2, candidates, []))
    monkeypatch.setattr(cli, "_the_proposal_road_is_open", lambda r: True)
    seen = []

    def bid(resources, target, *args):
        seen.append(target["target_id"])
        return {"ok": True}

    monkeypatch.setattr(cli, "_bid_through_the_draft_loop", bid)
    cli._process_market_opportunities(resources, {})
    assert seen == ["fresh"]


def test_storage_failure_prevents_dispatch(tmp_path, monkeypatch):
    resources = SimpleNamespace(store=SQLiteStore(tmp_path / "s.db"), agent_identity=None)
    monkeypatch.setattr(cli, "_market_scan_candidates", lambda r: (1, [{"target_id": "t"}], []))

    def unavailable(*args):
        raise OSError("database unavailable")

    monkeypatch.setattr(LoopGuard, "reserve", unavailable)
    monkeypatch.setattr(
        cli, "_the_proposal_road_is_open", lambda r: pytest.fail("must not dispatch")
    )
    with pytest.raises(OSError, match="database unavailable"):
        cli._process_market_opportunities(resources, {})
