import sqlite3
from dataclasses import replace

import pytest

from toll_harness.core.types import (
    AgentIdentity,
    AutonomyMode,
    EmailProvisioningStatus,
)
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.storage.secrets import FileSecretStore


def test_sqlite_separates_checkpoint_from_immutable_events(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("ship the report", AutonomyMode.AUTONOMOUS, "test-model")
    event = store.append_event(run.id, "fact", "human", {"value": 3})

    checkpoint = store.save_checkpoint(run.id, {"status": "working"}, event.sequence)

    assert checkpoint.data == {"status": "working"}
    assert checkpoint.revision == 1
    assert [item.payload for item in store.list_events(run.id)] == [{"value": 3}]


def test_artifacts_are_scoped_to_run_directory(tmp_path):
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    result = artifacts.write("abc123", "notes/result.txt", b"done")

    assert result["size"] == 4
    assert artifacts.read("abc123", "notes/result.txt") == b"done"
    assert artifacts.list("abc123") == [{"path": "notes/result.txt", "size": 4}]
    with pytest.raises(ValueError):
        artifacts.write("abc123", "../outside", b"blocked")


def test_sqlite_persists_opt_in_knowledge_by_namespace(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")

    store.save_knowledge("agent-one", {"useful_fact": "learned"})

    assert store.load_knowledge("agent-one") == {"useful_fact": "learned"}
    assert store.load_knowledge("agent-two") == {}


def test_permanent_agent_identity_is_immutable_and_modes_are_constrained(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    identity = AgentIdentity(
        id="c82a42a9-7ad6-49ae-827e-04c998302a60",
        name="Kori",
        intelligence="Mistral",
        company="House of Play",
        harness="Toll Harness 0.1",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        email_provider="book_of_houses",
        email_status=EmailProvisioningStatus.PENDING_PROVISIONING,
        email_verification_recipient="houseofplay@bookofhouses.com",
        email_address=None,
    )
    stored = store.register_agent(identity)

    assert stored.name == "Kori"
    assert stored.email_verification_recipient == "houseofplay@bookofhouses.com"
    assert stored.email_address is None
    with pytest.raises(ValueError, match="does not match"):
        store.register_agent(replace(identity, company="Another Company"))
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO runs (
                   id, agent_id, goal, requested_mode, status, model,
                   operator_message_count, created_at, updated_at
                   ) VALUES ('bad', ?, 'goal', 'hybrid', 'running', 'model', 0, 'now', 'now')""",
                (identity.id,),
            )

    provisioned = replace(
        identity,
        email_status=EmailProvisioningStatus.PROVISIONED,
        email_address="canonical-returned@bookofhouses.com",
    )
    assert store.register_agent(provisioned).email_address == provisioned.email_address
    with pytest.raises(ValueError, match="can only transition"):
        store.register_agent(replace(provisioned, email_address="changed@bookofhouses.com"))


def test_file_secret_store_is_owner_only_and_not_enumerable(tmp_path):
    store = FileSecretStore(tmp_path / "secrets")
    store.set("agent_token", "private-value")

    secret_path = tmp_path / "secrets" / "agent_token"
    assert store.get("agent_token") == "private-value"
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "secrets").stat().st_mode & 0o777 == 0o700
    assert not hasattr(store, "list")
