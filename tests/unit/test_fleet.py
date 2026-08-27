import sqlite3
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
        config_path=tmp_path / "oakleaf.yaml",
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
        config_path=tmp_path / "oakleaf.yaml",
    )
    fleet.mark_targets_reviewed(
        agent_id="agent-1",
        targets=[("target-1:round:2", "target-1", "2")],
    )

    restarted = FleetStore(path)

    assert restarted.reviewed_target_keys("agent-1") == {"target-1:round:2"}


def test_slots_from_an_earlier_round_do_not_count_against_the_repost(tmp_path):
    # A want that failed and reposted opens a new round on the same target id.
    # Round-1 slots (even confirmed ones) must not answer for, or cap, round 2.
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    for index in range(5):
        fleet.register_agent(
            agent_id=f"agent-{index}",
            name=f"Agent {index}",
            config_path=tmp_path / f"agent-{index}.yaml",
        )
    for index in range(4):
        reservation = fleet.reserve_proposal(
            target_id="target-1",
            target_round="1",
            agent_id=f"agent-{index}",
            idempotency_key=f"r1-{index}",
        )
        assert reservation.allowed
        fleet.confirm_proposal(
            target_id="target-1",
            target_round="1",
            agent_id=f"agent-{index}",
            proposal_id=f"proposal-{index}",
        )
    assert not fleet.reserve_proposal(
        target_id="target-1",
        target_round="1",
        agent_id="agent-4",
        idempotency_key="r1-4",
    ).allowed

    # Round 2: the same agents get fresh slots and a fresh cap.
    round_two = fleet.reserve_proposal(
        target_id="target-1",
        target_round="2",
        agent_id="agent-0",
        idempotency_key="r2-0",
    )

    assert round_two.allowed is True
    assert round_two.existing is False
    assert round_two.status == "reserved"
    assert round_two.count == 1
    assert fleet.proposal_count("target-1", "1") == 4
    assert fleet.proposal_count("target-1", "2") == 1


def test_round_blind_ledger_migrates_in_place(tmp_path):
    # A fleet database written before rounds existed must open cleanly, keep
    # its slots (stamped round 1), and leave later rounds free.
    path = tmp_path / "fleet.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE fleet_agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            config_path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE proposal_slots (
            target_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('reserved', 'confirmed')),
            proposal_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (target_id, agent_id),
            FOREIGN KEY (agent_id) REFERENCES fleet_agents(agent_id)
        );
        CREATE INDEX ix_proposal_slots_target ON proposal_slots(target_id, status);
        INSERT INTO fleet_agents VALUES ('agent-1', 'Tanjiro', '/tmp/a1.yaml', 't');
        INSERT INTO proposal_slots VALUES
            ('target-1', 'agent-1', 'old-key', 'confirmed', 'proposal-1', 't', 't');
        """
    )
    connection.close()

    fleet = FleetStore(path)

    assert fleet.proposal_count("target-1", "1") == 1
    assert fleet.proposal_count("target-1", "2") == 0
    migrated = fleet.reserve_proposal(
        target_id="target-1",
        target_round="1",
        agent_id="agent-1",
        idempotency_key="new-key",
    )
    assert migrated.existing is True
    assert migrated.proposal_id == "proposal-1"
    fresh = fleet.reserve_proposal(
        target_id="target-1",
        target_round="2",
        agent_id="agent-1",
        idempotency_key="round-2-key",
    )
    assert fresh.allowed is True
    assert fresh.existing is False
