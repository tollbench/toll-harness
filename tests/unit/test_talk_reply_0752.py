"""A TALK MESSAGE GETS ITS REPLY (0.75.2).

WHAT FORCED THESE TESTS. 0.74.0 took the `budget` argument off
`DraftLoop._ask`, but the operator Talk reply still passed `budget=24000`.
Every Talk message an operator sent raised TypeError before the model was
asked; the loop guard parked the obligation after three tries, so the
operator never got an answer, and a message sent before the plan was filed
also kept the plan from ever being built (Sam on staging, 2026-11-09, bench
contract 4.1.68's direct build). The test that existed replaced
`_operator_dialogue_reply` with a stub, so nothing ran the real call. These
run the real reply through the real DraftLoop and a scripted model.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.plan_door import reply
from toll_harness import cli
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import DraftLoop

NO_DRAFT = {"ok": False, "error": "no_draft", "status": 404,
            "message": "there is no draft on this target."}

FORM = {
    "overview": "A short thank-you note for each dinner guest.",
    "odds": 0.6,
    "span_days": 2,
    "steps": [
        {"verb": "writes", "do_line": "Write a note for each guest",
         "hand_over_line": "One note per guest", "need_line": "",
         "declared_odds": 0.6, "proof": "text", "who": "agent"},
    ],
}

QUESTION = {"id": "m-1", "role": "operator", "text": "Which step do I do myself?"}


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [ModelResponse(message=ModelMessage.text("assistant", json.dumps(answer)),
                       text=json.dumps(answer), tool_calls=[]) for answer in answers]
    )


class Door:
    """A plan door and Talk reply door; the brief carries a pending message
    until the reply door answers it."""

    fleet = None

    def __init__(self):
        self.calls: list[str] = []
        self.standing = None
        self.filed = None
        self.replies: list[tuple] = []
        self.pending = True

    def read_brief(self, target_id):
        dialogue = {"messages": [QUESTION],
                    "pending": {"id": "m-1", "mode": "message"} if self.pending else None}
        return {"brief": {"want": "Thank-you notes", "operator_dialogue": dialogue}}

    def operator_reply(self, target_id, message_id, text, revise_plan):
        self.calls.append("REPLY")
        self.replies.append((target_id, message_id, text, revise_plan))
        self.pending = False
        return {"ok": True}

    def read_draft(self, target_id, *, kind="bid"):
        self.calls.append("GET")
        return self.standing if self.standing is not None else dict(NO_DRAFT)

    def put_draft(self, target_id, outline, *, kind="bid"):
        self.calls.append("PUT")
        if not outline:
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

    def attention(self, wait=0):
        return {"attention": []}

    def list_act_kinds(self):
        return {}

    def plan_door_speaks_one_language(self):
        return True


def _resources(door, *answers):
    return SimpleNamespace(
        toll_bench=door, agent_identity=None,
        runtime=SimpleNamespace(model=_model(*answers), enabled_tools=None,
                                email_provider=None),
    )


OBLIGATION = {"kind": "file_informed_plan", "target_id": "t-1", "proposal_id": "p-1"}


def test_a_talk_message_gets_a_reply_through_the_real_loop():
    door = Door()
    loop = DraftLoop(_model({"reply": "Step 2 is yours: send the notes.",
                             "revise_plan": False}), door)
    out = cli._operator_dialogue_reply(SimpleNamespace(toll_bench=door), loop, "t-1",
                                       door.read_brief("t-1")["brief"],
                                       already_read=reply("clean"))
    assert out == {"ok": True, "revise_plan": False}
    assert door.replies == [("t-1", "m-1", "Step 2 is yours: send the notes.", False)]


def test_a_talk_message_before_the_plan_is_answered_then_the_plan_is_built():
    door = Door()
    resources = _resources(door, {"reply": "I will build it with two takes.",
                                  "revise_plan": True}, FORM)
    out = cli._file_the_informed_plan_from_draft(resources, dict(OBLIGATION), {"ok": True}, 1,
                                                 already_read=dict(NO_DRAFT))
    assert out["ok"] is True and out["plan_filing_verified"] is True, out
    assert door.calls[0] == "REPLY" and door.calls[-1] == "FILE"
    assert door.filed[:2] == ("t-1", "p-1")


def test_a_question_before_the_plan_does_not_block_the_next_cycles_build():
    door = Door()
    first = cli._file_the_informed_plan_from_draft(
        _resources(door, {"reply": "Step 2 is yours.", "revise_plan": False}),
        dict(OBLIGATION), {"ok": True}, 1, already_read=dict(NO_DRAFT))
    assert first["ok"] is True and first["dispatch"]["kind"] == "operator_dialogue", first
    assert door.calls == ["REPLY"] and door.filed is None
    second = cli._file_the_informed_plan_from_draft(
        _resources(door, FORM), dict(OBLIGATION), {"ok": True}, 1,
        already_read=dict(NO_DRAFT))
    assert second["ok"] is True and second["plan_filing_verified"] is True, second
    assert door.calls[-1] == "FILE" and door.filed[:2] == ("t-1", "p-1")
