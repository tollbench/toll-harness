from __future__ import annotations

import json
import tempfile
from pathlib import Path

from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import ModelMessage, ModelResponse, ModelUsage, ToolCall
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.tools.registry import build_standard_registry


def tool_response(call_id: str, name: str, arguments: dict) -> ModelResponse:
    call = ToolCall(call_id, name, arguments)
    return ModelResponse(
        message=ModelMessage(
            "assistant",
            [{"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}],
        ),
        text="",
        tool_calls=[call],
        usage=ModelUsage(),
        stop_reason="tool_use",
    )


with tempfile.TemporaryDirectory(prefix="toll-harness-") as directory:
    root = Path(directory)
    store = SQLiteStore(root / "harness.sqlite3")
    model = ScriptedModelAdapter(
        [
            tool_response(
                "save-1", "state.save", {"checkpoint": {"status": "ready", "answer": 42}}
            ),
            tool_response("done-1", "result.complete", {"summary": "The answer is 42."}),
        ]
    )
    runtime = HarnessRuntime(
        model=model,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(root / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["state.save", "result.complete", "result.fail"],
    )
    result = runtime.start("Save the number 42, then complete with it.")
    print(
        json.dumps(
            {
                "intelligence": model.model_id,
                "harness_version": "0.1.0",
                "autonomy_mode": result.observed_mode.value,
                "status": result.status.value,
                "checkpoint": result.checkpoint.data,
                "result": result.result,
                "actions": [
                    event.payload
                    for event in store.list_events(result.run_id)
                    if event.kind == "tool.called"
                ],
                "usage": result.usage.__dict__,
            },
            indent=2,
        )
    )
