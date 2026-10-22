"""`read_results` RIDES EVERY ACT ROW, WHOLE (0.65.1, contract 4.1.44).

WHAT FORCED THIS FILE. The bench now puts an always-present `read_results`
list on every act entry in `current_step` `acts[]` (also the check-in 201 and
step thread): one entry per read run, `{run, tool, ran, count, items, facts,
trimmed}` -- a calendar read's `items` are events `{start, end, all_day,
title, status, busy, id}`, trimmed server-side to 50 items / 8,000 characters.
`_act_row` in step.py copied a fixed allowlist of act keys
(act_id, kind, state, note, error, words, progress, next, failure, lines, plus
a handful of `_fit`'d fields) and `read_results` was not on it, so it never
reached the model: an agent asked to write "three open times" from a
calendar read had nothing but its own memory to write it from.

Fixed: `_act_row` now copies `read_results` through WHOLE when the bench
sends the key at all -- never re-clipped by this row's own 400-character
`_clip` pass, since the bench already trimmed it. Present as `[]` when the
bench sent an empty list, absent only when an older bench never sent the key.
"""
from __future__ import annotations

from tests.unit.test_step_ask import OBLIGATION, _payload
from toll_harness.toll_bench.step import MOVE_INSTRUCTIONS, _act_row, step_tail, the_move

CALENDAR_READ_RESULTS = [
    {
        "run": 1,
        "tool": "calendar.list_events",
        "ran": "2026-10-21T12:00:00Z",
        "count": 2,
        "items": [
            {
                "start": "2026-10-23T14:00:00Z",
                "end": "2026-10-23T15:00:00Z",
                "all_day": False,
                "title": "Open",
                "status": "confirmed",
                "busy": False,
                "id": "ev-1",
            },
            {
                "start": "2026-10-24T09:00:00Z",
                "end": "2026-10-24T10:00:00Z",
                "all_day": False,
                "title": "Open",
                "status": "confirmed",
                "busy": False,
                "id": "ev-2",
            },
        ],
        "facts": None,
        "trimmed": None,
    }
]


# ---------------------------------------------------------------------------
# 1. `_act_row` carries `read_results` whole
# ---------------------------------------------------------------------------
def test_act_row_keeps_read_results_whole():
    act = {
        "act_id": "a1",
        "kind": "meeting",
        "state": "executed",
        "read_results": CALENDAR_READ_RESULTS,
    }
    row = _act_row(act)
    assert row["read_results"] == CALENDAR_READ_RESULTS
    assert row["read_results"][0]["items"][0]["id"] == "ev-1"


def test_act_row_read_results_is_not_reclipped_at_400_chars():
    # A read_results block well past the row's own 400-char clip budget
    # survives whole -- the bench already trimmed it to its own cap, and a
    # second clip here would cut a calendar read down to a couple of events.
    big_items = [
        {
            "start": f"2026-10-{23 + (n % 5)}T{10 + n % 8:02d}:00:00Z",
            "end": f"2026-10-{23 + (n % 5)}T{11 + n % 8:02d}:00:00Z",
            "all_day": False,
            "title": f"Open slot {n}",
            "status": "confirmed",
            "busy": False,
            "id": f"ev-{n}",
        }
        for n in range(40)
    ]
    act = {
        "act_id": "a1",
        "kind": "meeting",
        "state": "executed",
        "read_results": [{"run": 1, "tool": "calendar.list_events", "ran": "now",
                           "count": 40, "items": big_items, "facts": None, "trimmed": None}],
    }
    row = _act_row(act)
    assert len(row["read_results"][0]["items"]) == 40
    assert row["read_results"][0]["items"][-1]["id"] == "ev-39"


def test_act_row_without_the_key_is_unchanged():
    act = {"act_id": "a1", "kind": "email", "state": "sent_back", "note": "make it 11-11:30"}
    row = _act_row(act)
    assert "read_results" not in row
    assert row["note"] == "make it 11-11:30"


def test_act_row_keeps_an_empty_read_results_list_present():
    act = {"act_id": "a1", "kind": "meeting", "state": "executed", "read_results": []}
    row = _act_row(act)
    assert row["read_results"] == []
    assert "read_results" in row


# ---------------------------------------------------------------------------
# 2. The tail the model actually reads carries it too
# ---------------------------------------------------------------------------
def test_read_results_reaches_the_step_tail_intact():
    payload = _payload(acts=[{
        "act_id": "a1",
        "kind": "meeting",
        "state": "executed",
        "read_results": CALENDAR_READ_RESULTS,
    }])
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    row = tail["acts"][0]
    assert row["read_results"] == CALENDAR_READ_RESULTS


def test_an_act_without_read_results_is_unchanged_in_the_tail():
    payload = _payload(acts=[{"act_id": "a1", "kind": "meeting", "state": "executed"}])
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    assert "read_results" not in tail["acts"][0]


def test_an_empty_read_results_list_stays_empty_in_the_tail():
    payload = _payload(acts=[{"act_id": "a1", "kind": "meeting", "state": "executed",
                              "read_results": []}])
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    assert tail["acts"][0]["read_results"] == []


# ---------------------------------------------------------------------------
# 3. The hand_back instruction points at read_results for a promised list
# ---------------------------------------------------------------------------
def test_hand_back_instruction_names_read_results():
    assert "`read_results`" in MOVE_INSTRUCTIONS["hand_back"]
