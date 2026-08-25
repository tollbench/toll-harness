from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_fleet_database() -> Path:
    return Path.home() / ".local/share/toll-harness/fleet.sqlite3"


@dataclass(frozen=True)
class ProposalReservation:
    allowed: bool
    target_id: str
    agent_id: str
    idempotency_key: str
    status: str | None = None
    proposal_id: str | None = None
    existing: bool = False
    count: int = 0
    limit: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "target_id": self.target_id,
            "agent_id": self.agent_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "proposal_id": self.proposal_id,
            "existing": self.existing,
            "count": self.count,
            "limit": self.limit,
        }


class FleetStore:
    """Operator-local admission ledger for proposals from this Harness fleet."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS fleet_agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    config_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposal_slots (
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
                CREATE INDEX IF NOT EXISTS ix_proposal_slots_target
                    ON proposal_slots(target_id, status);
                CREATE TABLE IF NOT EXISTS market_target_reviews (
                    agent_id TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    target_round TEXT,
                    reviewed_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, target_key),
                    FOREIGN KEY (agent_id) REFERENCES fleet_agents(agent_id)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def register_agent(self, *, agent_id: str, name: str, config_path: str | Path) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO fleet_agents(agent_id, name, config_path, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name=excluded.name,
                    config_path=excluded.config_path
                """,
                (agent_id, name, str(Path(config_path).resolve()), self._now()),
            )

    def reserve_proposal(
        self,
        *,
        target_id: str,
        agent_id: str,
        idempotency_key: str,
        limit: int = 4,
    ) -> ProposalReservation:
        if limit < 1:
            raise ValueError("Proposal limit must be positive")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT idempotency_key, status, proposal_id
                FROM proposal_slots
                WHERE target_id = ? AND agent_id = ?
                """,
                (target_id, agent_id),
            ).fetchone()
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM proposal_slots WHERE target_id = ?",
                    (target_id,),
                ).fetchone()[0]
            )
            if existing is not None:
                connection.commit()
                return ProposalReservation(
                    allowed=True,
                    target_id=target_id,
                    agent_id=agent_id,
                    idempotency_key=existing["idempotency_key"],
                    status=existing["status"],
                    proposal_id=existing["proposal_id"],
                    existing=True,
                    count=count,
                    limit=limit,
                )
            if count >= limit:
                connection.commit()
                return ProposalReservation(
                    allowed=False,
                    target_id=target_id,
                    agent_id=agent_id,
                    idempotency_key=idempotency_key,
                    count=count,
                    limit=limit,
                )
            now = self._now()
            connection.execute(
                """
                INSERT INTO proposal_slots(
                    target_id, agent_id, idempotency_key, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'reserved', ?, ?)
                """,
                (target_id, agent_id, idempotency_key, now, now),
            )
            connection.commit()
            return ProposalReservation(
                allowed=True,
                target_id=target_id,
                agent_id=agent_id,
                idempotency_key=idempotency_key,
                status="reserved",
                count=count + 1,
                limit=limit,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def confirm_proposal(self, *, target_id: str, agent_id: str, proposal_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE proposal_slots
                SET status='confirmed', proposal_id=?, updated_at=?
                WHERE target_id=? AND agent_id=?
                """,
                (proposal_id, self._now(), target_id, agent_id),
            )

    def release_reservation(self, *, target_id: str, agent_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM proposal_slots
                WHERE target_id=? AND agent_id=? AND status='reserved'
                """,
                (target_id, agent_id),
            )

    def proposal_count(self, target_id: str) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM proposal_slots WHERE target_id=?",
                    (target_id,),
                ).fetchone()[0]
            )

    def reviewed_target_keys(self, agent_id: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT target_key FROM market_target_reviews WHERE agent_id = ?",
                (agent_id,),
            ).fetchall()
        return {str(row["target_key"]) for row in rows}

    def mark_targets_reviewed(
        self,
        *,
        agent_id: str,
        targets: list[tuple[str, str, str | None]],
    ) -> None:
        if not targets:
            return
        now = self._now()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_target_reviews(
                    agent_id, target_key, target_id, target_round, reviewed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, target_key) DO UPDATE SET
                    reviewed_at=excluded.reviewed_at
                """,
                [
                    (agent_id, target_key, target_id, target_round, now)
                    for target_key, target_id, target_round in targets
                ],
            )
