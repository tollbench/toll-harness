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

import copy
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
    keys_to_take_off,
    problems_of,
    says_on_step,
    step_entry,
    whole_step_path,
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
    # The plan already has a who step, so nothing is inserted before the ask,
    # and its holes are written, so the fix ask is the first ask.
    answer["form_steps"][0]["person_slot"] = "who"
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.1.declared_odds", "value": 0.7}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "Introduce me")

    ask = _said(model)
    assert '"this_step"' in ask
    # Every part of a slot the door published, in the door's own words.
    for word in ('"name":"subject"', '"filler":"agent"', '"state":"needed"',
                 '"kind":"contact"', '"filler":"platform"',
                 '"source":"person.who"'):
        assert word in ask, word
    # The path to patch, and the sentence, under one heading.
    assert '"change_this"' in ask
    assert '"what_the_bench_says"' in ask


def test_no_legacy_code_ever_reaches_the_model():
    answer = holes_written(
        only(reply("email_step"), row(reply("email_step"), codes=["forecast"])))
    answer["form_steps"][0]["person_slot"] = "who"
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.1.declared_odds", "value": 0.7}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

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


def test_the_sentences_of_one_step_are_read_off_the_rows():
    answer = reply("email_step")
    said = says_on_step(problems_of(answer), 2)
    assert len(said) == len({s for s in said})
    assert all(isinstance(s, str) and s for s in said)
    assert says_on_step(problems_of(answer), 1) == []


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
        out = DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert out["error"] == "bench_must_fix"
    assert model.invocations == []
    assert door.patches == [] and door.drops == [] and door.inserts == []
    # And it shouts on our side, with what a person needs to find it.
    assert "THE BENCH MUST FIX THIS" in caplog.text
    assert "REJ-22" in caplog.text


