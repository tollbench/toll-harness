"""SLOTS BEFORE WORDS: the holes the form names get written.

WHAT FORCED IT (checker, 2026-09-17). The door said, in as many words, that
`subject` and `body` on an email step were the AGENT's to fill and still
needed. The harness read that table, showed it to the model, and then told the
model "change exactly the one thing named in `change_this`; do not touch any
other path" -- which is right for a fix and wrong for a hole. So the plan was
filed with an empty subject and an empty body, and nothing in the loop had
ever asked for either.

A hole is not answered by rewording the row above it. The loop now writes
every hole the form names before it asks a model to fix anything, in one ask
for however many there are.

And it NEVER writes `to`, `from`, `cc` or `bcc`. Those are people, they come
from the step where the person picks out of their own contact book, and an
address an agent writes is a person it invented.
"""
from __future__ import annotations

import json
import logging

import pytest

from tests.unit.plan_door import (
    FIXTURES,
    Door,
    SpyDoor,
    a_row,
    holes_written,
    only,
    reply,
    row,
)
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    NEVER_WRITTEN,
    DraftLoop,
    is_a_stand_in,
    slots_the_agent_owes,
    writes_a_person,
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


SUBJECT = "form.steps.1.tool.args.subject"
BODY = "form.steps.1.tool.args.body"


def _email_plan():
    """The real reply, with a who step already on it so the round under test
    is the writing and not the insert."""
    answer = reply("email_step")
    answer["form_steps"][0]["person_slot"] = "who"
    return answer


def _one_row_plan():
    answer = _email_plan()
    forecast = row(answer, codes=["forecast"])
    kept = only(answer, forecast)
    kept["form_steps"] = answer["form_steps"]
    return kept


# ---------------------------------------------------------------------------
# 1. WHICH SLOTS ARE THE AGENT'S TO WRITE NOW
# ---------------------------------------------------------------------------
def test_the_two_required_holes_on_the_send_step_are_the_agents():
    owed = slots_the_agent_owes(_email_plan(), 2)
    assert [slot["name"] for slot in owed] == ["subject", "body"]
    for slot in owed:
        assert slot["filler"] == "agent"
        assert slot["state"] == "needed"
        assert slot["required"] is True
        assert slot["path"]


def test_nothing_else_on_that_step_is():
    """Eleven slots on the step and two of them are holes. The rest are the
    platform's, or not required, or written per item when the step runs."""
    entry = [e for e in _email_plan()["form_steps"] if e["step"] == 2][0]
    assert len(entry["slots"]) > 2
    names = {slot["name"] for slot in slots_the_agent_owes(_email_plan(), 2)}
    assert names == {"subject", "body"}
    assert not names & NEVER_WRITTEN


def test_a_plain_step_owes_nothing_and_neither_does_a_missing_one():
    assert slots_the_agent_owes(reply("clean"), 1) == []
    assert slots_the_agent_owes(_email_plan(), 9) == []
    assert slots_the_agent_owes({}, 1) == []


def test_a_slot_written_at_the_step_is_not_a_hole_at_filing():
    """A step that walks a list writes its words one item at a time, and they
    are approved there. Filling it now writes one sentence over a hundred."""
    answer = _email_plan()
    for slot in answer["form_steps"][1]["slots"]:
        if slot["name"] == "subject":
            slot["source"] = "at_the_step"
    assert [s["name"] for s in slots_the_agent_owes(answer, 2)] == ["body"]


def test_a_recipient_slot_is_never_the_agents_however_it_is_marked():
    answer = _email_plan()
    for slot in answer["form_steps"][1]["slots"]:
        if slot["name"] in ("to", "cc", "from"):
            slot.update(filler="agent", state="needed", required=True,
                        source="at_filing")
    assert [s["name"] for s in slots_the_agent_owes(answer, 2)] == \
        ["subject", "body"]


# ---------------------------------------------------------------------------
# 2. THE ASK, AND WHAT COMES OUT OF IT
# ---------------------------------------------------------------------------
def test_two_holes_make_two_patches_on_their_own_paths():
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [
        {"path": SUBJECT, "value": "An introduction to three people"},
        {"path": BODY, "value": "Hello -- I am writing to introduce you."},
    ]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    assert len(door.patches) >= 1
    sent = door.patches[0]
    assert [entry["path"] for entry in sent] == [SUBJECT, BODY]
    for entry in sent:
        assert isinstance(entry["value"], str) and entry["value"].strip()
    # One round for both, not one round each.
    assert len(sent) == 2


