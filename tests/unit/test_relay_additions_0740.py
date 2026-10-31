"""H3, 0.74.0: relay additions bolted onto the thin core.

WHAT FORCED THEM (heat logs 2026-10-27, lane 3 rows 1, 3, 4, 9; README C1-C2,
C7): a refusal the bench explained was filed anyway; DJ4Fun's 26 passes were
pushed into bids at odds 0; a closed want cost a model call; a heat
invitation waited for the scan's five-minute cadence; a bench 5xx rode the
next ask as the model's own failure; a model that wrote its tool call as text
was called malformed.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_two_stages_r243_r245 import SMALL, _door_provider, _DoorApi
from toll_harness import cli
from toll_harness.toll_bench.api import BookOfHousesApiError
from toll_harness.toll_bench.draft import (
    DraftLoop,
    decline_of,
    heat_filings_of,
    read_proposal,
)
from toll_harness.toll_bench.step import tool_call_in_text

REFUSED = {"ok": False, "problems": [{"code": "REJ-09", "field": "total_ask_cents",
                                      "detail": "the price is over the want's budget",
                                      "fix": "Ask 30000 cents or less."}],
           "trimmed": [], "bench_fixed": [{"code": "question_dropped"}]}


class _DeclineApi(_DoorApi):
    def submit_proposal(self, target_id, proposal, idempotency_key):
        if "decline" in proposal:
            self.filed.append((target_id, dict(proposal), idempotency_key))
            return {"ok": True, "declined": True, "target_id": target_id,
                    "reason": proposal["decline"]}
        return super().submit_proposal(target_id, proposal, idempotency_key)


def _ask_text(model, index):
    return model.invocations[index]["messages"][0].content[0]["text"]


def test_the_doors_words_ride_one_repair_ask_and_only_what_passes_is_filed():
    api = _DoorApi(REFUSED, {"ok": True, "problems": [], "trimmed": []})
    model = _model(dict(SMALL, total_ask_cents=90_000), dict(SMALL, total_ask_cents=25_000))
    out = DraftLoop(model, _door_provider(api)).run(
        "t-1", kind="bid", brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is True and out["filed"] is True
    assert len(model.invocations) == 2
    repair = _ask_text(model, 1)
    assert "the_bench_refused" in repair and "Ask 30000 cents or less." in repair
    assert len(api.asked) == 2  # two free validations
    assert [p["total_ask_cents"] for _t, p, _k in api.filed] == [25_000]


def test_a_repair_the_door_still_refuses_is_recorded_and_never_filed():
    api = _DoorApi(REFUSED, REFUSED)
    model = _model(dict(SMALL, total_ask_cents=90_000), dict(SMALL, total_ask_cents=80_000))
    out = DraftLoop(model, _door_provider(api)).run(
        "t-1", kind="bid", brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False and out["error"] == "proposal_refused_at_validate"
    assert api.filed == []
    assert len(model.invocations) == 2


def test_a_pass_is_sent_to_the_bench_once_and_nothing_else_is_asked():
    api = _DeclineApi()
    model = _model({"decline": "This want needs a licensed electrician."})
    out = DraftLoop(model, _door_provider(api)).run(
        "t-1", kind="bid", brief={"want": "Rewire my house"}, idempotency_key="k")

    assert out["declined"] is True and out["filed"] is False and out["ok"] is True
    assert "declined_locally" not in out
    assert api.filed == [("t-1", {"decline": "This want needs a licensed electrician."},
                          api.filed[0][2])]
    assert api.asked == []  # no validation, no second model call
    assert len(model.invocations) == 1


def test_a_bench_without_the_decline_door_keeps_the_record_here():
    class OldBench(_DoorApi):
        def submit_proposal(self, target_id, proposal, idempotency_key):
            raise BookOfHousesApiError(422, "REJ-01", "pitch_title is required")

    out = DraftLoop(_model({"decline": "Not my kind of want."}),
                    _door_provider(OldBench())).run(
        "t-1", kind="bid", brief={"want": "x"}, idempotency_key="k")
    assert out["declined"] is True and out["declined_locally"] is True


def test_the_ask_tells_the_model_how_to_pass_and_the_odds_hole():
    model = _model({"decline": "no"})
    DraftLoop(model, _door_provider(_DeclineApi())).run(
        "t-1", kind="bid", brief={"want": "x"}, idempotency_key="k")
    ask = _ask_text(model, 0)
    assert '{"decline": "<one line: why>"}' in ask
    assert "0.35, never 35, never 0" in ask
    assert "REFUSED by the bench, not trimmed" not in ask


def test_odds_go_to_the_door_as_written():
    # The bench reads 1 < odds < 100 as a percent and says so (contract 4.1.55).
    assert read_proposal(dict(SMALL, odds=95))["odds"] == 95
    assert decline_of({"decline": True, "reason": "  off  scope "}) == "off scope"
    assert decline_of({"no_bid": "x" * 400}) == "x" * 280
    assert decline_of(SMALL) is None


def test_a_closed_brief_ends_the_candidate_with_no_model_call(monkeypatch):
    briefs = {"closed": {"ok": True, "brief": None,
                         "bidding": {"state": "closed", "reason": "selected"}},
              "paused": {"ok": True, "brief": {"want": "p"},
                         "bidding": {"state": "paused", "reason": "heat_copy_taken"}},
              "open": {"ok": True, "brief": {"want": "o"},
                       "bidding": {"state": "open"}}}
    resources = SimpleNamespace(
        agent_identity=None,
        toll_bench=SimpleNamespace(
            fleet=None,
            list_targets=lambda: {"targets": [
                {"target_id": name, "posted_at": f"2026-10-3{i}T00:00:00Z"}
                for i, name in enumerate(("open", "paused", "closed"))]},
            read_whole_brief=lambda target_id: briefs[target_id]))
    monkeypatch.setattr(cli, "_MARKET_BRIEF_OFFSET", 0)
    _count, candidates, _keys = cli._market_scan_candidates(resources)
    assert [c["target_id"] for c in candidates] == ["open"]


def test_a_closed_brief_at_the_filing_door_files_nothing():
    class Closed(_DoorApi):
        def target_brief(self, target_id):
            return {"ok": True, "brief": None,
                    "bidding": {"state": "closed", "reason": "selected",
                                "message": "Another agent was picked."}}

    api = Closed()
    out = _door_provider(api).submit_proposal("t-1", dict(SMALL), "k")
    assert out["error"] == "target_not_open" and out["terminal"] is True
    assert "Another agent was picked." in out["message"]
    assert api.asked == [] and api.filed == []


def test_an_invited_want_goes_first_and_the_scan_runs_this_cycle(monkeypatch):
    resources = SimpleNamespace(
        agent_identity=None,
        toll_bench=SimpleNamespace(
            fleet=None,
            list_targets=lambda: {"targets": [
                {"target_id": "newest", "posted_at": "2026-10-31T00:00:00Z"},
                {"target_id": "invited", "posted_at": "2026-10-01T00:00:00Z"}]},
            read_whole_brief=lambda target_id: {"ok": True, "brief": {"want": target_id}}))
    monkeypatch.setattr(cli, "_MARKET_BRIEF_OFFSET", 1)
    _count, candidates, _keys = cli._market_scan_candidates(resources, ["invited"])
    assert candidates[0]["target_id"] == "invited"
    cycle = {"ok": True, "notices": [{"kind": "everyone_post_invited", "target_id": "invited"},
                                     {"kind": "waiting", "target_id": "x"}]}
    assert cli._invited_targets(cycle) == ["invited"]


def test_a_bench_5xx_is_nobodys_attempt():
    assert cli._bench_was_down({"ok": False, "error": "book_of_houses_api_error",
                                "status": 502})
    assert cli._bench_was_down({"ok": False, "error": "book_of_houses_api_error",
                                "status": 0})
    assert not cli._bench_was_down({"ok": False, "error": "book_of_houses_api_error",
                                    "status": 422})
    assert not cli._bench_was_down({"ok": True})


def test_a_tool_call_written_as_text_is_read_before_it_is_called_malformed():
    tools = {"post_step_message": {"schema": {"type": "object"}}}
    text = 'Sure.\n```json\n{"name": "post_step_message", "arguments": {"message": "Hi"}}\n```'
    assert tool_call_in_text(text, tools) == ("post_step_message", {"message": "Hi"})
    assert tool_call_in_text(json.dumps({"post_step_message": {"message": "Hi"}}), tools) == (
        "post_step_message", {"message": "Hi"})
    assert tool_call_in_text('{"tool": "post_step_message", "message": "Hi"}', tools) == (
        "post_step_message", {"message": "Hi"})
    assert tool_call_in_text('{"name": "submit_step_outcome"}', tools) is None
    assert tool_call_in_text("no json here", tools) is None


def test_a_heat_copys_plan_filings_are_the_budget_and_none_left_stops_writes():
    from toll_harness.toll_bench.draft import _spent

    heat = {"heat_filings": {"used": 3, "left": 0, "cap": 3}, "rounds": {"left": 50}}
    assert heat_filings_of(heat) == {"used": 3, "left": 0, "cap": 3}
    assert _spent(heat) is True
    assert _spent({"heat_filings": None, "rounds": {"left": 5}}) is False
