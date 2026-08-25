from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from toll_harness.core.types import (
    AgentIdentity,
    AutonomyMode,
    Checkpoint,
    EmailProvisioningStatus,
    Event,
    JsonObject,
    RunRecord,
    RunStatus,
)
from toll_harness.storage.base import EventStore, StateStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore(StateStore, EventStore):
    """Local state, run metadata, and append-only audit events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    intelligence TEXT NOT NULL,
                    company TEXT NOT NULL,
                    harness TEXT NOT NULL,
                    autonomy_mode TEXT NOT NULL
                        CHECK (autonomy_mode IN ('autonomous', 'supported')),
                    email_provider TEXT NOT NULL,
                    email_status TEXT NOT NULL,
                    email_verification_recipient TEXT,
                    email_address TEXT,
                    created_at TEXT NOT NULL,
                    CHECK (email_status != 'provisioned' OR email_address IS NOT NULL)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT REFERENCES agents(id),
                    goal TEXT NOT NULL,
                    requested_mode TEXT NOT NULL
                        CHECK (requested_mode IN ('autonomous', 'supported')),
                    status TEXT NOT NULL,
                    model TEXT NOT NULL,
                    operator_message_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    data_json TEXT NOT NULL,
                    event_cursor INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS knowledge (
                    namespace TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "agent_id" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN agent_id TEXT")
            agent_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(agents)").fetchall()
            }
            if "email_verification_recipient" not in agent_columns:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN email_verification_recipient TEXT"
                )

    def register_agent(self, identity: AgentIdentity) -> AgentIdentity:
        if identity.email_status is EmailProvisioningStatus.PROVISIONED:
            if not identity.email_address:
                raise ValueError("A provisioned email status requires the returned address")
        elif identity.email_address is not None:
            raise ValueError("Email address must be null until provisioning succeeds")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (identity.id,)).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO agents (
                       id, name, intelligence, company, harness, autonomy_mode,
                       email_provider, email_status, email_verification_recipient,
                       email_address, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        identity.id,
                        identity.name,
                        identity.intelligence,
                        identity.company,
                        identity.harness,
                        identity.autonomy_mode.value,
                        identity.email_provider,
                        identity.email_status.value,
                        identity.email_verification_recipient,
                        identity.email_address,
                        identity.created_at or _now(),
                    ),
                )
            else:
                expected = {
                    "name": identity.name,
                    "intelligence": identity.intelligence,
                    "company": identity.company,
                    "harness": identity.harness,
                    "autonomy_mode": identity.autonomy_mode.value,
                    "email_provider": identity.email_provider,
                    "email_verification_recipient": identity.email_verification_recipient,
                }
                differences = {
                    key: (row[key], value) for key, value in expected.items() if row[key] != value
                }
                if differences:
                    raise ValueError(
                        f"Permanent agent identity does not match its database: {differences}"
                    )
                stored_status = EmailProvisioningStatus(row["email_status"])
                if (
                    stored_status is EmailProvisioningStatus.PENDING_PROVISIONING
                    and identity.email_status is EmailProvisioningStatus.PROVISIONED
                    and identity.email_address
                ):
                    connection.execute(
                        "UPDATE agents SET email_status = ?, email_address = ? WHERE id = ?",
                        (identity.email_status.value, identity.email_address, identity.id),
                    )
                elif (
                    stored_status is not identity.email_status
                    or row["email_address"] != identity.email_address
                ):
                    raise ValueError(
                        "Permanent agent email can only transition from pending_provisioning "
                        "to a production-returned provisioned address"
                    )
        return self.get_agent(identity.id)

    def get_agent(self, agent_id: str) -> AgentIdentity:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown agent: {agent_id}")
        return AgentIdentity(
            id=row["id"],
            name=row["name"],
            intelligence=row["intelligence"],
            company=row["company"],
            harness=row["harness"],
            autonomy_mode=AutonomyMode(row["autonomy_mode"]),
            email_provider=row["email_provider"],
            email_status=EmailProvisioningStatus(row["email_status"]),
            email_verification_recipient=row["email_verification_recipient"],
            email_address=row["email_address"],
            created_at=row["created_at"],
        )

    def create_run(
        self,
        goal: str,
        mode: AutonomyMode,
        model: str,
        agent_id: str | None = None,
    ) -> RunRecord:
        if agent_id is not None:
            identity = self.get_agent(agent_id)
            if mode is not identity.autonomy_mode:
                raise ValueError("Run autonomy must match the permanent agent autonomy")
        run_id = uuid.uuid4().hex
        now = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO runs (
                   id, agent_id, goal, requested_mode, status, model,
                   operator_message_count, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    run_id,
                    agent_id,
                    goal,
                    mode.value,
                    RunStatus.RUNNING.value,
                    model,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?, 0, 0, ?)",
                (run_id, "{}", now),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return RunRecord(
            id=row["id"],
            goal=row["goal"],
            requested_mode=AutonomyMode(row["requested_mode"]),
            status=RunStatus(row["status"]),
            model=row["model"],
            agent_id=row["agent_id"],
            operator_message_count=row["operator_message_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now(), run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown run: {run_id}")

    def increment_operator_messages(self, run_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE runs SET operator_message_count = operator_message_count + 1,
                   updated_at = ? WHERE id = ?""",
                (_now(), run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown run: {run_id}")

    def load_checkpoint(self, run_id: str) -> Checkpoint:
        run = self.get_run(run_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Missing checkpoint for run: {run_id}")
        return Checkpoint(
            run_id=run_id,
            goal=run.goal,
            data=json.loads(row["data_json"]),
            event_cursor=row["event_cursor"],
            revision=row["revision"],
            updated_at=row["updated_at"],
        )

    def save_checkpoint(self, run_id: str, data: JsonObject, event_cursor: int) -> Checkpoint:
        serialized = json.dumps(data, separators=(",", ":"), sort_keys=True)
        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE checkpoints
                   SET data_json = ?, event_cursor = ?, revision = revision + 1, updated_at = ?
                   WHERE run_id = ?""",
                (serialized, event_cursor, _now(), run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown run: {run_id}")
        return self.load_checkpoint(run_id)

    def load_knowledge(self, namespace: str) -> JsonObject:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT data_json FROM knowledge WHERE namespace = ?", (namespace,)
            ).fetchone()
        return json.loads(row["data_json"]) if row else {}

    def save_knowledge(self, namespace: str, data: JsonObject) -> None:
        if not namespace.strip():
            raise ValueError("Knowledge namespace must not be empty")
        serialized = json.dumps(data, separators=(",", ":"), sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO knowledge (namespace, data_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(namespace) DO UPDATE SET
                   data_json = excluded.data_json, updated_at = excluded.updated_at""",
                (namespace, serialized, _now()),
            )

    def append_event(self, run_id: str, kind: str, source: str, payload: JsonObject) -> Event:
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["sequence"])
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, sequence, kind, source, json.dumps(payload), now),
            )
        return Event(run_id, sequence, kind, source, payload, now)

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[Event]:
        self.get_run(run_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence",
                (run_id, after_sequence),
            ).fetchall()
        return [
            Event(
                run_id=row["run_id"],
                sequence=row["sequence"],
                kind=row["kind"],
                source=row["source"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
