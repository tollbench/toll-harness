"""ONE LANGUAGE AT THE PLAN DOOR (bench contract 4.0, Steven Ochs, 2026-09-17).

    "The form says what goes in. It never lists what is wrong."

WHAT FORCED IT. On 17 September a fleet agent fixed 21 of 24 plan problems in
three minutes and died on the last three, all on one email step: the same
missing subject came back under two codes, with a paragraph of advice, and one
of the fields was something the platform could have filled itself. Three tries
per problem is what ended that plan.

The bench REPLACED the plan door's reply rather than extending it, so this
harness reads the new one and only the new one. What is under test here:

  * the model is shown what GOES IN the step -- the door's own slot table --
    and the bench's sentences, and never a legacy code;
  * `who_fixes` is honoured: a platform row costs no model call and no try, a
    person row is a wait, and neither spins;
  * a key the form has no room for is taken off with no model call at all;
  * `email.send` is the one name the harness teaches for sending mail;
  * the brake is the bench's own key, so one fault under three codes is one
    fault here too.

Every reply below is REAL, captured off the bench's own code (tests/fixtures).
"""
from __future__ import annotations

import json
import logging

from tests.unit.plan_door import Door, a_row, holes_written, only, reply, row
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    EMAIL_SEND,
    FRONT_DOOR,
    DraftLoop,
    fix_key,
    fixed_by,
    form_steps_of,
    problems_of,
    step_entry,
)


def _says(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[]
    )


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [_says(json.dumps(a) if not isinstance(a, str) else a) for a in answers]
    )


