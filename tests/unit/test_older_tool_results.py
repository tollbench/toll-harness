"""TOOL RESULTS DON'T LIVE FOREVER (0.36.0, part three).

WHAT FORCED THESE TESTS. On the old road every tool result stayed in the
conversation for the rest of the run: a 48,529-character brief and a
213,097-character proposals list rode every one of nine calls on one fleet unit
(2026-09-09, 10:39-10:43), 79,500 input tokens a call. A result the model has
already read once is cut to its first 400 characters before the next call;
the one payload kept whole is the step it is walking.
"""
from __future__ import annotations

import json
import logging

from toll_harness.core.runtime import FOLD_KEEP_CHARS, FOLD_NOTE, HarnessRuntime
from toll_harness.core.types import (
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.tools.registry import build_standard_registry


def _call(name, arguments, call_id):
    call = ToolCall(call_id, name, arguments)
    return ModelResponse(
        message=ModelMessage("assistant", [{"type": "tool_call", "id": call.id, "name": name, "arguments": arguments}]),
        text="", tool_calls=[call],
        usage=ModelUsage(input_tokens=100, output_tokens=8, total_tokens=108), stop_reason="tool_use",
    )


def _runtime(tmp_path, model, *, extra_tools=()):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    registry = build_standard_registry()
    for name, output in extra_tools:
        registry.register(
            ToolDefinition(name, "a page", {"type": "object", "properties": {}, "required": []}),
            lambda _context, _arguments, output=output: output,
        )
    enabled = ["state.save", "result.complete", "result.fail"] + [n for n, _ in extra_tools]
    return HarnessRuntime(
        model=model, state_store=store, event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=registry, enabled_tools=enabled,
    )


def _tool_results(message):
    # messages: [goal, assistant, results, assistant, results, ...]
    return [b for b in message.content if b.get("type") == "tool_result"]


def test_a_result_the_model_already_read_is_folded_before_the_next_call(tmp_path, caplog):
    page = {"page": "x" * 5_000}
    model = ScriptedModelAdapter([
        _call("web.big", {}, "c1"),
        _call("web.big", {}, "c2"),
        _call("result.complete", {"summary": "done"}, "c3"),
    ])
    runtime = _runtime(tmp_path, model, extra_tools=[("web.big", page)])

    with caplog.at_level(logging.INFO, logger="toll_harness.runtime"):
        result = runtime.start("read two pages")

    assert result.status is RunStatus.COMPLETED
    # Call 2 saw the first result WHOLE: it had not been read yet.
    second = model.invocations[1]["messages"]
    assert _tool_results(second[2])[0]["output"] == page
    # Call 3: the first result is folded, the second (unread) is whole.
    third = model.invocations[2]["messages"]
    [old] = _tool_results(third[2])
    assert old["output"]["note"] == FOLD_NOTE
    assert len(old["output"]["older_result"]) == FOLD_KEEP_CHARS
    assert old["name"] == "web.big" and old["call_id"] == "c1"
    [fresh] = _tool_results(third[4])
    assert fresh["output"] == page
    assert "folded 1 older tool result(s) before model call 3" in caplog.text


def test_the_step_payload_is_kept_whole(tmp_path):
    step = {"ok": True, "current_step": {"id": "s-1", "title": "y" * 3_000}, "acts": []}
    model = ScriptedModelAdapter([
        _call("toll_bench.current_step", {}, "c1"),
        _call("toll_bench.current_step", {}, "c2"),
        _call("result.complete", {"summary": "done"}, "c3"),
    ])
    # The step payload is kept whole BY NAME, whatever hands it back.
    runtime = _runtime(tmp_path, model, extra_tools=[("toll_bench.current_step", step)])

    runtime.start("walk the step")

    third = model.invocations[2]["messages"]
    assert _tool_results(third[2])[0]["output"] == step


def test_a_small_result_and_a_folded_one_are_left_alone(tmp_path):
    small = {"ok": True}
    model = ScriptedModelAdapter([
        _call("web.small", {}, "c1"),
        _call("web.small", {}, "c2"),
        _call("web.small", {}, "c3"),
        _call("result.complete", {"summary": "done"}, "c4"),
    ])
    runtime = _runtime(tmp_path, model, extra_tools=[("web.small", small)])
    runtime.start("three small reads")
    fourth = model.invocations[3]["messages"]
    assert all(_tool_results(m)[0]["output"] == small for m in fourth[2::2][:3])


def test_folding_twice_does_not_fold_the_fold(tmp_path):
    page = {"page": "z" * 2_000}
    model = ScriptedModelAdapter([
        _call("web.big", {}, "c1"),
        _call("web.big", {}, "c2"),
        _call("web.big", {}, "c3"),
        _call("result.complete", {"summary": "done"}, "c4"),
    ])
    runtime = _runtime(tmp_path, model, extra_tools=[("web.big", page)])
    runtime.start("three pages")
    fourth = model.invocations[3]["messages"]
    first_seen_at_call_three = _tool_results(model.invocations[2]["messages"][2])[0]["output"]
    first_seen_at_call_four = _tool_results(fourth[2])[0]["output"]
    assert first_seen_at_call_four == first_seen_at_call_three
    assert json.dumps(first_seen_at_call_four)  # still plain JSON for every adapter
