"""THE MISSING TOOL ON A PASS, AND THE ALTERNATIVE, ONCE (0.75.0, contract 4.1.65).

WHAT FORCED IT (Steven, 2026-11-05): Milo declined "Call my friend and wish
him happy birthday" with "I can't place phone calls, and none of the tools on
this want can ring a phone." The reason went nowhere and nothing else was
offered. Steven ruled: a pass for a missing tool names the tool so the bench
can flag it for building, and the agent is asked, once, for a reasonable other
set of actions with the tools the want does have.
"""
from __future__ import annotations

import hashlib
import json
import logging

import pytest

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_two_stages_r243_r245 import SMALL, _door_provider, _DoorApi
from toll_harness.core.thinking import ThinkingStream
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.draft import (
    ALTERNATIVE_INSTRUCTION,
    PROPOSAL_INSTRUCTION,
    PROPOSAL_REPAIR_INSTRUCTION,
    DraftLoop,
    alternative_ask_of,
    decline_of,
    missing_tools_of,
)

PHONE = "place a phone call"
PASS = {"decline": "No tool on this want can ring a phone.", "missing_tools": [PHONE]}
ASK = {
    "ask": "No tool here can place a call. Could you get this done another way "
           "with the tools this want has?",
    "tool_slugs": ["gmail.message.send", "platform.research"],
    "how": "File a proposal on this want as usual; it lifts your decline.",
}
ALTERNATIVE = dict(SMALL, pitch_title="A birthday email he will remember",
                   tools_needed=["gmail.message.send"])
REFUSED = {"ok": False, "problems": [{"code": "REJ-09", "field": "total_ask_cents",
                                      "detail": "the price is over the want's budget",
                                      "fix": "Ask 30000 cents or less."}]}


class _PassApi(_DoorApi):
    """The proposals door with the 4.1.65 decline answer (or an older one)."""

    def __init__(self, *answers, alternative=ASK, keys=True):
        super().__init__(*answers)
        self.alternative = alternative
        self.keys = keys
        self.declines: list[tuple[str, dict, str]] = []

    def submit_proposal(self, target_id, proposal, idempotency_key):
        if "decline" in proposal:
            self.declines.append((target_id, json.loads(json.dumps(proposal)),
                                  idempotency_key))
            answer = {"ok": True, "declined": True, "target_id": target_id,
                      "reason": proposal["decline"]}
            if self.keys:
                missing = list(proposal.get("missing_tools") or [])
                answer["missing_tools"] = missing
                answer["alternative_ask"] = dict(self.alternative) if (
                    missing and self.alternative) else None
            return answer
        return super().submit_proposal(target_id, proposal, idempotency_key)


def _run(model, api, **options):
    return DraftLoop(model, _door_provider(api)).run(
        "t-1", kind="bid", brief={"want": "Call my friend and wish him happy birthday"},
        idempotency_key="draft-bid-t-1-1", **options)


def _ask_text(model, index):
    return model.invocations[index]["messages"][0].content[0]["text"]


# -- the model-facing words ---------------------------------------------------
def test_the_pass_line_names_the_missing_tools_slot_in_the_bid_and_the_repair():
    for text in (PROPOSAL_INSTRUCTION, PROPOSAL_REPAIR_INSTRUCTION):
        assert '{"decline": "<one line: why>"}' in text
        assert '"missing_tools": [' in text
        assert "no tool on this want can do a part of it" in text
        assert "e.g. place a phone call" in text
        assert "up to 3; otherwise leave it out." in text
    model = _model({"decline": "no"})
    _run(model, _PassApi())
    assert '"missing_tools": [' in _ask_text(model, 0)


# -- the parse ---------------------------------------------------------------
def test_missing_tools_are_cleaned_capped_at_three_and_cut_at_120():
    assert missing_tools_of(PASS) == [PHONE]
    assert missing_tools_of({"decline": "x", "missing_tools": [
        "  place   a phone\ncall ", 7, None, {"a": 1}, "", "   ", "send a text",
        "x" * 300, "fourth", "fifth"]}) == ["place a phone call", "send a text", "x" * 120]
    assert missing_tools_of({"missing_tools": "ring a phone"}) == ["ring a phone"]
    assert missing_tools_of({"missing_tools": 5}) == []
    assert missing_tools_of({"decline": "x"}) == []
    assert missing_tools_of("not a dict") == []
    # decline_of is unchanged by the new slot
    assert decline_of(PASS) == "No tool on this want can ring a phone."
    assert decline_of(dict(SMALL, missing_tools=[PHONE])) is None