def _said(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


# ---------------------------------------------------------------------------
# 1. THE SHAPE, as the bench actually answers it
# ---------------------------------------------------------------------------
def test_every_captured_reply_always_carries_both_lists():
    """ALWAYS PRESENT, INCLUDING EMPTY. An empty list and no visibility must
    be tellable apart, on the outline round and on a finished plan alike."""
    for name in ("outline_round", "clean", "email_step", "unknown_fields",
                 "platform_only", "edit_refused"):
        answer = reply(name)
        assert isinstance(answer["problems"], list), name
        assert isinstance(answer["form_steps"], list), name


def test_every_row_is_the_one_shape():
    """Eight keys always, `key` always, and two the bench adds when it has
    something more to say: `keys` on an unknown-key row off a tool argument,
    `call` on a row the door hands a whole call for."""
    always = {"step", "slot", "problem", "who_fixes", "say", "path", "accepted",
              "codes", "key"}
    spare = {"keys", "call"}
    for name in ("email_step", "unknown_fields", "two_unknown_one_step",
                 "platform_only", "edit_refused"):
        answer = reply(name)
        for entry in problems_of(answer):
            assert always <= set(entry), (name, entry)
            assert not set(entry) - always - spare, (name, entry)
            assert entry["who_fixes"] in ("agent", "person", "platform")
            assert entry["say"] and entry["key"]


def test_a_clean_plan_has_nothing_to_start_on():
    answer = reply("clean")
    assert answer["problems"] == []
    assert answer["next_fix"] is None
    assert answer["ready"] is True


def test_the_old_fields_are_gone_from_the_reply():
    """One language, not two. A harness that still read `code` or `question`
    off a plan row would be reading a key the bench stopped sending."""
    answer = reply("email_step")
    for entry in problems_of(answer) + [answer["next_fix"]]:
        for gone in ("code", "detail", "question", "fix", "step_index", "field"):
            assert gone not in entry, gone


# ---------------------------------------------------------------------------
# 2. WHAT THE MODEL IS SHOWN IS WHAT GOES IN
# ---------------------------------------------------------------------------
def test_the_fix_ask_hands_over_the_steps_own_slot_table():
    answer = holes_written(
        only(reply("email_step"), row(reply("email_step"), codes=["forecast"])))
    answer["form_steps"][0]["person_slot"] = "who"
    door = Door({"ok": True, "ready": True})
    model = _model({"form": dict(answer["form"], overview="A new overview")})

    DraftLoop(model, door)._fix_every_red_row("t-1", "plan", answer, "Introduce me")

    ask = _said(model)
    # The whole-form road (0.74.0, the only plan road) hands over the step's
    # own slot table under `what_goes_in`, in the door's own words.
    assert '"what_goes_in"' in ask
    for word in ('"name":"subject"', '"filler":"agent"', '"state":"needed"',
                 '"kind":"contact"', '"filler":"platform"',
                 '"source":"person.who"'):
        assert word in ask, word
    # Every red row, with the door's path and sentence.
    assert '"red"' in ask and '"say"' in ask


def test_no_legacy_code_ever_reaches_the_model():
    answer = holes_written(
        only(reply("email_step"), row(reply("email_step"), codes=["forecast"])))
    answer["form_steps"][0]["person_slot"] = "who"
    door = Door({"ok": True, "ready": True})
    model = _model({"form": dict(answer["form"], overview="A new overview")})

    DraftLoop(model, door)._fix_every_red_row("t-1", "plan", answer, "x")

    ask = _said(model)
    assert "REJ-" not in ask
    assert '"codes"' not in ask
    # The door's own sentence rides unchanged, code name in it or not; what
    # never rides is the `codes` list itself.
    assert answer["next_fix"]["say"] in ask


def test_the_codes_ride_the_log_line_instead():
    """A person looks a fault up in the run log, not in a prompt."""
    answer = only(reply("email_step"), row(reply("email_step"), codes=["forecast"]))
    loop = DraftLoop(None, None)
    loop._record("t-1", "plan", answer, "fix")

    line = loop.trail[-1]
    assert line["codes"] == ["forecast"]
    assert line["problem"] == fix_key(answer["next_fix"])
    assert line["rows"] == {"agent": 1, "person": 0, "platform": 0}


# ---------------------------------------------------------------------------
# 3. WHO FIXES IT IS HONOURED
# ---------------------------------------------------------------------------
def test_a_platform_only_draft_spends_no_model_call_and_shouts(caplog):
    answer = reply("platform_only")
    assert [r["who_fixes"] for r in problems_of(answer)] == ["platform"]
    assert answer["next_fix"] is None
    door = Door()
    model = _model()  # nothing scripted: the model must not be asked at all

    with caplog.at_level(logging.ERROR, logger="toll_harness.draft"):
        out = DraftLoop(model, door)._fix_every_red_row("t-1", "plan", answer, "x")

    assert out["error"] == "bench_must_fix"
    assert model.invocations == []
    assert door.puts == [] and door.patches == []
    # And it shouts on our side, with what a person needs to find it.
    assert "THE BENCH MUST FIX THIS" in caplog.text
    assert "REJ-22" in caplog.text


def test_a_person_only_draft_is_a_wait_and_not_a_failure():
    waiting = a_row("form.steps.0", step=1, slot="who", problem="empty",
                    who_fixes="person", say="They have not picked anybody yet.")
    answer = only(reply("clean"), waiting)
    door = Door()
    model = _model()

    out = DraftLoop(model, door)._fix_every_red_row("t-1", "plan", answer, "x")

    assert out["error"] == "waiting_on_the_person"
    assert model.invocations == []
    assert door.puts == [] and door.patches == []


def test_a_whole_run_on_our_own_bug_files_nothing_and_names_it():
    """End to end, against the real reply: nothing is filed, nothing is asked,
    and the run says whose bug it is."""

    class Bench(Door):
        def read_draft(self, target_id, *, kind="bid"):
            return reply("platform_only")

        def put_draft(self, target_id, outline, *, kind="bid"):
            return reply("platform_only")

        def file_plan_from_draft(self, *args, **kwargs):
            raise AssertionError("a plan with our bug on it must not file")

    model = _model()
    out = DraftLoop(model, Bench()).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "x"},
        idempotency_key="k")

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] == "bench_must_fix"
    assert model.invocations == []


