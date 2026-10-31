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

from tests.unit.plan_door import (
    FIXTURES,
    reply,
)
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    NEVER_WRITTEN,
    slots_the_agent_owes,
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


BODY = "form.steps.1.tool.args.body"


def _email_plan():
    """The real reply, with a who step already on it so the round under test
    is the writing and not the insert."""
    answer = reply("email_step")
    answer["form_steps"][0]["person_slot"] = "who"
    return answer


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
# 4. THE ADDRESS GATE IS IN THE SEND, SO EVERY ROAD GOES THROUGH IT
#
# WHAT FORCED IT (checker, 2026-09-17). The slots road read the slot table and
# refused `to` there. The FIX road read nothing: a model asked to reword a
# do_line could answer `...tool.args.to` and it went out. Only the scripted
# door refused it, so the suite could not see the hole.
# ---------------------------------------------------------------------------
TO = "form.steps.1.tool.args.to"

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



