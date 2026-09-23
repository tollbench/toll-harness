"""THE BRAKE READS THE BENCH'S OWN KEY, AND A PAUSE IS NOT A CLOSE.

WHAT FORCED IT (checker, 2026-09-17). Two notes on one loop.

The bench started naming its own rows (`key`) -- the name it counts tries
under -- and this package went on working one out for itself. Two counters
with two opinions is how one side stops a draft the other thinks is still
moving, and a key built out of the sentence moves every time somebody rewords
the sentence.

And the plan door can PAUSE: fifteen minutes when the thing in the way is the
bench's own bug, an hour when the same plan comes back five times. It says so
on every body it sends, 409s included. A loop that did not read it spent model
calls and rounds on a door that reads nothing, and the failures it collected
doing so burned the five-cycle breaker that exists to park a want which is
really broken.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from tests.unit.plan_door import Door, a_row, only, reply, row
from toll_harness import cli
from toll_harness.toll_bench.draft import (
    BENCH_FAULT_PAUSE,
    DRAFT_PAUSED,
    DraftLoop,
    fix_key,
    is_paused,
    pause_reason,
    paused_until,
    problems_of,
)


def _in(minutes: int) -> str:
    when = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return when.isoformat().replace("+00:00", "Z")


class _Silent(Door):
    """A door that fails the test if it is called at all."""

    def patch_draft(self, *args, **kwargs):
        raise AssertionError("a paused door must not be patched")

    def put_draft(self, *args, **kwargs):
        raise AssertionError("a paused door must not be PUT to")

    def drop_draft_step(self, *args, **kwargs):
        raise AssertionError("a paused door must not be edited")

    def insert_draft_step(self, *args, **kwargs):
        raise AssertionError("a paused door must not be edited")


# ---------------------------------------------------------------------------
# 1. ONE FAULT, THE BENCH'S OWN NAME FOR IT
# ---------------------------------------------------------------------------
def test_the_key_the_bench_sent_is_the_key_the_brake_counts():
    named = row(reply("email_step"), slot="subject")
    assert named["key"]
    assert fix_key(named) == named["key"]


def test_a_row_with_no_key_still_gets_the_old_one():
    """A bench that sends none, and every row a test builds by hand."""
    plain = a_row("form.steps.0", step=1, slot="do_line", problem="empty")
    assert fix_key(plain) == "1|do_line|empty"
    assert fix_key(dict(plain, key="")) == "1|do_line|empty"


def test_rewording_a_row_does_not_change_what_it_is_called():
    named = row(reply("email_step"), slot="body")
    reworded = dict(named, say="Say it some other way entirely.",
                    accepted=list(reversed(named["accepted"])),
                    something_new=["a key the bench adds next month"])
    assert fix_key(reworded) == fix_key(named)


def test_the_plan_fingerprint_holds_still_when_only_the_words_move():
    """`_plan_draft_fingerprint` and `_loop_state` both hash `fix_key`, so a
    reworded row must not read as progress -- that is what lifts a stall."""
    answer = reply("email_step")

    class Bench:
        def __init__(self, payload):
            self.payload = payload

        def read_draft(self, target_id, *, kind="bid"):
            return self.payload

    class Resources:
        def __init__(self, payload):
            self.toll_bench = Bench(payload)

    obligation = {"kind": "file_informed_plan", "target_id": "t-1"}
    before = cli._plan_draft_fingerprint(Resources(answer), obligation)
    moved = dict(answer)
    moved["problems"] = [
        dict(entry, say="different words", accepted=list(reversed(entry["accepted"])),
             a_key_from_the_future=1)
        for entry in problems_of(answer)
    ]
    moved["next_fix"] = moved["problems"][0]
    after = cli._plan_draft_fingerprint(Resources(moved), obligation)
    assert before == after

    state_before = cli._loop_state(Resources(answer), obligation)
    state_after = cli._loop_state(Resources(moved), obligation)
    assert state_before == state_after


def test_a_cleared_row_does_move_the_fingerprint():
    answer = reply("email_step")

    class Resources:
        def __init__(self, payload):
            self.toll_bench = type(
                "B", (), {"read_draft": lambda _self, _t, kind="bid": payload}
            )()

    obligation = {"kind": "file_informed_plan", "target_id": "t-1"}
    before = cli._plan_draft_fingerprint(Resources(answer), obligation)
    fewer = only(answer, *problems_of(answer)[1:])
    assert cli._plan_draft_fingerprint(Resources(fewer), obligation) != before


# ---------------------------------------------------------------------------
# 2. THE COUNT IS CUMULATIVE, AND THE DOCSTRING SAYS SO
# ---------------------------------------------------------------------------
def test_the_brake_is_written_down_as_cumulative():
    """The code counts every naming in the run, not consecutive rounds, and
    `test_two_problems_taking_turns_still_trip_the_guard` pins it. The words
    said "consecutive", which is the count that let two problems taking turns
    run for ever."""
    words = DraftLoop._answer_the_fixes.__doc__ or ""
    assert "CUMULATIVE" in words.upper()
    assert "THIRD consecutive round" not in words


# ---------------------------------------------------------------------------
# 3. A PAUSE IS READ OFF EVERY BODY
# ---------------------------------------------------------------------------
def test_the_paused_409_is_read_as_a_pause_and_not_as_a_close():
    answer = reply("paused")
    assert answer["error"] == DRAFT_PAUSED
    assert pause_reason(answer) == BENCH_FAULT_PAUSE
    assert paused_until(answer)
    # The captured instant is in the past by now, which is the point: a pause
    # that has run out is not a pause.
    assert is_paused(answer, now=datetime(2026, 9, 17, 23, 0,
                                          tzinfo=timezone.utc))
    assert not is_paused(answer, now=datetime(2027, 1, 1, tzinfo=timezone.utc))


def test_a_body_with_no_pause_on_it_is_not_paused():
    for name in ("clean", "email_step", "outline_round", "edit_refused"):
        assert not is_paused(reply(name)), name
    assert not is_paused({}) and not is_paused(None)


def test_an_unreadable_instant_is_treated_as_a_pause():
    """The bench only writes the field while one stands. Spending rounds
    against a shut door is the worse of the two mistakes."""
    assert is_paused({"paused_until": "some time next week"})


def test_a_paused_draft_costs_no_model_call_and_no_door_call(caplog):
    answer = dict(reply("paused"), paused_until=_in(15))
    loop = DraftLoop(None, _Silent())

    with caplog.at_level(logging.ERROR, logger="toll_harness.draft"):
        out = loop._answer_the_fixes("t-1", "plan", answer, "a want")

    assert out["error"] == DRAFT_PAUSED
    assert loop.calls == 0 and loop.rounds == 0
    # Our own fault, so it shouts the way a platform row does.
    assert "THE BENCH MUST FIX THIS" in caplog.text


def test_a_pause_that_arrives_mid_loop_stops_the_next_round():
    paused = dict(reply("paused"), paused_until=_in(15), ready=False,
                  closed=None, rounds={"used": 1, "left": 40, "cap": 42})
    open_row = row(reply("email_step"), slot="subject")
    answer = only(reply("email_step"), open_row)
    # The plan already has a who step, so the round is a patch and nothing else.
    answer["form_steps"][0]["person_slot"] = "who"
    door = Door(paused)

    loop = DraftLoop(None, door)
    loop._ask = lambda *a, **k: {"patches": [{"path": open_row["path"],
                                             "value": "An introduction"}]}
    out = loop._answer_the_fixes("t-1", "plan", answer, "a want")

    assert out["error"] == DRAFT_PAUSED
    # One patch went out before the bench said stop, and not a second one.
    assert len(door.patches) == 1


def test_a_whole_run_on_a_paused_door_sends_nothing():
    class Bench(_Silent):
        def read_draft(self, target_id, *, kind="bid"):
            return dict(reply("paused"), paused_until=_in(15))

        def file_plan_from_draft(self, *args, **kwargs):
            raise AssertionError("a paused plan must not file")

    loop = DraftLoop(None, Bench())
    out = loop.run("t-1", kind="plan", proposal_id="p-9", brief={"want": "x"},
                   idempotency_key="k")

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] == DRAFT_PAUSED
    assert out["model_calls"] == 0 and out["rounds"] == 0


def test_a_pause_parks_the_want_for_the_round_instead_of_failing_it():
    assert DRAFT_PAUSED in cli._TERMINAL_DOOR_ERRORS
    cli._CLOSED_TARGET_MEMO.clear()
    target = {"target_id": "t", "round": 4}
    assert cli._remember_closed(target, DRAFT_PAUSED)
    assert cli._door_closed_this_round(target)
    assert not cli._door_closed_this_round(dict(target, round=5))
    cli._CLOSED_TARGET_MEMO.clear()
