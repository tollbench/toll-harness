"""Operator-authored instructions reach the run seed, uncapped and optional.

Steven's ruling: an operator can attach their own free-text instructions to
their agent with no length cap ("as much as they want, it's their agent"), and
those instructions must be delivered to the model on every run. They ride the
agent payload, distinct from the model's own state.save scratchpad, and are
omitted entirely when unset so existing agents are byte-for-byte unchanged.
"""

from __future__ import annotations

import json

from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import (
    AgentIdentity,
    AutonomyMode,
    EmailProvisioningStatus,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id="11111111-1111-4111-8111-111111111111",
        name="Testy",
        intelligence="TestModel",
        company="House of Test",
        harness="Toll Harness 0.1",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        email_provider="disabled",
        email_status=EmailProvisioningStatus.INELIGIBLE,
        email_verification_recipient=None,
        email_address=None,
    )


def _runtime(tmp_path, *, operator_instructions=None):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    identity = store.register_agent(_identity())
    runtime = HarnessRuntime(
        model=ScriptedModelAdapter([]),
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=None,
        enabled_tools=[],
        agent_identity=identity,
        operator_instructions=operator_instructions,
    )
    return runtime, store


def test_operator_instructions_reach_the_agent_payload(tmp_path):
    text = "Always sign off as Testy. " + ("word " * 5000)  # uncapped, long on purpose
    runtime, _ = _runtime(tmp_path, operator_instructions=text)

    payload = runtime._agent_payload()

    assert payload is not None
    assert payload["operator_instructions"] == text
    # No truncation or length cap: the exact text survives verbatim.
    assert len(payload["operator_instructions"]) == len(text)


def test_operator_instructions_ride_the_run_seed(tmp_path):
    text = "Prefer official over quick. Escalate anything touching real money."
    runtime, store = _runtime(tmp_path, operator_instructions=text)
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "TestModel", runtime.agent_identity.id)

    message, _cursor = runtime._initial_message(run.id)

    seed = json.loads(message.content[0]["text"])
    assert seed["agent_identity"]["operator_instructions"] == text


def test_absent_operator_instructions_omit_the_key(tmp_path):
    runtime, store = _runtime(tmp_path, operator_instructions=None)

    payload = runtime._agent_payload()
    assert "operator_instructions" not in payload

    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "TestModel", runtime.agent_identity.id)
    seed = json.loads(runtime._initial_message(run.id)[0].content[0]["text"])
    assert "operator_instructions" not in seed["agent_identity"]


def test_empty_operator_instructions_omit_the_key(tmp_path):
    # An empty string is falsey noise, not an instruction: omit it just like None.
    runtime, _ = _runtime(tmp_path, operator_instructions="")
    assert "operator_instructions" not in runtime._agent_payload()
