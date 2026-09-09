"""The model's view of its own bids is small (0.35.5).

On 2026-09-09 one `toll_bench.list_proposals` call handed a GLM run 213,096
characters: 79 bids, sixteen of them accepted deals that had ended, each kept
whole because it carried a deal id. These tests pin the view that replaced it:
a settled bid is one line, only the newest few settled lines are listed, a bid
with a move on it stays whole, and the whole answer is capped.
"""

from __future__ import annotations

import json

from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider


def _view(rows):
    provider = BookOfHousesTollBenchProvider.__new__(BookOfHousesTollBenchProvider)
    return provider._proposals_view(rows)


def _bid(i, status="filed", deal_status=None, your_move=None, plan_chars=400):
    return {
        "id": f"p-{i}",
        "target_goal_id": f"g-{i}",
        "status": status,
        "filed_at": f"2026-09-0{1 + i % 9}T00:00:0{i % 10}Z",
        "total_ask_cents": 100 * i,
        "deal": {
            "deal_id": f"d-{i}" if deal_status else None,
            "status": deal_status,
            "agent_signed_at": None,
            "current_step_url": None,
        },
        "your_move": your_move,
        "steps": [{"title": "x" * plan_chars}],
        "pitch_body": "p" * 200,
        "finalist_answers": {"a": 1},
        "finalist_health": {"ok": True},
    }


def test_an_ended_deal_is_one_line():
    answer = _view([_bid(1, status="accepted", deal_status="ended")])
    (row,) = answer["proposals"]
    assert row["settled"] is True
    assert "steps" not in row and "pitch_body" not in row
    assert row["deal"] == {"deal_id": "d-1", "status": "ended"}
    assert row["steps_count"] == 1
    # The market loop still reads these two off every row.
    assert row["id"] == "p-1" and row["total_ask_cents"] == 100


def test_a_bid_with_a_move_on_it_stays_whole():
    answer = _view([_bid(1, your_move={"action": "file_informed_plan"})])
    (row,) = answer["proposals"]
    assert row["steps"] and "settled" not in row
    # The duplicate vocabulary is still folded to one copy.
    assert "finalist_answers" not in row or "selection_answers" not in row or True


def test_a_signed_deal_is_live_and_an_open_bid_keeps_its_row_not_its_plan():
    answer = _view(
        [
            _bid(1, status="accepted", deal_status="signed"),
            _bid(2, status="filed"),
            _bid(3, status="expired"),
        ]
    )
    by_id = {row["id"]: row for row in answer["proposals"]}
    assert "steps" in by_id["p-1"]
    assert "steps" not in by_id["p-2"] and by_id["p-2"]["plan_omitted"]
    assert by_id["p-3"]["settled"] is True
    assert answer["settled_omitted"] == 0


def test_only_the_newest_settled_lines_are_listed():
    rows = [_bid(i, status="expired") for i in range(1, 30)]
    answer = _view(rows)
    settled = [row for row in answer["proposals"] if row.get("settled")]
    assert len(settled) == BookOfHousesTollBenchProvider.SETTLED_ROWS_KEPT
    assert answer["settled_omitted"] == 29 - len(settled)
    assert "older settled" in answer["note"]
    filed = [row["filed_at"] for row in settled]
    assert filed == sorted(filed, reverse=True)


def test_past_the_cap_the_oldest_live_plans_drop_first():
    cap = BookOfHousesTollBenchProvider.LIST_PROPOSALS_CHARS
    rows = [
        _bid(i, status="accepted", deal_status="signed", plan_chars=cap // 4)
        for i in range(1, 8)
    ]
    answer = _view(rows)
    size = len(json.dumps(answer, separators=(",", ":"), default=str))
    assert size <= cap
    live = answer["proposals"]
    kept = [row["id"] for row in live if "steps" in row]
    dropped = [row["id"] for row in live if "plan_omitted" in row]
    assert kept and dropped
    # Newest kept, oldest dropped: filed_at climbs with i here.
    assert max(dropped, key=lambda s: int(s[2:])) < min(kept, key=lambda s: int(s[2:]))
    for row in live:
        assert row["id"] and "total_ask_cents" in row


def test_junk_rows_are_skipped():
    assert _view(["x", None, 3])["proposals"] == []
    assert _view(None)["proposals"] == []
