from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def default_fleet_database() -> Path:
    return Path.home() / ".local/share/toll-harness/fleet.sqlite3"


def market_target_key(target_id: str, round_value: str | None) -> str:
    """One key per (target, repost round). A repost reuses the target id and
    bumps the round, so keys — not bare ids — are what dedupe safely."""
    return f"{target_id}:round:{round_value or '1'}"


class FleetStore:
    """Operator-local memory of the wants each agent of this fleet has reviewed
    (0.74.0: the per-want proposal cap and its slots table are gone; how many
    proposals a want takes is the bench's to say)."""

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
        """Record this agent in the fleet ledger; idempotent by agent_id.

        Names stay unique per database (older databases carry UNIQUE(name) in
        their schema, and the check below keeps both old and new ones from
        ever reaching that constraint). Two configs with the same name but
        different agent ids get a clear error instead of a raw IntegrityError.
        """
        resolved = str(Path(config_path).resolve())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            clash = connection.execute(
                "SELECT agent_id, config_path FROM fleet_agents"
                " WHERE name = ? AND agent_id != ?",
                (name, agent_id),
            ).fetchone()
            if clash is not None:
                raise ValueError(
                    f"Fleet database {self.path} already has an agent named {name!r} "
                    f"(agent id {clash['agent_id']}, config {clash['config_path']}); "
                    f"{resolved} is a different agent (agent id {agent_id}), so give "
                    "this config its own fleet.database"
                )
            taken = connection.execute(
                "SELECT agent_id FROM fleet_agents WHERE config_path = ? AND agent_id != ?",
                (resolved, agent_id),
            ).fetchone()
            if taken is not None:
                raise ValueError(
                    f"Fleet database {self.path} already registers config {resolved} "
                    f"under agent id {taken['agent_id']}, not {agent_id}; give this "
                    "config its own fleet.database"
                )
            connection.execute(
                """
                INSERT INTO fleet_agents(agent_id, name, config_path, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name=excluded.name,
                    config_path=excluded.config_path
                """,
                (agent_id, name, resolved, self._now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

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

    def mark_target_reviewed(
        self,
        *,
        agent_id: str,
        target_id: str,
        target_round: str | None,
    ) -> None:
        round_value = str(target_round or "1")
        self.mark_targets_reviewed(
            agent_id=agent_id,
            targets=[(market_target_key(target_id, round_value), target_id, round_value)],
        )