def test_the_ask_names_every_hole_and_its_path():
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": SUBJECT, "value": "An introduction"},
                                {"path": BODY, "value": "Hello there."}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    ask = _said(model)
    assert '"needs"' in ask
    for word in ('"name":"subject"', '"name":"body"', SUBJECT, BODY,
                 '"action":"email.send"'):
        assert word in ask, word
    # No codes, and no list of faults: this ask is about what GOES IN.
    assert "REJ-" not in ask and '"codes"' not in ask


def test_the_loop_never_writes_who_a_message_goes_to():
    """Every patch of a whole run, checked. A model that answers with `to`
    anyway is not obeyed: the address never reaches the door."""
    door = Door({"ok": True, "ready": True}, {"ok": True, "ready": True})
    model = _model({"patches": [
        {"path": SUBJECT, "value": "An introduction"},
        {"path": BODY, "value": "Hello there."},
        {"path": "form.steps.1.tool.args.to", "value": "someone@example.com"},
        {"path": "form.steps.1.tool.args.cc", "value": "other@example.com"},
    ]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    for call in door.patches:
        for entry in call:
            tail = entry["path"].rsplit(".", 1)[-1].lower()
            assert tail not in NEVER_WRITTEN, entry
            assert "@" not in json.dumps(entry["value"]), entry


def test_an_empty_answer_writes_nothing_and_does_not_spend_a_round():
    """A blank is asked for once more, and a blank the second time is not
    sent at all."""
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": SUBJECT, "value": "   "}]},
                   {"patches": [{"path": SUBJECT, "value": ""}]},
                   {"patches": [{"path": "form.steps.1.declared_odds",
                                 "value": 0.7}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    # The blank subject never went out; the loop carried on to the row.
    for call in door.patches:
        for entry in call:
            assert entry["path"] != SUBJECT


def test_a_step_is_asked_for_once_a_run():
    """A door that keeps naming a slot after it has been written is a fix, and
    the fix road with its brake is what answers that -- not this ask again."""
    still_empty = _one_row_plan()
    door = Door(still_empty, still_empty, {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": SUBJECT, "value": "An introduction"},
                     {"path": BODY, "value": "Hello there."}]},
        *[{"patches": [{"path": "form.steps.1.declared_odds", "value": 0.7}]}
          for _ in range(4)],
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", still_empty, "x")

    asks = [_said(model, n) for n in range(len(model.invocations))]
    assert len([ask for ask in asks if '"needs"' in ask]) == 1


def test_a_plan_with_no_holes_asks_nothing_extra():
    answer = only(reply("clean"), row(reply("email_step"), codes=["forecast"]))
    answer["form_steps"] = reply("clean")["form_steps"]
    door = Door({"ok": True, "ready": True})
    model = _model({"patches": [{"path": "form.steps.1.declared_odds",
                                 "value": 0.7}]})

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert len(model.invocations) == 1
    assert '"needs"' not in _said(model)


# ---------------------------------------------------------------------------
# 3. A PLACEHOLDER IS A HOLE THAT LEARNED TO TYPE
#
# WHAT FORCED IT (checker, 2026-09-17). The ask says "never write the word yet
# or TBD" and nothing read the answer back, so a model with nothing to say
# could put `TBD` in the subject line of a real message and the door took it.
# ---------------------------------------------------------------------------
STAND_INS = [
    "", "   ", "TBD", "tbd", "TODO", "yet", "N/A", "none", "...", "[ ]",
    "[subject]", "[Person Name]", "<subject>", "subject", "x",
]


@pytest.mark.parametrize("stand_in", STAND_INS)
def test_a_placeholder_never_reaches_the_door(stand_in):
    """Every shape a model reaches for when it has no words. The body, which
    is real, still goes out in the same round."""
    door = Door({"ok": True, "ready": True}, {"ok": True, "ready": True})
    model = _model(
        {"patches": [
            {"path": SUBJECT, "value": stand_in},
            {"path": BODY, "value": "Hello, I am writing to introduce you."},
        ]},
        {"patches": [{"path": SUBJECT, "value": stand_in}]},
        {"patches": [{"path": "form.steps.1.declared_odds", "value": 0.7}]},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    sent = [entry for call in door.patches for entry in call]
    assert [entry for entry in sent if entry["path"] == SUBJECT] == []
    assert [entry["path"] for entry in sent if entry["path"] == BODY] == [BODY]


def test_a_placeholder_is_asked_for_once_more_and_the_real_words_go_out():
    door = Door({"ok": True, "ready": True})
    model = _model(
        {"patches": [
            {"path": SUBJECT, "value": "TBD"},
            {"path": BODY, "value": "Hello, I am writing to introduce you."},
        ]},
        {"patches": [{"path": SUBJECT, "value": "An introduction to three people"}]},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    assert len(model.invocations) == 2
    assert "A STAND-IN IS NOT WORDS" in _said(model, 1)
    sent = {entry["path"]: entry["value"] for call in door.patches
            for entry in call}
    assert sent[SUBJECT] == "An introduction to three people"
    assert sent[BODY].startswith("Hello")
    # Still one round for both: the second ask costs a model call, not a round.
    assert len(door.patches) == 1


def test_the_second_ask_is_the_last_one():
    """At most once. A model that answers `TBD` twice has said what it has to
    say; the slot stays a hole and the door names it as an `empty` row."""
    door = Door({"ok": True, "ready": True}, {"ok": True, "ready": True})
    model = _model(
        {"patches": [{"path": SUBJECT, "value": "TBD"},
                     {"path": BODY, "value": "TBD"}]},
        {"patches": [{"path": SUBJECT, "value": "TODO"},
                     {"path": BODY, "value": "[body]"}]},
        {"patches": [{"path": "form.steps.1.declared_odds", "value": 0.7}]},
    )

    DraftLoop(model, door)._answer_the_fixes("t-1", "plan", _one_row_plan(), "x")

    asks = [_said(model, n) for n in range(len(model.invocations))]
    assert len([ask for ask in asks if '"needs"' in ask]) == 2
    for call in door.patches:
        for entry in call:
            assert entry["path"] not in (SUBJECT, BODY), entry


def test_real_words_and_real_values_are_left_alone():
    """The list is short on purpose. Nothing here polices a real answer."""
    for good in ("Hi", "An introduction", "No thanks, not this week",
                 "Notes for Tuesday"):
        assert not is_a_stand_in(good, "subject", "text")
    # Not a string: a number, a list or a map is a real answer in its own
    # shape and nothing here touches it.
    for other in (0.7, 0, 5, ["a"], {"a": 1}, True):
        assert not is_a_stand_in(other, "declared_odds", "number")
    # A one-character answer is a hole for words, and fine for anything else.
    assert is_a_stand_in("x", "subject", "text")
    assert not is_a_stand_in("5", "count", "number")


# ---------------------------------------------------------------------------
# 4. THE ADDRESS GATE IS IN THE SEND, SO EVERY ROAD GOES THROUGH IT
#
# WHAT FORCED IT (checker, 2026-09-17). The slots road read the slot table and
# refused `to` there. The FIX road read nothing: a model asked to reword a
# do_line could answer `...tool.args.to` and it went out. Only the scripted
# door refused it, so the suite could not see the hole.
# ---------------------------------------------------------------------------
TO = "form.steps.1.tool.args.to"
CC_EMAILS = "form.steps.1.tool.args.cc_emails"
NESTED = "form.steps.1.tool.args.to_recipients.0.emailAddress.address"


def test_the_fix_road_never_writes_who_a_message_goes_to(caplog):
    """The spy refuses nothing, so what is asserted here is the harness's own
    guard and not the test door's."""
    answer = holes_written(_email_plan())
    fix = a_row("form.steps.1.do_line", step=2, slot="do_line",
                problem="empty", say="Step 2: say what this step does.")
    answer = only(answer, fix)
    door = SpyDoor({"ok": True, "ready": True}, {"ok": True, "ready": True})
    model = _model(
        *[{"patches": [{"path": TO, "value": "someone@example.com"}]}
          for _ in range(3)]
    )

    with caplog.at_level(logging.WARNING):
        DraftLoop(model, door)._answer_the_fixes("t-1", "plan", answer, "x")

    assert door.patches == []
    # The path is logged so a stall is readable; the address never is.
    assert TO in caplog.text
    assert "someone@example.com" not in caplog.text


def test_the_address_is_refused_wherever_it_sits_in_the_path():
    for path in (TO, CC_EMAILS, NESTED,
                 "form.steps.1.tool.args.from",
                 "form.steps.1.tool.args.bcc",
                 "form.steps.1.tool.args.recipients",
                 "form.steps.1.tool.args.recipient_emails",
                 "form.steps.1.tool.args.sender",
                 "form.steps.1.tool.args.attendees.0.email_address",
                 "form.steps.1.tool.args.personalizations.0.to.0.email"):
        assert writes_a_person(path), path
    for path in ("form.steps.1.tool.args.subject",
                 "form.steps.1.tool.args.body",
                 "form.steps.1.tool.args.in_reply_to",
                 "form.steps.1.do_line",
                 "form.odds", ""):
        assert not writes_a_person(path), path


def test_a_send_with_nothing_left_in_it_keeps_the_draft_that_stands(caplog):
    """Every patch refused is not an empty PATCH to the door: that would
    spend a round to change nothing. The answer in hand comes straight back
    and the brake counts the round."""
    door = SpyDoor()
    loop = DraftLoop(None, door)
    standing = {"ok": True, "ready": False, "problems": []}

    with caplog.at_level(logging.WARNING):
        back = loop._patch("t-1", "plan", [{"path": CC_EMAILS, "value": "a@b.c"}],
                           "fix", standing=standing)

    assert door.patches == []
    assert back is standing
    assert loop.rounds == 0
    assert "a@b.c" not in caplog.text


# ---------------------------------------------------------------------------
# 5. THE SERVER'S OWN LIST OF ADDRESS NAMES, ASKED OF OUR RULE
#
# WHAT FORCED IT (checker, 2026-09-17). The guard above was a LIST of names we
# spell, and the services spell the same things their own way. Read against
# the server's own answer -- `act_kinds.calls.address_roles` over every tool in
# the catalog -- that list missed thirteen of the thirty-one names the door
# marks, `extra_recipients` and `to_number` and `email` among them. The door
# refuses all of them, so nothing ever escaped; it cost a round each time, and
# a round is what this loop has least of.
#
# The names below are the SERVER'S, captured into a fixture with the commit
# they came from and how they were produced, so this suite never reaches a
# server or a network to ask.
# ---------------------------------------------------------------------------
MARKED_BY_THE_SERVER = json.loads(
    (FIXTURES / "address_arguments_the_server_marks.json").read_text()
)


def test_the_capture_says_where_it_came_from():
    """A fixture nobody can trace is a fixture nobody can re-take."""
    assert MARKED_BY_THE_SERVER["server_sha"]
    assert "address_roles" in MARKED_BY_THE_SERVER["how"]
    assert len(MARKED_BY_THE_SERVER["names"]) == 31
    assert set(MARKED_BY_THE_SERVER["names"].values()) == {"recipient", "sender"}


@pytest.mark.parametrize("name", sorted(MARKED_BY_THE_SERVER["names"]))
def test_every_name_the_server_calls_an_address_is_refused_here(name):
    """Wherever it sits along the path, and on its own."""
    assert writes_a_person(name), name
    assert writes_a_person(f"form.steps.1.tool.args.{name}"), name
    assert writes_a_person(f"form.steps.1.tool.args.{name}.0.value"), name


# The other half of strict: a name that merely stands NEAR an address is an
# ordinary slot, and refusing one costs the model the round it spends writing
# it again. `from_date` is a date, `attendee_count` is a number, `email_subject`
# and `mail_body` are words, and an id is the row's.
@pytest.mark.parametrize("name", [
    "subject", "body", "email_subject", "mail_body", "from_date",
    "attendee_count", "message_id", "thread_id", "in_reply_to", "do_line",
    "title", "when", "note", "label", "start_time",
])
def test_an_ordinary_name_is_not_an_address(name):
    assert not writes_a_person(name), name
    assert not writes_a_person(f"form.steps.1.tool.args.{name}"), name
