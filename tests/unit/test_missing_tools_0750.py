"""THE MISSING TOOL ON A PASS (0.75.0, contract 4.1.65).

WHAT FORCED IT (Steven, 2026-11-05): Milo declined "Call my friend and wish
him happy birthday" with "I can't place phone calls, and none of the tools on
this want can ring a phone." The reason went nowhere. A pass for a missing tool
now names the tool so the bench can flag it for building. Nothing more is
asked after a pass: "since tollbench is a benchmark, we shouldnt be asking
them to do this. if they come up with a workaround it has to be of their own
accord." (Steven, the same day.)
"""
from __future__ import annotations

import hashlib
import json

import pytest

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_two_stages_r243_r245 import SMALL, _door_provider, _DoorApi
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.draft import (
    PROPOSAL_INSTRUCTION,
    PROPOSAL_REPAIR_INSTRUCTION,
    DraftLoop,
    decline_of,
    missing_tools_of,
)

PHONE = "place a phone call"
PASS = {"decline": "No tool on this want can ring a phone.", "missing_tools": [PHONE]}
REFUSED = {"ok": False, "problems": [{"code": "REJ-09", "field": "total_ask_cents",
                                      "detail": "the price is over the want's budget",
                                      "fix": "Ask 30000 cents or less."}]}
# What a bench before contract 4.1.66 put on the decline answer. It is never
# acted on.
ALTERNATIVE_ASK = {"ask": "Could you get this done another way?",
                   "tool_slugs": ["gmail.message.send"], "how": "File a proposal."}


class _PassApi(_DoorApi):
    """The proposals door with the 4.1.65 decline answer (or an older one)."""

    def __init__(self, *answers, keys=True, alternative_ask=None):
        super().__init__(*answers)
        self.keys = keys
        self.alternative_ask = alternative_ask
        self.declines: list[tuple[str, dict, str]] = []

    def submit_proposal(self, target_id, proposal, idempotency_key):
        if "decline" in proposal:
            self.declines.append((target_id, json.loads(json.dumps(proposal)),
                                  idempotency_key))
            answer = {"ok": True, "declined": True, "target_id": target_id,
                      "reason": proposal["decline"]}
            if self.keys:
                answer["missing_tools"] = list(proposal.get("missing_tools") or [])
            if self.alternative_ask is not None:
                answer["alternative_ask"] = dict(self.alternative_ask)
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


def test_no_model_facing_text_nudges_toward_a_workaround():
    # A benchmark measures what the agent does of its own accord.
    for text in (PROPOSAL_INSTRUCTION, PROPOSAL_REPAIR_INSTRUCTION, draft.MISSING_TOOLS_LINE):
        lowered = text.lower()
        for word in ("workaround", "alternative", "another way", "instead"):
            assert word not in lowered, word
    assert not hasattr(draft, "ALTERNATIVE_INSTRUCTION")


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


@pytest.mark.parametrize("constant", ["MISSING_TOOLS_MAX", "MISSING_TOOL_CHARS"])
def test_the_caps_are_the_contracts(constant):
    assert {"MISSING_TOOLS_MAX": 3, "MISSING_TOOL_CHARS": 120}[constant] == getattr(
        draft, constant)


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
    assert answer["missing_tools"] == [PHONE]
    # The key for a plain pass is what it always was.
    plain = [key for target, _b, key in api.declines if target == "t-3"][0]
    assert plain == "decline-" + hashlib.sha256(b"t-3|why").hexdigest()[:24]


# -- the loop ------------------------------------------------------------------
def test_the_loop_sends_the_slot_with_the_pass_and_never_an_empty_list():
    api = _PassApi()
    first = _run(_model(PASS), api)
    second = _run(_model({"decline": "Not my kind of want.", "missing_tools": []}), api)
    assert [body for _t, body, _k in api.declines] == [
        {"decline": PASS["decline"], "missing_tools": [PHONE]},
        {"decline": "Not my kind of want."}]
    assert first["declined"] is True and first["missing_tools"] == [PHONE]
    assert "missing_tools" not in second


def test_a_pass_on_the_repair_ask_carries_its_missing_tools_too():
    api = _PassApi(REFUSED)
    model = _model(dict(SMALL, total_ask_cents=90_000), PASS)
    out = _run(model, api)

    assert len(model.invocations) == 2  # the bid, then the one repair
    assert out["declined"] is True and out["filed"] is False and api.filed == []
    assert [body for _t, body, _k in api.declines] == [
        {"decline": PASS["decline"], "missing_tools": [PHONE]}]


def test_a_decline_answer_carrying_alternative_ask_triggers_no_further_turn():
    api = _PassApi(alternative_ask=ALTERNATIVE_ASK)
    model = _model(PASS, dict(SMALL, pitch_title="A birthday email"))
    out = _run(model, api)

    assert len(model.invocations) == 1  # the second scripted answer is never asked for
    assert out["declined"] is True and out["filed"] is False and out["model_calls"] == 1
    assert len(api.declines) == 1 and api.filed == [] and api.asked == []
    assert "alternative" not in out and "declined_first" not in out


def test_an_older_bench_without_the_keys_changes_nothing():
    api = _PassApi(keys=False)
    model = _model(PASS)
    out = _run(model, api)

    assert len(model.invocations) == 1
    assert out["declined"] is True and out["filed"] is False
    assert "declined_locally" not in out
    assert out["response"] == {"ok": True, "declined": True, "target_id": "t-1",
                               "reason": PASS["decline"]}


def test_a_dry_run_sends_nothing():
    api = _PassApi()
    out = _run(_model(PASS), api, file=False)
    assert api.declines == [] and out["declined_locally"] is True
