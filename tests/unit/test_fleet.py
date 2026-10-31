
from toll_harness.fleet import FleetStore


def test_fleet_persists_per_agent_market_reviews(tmp_path):
    path = tmp_path / "fleet.sqlite3"
    fleet = FleetStore(path)
    fleet.register_agent(
        agent_id="agent-1",
        name="Tanjiro",
        config_path=tmp_path / "oakleaf.yaml",
    )
    fleet.mark_targets_reviewed(
        agent_id="agent-1",
        targets=[("target-1:round:2", "target-1", "2")],
    )

    restarted = FleetStore(path)

    assert restarted.reviewed_target_keys("agent-1") == {"target-1:round:2"}



