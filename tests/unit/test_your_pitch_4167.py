"""THE PITCH THE PERSON PICKED RIDES THE PLAN ASKS (bench contract 4.1.67).

Steven 2026-11-06: "make sure the original bid gets passed back to the agent
when they are planning so they are less likely to change it willy nilly."

WHAT FORCED IT. On want 9b2fc180 Sam pitched "I can't place the call, but I
make sure you do", was picked, and 40 s later wrote a plan in which Sam placed
the call himself. The form ask carried `your_proposal` (the bid's steps, empty
since rule 243) and never the pitch's words.

Held here: the bench's `your_pitch` words ride the form ask and every fix
round, the bench's own sentence hangs under the instruction, and nothing
changes when the bench sends no pitch.
"""
from __future__ import annotations

import json

from tests.unit.plan_door import Door, holes_written, only, reply, row
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    FORM_INSTRUCTION,
    YOUR_PITCH_KEY,
    DraftLoop,
    with_your_pitch,
)

NOTE = ("This is the pitch the person picked you for. Your plan carries it out: "
        "keep its way of doing it, who does each key part, and everything it "
        "promised. Fill in the details it left open.")
PITCH = {"pitch_title": "I can't place the call, but I make sure you do",
         "headline": "Make the call happen",
         "pitch_body": "A calendar reminder, talking points and a backup text.",
         "note": NOTE}


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter([
        ModelResponse(message=ModelMessage.text("assistant", json.dumps(a)),
                      text=json.dumps(a), tool_calls=[])
        for a in answers
    ])


def _said(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


class _FormDoor(Door):
    def put_draft(self, target_id, outline, *, kind="plan"):
        return {"ok": True, "ready": True}


def test_no_pitch_changes_nothing():
    for answer in ({}, {YOUR_PITCH_KEY: None}, {YOUR_PITCH_KEY: "x"},
                   {YOUR_PITCH_KEY: {"note": NOTE}}, None):
        payload: dict = {}
        assert with_your_pitch("ask", payload, answer) == "ask"
        assert YOUR_PITCH_KEY not in payload


def test_the_words_go_in_the_payload_and_the_note_under_the_ask():
    payload: dict = {}
    out = with_your_pitch("ask", payload, {YOUR_PITCH_KEY: dict(PITCH)})
    assert payload[YOUR_PITCH_KEY] == {k: PITCH[k] for k in
                                       ("pitch_title", "headline", "pitch_body")}
    assert out == "ask\n`your_pitch`: " + NOTE
    # A slot the agent left empty is not sent as an empty string.
    payload = {}
    with_your_pitch("ask", payload, {YOUR_PITCH_KEY: dict(PITCH, headline=None)})
    assert "headline" not in payload[YOUR_PITCH_KEY]


def test_the_form_ask_the_model_reads_carries_the_pitch():
    answer = reply("outline_round")
    answer[YOUR_PITCH_KEY] = dict(PITCH)
    model = _model({"overview": "x", "odds": 0.4, "span_days": 7, "steps": [
        {"verb": "calendar", "who": "agent", "do_line": "Put the call on your calendar",
         "hand_over_line": "A reminder", "need_line": "", "declared_odds": 0.5,
         "proof": "text"}]})
    DraftLoop(model, _FormDoor())._fill_the_form(
        "t-1", "plan", answer, "Call my friend and wish him happy birthday", None, {})
    asked = _said(model)
    assert "I can't place the call, but I make sure you do" in asked
    assert "A calendar reminder, talking points and a backup text." in asked
    assert NOTE in asked
    # The prefix instruction itself is untouched: the cache still hits.
    assert NOTE not in FORM_INSTRUCTION


def test_every_fix_round_carries_the_pitch_too():
    answer = holes_written(
        only(reply("email_step"), row(reply("email_step"), codes=["forecast"])))
    answer["form_steps"][0]["person_slot"] = "who"
    answer[YOUR_PITCH_KEY] = dict(PITCH)
    model = _model({"form": dict(answer["form"], overview="A new overview")})
    DraftLoop(model, Door({"ok": True, "ready": True}))._fix_every_red_row(
        "t-1", "plan", answer, "Call my friend")
    asked = _said(model)
    assert '"your_pitch"' in asked
    assert "I can't place the call, but I make sure you do" in asked
    assert NOTE in asked
