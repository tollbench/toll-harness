"""EVERY RED ROW, EVERY ROUND (0.68.0).

WHAT FORCED THESE TESTS. After the form went in, the loop asked the model
about ONE red row, patched it, read the door again and asked about the next:
thirteen model calls for one lab model's plan on 2026-10-22, while the door
had already named every red row with its step and takes a whole form in one
PUT. Each round now shows the model the whole form and the whole red list and
files the whole answer. The three rows that need no model still go first, the
brake is still the bench's own key counted across the run, and the older
outline road keeps its one-row road.
"""
from __future__ import annotations

import copy
import json

from tests.unit.plan_door import a_row, only, reply
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import (
    WHOLE_FORM_AGAIN,
    WHOLE_FORM_STILL_RED_INSTRUCTION,
    DraftLoop,
    take_off_addresses,
)


def _model(*answers: object) -> ScriptedModelAdapter:
    def said(answer):
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return ModelResponse(message=ModelMessage.text("assistant", text), text=text,
                             tool_calls=[])

    return ScriptedModelAdapter([said(answer) for answer in answers])


def _asked(model: ScriptedModelAdapter, n: int) -> str:
    return model.invocations[n]["messages"][0].content[0]["text"]


class ScriptDoor:
    """The plan door, scripted: one answer per write, every call recorded."""

    fleet = None
    insert_draft_step = None  # no insert call: the who row is the model's

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, object]] = []

    def _next(self):
        return self.answers.pop(0) if self.answers else reply("clean")

    def read_draft(self, target_id, *, kind="bid"):
        raise AssertionError("the loop was handed this cycle's read")

    def put_draft(self, target_id, body, *, kind="bid"):
        self.calls.append(("PUT", copy.deepcopy(body)))
        return self._next()

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.calls.append(("PATCH", copy.deepcopy(patches)))
        return self._next()

    def file_plan_from_draft(self, target_id, proposal_id, idempotency_key):
        self.calls.append(("FILE", proposal_id))
        return {"ok": True, "plan_revised": True}


THREE_RED = (
    a_row("form.steps.0.do_line", step=1, slot="do_line", problem="wrong_kind",
          say="Step 1's title says what you do, not what the person gets.",
          codes=["REJ-77"]),
    a_row("form.steps.0.hand_over_line", step=1, slot="hand_over_line",
          say="Step 1 needs the thing you hand over.", codes=["REJ-01"]),
    a_row("form.overview", slot="overview",
          say="The plan needs its short pitch.", codes=["REJ-02"]),
)


def _standing(*rows):
    return only(reply("clean"), *rows)


def _whole(**step):
    form = copy.deepcopy(reply("clean")["form"])
    form["steps"][0].update(step)
    return form


def _run(model, door, standing):
    return DraftLoop(model, door).run(
        "t-1", kind="plan", proposal_id="p-1", idempotency_key="k",
        brief={"want": "An introduction email to three people"}, already_read=standing,
    )


def test_every_red_row_rides_one_call_and_the_whole_form_goes_back_in_one_put():
    door = ScriptDoor(reply("clean"))
    fixed = _whole(hand_over_line="The finished note, ready for you to read")
    model = _model({"form": fixed, "drop": []})

    out = _run(model, door, _standing(*THREE_RED))

    assert out["filed"] is True, out
    assert len(model.invocations) == 1
    assert [call[0] for call in door.calls] == ["PUT", "FILE"]
    sent = door.calls[0][1]["form"]
    assert len(sent["steps"]) == 1
    assert sent["steps"][0]["hand_over_line"] == "The finished note, ready for you to read"
    ask = _asked(model, 0)
    for row in THREE_RED:
        assert f'"path":"{row["path"]}"' in ask
        assert row["say"] in ask
    assert '"step":1' in ask and '"red":' in ask and '"form":' in ask
    # The legacy codes are for the log, never for the model.
    assert "REJ-77" not in ask and "REJ-01" not in ask


def test_the_rows_that_need_no_model_still_go_first_and_cost_no_call():
    answer = reply("unknown_fields")
    rest = [row for row in answer["problems"] if row["problem"] != "unknown_field"]
    after_take_off = only(answer, *rest)
    door = ScriptDoor(
        only(answer, *[row for row in answer["problems"]
                       if not (row["problem"] == "unknown_field" and row["step"] == 1)]),
        after_take_off,
        reply("clean"),
    )
    held = copy.deepcopy(answer["form"])
    held["steps"][1]["declared_odds"] = 0.65  # the falling line, fixed
    model = _model({"form": held})

    out = _run(model, door, answer)

    assert out["filed"] is True, out
    verbs = [call[0] for call in door.calls]
    assert verbs == ["PATCH", "PATCH", "PUT", "FILE"]
    assert len(model.invocations) == 1