def test_a_person_only_draft_is_a_wait_and_not_a_failure():
    waiting = a_row("form.steps.0", step=1, slot="who", problem="empty",
                    who_fixes="person", say="They have not picked anybody yet.")
    answer = only(reply("clean"), waiting)
    door = Door()
    model = _model()

    out = DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert out["error"] == "waiting_on_the_person"
    assert model.invocations == []
    assert door.patches == []


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
    model = _model({"patches": [{"path": mine["path"], "value": 0.7}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert len(model.invocations) == 1
    assert door.patches == [[{"path": mine["path"], "value": 0.7}]]


# ---------------------------------------------------------------------------
# 4. A KEY THE FORM HAS NO ROOM FOR IS TAKEN OFF
# ---------------------------------------------------------------------------
def test_an_unknown_step_field_is_removed_with_no_model_call():
    answer = reply("unknown_fields")
    unknown = row(answer, problem="unknown_field", slot="step")
    door = Door({"ok": True, "ready": True})
    model = _model()

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", only(answer, unknown),
                                             "x")

    assert model.invocations == []
    assert len(door.patches) == 1
    sent = door.patches[0][0]
    assert sent["path"] == "form.steps.0"
    assert "room_number" not in sent["value"]
    # And nothing else on the step moved.
    assert sent["value"]["do_line"] == answer["form"]["steps"][0]["do_line"]


def test_an_unknown_key_inside_a_wait_condition_is_removed_in_place():
    """Marcia's step 6 wrote `observation` and `condition` inside a wait
    condition. The row names the CONTAINER on `slot` and the nearest patchable
    path on `path`, so the fix lands on the wait, not on the whole step."""
    answer = reply("unknown_fields")
    unknown = row(answer, problem="unknown_field", slot="wait.conditions.0")
    door = Door({"ok": True, "ready": True})
    model = _model()

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", only(answer, unknown),
                                             "x")

    assert model.invocations == []
    sent = door.patches[0][0]
    assert sent["path"] == "form.steps.1.wait"
    condition = sent["value"]["conditions"][0]
    assert "observation" not in condition and "condition" not in condition
    # What the form DOES read is still there, and so is the rest of the wait.
    assert condition["source"] == "email_reply"
    assert sent["value"]["days"] == 5


def test_the_remover_reads_the_rows_own_accepted_list():
    answer = reply("unknown_fields")
    form = answer["form"]
    keys, path, value = keys_to_take_off([row(answer, slot="step")], form)
    assert keys == ["room_number"]
    assert path == "form.steps.0"
    assert "room_number" not in value
    keys, path, value = keys_to_take_off(
        [row(answer, slot="wait.conditions.0")], form)
    assert keys == ["condition", "observation"]
    assert path == "form.steps.1.wait"
    # A row that is not about an unknown key is not this function's business.
    assert keys_to_take_off([row(answer, codes=["forecast"])], form) is None
    assert keys_to_take_off([{"problem": "unknown_field"}], form) is None
    assert keys_to_take_off([], form) is None


def test_only_the_keys_the_row_names_come_off():
    """NEVER A SIBLING. The remover used to pop everything the container held
    that was not on one row's accepted list, which is the same answer only
    while one row is the whole story."""
    answer = reply("unknown_fields")
    step = answer["form"]["steps"][0]
    kept = {name: value for name, value in step.items() if name != "room_number"}
    keys, _path, value = keys_to_take_off([row(answer, slot="step")],
                                          answer["form"])
    assert keys == ["room_number"]
    assert value == kept


def test_a_container_left_empty_comes_off_instead_of_going_back_as_nothing():
    """A wait condition stripped to `{}` reads as a condition with no source,
    and the bench refuses the wait for it: "A planned wait needs days 1-365
    and conditions"."""
    answer = reply("unknown_fields")
    form = copy.deepcopy(answer["form"])
    # A condition whose ONLY keys are the ones the form has no room for.
    form["steps"][1]["wait"]["conditions"][0] = {"observation": "they replied",
                                                 "condition": "any reply"}
    keys, path, value = keys_to_take_off(
        [row(answer, slot="wait.conditions.0")], form)

    assert keys == ["condition", "observation"]
    assert path == "form.steps.1.wait"
    # NOT `{}` ANYWHERE. The emptied condition came off, and the list it left
    # empty came off with it; `days` is the agent's own and stays, which is a
    # wait the bench still reads (`wait_monitor.planned` asks for days).
    assert value == {"days": 5}
    assert json.dumps(value).count("{}") == 0


def test_two_unknown_keys_on_one_step_are_one_patch_and_one_round():
    answer = reply("two_unknown_one_step")
    rows = [entry for entry in problems_of(answer)
            if entry["problem"] == "unknown_field"]
    assert len(rows) == 2 and {entry["step"] for entry in rows} == {1}
    door = Door({"ok": True, "ready": True})
    model = _model()

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert model.invocations == []
    assert len(door.patches) == 1 and len(door.patches[0]) == 1
    sent = door.patches[0][0]
    # Both rows answered at once, so the whole step goes back rather than a
    # second patch built off a step the first already changed.
    assert sent["path"] == "form.steps.0"
    assert "room_number" not in sent["value"]
    assert "observation" not in sent["value"]["wait"]["conditions"][0]
    # And what the form DOES read is still there.
    assert sent["value"]["wait"]["days"] == 5
    assert sent["value"]["wait"]["conditions"][0]["source"] == "email_reply"
    assert sent["value"]["do_line"] == answer["form"]["steps"][0]["do_line"]


def test_the_keys_the_bench_names_outright_are_the_ones_taken_off():
    """CONSTRUCTED, and labelled as such: the unknown-key rows that come off a
    TOOL ARGUMENT carry `keys`, and no capture of one exists on this box --
    the normalizer takes an argument the send does not have off the form
    before the check sees it. The reader has to handle both kinds."""
    answer = reply("email_step")
    form = copy.deepcopy(answer["form"])
    form["steps"][1]["tool"]["args"]["urgency"] = "high"
    named = a_row("form.steps.1.tool.args", step=2, slot="urgency",
                  problem="unknown_field", keys=["urgency"],
                  say="Step 2: urgency is not something this action takes.")
    named["slot"] = "tool.args"

    keys, path, value = keys_to_take_off([named], form)

    assert keys == ["urgency"]
    assert path == "form.steps.1.tool.args"
    assert "urgency" not in value
    # The subject and body it was sitting beside are untouched.
    assert set(value) == {"subject", "body"}


def test_an_unknown_key_is_taken_off_even_when_the_door_points_elsewhere():
    """It costs no model call, so waiting for the door to work down to it is
    paying for a fix already in hand."""
    answer = reply("unknown_fields")
    assert answer["next_fix"]["problem"] == "unknown_field"
    # Put the forecast row in front, the way the door would on another plan.
    forecast = row(answer, codes=["forecast"])
    unknown = row(answer, slot="step")
    answer["problems"] = [forecast, unknown]
    answer["next_fix"] = forecast
    door = Door({"ok": True, "ready": True})
    model = _model()

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert model.invocations == []
    assert door.patches[0][0]["path"] == "form.steps.0"


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


def test_the_patch_lands_on_the_slots_own_path_when_the_row_names_the_step():
    """A row that names a slot and stops at the step is still about that one
    slot: replacing a whole step to change one argument is how a fix round
    loses the rest of the step."""
    answer = reply("email_step")
    answer["form_steps"][0]["person_slot"] = "who"
    named = a_row("form.steps.1", step=2, slot="subject", problem="empty",
                  say="Step 2: the subject is empty.")
    loop = DraftLoop(None, Door())
    assert loop._patch_path(answer, named) == "form.steps.1.tool.args.subject"
    # A step with no slot table keeps the path the row gave for a name that
    # is not one of the step's own fields.
    plain = a_row("form.steps.0", step=1, slot="subject")
    assert loop._patch_path(reply("clean"), plain) == "form.steps.0"
    # A FIELD OF THE STEP ITSELF IS ITS OWN ADDRESS (0.55.3): the falling-odds
    # row names `declared_odds` on the step's path, and it is one number.
    own = a_row("form.steps.0", step=1, slot="do_line")
    assert loop._patch_path(reply("clean"), own) == "form.steps.0.do_line"
    odds = a_row(None, step=1, slot="declared_odds")
    assert loop._patch_path(reply("clean"), odds) == "form.steps.0.declared_odds"


# ---------------------------------------------------------------------------
# 5b. NO PATH AT ALL MEANS THE STEP
# ---------------------------------------------------------------------------
def test_a_row_with_no_path_is_answered_by_sending_the_step_back():
    """`path: null` says this row has no patchable address. It used to come
    out as `""` and the model was asked to patch a path of nothing."""
    loop = DraftLoop(None, Door())
    nowhere = a_row("", step=2, slot=None, problem="not_allowed",
                    say="Step 2 cannot stand as it is.")
    assert nowhere["path"] is None
    assert loop._patch_path(reply("email_step"), nowhere) == "form.steps.1"
    # And that is the whole-step road: the step, not a line of it.
    assert whole_step_path(loop._patch_path(reply("email_step"), nowhere)) == 1


def test_a_row_with_no_path_and_no_step_has_nowhere_to_go():
    loop = DraftLoop(None, Door())
    assert loop._patch_path(reply("clean"), a_row("")) == ""


def test_the_whole_step_goes_back_when_the_row_names_no_path():
    answer = holes_written(reply("email_step"))
    answer["form_steps"][0]["person_slot"] = "who"
    nowhere = a_row("", step=2, slot=None, problem="not_allowed",
                    say="Step 2 cannot stand as it is.")
    answer = only(answer, nowhere)
    door = Door({"ok": True, "ready": True})
    whole = {"verb": "emails", "do_line": "Send the introduction",
             "hand_over_line": "The sent message", "who": "agent"}
    model = _model({"patches": [{"path": "form.steps.1", "value": whole}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    ask = _said(model)
    # The model is told it is the STEP, given every field of it and the two
    # exits -- never asked to patch a path of nothing.
    assert "TWO EXITS AND NO THIRD" in ask
    assert '"path":""' not in ask and '"path": ""' not in ask
    assert door.patches == [[{"path": "form.steps.1", "value": whole}]]


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
