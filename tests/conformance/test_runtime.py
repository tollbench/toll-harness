import json

from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import (
    AutonomyMode,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunStatus,
    ToolCall,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.operator.channel import OperatorChannel
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry


def response(*calls):
    tool_calls = [
        ToolCall(f"call-{index}", name, arguments) for index, (name, arguments) in enumerate(calls)
    ]
    return ModelResponse(
        message=ModelMessage(
            "assistant",
            [
                {"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}
                for call in tool_calls
            ],
        ),
        text="",
        tool_calls=tool_calls,
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        stop_reason="tool_use",
    )


def make_runtime(tmp_path, model, knowledge_namespace=None):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    runtime = HarnessRuntime(
        model=model,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["state.save", "human.request", "result.complete", "result.fail"],
        knowledge_namespace=knowledge_namespace,
    )
    return runtime, store


def test_intelligence_saves_checkpoint_then_completes(tmp_path):
    model = ScriptedModelAdapter(
        [
            response(("state.save", {"checkpoint": {"status": "done", "important_facts": ["42"]}})),
            response(("result.complete", {"summary": "The answer is 42."})),
        ]
    )
    runtime, store = make_runtime(tmp_path, model)

    result = runtime.start("Find the answer", AutonomyMode.AUTONOMOUS)

    assert result.status is RunStatus.COMPLETED
    assert result.result == {"summary": "The answer is 42.", "evidence": []}
    assert result.checkpoint.data["important_facts"] == ["42"]
    assert result.usage.total_tokens == 30
    assert result.observed_mode is AutonomyMode.AUTONOMOUS
    assert [event.kind for event in store.list_events(result.run_id)].count("tool.called") == 2


def test_resume_uses_goal_checkpoint_and_only_new_events(tmp_path):
    model = ScriptedModelAdapter(
        [
            response(
                ("state.save", {"checkpoint": {"status": "waiting", "pending": ["name"]}}),
                ("human.request", {"question": "What is the name?"}),
            ),
            response(("result.complete", {"summary": "Received Ada"})),
        ]
    )
    runtime, _ = make_runtime(tmp_path, model)
    first = runtime.start("Learn the name")
    assert first.status is RunStatus.WAITING

    runtime.add_human_input(first.run_id, "Ada")
    second = runtime.resume(first.run_id)

    assert second.status is RunStatus.COMPLETED
    resume_payload = json.loads(model.invocations[1]["messages"][0].content[0]["text"])
    assert resume_payload["goal"] == "Learn the name"
    assert resume_payload["checkpoint"]["pending"] == ["name"]
    assert any(event["kind"] == "human.message" for event in resume_payload["new_events"])


def test_supported_run_is_classified_by_live_message(tmp_path):
    model = ScriptedModelAdapter(
        [
            response(("human.request", {"question": "Need a hint"})),
            response(("result.complete", {"summary": "Used the hint"})),
        ]
    )
    runtime, store = make_runtime(tmp_path, model)
    first = runtime.start("Solve it", AutonomyMode.SUPPORTED)
    OperatorChannel(store, store).message(first.run_id, "Look at record 7")

    result = runtime.resume(first.run_id)

    assert result.observed_mode is AutonomyMode.SUPPORTED


def test_opt_in_knowledge_is_available_to_a_later_run(tmp_path):
    teaching_model = ScriptedModelAdapter(
        [
            response(
                (
                    "state.save",
                    {
                        "checkpoint": {"status": "done"},
                        "persistent_knowledge": {"useful_fact": "record 7 is relevant"},
                    },
                )
            ),
            response(("result.complete", {"summary": "Learned"})),
        ]
    )
    teaching_runtime, _ = make_runtime(tmp_path, teaching_model, "named-agent")
    assert teaching_runtime.start("Learn a fact").status is RunStatus.COMPLETED

    later_model = ScriptedModelAdapter(
        [response(("result.complete", {"summary": "Reused knowledge"}))]
    )
    later_runtime, _ = make_runtime(tmp_path, later_model, "named-agent")
    assert later_runtime.start("Use the learned fact").status is RunStatus.COMPLETED

    payload = json.loads(later_model.invocations[0]["messages"][0].content[0]["text"])
    assert payload["persistent_knowledge"] == {"useful_fact": "record 7 is relevant"}


def test_three_failed_protected_writes_terminate_run(tmp_path):
    class RejectingBench:
        def submit_proposal(self, target_id, proposal, idempotency_key):
            return {"ok": False, "error": "invalid_proposal"}

    model = ScriptedModelAdapter(
        [
            response(
                (
                    "toll_bench.submit_proposal",
                    {"target_id": "t1", "proposal": {}, "idempotency_key": f"attempt-{index}"},
                )
            )
            for index in range(3)
        ]
    )
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    runtime = HarnessRuntime(
        model=model,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=add_toll_bench_tools(build_standard_registry()),
        enabled_tools=["toll_bench.submit_proposal"],
        toll_bench_provider=RejectingBench(),
    )

    result = runtime.start("Try an invalid protected write")

    assert result.status is RunStatus.FAILED
    assert result.result["reason"] == "Protected write attempt limit reached"
    assert result.result["failed_attempts"] == 3
    assert len(model.invocations) == 3
