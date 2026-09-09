"""0.35.7: a want the door closed is not asked again until it is posted again."""

from __future__ import annotations

from toll_harness import cli


def setup_function():
    cli._CLOSED_TARGET_MEMO.clear()


def test_a_bidding_closed_answer_memos_the_want_for_its_round():
    target = {"target_id": "t-1", "round": 2}
    assert cli._remember_closed(target, "bidding_closed") is True
    assert cli._door_closed_this_round(target) is True
    # Same want, next round: a repost is a new want to the memo.
    assert cli._door_closed_this_round({"target_id": "t-1", "round": 3}) is False
    # Said once.
    assert cli._remember_closed(target, "bidding_closed") is False


def test_other_errors_are_not_terminal():
    target = {"target_id": "t-2", "round": 1}
    assert cli._remember_closed(target, "draft_not_ready") is False
    assert cli._remember_closed(target, None) is False
    assert cli._door_closed_this_round(target) is False


def test_the_scan_skips_a_closed_want(monkeypatch):
    """Read the scan itself: the memo is consulted beside the reviewed keys."""
    import inspect
    source = inspect.getsource(cli)
    scan = source[source.index("reviewed = ("):]
    assert "if target_key in _CLOSED_TARGET_MEMO:" in scan[:2500]
