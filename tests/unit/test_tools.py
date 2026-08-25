import pytest

from toll_harness.core.types import AutonomyMode
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
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
