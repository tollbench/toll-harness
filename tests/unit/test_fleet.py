from concurrent.futures import ThreadPoolExecutor

from toll_harness.fleet import FleetStore


def test_fleet_admission_is_atomic_at_four_agents(tmp_path):
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    for index in range(5):
        fleet.register_agent(
            agent_id=f"agent-{index}",
            name=f"Agent {index}",
            config_path=tmp_path / f"agent-{index}.yaml",
        )

    def reserve(index):
        return fleet.reserve_proposal(
            target_id="target-1",
            agent_id=f"agent-{index}",
            idempotency_key=f"key-{index}",
            limit=4,
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(reserve, range(5)))

    assert sum(result.allowed for result in results) == 4
    assert sum(not result.allowed for result in results) == 1
    assert fleet.proposal_count("target-1") == 4


def test_fleet_reservation_is_idempotent_and_confirmed(tmp_path):
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    fleet.register_agent(
        agent_id="agent-1",
        name="Tanjiro",
        config_path=tmp_path / "tanjiro.yaml",
    )

    first = fleet.reserve_proposal(
        target_id="target-1",
        agent_id="agent-1",
        idempotency_key="first-key",
    )
    second = fleet.reserve_proposal(
        target_id="target-1",
        agent_id="agent-1",
        idempotency_key="different-key",
    )
    fleet.confirm_proposal(
        target_id="target-1",
        agent_id="agent-1",
        proposal_id="proposal-1",
    )
    confirmed = fleet.reserve_proposal(
        target_id="target-1",
        agent_id="agent-1",
        idempotency_key="third-key",
    )

    assert first.allowed is True
    assert second.existing is True
    assert second.idempotency_key == "first-key"
    assert confirmed.status == "confirmed"
    assert confirmed.proposal_id == "proposal-1"
    assert fleet.proposal_count("target-1") == 1


def test_fleet_persists_per_agent_market_reviews(tmp_path):
    path = tmp_path / "fleet.sqlite3"
    fleet = FleetStore(path)
    fleet.register_agent(
        agent_id="agent-1",
        name="Tanjiro",
        config_path=tmp_path / "tanjiro.yaml",
    )
    fleet.mark_targets_reviewed(
        agent_id="agent-1",
        targets=[("target-1:round:2", "target-1", "2")],
    )

    restarted = FleetStore(path)

    assert restarted.reviewed_target_keys("agent-1") == {"target-1:round:2"}
