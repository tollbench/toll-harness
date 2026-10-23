"""NOTICES COST NO MODEL CALL (0.68.0).

WHAT FORCED THESE TESTS. Lab agent A-0667 (DeepSeek, staging, 2026-10-23) on
plain `market watch` ran a full model call for every attention item 0.67.0
did not rank -- `everyone_post_invited`, `waiting`, `tip_waiting` -- with
every obligation's tools, none of which can do anything about a notice. An
attention item this harness has no action for is now reported on the cycle's
`notices` and never dispatched; real obligations beside it run as before.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from toll_harness import cli

NOTICES = [
    {"kind": "everyone_post_invited", "your_move": "File a proposal -- you're invited",
     "target_id": "t-1", "proposal_id": None, "deal_id": None, "step_id": None},
    {"kind": "waiting", "your_move": "Nothing: the person is reviewing", "deal_id": "d-1",
     "step_id": "s-1", "target_id": "t-2", "turn": {"party": "person"}},
    {"kind": "tip_waiting", "your_move": "Finish payout setup to receive it"},
    {"kind": "sign_deal", "your_move": "Countersign the deal to begin work",
     "deal_id": "d-2", "target_id": "t-3"},
]


def _resources(items, started):
    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {"attention": [dict(item) for item in items]},
        list_proposals=lambda: {"proposals": []},
        current_step=lambda deal_id: {"current_step": {"id": "s-9", "number": 1}},
    )

    def start(goal, mode):
        started.append(goal)
        raise AssertionError("a notice must not start a model run")

    runtime = SimpleNamespace(email_provider=None, enabled_tools=None, start=start,
                              model=None)
    return SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None,
                           store=None)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})


def test_a_cycle_of_notices_alone_costs_no_model_call():
    started: list = []
    result = cli._process_market_attention(_resources(NOTICES, started), wait=0)

    assert started == []
    assert result["ok"] is True and result["run"] is None
    assert [notice["kind"] for notice in result["notices"]] == [
        "everyone_post_invited", "waiting", "tip_waiting", "sign_deal"]
    assert result["notices"][0] == {
        "kind": "everyone_post_invited", "your_move": "File a proposal -- you're invited",
        "target_id": "t-1"}
    assert result["retry_after_seconds"] == 60.0


def test_a_real_obligation_beside_the_notices_still_runs(monkeypatch):
    started: list = []
    dispatched = []

    def step_ask(resources, obligation, *args, **kwargs):
        dispatched.append(obligation["kind"])
        return {"ok": True, "run": None, "dispatch": {"kind": "step_ask"}}

    monkeypatch.setattr(cli, "platform_move", lambda *a: None)
    monkeypatch.setattr(cli, "_refusal_brake_holds", lambda *a: None)
    monkeypatch.setattr(cli, "_step_ask_available", lambda *a: True)
    monkeypatch.setattr(cli, "_the_step_ask", step_ask)
    obligation = {"kind": "deal_step", "deal_id": "d-9", "step_id": "s-9"}
    result = cli._process_market_attention(
        _resources([*NOTICES, obligation], started), wait=0)

    assert dispatched == ["deal_step"] and started == []
    assert len(result["notices"]) == 4