def test_the_benchs_ask_back_is_read_whole_and_absent_or_null_is_none():
    assert alternative_ask_of({"declined": True, "alternative_ask": ASK}) == ASK
    assert alternative_ask_of({"declined": True, "alternative_ask": None}) is None
    assert alternative_ask_of({"declined": True}) is None
    assert alternative_ask_of({"alternative_ask": {}}) is None
    assert alternative_ask_of(None) is None


# -- the provider ------------------------------------------------------------
def test_the_provider_sends_missing_tools_only_when_it_names_something():
    api = _PassApi()
    provider = _door_provider(api)
    answer = provider.decline_target("t-1", "why", missing_tools=[PHONE])
    provider.decline_target("t-2", "why", missing_tools=[])
    provider.decline_target("t-3", "why")
    provider.decline_target("t-4", "why", missing_tools=["  ", ""])
    bodies = [body for _t, body, _k in api.declines]
    assert bodies == [{"decline": "why", "missing_tools": [PHONE]}, {"decline": "why"},
                      {"decline": "why"}, {"decline": "why"}]
    # The bench's answer comes back whole, so the loop can read the ask back.
    assert answer["alternative_ask"] == ASK and answer["missing_tools"] == [PHONE]
    # The key for a plain pass is what it always was.
    plain = [key for target, _b, key in api.declines if target == "t-3"][0]
    assert plain == "decline-" + hashlib.sha256(b"t-3|why").hexdigest()[:24]


def test_the_loop_sends_the_slot_with_the_pass_and_never_an_empty_list():
    api = _PassApi(alternative=None)
    _run(_model(PASS), api)
    _run(_model({"decline": "Not my kind of want.", "missing_tools": []}), api)
    assert [body for _t, body, _k in api.declines] == [
        {"decline": PASS["decline"], "missing_tools": [PHONE]},
        {"decline": "Not my kind of want."}]


# -- the alternative, once ---------------------------------------------------
def test_an_ask_back_gets_exactly_one_more_turn_with_the_benchs_own_words():
    api = _PassApi()
    model = _model(PASS, {"decline": "An email is not a birthday call."})
    _run(model, api)

    assert len(model.invocations) == 2
    ask = _ask_text(model, 1)
    assert ALTERNATIVE_INSTRUCTION in ask
    assert "Answer the want below with a PROPOSAL" in ask  # the same shape asked
    assert ASK["ask"] in ask and ASK["how"] in ask
    assert '"tool_slugs":["gmail.message.send","platform.research"]' in ask
    assert '"your_pass":{"decline":"No tool on this want can ring a phone.",' in ask
    assert "Call my friend and wish him happy birthday" in ask
    # same prefix (system) as the first ask: the same context it drafted in
    assert model.invocations[1]["system"] == model.invocations[0]["system"]


def test_a_proposal_from_that_turn_takes_the_normal_filing_path():
    api = _PassApi({"ok": True, "problems": [], "trimmed": []})
    model = _model(PASS, ALTERNATIVE)
    out = _run(model, api)

    assert out["ok"] is True and out["filed"] is True and "declined" not in out
    assert out["proposal_id"] == "p-1"
    assert len(api.declines) == 1  # the pass went once
    assert len(api.asked) == 1  # the free validate door, as any bid
    [(target, filed, key)] = api.filed
    assert target == "t-1" and key == "draft-bid-t-1-1"
    assert filed["pitch_title"] == "A birthday email he will remember"
    assert filed["tools_needed"] == ["gmail.message.send"]
    assert out["declined_first"]["missing_tools"] == [PHONE]
    assert out["alternative"]["answer"] == "proposal" and out["alternative"]["ask"] == ASK


def test_the_alternative_gets_the_links_follow_up_like_any_bid():
    api = _PassApi({"ok": True, "problems": [], "trimmed": []})
    links = {"research_links": [{"url": "https://example.test/cards", "note": "Ideas."}]}
    model = _model(PASS, dict(ALTERNATIVE, research_links=[]), links)
    out = _run(model, api)

    assert out["filed"] is True and len(model.invocations) == 3
    assert api.filed[0][1]["research_links"] == links["research_links"]


