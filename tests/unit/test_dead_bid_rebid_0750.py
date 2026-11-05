"""A bid that ended does not end the agent's chance (0.75.0, 2026-11-05).

What forced it: Milo's bid on staging want 9b2fc180 was withdrawn. The bench
lets an agent bid again once its bid is withdrawn, rejected or expired (only a
LIVE bid blocks: "Withdraw it before refiling"), but the harness skipped the
want forever because the board and the brief still carried `your_bid`.
"""
import pytest

from toll_harness.fleet import FleetStore
from toll_harness.toll_bench.book_of_houses import DEAD_BID_STATUSES, bid_is_live

from tests.unit.test_toll_bench import FakeApi, _fleet_provider, _valid_proposal


@pytest.mark.parametrize("status", sorted(DEAD_BID_STATUSES))
def test_an_ended_bid_is_not_live(status):
    assert bid_is_live({"proposal_id": "p", "status": status}) is False
    assert bid_is_live({"proposal_id": "p", "status": status.upper()}) is False


@pytest.mark.parametrize("bid", [{"status": "filed"}, {"status": "accepted"},
                                 {"proposal_id": "p"}, "p-123"])
def test_a_live_or_unclear_bid_still_blocks(bid):
    assert bid_is_live(bid) is True


@pytest.mark.parametrize("bid", [None, {}])
def test_no_bid_is_not_live(bid):
    assert bid_is_live(bid) is False


@pytest.mark.parametrize("status", ["withdrawn", "rejected", "expired", "declined_by_agent"])
def test_the_provider_files_again_after_its_bid_ended(tmp_path, status):
    api = FakeApi()
    api.brief_your_bid = {"proposal_id": "p-old", "status": status}
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    provider = _fleet_provider(api, fleet, tmp_path=tmp_path)

    provider.submit_proposal("t1", _valid_proposal(), "key-1")

    assert len(api.submissions) == 1


def test_a_live_bid_still_short_circuits(tmp_path):
    api = FakeApi()
    api.brief_your_bid = {"proposal_id": "p-live", "status": "filed"}
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    provider = _fleet_provider(api, fleet, tmp_path=tmp_path)

    result = provider.submit_proposal("t1", _valid_proposal(), "key-1")

    assert result["idempotent"] is True
    assert api.submissions == []


def test_the_market_scan_keeps_a_want_whose_bid_was_withdrawn():
    from toll_harness import cli

    targets = [
        {"target_id": "t-withdrawn", "round": 1, "your_bid": {"status": "withdrawn"}},
        {"target_id": "t-live", "round": 1, "your_bid": {"status": "filed"}},
        {"target_id": "t-new", "round": 1, "your_bid": None},
    ]
    kept = [t["target_id"] for t in targets if not cli.bid_is_live(t.get("your_bid"))]
    assert kept == ["t-withdrawn", "t-new"]