def test_a_whole_run_on_a_clean_plan_still_files():
    class Bench(Door):
        def read_draft(self, target_id, *, kind="bid"):
            return reply("clean")

        def file_plan_from_draft(self, target_id, proposal_id, key=""):
            return {"ok": True, "proposal_id": proposal_id}

    out = DraftLoop(_model(), Bench()).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "x"},
        idempotency_key="k")

    assert out["ok"] is True and out["filed"] is True


def test_both_stops_park_the_want_for_this_round_and_not_for_ever():
    from toll_harness import cli

    for error in ("bench_must_fix", "waiting_on_the_person"):
        assert error in cli._TERMINAL_DOOR_ERRORS
    # The memo is keyed on the ROUND, so a repost asks again.
    cli._CLOSED_TARGET_MEMO.clear()
    target = {"target_id": "t", "round": 2}
    assert cli._remember_closed(target, "bench_must_fix")
    assert cli._door_closed_this_round(target)
    assert not cli._door_closed_this_round(dict(target, round=3))
    cli._CLOSED_TARGET_MEMO.clear()


def test_an_agent_row_beside_our_bug_is_still_the_agents_to_fix():
    """A platform row never stops work that IS the agent's."""
    ours = row(reply("platform_only"), who_fixes="platform")
    mine = row(reply("unknown_fields"), path="form.steps.1.declared_odds")
    answer = only(reply("unknown_fields"), ours, mine)
    door = Door({"ok": True, "ready": True})
    form = dict(answer["form"], overview="The agent's own fix")
    model = _model({"form": form})

    DraftLoop(model, door)._fix_every_red_row("t-1", "plan", answer, "x")

    assert len(model.invocations) == 1
    assert len(door.puts) == 1


# ---------------------------------------------------------------------------
# 5. SLOTS BEFORE WORDS
# ---------------------------------------------------------------------------
def test_the_slot_table_names_the_neutral_action():
    entry = step_entry(reply("email_step"), 2)
    assert entry["action"] == EMAIL_SEND == "email.send"


def test_the_front_door_teaches_the_neutral_name_and_not_a_provider():
    assert EMAIL_SEND in FRONT_DOOR
    assert "gmail.message.send" not in FRONT_DOOR


def test_a_plain_step_says_it_has_no_slots():
    entry = step_entry(reply("clean"), 1)
    assert entry["slots"] == []
    assert entry["person_slot"] is None


# ---------------------------------------------------------------------------
# 6. ONE FAULT, ONE KEY
# ---------------------------------------------------------------------------
def test_one_fault_under_three_codes_is_one_key():
    base = {"step": 5, "slot": "subject", "problem": "empty",
            "who_fixes": "agent", "say": "the subject is empty",
            "path": "form.steps.4.tool.args.subject", "accepted": []}
    assert fix_key(dict(base, codes=["REJ-41"])) == "5|subject|empty"
    assert fix_key(dict(base, codes=["REJ-41", "REJ-33", "tool_input"])) == (
        "5|subject|empty"
    )


def test_two_faults_on_one_step_with_no_slot_are_counted_apart():
    base = {"step": 3, "slot": None, "problem": "not_allowed",
            "who_fixes": "agent", "say": "no", "path": "form.steps.2",
            "accepted": []}
    assert fix_key(dict(base, codes=["REJ-17"])) != fix_key(dict(base, codes=["REJ-19"]))


def test_the_readers_never_raise_on_a_door_that_sends_nothing():
    for empty in ({}, {"problems": None}, "not a dict", None):
        assert problems_of(empty) == []
        assert form_steps_of(empty) == []
        assert fixed_by(problems_of(empty), "agent") == []
    assert step_entry({}, 1) is None
    assert step_entry(reply("clean"), None) is None
