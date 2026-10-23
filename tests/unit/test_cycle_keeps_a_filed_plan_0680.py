"""THE CYCLE'S PRINTED JSON KEEPS A FILED PLAN WHEN A BOARD SCAN FOLLOWS (0.68.0).

WHAT FORCED THESE TESTS. Lab agent A-0667 (staging, 2026-10-23) filed its
plan through the draft loop and the cycle's printed JSON said nothing about
it: the plan road answers with `run: None` (the loop is not a runtime run),
and the watch loop read `ok and run is None` as "the obligation side had
nothing model-worthy" and printed the board scan in its place. A cycle that
dispatched work (a filed plan, a step ask) now keeps its own result with the
scan alongside; a cycle that dispatched nothing still prints the scan, with
its notices carried.
"""
from __future__ import annotations

from argparse import Namespace

from toll_harness import cli

FILED = {
    "ok": True,
    "reachability": {"ok": True},
    "attention_count": 1,
    "dispatch": {"kind": "file_informed_plan_draft_loop", "model_calls": 2, "rounds": 1},
    "plan_filing_verified": True,
    "draft_loop": {"ok": True, "filed": True, "proposal_id": "p-1"},
    "run": None,
}
SCAN = {"ok": True, "market_scan": True, "proposal_filed": False, "run": None}


class _Resources:
    def close(self):
        pass


def _one_cycle(monkeypatch, attention):
    printed = []
    monkeypatch.setattr(cli, "build_runtime", lambda _config: _Resources())
    monkeypatch.setattr(cli, "_sync_default_approach", lambda _resources: None)
    monkeypatch.setattr(cli, "_process_wakes", lambda _resources: [])
    monkeypatch.setattr(cli, "_watch_update_check", lambda _config: None)
    monkeypatch.setattr(cli, "_print", printed.append)
    monkeypatch.setattr(cli, "_process_market_attention",
                        lambda *args, **kwargs: dict(attention))
    monkeypatch.setattr(cli, "_process_market_opportunities",
                        lambda *args, **kwargs: dict(SCAN))
    arguments = Namespace(config="agent.yaml", wait=0, interval=2.0, scan_interval=300.0,
                          no_bid=False, dry_run=False, once=True, proposals_only=False)
    assert cli.command_market_watch(arguments) == 0
    return printed[0]


def test_a_filed_plan_stays_the_cycles_story_with_the_scan_beside_it(monkeypatch):
    printed = _one_cycle(monkeypatch, FILED)
    assert printed["plan_filing_verified"] is True
    assert printed["draft_loop"]["filed"] is True
    assert printed["dispatch"]["kind"] == "file_informed_plan_draft_loop"
    assert printed["scan"]["market_scan"] is True


def test_a_cycle_that_dispatched_nothing_prints_the_scan_and_keeps_its_notices(monkeypatch):
    quiet = {"ok": True, "reachability": {"ok": True}, "attention_count": 0, "run": None,
             "notices": [{"kind": "waiting", "deal_id": "d-1"}]}
    printed = _one_cycle(monkeypatch, quiet)
    assert printed["market_scan"] is True
    assert printed["notices"] == [{"kind": "waiting", "deal_id": "d-1"}]
    assert "scan" not in printed
