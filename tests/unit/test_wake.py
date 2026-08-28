import time
from argparse import Namespace
from types import SimpleNamespace

import pytest

from toll_harness import cli
from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import (
    AutonomyMode,
    Checkpoint,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunResult,
    RunStatus,
    ToolCall,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.tools.registry import (
    WAKE_TIMERS_NAMESPACE,
    ToolContext,
    build_standard_registry,
)


def _run_result(run_id):
    return RunResult(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        result={"summary": "Handled."},
        checkpoint=Checkpoint(
            run_id=run_id,
            goal="goal",
            data={},
            event_cursor=0,
            revision=0,
            updated_at="2026-08-27T00:00:00Z",
        ),
        usage=ModelUsage(),
        iterations=1,
        observed_mode=AutonomyMode.AUTONOMOUS,
    )


def _resources(tmp_path, email_provider=None):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    resumed = []

    def resume(run_id, *, cause=None, note=None):
        resumed.append({"run_id": run_id, "cause": cause, "note": note})
        return _run_result(run_id)

    runtime = SimpleNamespace(resume=resume, email_provider=email_provider)
    return SimpleNamespace(store=store, runtime=runtime), resumed


def test_wake_set_timer_persists_the_wake_and_parks_the_run(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    context = ToolContext(
        run.id, store, store, FilesystemArtifactStore(tmp_path / "artifacts"), 0
    )
    registry = build_standard_registry()

    before = time.time()
    result = registry.execute(
        context,
        "call-1",
        "wake.set_timer",
        {"seconds": 3600, "note": "follow up with the venue"},
    )

    assert not result.is_error
    assert result.output["wake_at"].endswith("+00:00")
    entry = store.load_knowledge(WAKE_TIMERS_NAMESPACE)[run.id]
    assert before + 3600 <= entry["wake_at"] <= time.time() + 3600
    assert entry["note"] == "follow up with the venue"
    assert context.wait_requested is True


def test_wake_set_timer_bounds_are_validated(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    context = ToolContext(
        run.id, store, store, FilesystemArtifactStore(tmp_path / "artifacts"), 0
    )
    registry = build_standard_registry()

    result = registry.execute(context, "call-1", "wake.set_timer", {"seconds": 30})

    assert result.is_error
    assert "minimum" in result.output["error"]
    assert store.load_knowledge(WAKE_TIMERS_NAMESPACE) == {}


def test_due_timer_wakes_the_run_and_clears_the_timer(tmp_path):
    resources, resumed = _resources(tmp_path)
    resources.store.save_knowledge(
        WAKE_TIMERS_NAMESPACE, {"run-1": {"wake_at": time.time() - 5, "note": "poke"}}
    )

    woken = cli._process_wakes(resources)

    assert resumed == [{"run_id": "run-1", "cause": "timer", "note": "poke"}]
    assert woken[0]["cause"] == "timer"
    assert woken[0]["run"]["status"] == "completed"
    assert cli._pending_wake_timers(resources.store) == {}


def test_future_timer_is_left_parked_and_bounds_the_sleep(tmp_path):
    resources, resumed = _resources(tmp_path)
    wake_at = time.time() + 120
    resources.store.save_knowledge(WAKE_TIMERS_NAMESPACE, {"run-1": {"wake_at": wake_at}})

    assert cli._process_wakes(resources) == []
    assert resumed == []
    assert cli._earliest_wake_at(resources) == wake_at


def test_new_inbound_email_wakes_parked_runs_early(tmp_path):
    inbox = {"threads": [{"thread_id": "t1", "last_inbound_at": "2026-08-27T00:00:00+00:00"}]}
    provider = SimpleNamespace(list=lambda limit=50: inbox["threads"])
    resources, resumed = _resources(tmp_path, email_provider=provider)
    resources.store.save_knowledge(
        WAKE_TIMERS_NAMESPACE,
        {"run-1": {"wake_at": time.time() + 3600, "note": "waiting on a reply"}},
    )

    # First observation baselines the cursor: history never wakes anything.
    assert cli._process_wakes(resources) == []
    assert resumed == []

    # New mail lands; the parked run wakes before its timer, cause inbound_email.
    inbox["threads"] = [{"thread_id": "t1", "last_inbound_at": "2026-08-28T09:00:00+00:00"}]
    woken = cli._process_wakes(resources)

    assert resumed == [
        {"run_id": "run-1", "cause": "inbound_email", "note": "waiting on a reply"}
    ]
    assert woken[0]["cause"] == "inbound_email"
    assert cli._pending_wake_timers(resources.store) == {}

    # The cursor advanced: the same mail wakes nothing twice.
    resources.store.save_knowledge(
        WAKE_TIMERS_NAMESPACE, {"run-2": {"wake_at": time.time() + 3600}}
    )
    assert cli._process_wakes(resources) == []


def test_email_check_failure_does_not_kill_the_cycle(tmp_path):
    def broken_list(limit=50):
        raise RuntimeError("mail briefly down")

    provider = SimpleNamespace(list=broken_list)
    resources, resumed = _resources(tmp_path, email_provider=provider)
    resources.store.save_knowledge(
        WAKE_TIMERS_NAMESPACE, {"run-1": {"wake_at": time.time() + 3600}}
    )

    assert cli._process_wakes(resources) == []
    assert resumed == []
    assert cli._pending_wake_timers(resources.store) != {}


def test_unresumable_run_timer_is_cleared_not_retried(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")

    def resume(run_id, *, cause=None, note=None):
        raise ValueError("Run is already terminal: completed")

    resources = SimpleNamespace(
        store=store, runtime=SimpleNamespace(resume=resume, email_provider=None)
    )
    store.save_knowledge(WAKE_TIMERS_NAMESPACE, {"run-1": {"wake_at": time.time() - 5}})

    woken = cli._process_wakes(resources)

    assert woken[0]["error"] == "wake_failed"
    assert cli._pending_wake_timers(store) == {}


def test_watch_sleep_never_passes_a_pending_wake(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    store.save_knowledge(WAKE_TIMERS_NAMESPACE, {"run-1": {"wake_at": time.time() + 5}})
    resources = SimpleNamespace(
        store=store,
        runtime=SimpleNamespace(email_provider=None, resume=None),
        close=lambda: None,
    )
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(
        cli,
        "_process_market_attention",
        lambda _resources, _wait, previous_failure=None: {
            "ok": True,
            "attention_count": 0,
            "reachability": None,
            "run": None,
        },
    )
    delays = []

    def sleep(delay):
        delays.append(delay)
        raise RuntimeError("stop test loop")

    monkeypatch.setattr(cli.time, "sleep", sleep)

    with pytest.raises(RuntimeError, match="stop test loop"):
        cli.command_market_watch(
            Namespace(config="agent.yaml", wait=20, interval=300.0, once=False)
        )

    assert 1.0 <= delays[0] <= 5.0


def _response(*calls):
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


def test_timer_wake_resumes_a_parked_run_end_to_end(tmp_path):
    model = ScriptedModelAdapter(
        [
            _response(("wake.set_timer", {"seconds": 60, "note": "check back"})),
            _response(("result.complete", {"summary": "Followed up."})),
        ]
    )
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    runtime = HarnessRuntime(
        model=model,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["wake.set_timer", "result.complete", "result.fail"],
    )

    started = runtime.start("Follow up later", AutonomyMode.AUTONOMOUS)
    assert started.status is RunStatus.WAITING

    # Bring the wake time due, then run one worker wake pass.
    timers = store.load_knowledge(WAKE_TIMERS_NAMESPACE)
    timers[started.run_id]["wake_at"] = time.time() - 1
    store.save_knowledge(WAKE_TIMERS_NAMESPACE, timers)
    resources = SimpleNamespace(store=store, runtime=runtime)

    woken = cli._process_wakes(resources)

    assert woken[0]["cause"] == "timer"
    assert woken[0]["run"]["status"] == "completed"
    resumed_events = [
        event for event in store.list_events(started.run_id) if event.kind == "run.resumed"
    ]
    assert resumed_events[0].payload == {"cause": "timer", "note": "check back"}
    assert cli._pending_wake_timers(store) == {}
