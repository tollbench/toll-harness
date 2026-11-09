"""FUNDING RESEARCH ATTENTION (0.75.0, bench contract 4.1.62).

WHAT FORCED IT: from 2026-11-01 the bench puts a `funding_research` item on
attention when a funding pack's week is due (or a lead was sent back). The
harness had no row for that kind, so the item was logged as a notice and the
model was never called: the research card never got done. The item already
carries its own brief, form and door, so the row only points at it.
"""
from __future__ import annotations

from types import SimpleNamespace

from toll_harness import cli

PACK = "pack-1"
ITEM = {
    "kind": "funding_research", "id": f"{PACK}:2026-11-09T00:00:00+00:00",
    "pack_id": PACK, "why": "This week's funding research is due.",
    "your_move": "Find new grants, credits and programs that fit.",
    "call": f"POST /api/bench/packs/{PACK}/funding-leads",
    "target_id": None, "proposal_id": None, "deal_id": None, "step_id": None,
    "research": {"pack_id": PACK, "due": True, "brief": "Seed money for a co-op.",
                 "lead_form": [], "leads_on_file": [], "sent_back": [],
                 "file_call": f"POST /api/bench/packs/{PACK}/funding-leads",
                 "file_body": {"leads": [], "note": "..."}, "how": "File new leads."},
}


def test_the_row_points_at_the_item_and_carries_the_bench_doors():
    row = cli._OBLIGATION_DISPATCH["funding_research"]
    assert {"toll_bench.doors", "toll_bench.call", "web.search", "web.fetch"} <= row["tools"]
    assert cli._BOOKKEEPING_TOOLS <= row["tools"]
    for words in ("your_move", "research.how", "call", "research.file_body"):
        assert words in row["instruction"]
    # The bench's lead rules are said once, on the item, never restated here.
    assert "duplicate" not in row["instruction"] and "apply" not in row["instruction"]


def test_one_week_of_one_pack_is_one_piece_of_work():
    key = cli._obligation_key(ITEM)
    assert key == ("funding_research", PACK, "2026-11-09T00:00:00+00:00")
    next_week = dict(ITEM, id=f"{PACK}:2026-11-16T00:00:00+00:00")
    other_pack = dict(ITEM, id="pack-2:2026-11-09T00:00:00+00:00", pack_id="pack-2")
    assert cli._loop_key(next_week) != cli._loop_key(ITEM)
    assert cli._loop_key(other_pack) != cli._loop_key(ITEM)


def test_a_sent_back_lead_is_new_work_in_the_same_week():
    resources = SimpleNamespace(toll_bench=SimpleNamespace())
    sent_back = dict(ITEM, research={**ITEM["research"], "sent_back": [
        {"lead_id": "l-1", "note": "Wrong deadline.", "lead": {"name": "Grant"}}]})
    assert cli._loop_key(sent_back) == cli._loop_key(ITEM)
    assert cli._loop_state(resources, sent_back) != cli._loop_state(resources, ITEM)


def test_the_cycle_calls_the_model_instead_of_logging_a_notice(monkeypatch):
    started = []

    def start(goal, _mode):
        started.append((goal, tuple(runtime.enabled_tools)))
        return SimpleNamespace(status=SimpleNamespace(value="completed"))

    runtime = SimpleNamespace(
        enabled_tools=["toll_bench.doors", "toll_bench.call", "web.search",
                       "toll_bench.submit_proposal", "email.send"],
        start=start, model=None, email_provider=None)
    bench = SimpleNamespace(ensure_reachable=lambda: {"ok": True},
                            attention=lambda wait: {"attention": [dict(ITEM)]},
                            list_proposals=lambda: {"proposals": []})
    resources = SimpleNamespace(toll_bench=bench, runtime=runtime,
                                agent_identity=None, store=None)
    monkeypatch.setattr(cli, "_result_payload", lambda result: {"status": result.status.value})
    monkeypatch.setattr(cli, "_dispatch_meter", lambda *args: {})
    result = cli._process_market_attention(resources, wait=0)
    assert result["ok"] and not result.get("notices")
    assert len(started) == 1
    goal, tools = started[0]
    assert ITEM["call"] in goal and "file_body" in goal
    assert set(tools) == {"toll_bench.doors", "toll_bench.call", "web.search"}


def test_a_deal_step_still_goes_first():
    step = {"kind": "deal_step", "deal_id": "d-1", "step_id": "s-1"}
    assert cli._select_obligation([ITEM, step]) == step
    assert cli._select_obligation([ITEM]) == ITEM
