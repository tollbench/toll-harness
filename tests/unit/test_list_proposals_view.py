"""ONE SIZE CAP, ON THE LIST OF THE AGENT'S OWN BIDS ONLY (0.35.5; 0.74.0).

On 2026-09-09 one `toll_bench.list_proposals` call handed a GLM run 213,096
characters: 79 bids, each with its whole plan. Since 0.74.0 every row goes as
the bench sent it -- no settled-line view, no plan stripped -- and the one cap
left drops the oldest rows with no move on them, counted and said.
"""

from __future__ import annotations

import json

from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider


class _Api:
    def __init__(self, rows):
        self.rows = rows

    def proposals(self):
        return self.rows


def _view(rows):
    return BookOfHousesTollBenchProvider(_Api(rows)).list_proposals()


def _bid(i, your_move=None, plan_chars=400):
    return {
        "id": f"p-{i}",
        "status": "filed",
        "filed_at": f"2026-09-{10 + i:02d}T00:00:00Z",
        "your_move": your_move,
        "steps": [{"title": "x" * plan_chars}],
    }


def test_rows_go_as_the_bench_sent_them():
    rows = [_bid(1), _bid(2, your_move="file_informed_plan")]
    answer = _view(rows)
    assert answer["proposals"] == rows
    assert answer["omitted"] == 0 and "note" not in answer


def test_past_the_cap_the_oldest_rows_with_no_move_drop_first():
    cap = BookOfHousesTollBenchProvider.LIST_PROPOSALS_CHARS
    rows = [_bid(i, plan_chars=cap // 6) for i in range(1, 10)]
    rows[0]["your_move"] = "sign_the_deal"  # the oldest, and it has a move
    answer = _view(rows)
    assert len(json.dumps(answer["proposals"], separators=(",", ":"))) <= cap
    kept = [row["id"] for row in answer["proposals"]]
    assert "p-1" in kept  # a row with a move on it is never dropped
    dropped = [row["id"] for row in rows if row["id"] not in kept]
    assert dropped and answer["omitted"] == len(dropped)
    newest_dropped = max(int(i[2:]) for i in dropped)
    assert all(int(i[2:]) > newest_dropped for i in kept if i != "p-1")
    assert "older bid(s)" in answer["note"]


def test_junk_rows_are_skipped():
    assert _view(["x", None, 3])["proposals"] == []
    assert _view(None)["proposals"] == []
