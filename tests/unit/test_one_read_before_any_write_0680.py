"""ONE READ BEFORE ANY WRITE (0.68.0).

WHAT FORCED THESE TESTS. The plan path read the draft twice before it wrote
anything: the market watch's loop-guard probe read it to fingerprint the
work, then the draft loop read the very same door again before it opened the
draft (both 404 on a plan nobody had opened yet). The probe's answer now rides
into the loop, so the next draft call after the probe is the PUT that creates
the draft, and what happens after that is read off the door's own answers.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.plan_door import reply
from toll_harness import cli
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.local import SQLiteStore
from toll_harness.toll_bench.draft import DraftLoop

NO_DRAFT = {"ok": False, "error": "no_draft", "status": 404,
            "message": "there is no draft on this target."}

FORM = {
    "overview": "Three places that take a booking for four, offered to you.",
    "odds": 0.4,
    "span_days": 7,
    "steps": [
        {"verb": "finds", "do_line": "Find three places for four",
         "hand_over_line": "Three places that take a booking", "need_line": "",
         "declared_odds": 0.5, "proof": "text", "who": "agent"},
    ],
}


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [ModelResponse(message=ModelMessage.text("assistant", json.dumps(answer)),
                       text=json.dumps(answer), tool_calls=[]) for answer in answers]
    )


class Door:
    """A plan door that records every call, in order."""

    fleet = None

    def __init__(self, *, standing: dict | None = None):
        self.calls: list[str] = []
        self.standing = standing
        self.filed = None

    def read_draft(self, target_id, *, kind="bid"):
        self.calls.append("GET")
        return self.standing if self.standing is not None else dict(NO_DRAFT)

    def put_draft(self, target_id, outline, *, kind="bid"):
        self.calls.append("PUT")
        if not outline:
            # The empty open: the door asks for the form.
            return reply("outline_round")
        self.standing = reply("clean")
        return self.standing

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.calls.append("PATCH")
        return reply("clean")

    def file_plan_from_draft(self, target_id, proposal_id, idempotency_key):
        self.calls.append("FILE")
        self.filed = (target_id, proposal_id, idempotency_key)
        return {"ok": True, "plan_revised": True}


def test_the_cycles_own_404_goes_straight_to_the_put_that_creates_the_draft():
    door = Door()
    outcome = DraftLoop(_model(FORM), door).run(
        "t-1", kind="plan", proposal_id="p-1", idempotency_key="k",
        brief={"want": "A table for four"}, already_read=dict(NO_DRAFT),
    )
    assert outcome["filed"] is True
    # Created first, then read off the door's own answers: no GET at all.
    assert door.calls == ["PUT", "PUT", "FILE"]


def test_without_a_read_in_hand_the_loop_reads_once_and_then_creates():
    door = Door()
    outcome = DraftLoop(_model(FORM), door).run(
        "t-1", kind="plan", proposal_id="p-1", idempotency_key="k",
        brief={"want": "A table for four"},
    )
    assert outcome["filed"] is True
    assert door.calls == ["GET", "PUT", "PUT", "FILE"]


def test_a_standing_draft_handed_in_is_resumed_never_put_over():
    door = Door()
    outcome = DraftLoop(_model(), door).run(
        "t-1", kind="plan", proposal_id="p-1", idempotency_key="k",
        brief={"want": "A table for four"}, already_read=reply("clean"),
    )
    assert outcome["filed"] is True
    assert door.calls == ["FILE"]


def test_the_market_watch_reads_the_plan_draft_once_before_it_writes(tmp_path):
    """End to end through `_process_market_attention` with the loop guard on:
    the guard's probe is the one read, and the draft loop starts from it."""
    obligation = {"kind": "file_informed_plan", "target_id": "t-1", "proposal_id": "p-1"}
    door = Door()
    door.ensure_reachable = lambda: {"ok": True}
    door.attention = lambda wait=0: {"attention": [] if door.filed else [dict(obligation)]}
    door.list_proposals = lambda: {"proposals": [{"id": "p-1", "total_ask_cents": 0}]}
    door.read_brief = lambda target_id: {"brief": {"want": "A table for four"}}
    door.list_act_kinds = lambda: {}
    door.plan_door_speaks_one_language = lambda: True
    resources = SimpleNamespace(
        toll_bench=door,
        store=SQLiteStore(tmp_path / "harness.sqlite3"),
        agent_identity=None,
        runtime=SimpleNamespace(model=_model(FORM), enabled_tools=None,
                                email_provider=None),
    )

    result = cli._process_market_attention(resources, wait=0)

    assert result["ok"] is True and result["plan_filing_verified"] is True
    assert door.calls == ["GET", "PUT", "PUT", "FILE"]


def test_the_probe_keeps_what_it_read_only_when_asked():
    door = Door(standing=reply("clean"))
    resources = SimpleNamespace(toll_bench=door)
    obligation = {"kind": "file_informed_plan", "target_id": "t-9"}
    seen: dict = {}
    with_seen = cli._loop_state(resources, obligation, seen=seen)
    assert seen["t-9"]["ready"] is True
    # The fingerprint is the same whether or not the read is kept.
    assert cli._loop_state(resources, obligation) == with_seen


def test_an_operator_reply_turns_the_kept_read_stale(monkeypatch):
    """The reply door may stamp the operator's request on the draft, so the
    loop reads the draft fresh after an operator conversation."""
    seen = {}

    class Loop:
        def __init__(self, *args, **kwargs):
            self.calls = 0
            self.own_means = ()
            self.operator_instructions = ""
            self.target_id = ""

        def run(self, target_id, **kwargs):
            seen["already_read"] = kwargs.get("already_read")
            return {"ok": True, "filed": True}

    monkeypatch.setattr(cli, "DraftLoop", Loop)
    monkeypatch.setattr(cli, "_operator_dialogue_reply",
                        lambda *a, **k: {"ok": True, "revise_plan": True})
    door = Door()
    door.attention = lambda wait=0: {"attention": []}
    door.read_brief = lambda target_id: {"brief": {"want": "x"}}
    door.list_act_kinds = lambda: {}
    resources = SimpleNamespace(
        toll_bench=door, agent_identity=None,
        runtime=SimpleNamespace(model=object(), enabled_tools=None),
    )
    cli._file_the_informed_plan_from_draft(
        resources, {"kind": "file_informed_plan", "target_id": "t-1", "proposal_id": "p-1"},
        {"ok": True}, 1, 3, already_read=dict(NO_DRAFT),
    )
    assert seen["already_read"] is None
