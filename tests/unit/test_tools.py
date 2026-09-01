import json

import pytest

from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import AutonomyMode
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.storage.secrets import FileSecretStore
from toll_harness.tools.registry import (
    ToolContext,
    _validate,
    add_toll_bench_tools,
    build_standard_registry,
)


def _context(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    return ToolContext(run.id, store, store, FilesystemArtifactStore(tmp_path / "artifacts"), 0)


def test_state_save_rejects_secret_shaped_data_and_redacts_checkpoint(tmp_path):
    registry = build_standard_registry()
    context = _context(tmp_path)
    result = registry.execute(
        context,
        "call-1",
        "state.save",
        {"checkpoint": {"status": "working", "api_token": "must-not-persist"}},
    )

    assert result.is_error
    assert "secrets" in result.output["error"]
    assert context.state_store.load_checkpoint(context.run_id).data == {}


def test_tool_arguments_are_validated(tmp_path):
    registry = build_standard_registry()
    result = registry.execute(_context(tmp_path), "call-1", "result.complete", {})

    assert result.is_error
    assert "summary is required" in result.output["error"]


def test_secret_generate_creates_once_without_revealing_name_or_value(tmp_path):
    secret_name = "AGENT_NEW_ACCOUNT_PASSWORD"
    secret_store = FileSecretStore(tmp_path / "secrets")
    context = _context(tmp_path)
    context.secret_store = secret_store
    registry = build_standard_registry()

    arguments = {"secret_name": secret_name}
    first = registry.execute(context, "call-generate-1", "secret.generate", arguments)
    value = secret_store.get(secret_name)
    second = registry.execute(context, "call-generate-2", "secret.generate", arguments)

    assert not first.is_error
    assert first.output == {"ready": True, "created": True}
    assert second.output == {"ready": True, "created": False}
    assert secret_store.get(secret_name) == value
    assert isinstance(value, str) and len(value) >= 32
    serialized = json.dumps(
        {
            "result": first.output,
            "audit": HarnessRuntime._audit_arguments("secret.generate", arguments),
        }
    )
    assert secret_name not in serialized
    assert value not in serialized


def test_browser_type_secret_fills_agent_secret_without_returning_it(tmp_path):
    class FakeBrowser:
        def type(self, ref, text, submit=False):
            self.received = (ref, text, submit)
            return {"typed": ref, "submitted": submit, "url": "https://example.test/login"}

    secret_name = "AGENT_LOGIN_PASSWORD"
    secret_value = "owner-authorized-agent-secret-7f3a"
    secret_store = FileSecretStore(tmp_path / "secrets")
    secret_store.set(secret_name, secret_value)
    browser = FakeBrowser()
    context = _context(tmp_path)
    context.browser_provider = browser
    context.secret_store = secret_store
    registry = build_standard_registry()

    arguments = {"ref": "e2", "secret_name": secret_name, "submit": True}
    result = registry.execute(
        context,
        "call-secret",
        "browser.type_secret",
        arguments,
    )

    assert not result.is_error
    assert browser.received == ("e2", secret_value, True)
    serialized_result = json.dumps(result.output)
    assert secret_name not in serialized_result
    assert secret_value not in serialized_result
    audit = json.dumps(HarnessRuntime._audit_arguments("browser.type_secret", arguments))
    assert secret_name not in audit
    assert secret_value not in audit
    refused = registry.execute(
        context,
        "call-person-secret",
        "browser.type_secret",
        {"ref": "e2", "secret_name": "PERSON_PASSWORD"},
    )
    assert refused.is_error
    assert "must start with AGENT_" in refused.output["error"]


def test_numeric_exclusive_bounds_are_validated():
    schema = {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}

    _validate(0.99, schema)
    with pytest.raises(ValueError, match="less than 1"):
        _validate(1, schema)


def test_informed_plan_schema_requires_complete_execution_steps():
    registry = add_toll_bench_tools(build_standard_registry())
    definition = next(
        item
        for item in registry.definitions()
        if item.name == "toll_bench.submit_informed_plan"
    )
    step_schema = definition.input_schema["properties"]["plan"]["properties"]["steps"][
        "items"
    ]

    assert set(step_schema["required"]) == {
        "title",
        "ask",
        "outcome_promise",
        "person_minutes",
        "agent_court_estimate",
        "line_item_amount",
        "declared_odds",
    }


def test_step_reply_tool_requires_thread_and_idempotency_fields():
    registry = add_toll_bench_tools(build_standard_registry())
    definition = next(
        item for item in registry.definitions() if item.name == "toll_bench.reply_step_message"
    )

    assert set(definition.input_schema["required"]) == {
        "deal_id",
        "step_id",
        "reply",
        "idempotency_key",
    }
