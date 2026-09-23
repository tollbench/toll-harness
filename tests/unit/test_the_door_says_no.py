"""THE 409 IS THE BODY THIS LOOP MOST NEEDS TO READ, IN EITHER SHAPE.

WHAT FORCED IT (checker, 2026-09-17). The plan door answers every open call in
the new row language and used to answer its own 409s in the old one. The bench
has fixed that, so a plan 409 now carries `problems` in the new shape,
`form_steps`, `paused_until` and `paused_reason`. But an older bench is still
out there and the BID door never changed at all, so both shapes reach this
package, and the one body a loop gets when things go wrong is the one it must
not break on.

Whichever shape arrives: nothing crashes, nothing is asked of a model, nothing
is sent to the door, and the run comes back saying which kind of no it was --
a pause that lifts by itself, or a close that does not.
"""
from __future__ import annotations

import json

from tests.unit.plan_door import Door, reply, status
from toll_harness.toll_bench.draft import (
    DRAFT_PAUSED,
    DraftLoop,
    is_paused,
    problems_of,
)


def _model(*answers: object):
    from toll_harness.core.types import ModelMessage, ModelResponse
    from toll_harness.models.scripted import ScriptedModelAdapter

    return ScriptedModelAdapter(
        [
            ModelResponse(
                message=ModelMessage.text("assistant", json.dumps(a)),
                text=json.dumps(a),
                tool_calls=[],
            )
            for a in answers
        ]
    )


# THE OLD SHAPE, CONSTRUCTED and labelled as such. This is what the BID door
# answers with, and what an older plan door answered with: the pre-4.0 problem
# rows, no `form_steps`, no `paused_until`. There is no capture of it here
# because the bench on this box does not send it any more.
OLD_SHAPE_409 = {
    "ok": False,
    "error": "draft_closed",
    "status": 409,
    "closed": "this draft is used up; it opens again when it expires",
    "problems": [
        {
            "code": "REJ-15",
            "path": "form.steps.0.do_line",
            "detail": "the line does not say what gets done",
            "question": "what gets done on this step?",
            "fix": "say what gets done",
            "step_index": 0,
            "field": "do_line",
        }
    ],
    "draft": {"steps": [{"title": "Write the note"}]},
    "rounds": {"used": 45, "left": 0, "cap": 45},
}


def _new_shape_closed():
    """The plan door's own 409, with the close on it instead of the pause."""
    body = reply("paused")
    body["error"] = "draft_closed"
    body["paused_until"] = None
    body["paused_reason"] = None
    body["closed"] = "this draft is used up; it opens again when it expires"
    return body


class _Shut(Door):
    """A door that answers one 409 body to everything, and records writes."""

    def __init__(self, body):
        super().__init__()
        self.body = body

    def read_draft(self, target_id, *, kind="bid"):
        return dict(self.body)

    def put_draft(self, target_id, outline, *, kind="bid"):
        raise AssertionError("a shut door must not be PUT to")

    def file_plan_from_draft(self, *args, **kwargs):
        raise AssertionError("a shut door must not file")


def test_the_new_shape_409_is_the_one_language_all_through():
    body = reply("paused")
    assert status("paused") == 409
    assert isinstance(body["problems"], list)
    assert isinstance(body["form_steps"], list)
    assert "correction_attempts" in body
    assert body["paused_until"] and body["paused_reason"]


def test_a_paused_409_backs_off_and_says_which_kind_of_no_it_is():
    from datetime import datetime, timedelta, timezone

    later = (datetime.now(timezone.utc) + timedelta(minutes=15))
    body = dict(reply("paused"),
                paused_until=later.isoformat().replace("+00:00", "Z"))
    door = _Shut(body)
    model = _model()

    out = DraftLoop(model, door).run("t-1", kind="plan", proposal_id="p-1",
                                     brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] == DRAFT_PAUSED
    assert model.invocations == []
    assert door.patches == [] and door.drops == [] and door.inserts == []
    assert out["message"]


def test_a_closed_409_in_the_new_shape_does_not_crash_and_stops():
    door = _Shut(_new_shape_closed())
    model = _model()

    out = DraftLoop(model, door).run("t-1", kind="plan", proposal_id="p-1",
                                     brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] != DRAFT_PAUSED
    assert model.invocations == []
    assert door.patches == []


def test_the_old_shape_409_does_not_crash_and_stops():
    """The bid door still answers this way, and so does an older bench."""
    door = _Shut(OLD_SHAPE_409)
    model = _model()

    out = DraftLoop(model, door).run("t-1", kind="plan", proposal_id="p-1",
                                     brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] == "draft_closed"
    assert model.invocations == []
    assert door.patches == []


def test_the_readers_do_not_choke_on_the_old_rows():
    """Nothing here reads a pre-4.0 row, and nothing here raises on one."""
    rows = problems_of(OLD_SHAPE_409)
    assert len(rows) == 1
    # No `who_fixes` on an old row, so it belongs to nobody the new way -- and
    # asking is not an error.
    assert rows[0].get("who_fixes") is None
    assert not is_paused(OLD_SHAPE_409)


def test_a_409_handed_straight_to_the_fix_loop_is_not_worked_on():
    for body in (OLD_SHAPE_409, _new_shape_closed(), reply("paused")):
        door = Door()
        model = _model()
        out = DraftLoop(model, door)._answer_the_fixes(
            "t-1", "plan", dict(body), "x")
        assert model.invocations == []
        assert door.patches == []
        assert isinstance(out, dict)
