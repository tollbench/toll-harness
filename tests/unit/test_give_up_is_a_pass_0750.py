"""A GIVE-UP IS TOLD TO THE BENCH (0.75.0, bench 23e54d91c).

WHAT FORCED IT: from 2026-11-08 the bench counts an agent that opened a
want's brief as "deciding" until it bids or passes, or 20 minutes pass, and
the person's card reads "Gathering proposals" until then. The bid road gave
up in silence twice: when the free validate door still refused the proposal
after the repair ask, and when the model's reply could not be read twice.
Nothing was filed and the bench was never told, so the person waited the full
20 minutes on this agent. Both now send a pass through the decline door, in
the bench's own words or the parser's, and a dry run still sends nothing.
"""
from __future__ import annotations

import logging

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_missing_tools_0750 import _PassApi
from tests.unit.test_two_stages_r243_r245 import SMALL, _door_provider
from toll_harness.toll_bench.draft import DECLINE_MAX, REFUSED_AT_VALIDATE, DraftLoop

ROW = {"code": "REJ-09", "field": "total_ask_cents",
       "detail": "the price is over the want's budget", "fix": "Ask 30000 cents or less."}
REFUSED = {"ok": False, "problems": [ROW], "trimmed": []}


def _run(model, api, file=True):
    return DraftLoop(model, _door_provider(api)).run(
        "t-1", kind="bid", brief={"want": "x"}, idempotency_key="k", file=file)


def test_a_refusal_the_repair_did_not_clear_is_a_pass_in_the_doors_words(caplog):
    api = _PassApi(REFUSED, REFUSED)
    model = _model(dict(SMALL, total_ask_cents=90_000), dict(SMALL, total_ask_cents=80_000))
    with caplog.at_level(logging.INFO):
        out = _run(model, api)

    assert out["declined"] is True and out["cause"] == REFUSED_AT_VALIDATE
    assert [(t, p) for t, p, _k in api.declines] == [
        ("t-1", {"decline": "the price is over the want's budget"})]
    assert not [p for _t, p, _k in api.filed if "decline" not in p]
    assert ("proposal for target=t-1: declined (the price is over the want's budget); "
            "cause: proposal_refused_at_validate") in [r.getMessage() for r in caplog.records]


def test_the_rows_say_wins_and_the_reason_is_cut_at_the_cap():
    said = {"ok": False, "problems": [dict(ROW, say="x " * 400)], "trimmed": []}
    api = _PassApi(said, said)
    out = _run(_model(dict(SMALL), dict(SMALL)), api)
    reason = api.declines[0][1]["decline"]
    assert reason.startswith("x x") and len(reason) <= DECLINE_MAX
    assert out["cause"] == REFUSED_AT_VALIDATE


def test_an_unreadable_reply_twice_is_a_pass_in_the_parsers_words(caplog):
    api = _PassApi()
    with caplog.at_level(logging.INFO):
        out = _run(_model({"odds": 0.5}, {"odds": 0.5}), api)

    assert out["declined"] is True and out["cause"] == "no_proposal"
    [(_t, sent, _k)] = api.declines
    assert sent["decline"].startswith("the object had no pitch_title or pitch_body")
    assert any("cause: no_proposal" in r.getMessage() for r in caplog.records)


def test_a_dry_run_sends_no_pass():
    api = _PassApi(REFUSED)
    out = _run(_model({"odds": 0.5}, {"odds": 0.5}), api, file=False)
    assert out["ok"] is False and out["error"] == "no_proposal"
    assert api.declines == []
