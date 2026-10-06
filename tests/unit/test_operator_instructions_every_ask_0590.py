"""THE OPERATOR'S STANDING INSTRUCTIONS RIDE EVERY MODEL CALL, IN FULL (0.59.0).

WHAT FORCED THESE TESTS (staging, 2026-10-05). Lab agent DJ 4-Fun's operator
instructions (about 5,100 characters) say how to write lyrics and a sound line,
and the lyrics it filed on a step were bland and the style line thin. The
instructions reached the proposal ask as a 600-character profile line and
nothing else: the plan form and the step ask (every move) started from the
bench's front door alone. Steven, 2026-10-06: "I dont want the harness to
hobble, we want to make sure the agents have full capabilites."

So: one labeled block, the WHOLE text, beside the system text of the proposal
ask, the plan form (and every other draft-loop ask) and every step-ask move. No
cap, no floor, never trimmed, never shed, never the reason a "tail ran to N
characters" warning fires. The step ask's shedding never touches the person's
own material (their messages, their send-back reasons, what they handed over).
With no instructions, every ask is byte for byte what it was.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from tests.unit.test_outside_act_w29 import OutsideBench, _outside
from tests.unit.test_step_ask import BRIEF as STEP_BRIEF
from tests.unit.test_step_ask import OBLIGATION, FakeBench, _payload
from tests.unit.test_step_ask import _model as _step_model
from tests.unit.test_two_stages_r243_r245 import (
    FILLED_STEP,
    PROPOSAL,
    FormBench,
    _FilingBench,
    _model,
)
from toll_harness import cli
from toll_harness.core.operator_block import (
    OPERATOR_BLOCK_HEADER,
    operator_block,
    with_operator_block,
)
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.step import PROMPT_CHAR_BUDGET, StepAsk

# Longer than any cap the old profile line or a prompt budget ever had, on
# purpose: the whole of it must ride.
SKILL = "Write the lyrics in the person's own words, with their names. " * 330
REPLY = {"call": "reply_step_message", "reply": "On it."}


def _system(model, index=0):
    return model.invocations[index]["system"]


def _message(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


# ---------------------------------------------------------------------------
# 1. The block
# ---------------------------------------------------------------------------
def test_the_block_is_the_header_and_the_whole_text():
    assert len(SKILL) > 20_000
    block = operator_block(SKILL)

    assert block == OPERATOR_BLOCK_HEADER + "\n" + SKILL.strip()
    assert OPERATOR_BLOCK_HEADER.startswith("Your operator's standing instructions")
    assert with_operator_block("RULES", SKILL) == "RULES\n\n" + block


def test_no_instructions_is_no_block_and_the_system_unchanged():
    for nothing in (None, "", "   \n", 7):
        assert operator_block(nothing) == ""
        system = "RULES"
        assert with_operator_block(system, nothing) is system


# ---------------------------------------------------------------------------
# 2. The proposal ask, the plan form and every draft-loop ask
# ---------------------------------------------------------------------------
def _bid(instructions=None):
    model = _model(PROPOSAL)
    loop = draft.DraftLoop(model, _FilingBench())
    if instructions is not None:
        loop.operator_instructions = instructions
    loop.run("t-1", kind="bid", brief={"want": "A song for my wife"}, idempotency_key="k")
    return model


def test_the_proposal_ask_carries_the_whole_block_beside_the_system_text():
    model = _bid(SKILL)
    bare = _bid()

    assert _system(model) == _system(bare) + "\n\n" + operator_block(SKILL)
    # The ask itself (the tail) is untouched: the block never displaced it.
    assert _message(model) == _message(bare)


def test_the_proposal_ask_is_byte_for_byte_unchanged_without_instructions():
    bare, empty = _bid(), _bid("")
    assert bare.invocations == empty.invocations
    assert OPERATOR_BLOCK_HEADER not in _system(bare)


def _plan(instructions=None):
    model = _model({"steps": [dict(FILLED_STEP)] * 3, "span_days": 14})
    loop = draft.DraftLoop(model, FormBench())
    if instructions is not None:
        loop.operator_instructions = instructions
    out = loop.run("t-1", kind="plan", proposal_id="p-9",
                   brief={"want": "A song for my wife"}, idempotency_key="k")
    assert out["ok"] is True, out
    return model


def test_every_plan_form_ask_carries_the_whole_block():
    model, bare = _plan(SKILL), _plan()

    assert len(model.invocations) == len(bare.invocations) >= 1
    for index in range(len(model.invocations)):
        assert _system(model, index) == _system(bare, index) + "\n\n" + operator_block(SKILL)
        assert _message(model, index) == _message(bare, index)


def test_the_plan_form_ask_is_byte_for_byte_unchanged_without_instructions():
    assert _plan().invocations == _plan("").invocations


def test_a_long_block_never_makes_a_draft_ask_shed_or_warn(caplog):
    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        _plan(SKILL)
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "tail ran to" not in said and "dropped `" not in said


# ---------------------------------------------------------------------------
# 3. Every step-ask move
# ---------------------------------------------------------------------------
def _step(payload, instructions=None, answer=REPLY, bench=None):
    model = _step_model(answer)
    ask = StepAsk(model, bench or FakeBench())
    if instructions is not None:
        ask.operator_instructions = instructions
    ask.run(OBLIGATION, payload, brief=STEP_BRIEF)
    return model


SPOKE = dict(step_thread={
    "messages": [{"id": "m1", "who": "person", "text": "Make the chorus about her laugh."}],
    "unread_from_person": 1,
    "unanswered_elsewhere": [],
})
EVIDENCE = {"call": "file_outside_evidence", "summary": "Recorded it."}


def test_every_move_carries_the_whole_block():
    for payload, answer, bench in (
        (_payload(), REPLY, None),                    # hand_back
        (_payload(**SPOKE), REPLY, None),             # answer_person
        (_outside(), EVIDENCE, OutsideBench()),       # outside_work
    ):
        model = _step(payload, SKILL, answer, bench)
        bare = _step(payload, None, answer, bench and OutsideBench())
        assert _system(model) == _system(bare) + "\n\n" + operator_block(SKILL)
        assert _message(model) == _message(bare)


def test_a_step_ask_is_byte_for_byte_unchanged_without_instructions():
    for payload in (_payload(), _payload(**SPOKE)):
        assert _step(payload).invocations == _step(payload, "").invocations


def test_a_long_block_never_makes_the_step_ask_shed_or_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="toll_harness.step"):
        model = _step(_payload(), SKILL)
    assert len(_message(model)) <= PROMPT_CHAR_BUDGET
    assert not [r for r in caplog.records if "tail ran to" in r.getMessage()]


# ---------------------------------------------------------------------------
# 4. The step ask sheds machinery, never the person's own material
# ---------------------------------------------------------------------------
def test_the_persons_messages_reasons_and_materials_are_never_shed(caplog):
    said = [f"Message {i}: " + "her laugh, the tennis, the snacks. " * 16 for i in range(6)]
    payload = _payload(
        step_thread={
            "messages": [{"id": f"m{i}", "who": "person", "text": text}
                         for i, text in enumerate(said)],
            "unread_from_person": 0,
            "unanswered_elsewhere": [],
        },
        drafts_sent_back=[{"approval_id": "ap-1",
                           "sent_back_reason": "Warmer, and say her name in the chorus."}],
        released_materials=[{"name": "photo-of-us.jpg", "url": "https://example.org/p.jpg"}],
        acts=[{"act_id": f"a{i}", "kind": "email", "state": "sent", "subject": f"S{i}",
               "words": "machine words " * 50} for i in range(4)],
        inbound_replies=[{"id": f"r{i}", "body": "out of office " * 50} for i in range(2)],
    )

    with caplog.at_level(logging.WARNING, logger="toll_harness.step"):
        text = _message(_step(payload, SKILL))

    for message in said:
        assert message[:40] in text
    assert "Warmer, and say her name in the chorus." in text
    assert "photo-of-us.jpg" in text
    # The machinery went first: one act left, no inbound replies.
    assert text.count('"act_id":"a') == 1 and '"inbound_replies":[]' in text


# ---------------------------------------------------------------------------
# 5. The cli hands the whole text on; the profile line stays
# ---------------------------------------------------------------------------
def test_the_cli_hands_the_whole_text_to_every_asker(monkeypatch):
    seen = []

    class Loop:
        def __init__(self, model, provider):
            pass

        def run(self, target_id, **kwargs):
            seen.append((kwargs.get("kind"), self.operator_instructions))
            return {"ok": False, "error": "not_found", "status": 404}

    class Ask:
        def __init__(self, model, provider):
            pass

        def run(self, obligation, payload, **kwargs):
            seen.append(("step", self.operator_instructions))
            return {"ok": False, "road": "agentic", "why": "test"}

    monkeypatch.setattr(cli, "DraftLoop", Loop)
    monkeypatch.setattr(cli, "StepAsk", Ask)
    monkeypatch.setattr(cli, "_brief_for_the_loop", lambda *_a, **_k: {"want": "x"})
    monkeypatch.setattr(cli, "_act_kinds_for_the_loop", lambda _r: None)
    monkeypatch.setattr(cli, "_lab_lead_direction", lambda _r: "")
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})
    monkeypatch.setattr(cli, "_STEP_REFUSALS", {})
    resources = SimpleNamespace(
        runtime=SimpleNamespace(
            model=None,
            enabled_tools=["toll_bench.submit_informed_plan"],
            operator_instructions=SKILL,
        ),
        toll_bench=SimpleNamespace(fleet=None),
        agent_identity=None,
        approach_default=None,
        store=None,
    )

    cli._bid_through_the_draft_loop(resources, {"target_id": "t-1"}, {}, 1, [], True)
    cli._file_the_informed_plan_from_draft(
        resources, {"target_id": "t-1", "proposal_id": "p-1"}, {"ok": True}, 1, 5)
    cli._the_step_ask(
        resources, {"kind": "deal_step", "deal_id": "d-1", "step_id": "s-1"},
        {"current_step": {"id": "s-1", "number": 2}, "deal": {"id": "d-1"}},
        {"ok": True}, 1, 5)

    assert seen == [("bid", SKILL), ("plan", SKILL), ("step", SKILL)]


def test_no_instructions_hands_on_an_empty_string():
    asker = SimpleNamespace()
    cli._hand_on_the_operator_instructions(SimpleNamespace(runtime=SimpleNamespace()), asker)
    assert asker.operator_instructions == ""
    cli._hand_on_the_operator_instructions(SimpleNamespace(), asker)
    assert asker.operator_instructions == ""


def test_the_compact_profile_line_still_rides_the_proposal():
    resources = SimpleNamespace(
        runtime=SimpleNamespace(enabled_tools=[], operator_instructions=SKILL),
        agent_identity=None,
    )
    profile = cli._agent_profile(resources)
    assert profile["operator_instructions"] == SKILL.strip()[: cli.PROFILE_INSTRUCTIONS_MAX]