def test_a_row_still_red_after_two_whole_forms_ends_the_draft():
    row = THREE_RED[0]
    door = ScriptDoor(_standing(row), _standing(row), _standing(row))
    model = _model({"form": _whole(do_line="Write the note")},
                   {"form": _whole(do_line="Write the three notes")})

    out = _run(model, door, _standing(row))

    assert out["ok"] is False and out["error"] == "draft_stalled", out
    assert len(model.invocations) == 2
    assert [call[0] for call in door.calls] == ["PUT", "PUT"]
    assert WHOLE_FORM_STILL_RED_INSTRUCTION.split(".")[0] in _asked(model, 1)
    assert '"still_red":' in _asked(model, 1)
    assert '"still_red":' not in _asked(model, 0)


def test_a_part_of_a_form_is_asked_again_and_never_filed():
    standing = _standing(THREE_RED[2])
    standing["form"]["steps"] = standing["form"]["steps"] * 3
    one_step = {"form": dict(_whole(), overview="Three notes, sent when you say.")}
    door = ScriptDoor()
    model = _model(one_step, one_step)

    out = _run(model, door, standing)

    assert out["filed"] is False
    assert door.calls == []
    assert len(model.invocations) == 2
    assert WHOLE_FORM_AGAIN["partial"].split(".")[0] in _asked(model, 1)


def test_a_step_taken_out_on_purpose_is_named_in_drop_and_filed():
    standing = _standing(THREE_RED[2])
    standing["form"]["steps"] = standing["form"]["steps"] * 2
    door = ScriptDoor(reply("clean"))
    model = _model({"form": dict(_whole(), overview="One note, sent when you say."),
                    "drop": [2]})

    out = _run(model, door, standing)

    assert out["filed"] is True, out
    assert len(door.calls[0][1]["form"]["steps"]) == 1


def test_an_unchanged_form_is_asked_once_more_then_the_draft_stops():
    standing = _standing(THREE_RED[0])
    door = ScriptDoor()
    model = _model({"form": standing["form"]}, {"form": standing["form"]})

    out = _run(model, door, standing)

    assert out["filed"] is False and door.calls == []
    assert len(model.invocations) == 2
    assert WHOLE_FORM_AGAIN["unchanged"].split(",")[0] in _asked(model, 1)


def test_the_plans_own_fields_alone_keep_every_step():
    standing = _standing(THREE_RED[2])
    standing["form"]["steps"] = standing["form"]["steps"] * 2
    door = ScriptDoor(reply("clean"))
    model = _model({"overview": "Two notes, sent when you say."})

    out = _run(model, door, standing)

    assert out["filed"] is True, out
    sent = door.calls[0][1]["form"]
    assert sent["overview"] == "Two notes, sent when you say."
    assert len(sent["steps"]) == 2


def test_who_a_message_goes_to_never_rides_the_whole_form():
    standing = _standing(THREE_RED[2])
    door = ScriptDoor(reply("clean"))
    step = {"tool": {"name": "email.send", "on": "composio:gmail",
                     "args": {"to": "someone@example.test", "cc_emails": ["x@example.test"],
                              "subject": "Hello", "body": "A short note."}}}
    model = _model({"form": dict(_whole(**step), overview="One note.")})

    out = _run(model, door, standing)

    assert out["filed"] is True, out
    args = door.calls[0][1]["form"]["steps"][0]["tool"]["args"]
    assert args == {"subject": "Hello", "body": "A short note."}


def test_take_off_addresses_reads_a_list_of_calls_too():
    form = {"steps": [{"tool": [{"name": "a", "args": {"to_number": "+1", "text": "hi"}},
                                {"name": "b", "args": {"subject": "s"}}]}]}
    kept, gone = take_off_addresses(form)
    assert gone == ["form.steps.0.tool.0.args.to_number"]
    assert kept["steps"][0]["tool"][0]["args"] == {"text": "hi"}
    assert kept["steps"][0]["tool"][1]["args"] == {"subject": "s"}
    assert form["steps"][0]["tool"][0]["args"]["to_number"] == "+1"  # a copy


def test_only_a_held_form_in_the_one_language_takes_the_whole_form():
    held = _standing(THREE_RED[0])
    assert DraftLoop.takes_the_whole_form("plan", held)
    assert not DraftLoop.takes_the_whole_form("bid", held)
    assert not DraftLoop.takes_the_whole_form("plan", reply("outline_round"))
    older = {k: v for k, v in held.items() if k != "problems"}
    assert not DraftLoop.takes_the_whole_form("plan", older)
    assert not DraftLoop.takes_the_whole_form("plan", {"draft": {"steps": [{}]}})
