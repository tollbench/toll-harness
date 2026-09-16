"""Durable dispatch budgets. Error wording, clocks and restarts do not buy retries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATTEMPT_LIMIT = 3
# These describe polling or the agent's own activity, not fresh work.
_VOLATILE = frozenset(
    {
        "updated_at",
        "fetched_at",
        "server_time",
        "now",
        "age_seconds",
        "elapsed_seconds",
        "remaining_seconds",
        "retry_after_seconds",
        "latest_work_pulse",
        "pulse_due",
        "pulse_overdue",
        "unread_from_person",
        "agent_elapsed_seconds",
        "person_elapsed_seconds",
        "agent_clock_running_since",
        "last_polled_at",
        "last_seen_at",
    }
)


def fingerprint(value: Any) -> str:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items() if k not in _VOLATILE}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        return item

    raw = json.dumps(clean(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class LoopGuard:
    """Reserve before dispatch, including exceptions/crashes and both model roads.

    Each previously seen work-state gets three attempts, even if states alternate.
    Only fresh state or an explicit operator reset grants another budget. All
    reservations are atomic across processes sharing the same agent database.
    """

    def __init__(self, path: str | Path):
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS loop_guard (
                work_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                attempts INTEGER NOT NULL, last_attempt_at TEXT NOT NULL,
                PRIMARY KEY (work_key, fingerprint))""")

    def held(self, key: str, state: str) -> bool:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute(
                "SELECT attempts FROM loop_guard WHERE work_key=? AND fingerprint=?",
                (key, state),
            ).fetchone()
        return bool(row and row[0] >= ATTEMPT_LIMIT)

    def reserve(self, key: str, state: str) -> bool:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempts FROM loop_guard WHERE work_key=? AND fingerprint=?",
                (key, state),
            ).fetchone()
            attempts = row[0] if row else 0
            if attempts >= ATTEMPT_LIMIT:
                return False
            connection.execute(
                """INSERT INTO loop_guard VALUES (?,?,?,?)
                ON CONFLICT(work_key,fingerprint) DO UPDATE SET
                attempts=excluded.attempts,last_attempt_at=excluded.last_attempt_at""",
                (key, state, attempts + 1, datetime.now(timezone.utc).isoformat()),
            )
        return True

    def status(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM loop_guard ORDER BY last_attempt_at DESC"
            ).fetchall()
        return [dict(row, parked=row["attempts"] >= ATTEMPT_LIMIT) for row in rows]

    def reset(self, key: str) -> int:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            return connection.execute("DELETE FROM loop_guard WHERE work_key=?", (key,)).rowcount