def test_a_pass_from_that_turn_files_nothing_more():
    api = _PassApi()
    model = _model(PASS, {"decline": "An email is not a birthday call."})
    out = _run(model, api)

    assert out["ok"] is True and out["declined"] is True and out["filed"] is False
    assert out["reason"] == PASS["decline"]  # the first pass stands
    assert out["alternative"] == {"asked": True, "answer": "declined",
                                  "reason": "An email is not a birthday call."}
    assert len(api.declines) == 1 and api.filed == [] and api.asked == []
    assert out["model_calls"] == 2


def test_a_refused_alternative_behaves_as_a_refused_bid_and_never_loops():
    # The door refuses the alternative; the one repair ask passes with a missing
    # tool again. The first pass stands: no second pass sent, no second
    # alternative asked, nothing filed.
    api = _PassApi(REFUSED)
    model = _model(PASS, ALTERNATIVE, PASS)
    out = _run(model, api)

    assert len(model.invocations) == 3  # proposal, alternative, repair
    assert "the_bench_refused" in _ask_text(model, 2)
    assert len(api.declines) == 1 and api.filed == []
    assert out["declined"] is True and out["alternative"]["answer"] == "declined"


def test_a_refused_alternative_is_recorded_like_any_refused_bid():
    api = _PassApi(REFUSED, REFUSED)
    model = _model(PASS, ALTERNATIVE, dict(ALTERNATIVE, total_ask_cents=80_000))
    out = _run(model, api)

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] == "proposal_refused_at_validate"
    assert "declined" not in out and api.filed == []
    assert len(model.invocations) == 3


def test_a_pass_after_a_refused_bid_gets_its_one_alternative_too():
    api = _PassApi(REFUSED, {"ok": True, "problems": [], "trimmed": []})
    model = _model(dict(SMALL, total_ask_cents=90_000), PASS, ALTERNATIVE)
    out = _run(model, api)

    assert len(model.invocations) == 3  # bid, repair (a pass), alternative
    assert len(api.declines) == 1
    assert out["filed"] is True and api.filed[0][1]["pitch_title"] == ALTERNATIVE["pitch_title"]


def test_an_older_bench_without_the_keys_changes_nothing():
    api = _PassApi(keys=False)
    model = _model(PASS)
    out = _run(model, api)

    assert len(model.invocations) == 1
    assert out["declined"] is True and out["filed"] is False
    assert "alternative" not in out and "declined_locally" not in out
    assert out["response"] == {"ok": True, "declined": True, "target_id": "t-1",
                               "reason": PASS["decline"]}


def test_a_null_ask_back_changes_nothing():
    api = _PassApi(alternative=None)
    model = _model(PASS)
    out = _run(model, api)
    assert len(model.invocations) == 1 and "alternative" not in out
    assert out["response"]["alternative_ask"] is None


def test_a_dry_run_never_asks_the_alternative():
    api = _PassApi()
    model = _model(PASS)
    out = _run(model, api, file=False)
    assert len(model.invocations) == 1 and api.declines == []
    assert out["declined_locally"] is True


# -- the logs ------------------------------------------------------------------
def test_the_alternative_is_on_the_market_log_and_the_thinking_stream(caplog, monkeypatch):
    monkeypatch.delenv("TOLL_HARNESS_THINKING", raising=False)
    posts: list[tuple[str, dict]] = []
    model = _model(PASS, {"decline": "Still no."})
    model.thinking = ThinkingStream(lambda target, payload: posts.append((target, payload))
                                    or {"ok": True}, flush_seconds=60.0)
    with caplog.at_level(logging.INFO, logger="toll_harness"):
        _run(model, _PassApi())
    assert model.thinking.drain(5)
    lines = [record.getMessage() for record in caplog.records]
    assert any("flagged place a phone call for building" in line for line in lines)
    assert any("ask=proposal (alternative)" in line for line in lines)
    assert any("the first pass stands, nothing more filed" in line for line in lines)
    entries = [entry for target, payload in posts if target == "t-1"
               for entry in payload["entries"]]
    notes = [entry["text"] for entry in entries if entry["kind"] == "note"]
    assert any(note.startswith("after the pass: the bench flagged place a phone call")
               for note in notes)
    assert [entry["kind"] for entry in entries].count("model_call") == 2


@pytest.mark.parametrize("constant", ["MISSING_TOOLS_MAX", "MISSING_TOOL_CHARS"])
def test_the_caps_are_the_contracts(constant):
    assert {"MISSING_TOOLS_MAX": 3, "MISSING_TOOL_CHARS": 120}[constant] == getattr(
        draft, constant)
