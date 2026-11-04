"""THE AGENT'S OWN RUN, LIVE, ON THE WANT'S LOG (0.75.0).

WHAT FORCED THESE TESTS (Steven, 2026-11-04). The server's one plain-text log
per want carried everything the bench saw and nothing the agent thought. He
picked "the harness streams everything": every model turn goes, live, to
POST /api/bench/targets/{target_id}/thinking. The promise is in
core/thinking.py and each test holds one line of it: the order a turn reads
in, the batching, the end of a run, a server that fails or hangs never
touching the run, the bounded queue, the secrets, the switch, the cap.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest
import yaml

from tests.unit.test_onboarding import _answers
from tests.unit.test_step_ask import BRIEF, CAFES, OBLIGATION, FakeBench, _model, _payload
from toll_harness import cli
from toll_harness import config as config_module
from toll_harness.core import thinking
from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.thinking import TEXT_CAP, ThinkingStream
from toll_harness.core.types import (
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from toll_harness.models.cli_rail import ClaudeCodeCliAdapter, claude_trail, codex_trail
from toll_harness.models.openai import OpenAIModelAdapter
from toll_harness.models.pricing import price_usd
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.onboarding import create_configuration
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.storage.secrets import FileSecretStore
from toll_harness.toll_bench.api import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.toll_bench.draft import DraftLoop
from toll_harness.toll_bench.step import StepAsk
from toll_harness.tools.registry import build_standard_registry

OWN_TOKEN = "tok-agent-own-7f3a9c2e51"


@pytest.fixture(autouse=True)
def _stream_on(monkeypatch):
    """The suite turns the stream off (tests/conftest.py); these tests are it."""
    monkeypatch.delenv("TOLL_HARNESS_THINKING", raising=False)


class FakeDoor:
    """The bench's thinking door, in memory: records every batch it takes."""

    def __init__(self, *, fail=None, delay=0.0, answer=None, hold=None):
        self.posts: list[tuple[str, dict]] = []
        self.calls = 0
        self.fail = fail
        self.delay = delay
        self.answer = answer
        self.hold = hold  # an Event the door waits on before answering
        self.entered = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, target_id, payload):
        with self.lock:
            self.calls += 1
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(10)
        if self.delay:
            time.sleep(self.delay)
        fail = self.fail(target_id) if callable(self.fail) else self.fail
        if fail is not None:
            raise fail
        with self.lock:
            self.posts.append((target_id, json.loads(json.dumps(payload))))
        return self.answer or {"ok": True, "written": len(payload["entries"]), "trimmed": 0,
                               "not_written": 0, "dropped_past_cap": 0, "notes": []}

    def entries(self, target=None):
        with self.lock:
            return [entry for posted, payload in self.posts for entry in payload["entries"]
                    if target is None or posted == target]


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _turn(text, *calls, input_tokens=900, output_tokens=60):
    tool_calls = [ToolCall(f"c{index}", name, args) for index, (name, args) in enumerate(calls)]
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}
        for call in tool_calls
    ]
    return ModelResponse(
        message=ModelMessage("assistant", content), text=text, tool_calls=tool_calls,
        usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                         total_tokens=input_tokens + output_tokens),
        stop_reason="tool_use" if tool_calls else "end_turn",
    )


def _runtime(tmp_path, model):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    return HarnessRuntime(
        model=model, state_store=store, event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["state.save", "state.load", "result.complete", "result.fail"],
    )


def _stream(door, **options):
    options.setdefault("flush_seconds", 60.0)
    return ThinkingStream(door, secrets=[OWN_TOKEN], **options)


HERE = {"target_id": "t-1", "run_id": "r-1"}


# ---------------------------------------------------------------------------
# 1. A turn reads in order, and the end of a run sends it
# ---------------------------------------------------------------------------
def test_a_turn_reads_model_call_think_each_call_then_each_result(tmp_path):
    door = FakeDoor()
    model = ScriptedModelAdapter([
        _turn("I will save where I am, then read it back.",
              ("state.save", {"checkpoint": {"status": "working"}}),
              ("state.load", {})),
        _turn("", ("result.complete", {"summary": "done"})),
    ])
    stream = model.thinking = _stream(door)  # 60 s timer: only the run's end sends it
    runtime = _runtime(tmp_path, model)

    with thinking.working_on(target_id="t-1", deal_id="d-1"):
        result = runtime.start("Walk the deal")

    assert result.status is RunStatus.COMPLETED
    assert _wait_for(lambda: len(door.entries()) == 9)
    kinds = [entry["kind"] for entry in door.entries()]
    assert kinds == ["model_call", "think", "tool_call", "tool_call", "tool_result",
                     "tool_result", "model_call", "tool_call", "tool_result"]
    entries = door.entries()
    assert entries[1]["text"] == "I will save where I am, then read it back."
    assert [entries[i]["tool"] for i in (2, 3, 4, 5)] == [
        "state.save", "state.load", "state.save", "state.load"]
    assert entries[2]["arguments"] == {"checkpoint": {"status": "working"}}
    assert entries[4]["is_error"] is False and isinstance(entries[4]["duration_ms"], int)
    first = entries[0]
    assert first["model"] == "scripted.test-v1"
    assert (first["tokens_in"], first["tokens_out"], first["cost_cents"]) == (900, 60, None)
    assert isinstance(first["duration_ms"], int)
    assert all(entry["at"].endswith("Z") for entry in entries)
    for target, payload in door.posts:
        assert target == "t-1"
        assert payload["deal_id"] == "d-1" and payload["run_id"] == result.run_id
        assert "step_id" not in payload
    assert stream.stats()["sent_entries"] == 9


def test_a_run_that_names_its_want_carries_what_came_before(tmp_path):
    door = FakeDoor()
    model = ScriptedModelAdapter([
        _turn("Looking around first.", ("state.load", {})),
        _turn("Now the want.", ("state.save", {"checkpoint": {"target_id": "x"},
                                               "target_id": "t-7"})),
        _turn("", ("result.complete", {"summary": "done"})),
    ])
    model.thinking = _stream(door)
    _runtime(tmp_path, model).start("Look, then work")

    assert _wait_for(lambda: len(door.entries()) == 11)
    texts = [entry.get("text") for entry in door.entries() if entry["kind"] == "think"]
    assert texts == ["Looking around first.", "Now the want."]
    assert {target for target, _ in door.posts} == {"t-7"}


def test_a_run_that_never_names_a_want_sends_nothing(tmp_path):
    door = FakeDoor()
    model = ScriptedModelAdapter([_turn("Nothing to do.", ("result.complete", {"summary": "ok"}))])
    stream = model.thinking = _stream(door, flush_seconds=0.05)
    _runtime(tmp_path, model).start("A goal with no want")
    stream.drain(1)
    assert door.calls == 0
    assert stream.stats()["queued"] == 0


# ---------------------------------------------------------------------------
# 2. Batching: by size, by time, and at the end
# ---------------------------------------------------------------------------
def test_batches_by_size(tmp_path):
    door = FakeDoor()
    stream = _stream(door, batch_size=3)
    for number in range(7):
        stream.note(f"line {number}", place=HERE)
    assert _wait_for(lambda: len(door.posts) == 2)
    assert [len(payload["entries"]) for _, payload in door.posts] == [3, 3]
    time.sleep(0.2)
    assert len(door.posts) == 2  # the seventh waits for the timer or a flush
    stream.flush()
    assert _wait_for(lambda: len(door.posts) == 3)
    assert [entry["text"] for entry in door.entries()] == [f"line {n}" for n in range(7)]


def test_batches_by_time():
    door = FakeDoor()
    stream = _stream(door, flush_seconds=0.4)
    started = time.monotonic()
    stream.note("one", place=HERE)
    stream.note("two", place=HERE)
    assert door.calls == 0
    assert _wait_for(lambda: len(door.posts) == 1, timeout=5)
    assert time.monotonic() - started >= 0.3
    assert [entry["text"] for entry in door.entries()] == ["one", "two"]


def test_a_batch_never_mixes_two_steps_and_keeps_the_order():
    door = FakeDoor()
    stream = _stream(door)
    stream.note("a", place={**HERE, "step_id": "s-1"})
    stream.note("b", place={**HERE, "step_id": "s-2"})
    stream.note("c", place={**HERE, "step_id": "s-1"})
    assert stream.drain(5)
    assert [(payload.get("step_id"), [e["text"] for e in payload["entries"]])
            for _, payload in door.posts] == [("s-1", ["a"]), ("s-2", ["b"]), ("s-1", ["c"])]


# ---------------------------------------------------------------------------
# 3. A server that fails or hangs never reaches the run
# ---------------------------------------------------------------------------
def test_a_failing_server_never_raises_into_the_run_and_is_counted(tmp_path, caplog):
    door = FakeDoor(fail=RuntimeError("the bench is down"))
    model = ScriptedModelAdapter([
        _turn("Saving.", ("state.save", {"checkpoint": {"n": 1}})),
        _turn("", ("result.complete", {"summary": "done"})),
    ])
    stream = model.thinking = _stream(door, batch_size=2)
    caplog.set_level(logging.WARNING, logger="toll_harness.thinking")
    with thinking.working_on(target_id="t-1"):
        result = _runtime(tmp_path, model).start("Walk it")
    assert result.status is RunStatus.COMPLETED
    assert _wait_for(lambda: stream.stats()["dropped_failed"] == 7)
    stats = stream.stats()
    assert stats["failed_batches"] == 4 and stats["sent_entries"] == 0
    lines = [r.getMessage() for r in caplog.records if r.name == "toll_harness.thinking"]
    assert len(lines) == 1  # rate-limited: one line, not one per batch
    assert "RuntimeError" in lines[0] and "the run goes on" in lines[0]


def test_a_door_that_says_no_is_counted_not_raised():
    door = FakeDoor(answer={"ok": False, "error": "bad_entries"})
    stream = _stream(door)
    stream.note("hello", place=HERE)
    assert stream.drain(5)
    assert stream.stats()["failed_batches"] == 1 and stream.stats()["dropped_failed"] == 1


def test_a_slow_server_never_delays_the_run(tmp_path):
    door = FakeDoor(delay=1.0)
    model = ScriptedModelAdapter(
        [_turn(f"turn {n}", ("state.save", {"checkpoint": {"n": n}})) for n in range(5)]
        + [_turn("", ("result.complete", {"summary": "done"}))]
    )
    model.thinking = _stream(door, batch_size=2)
    runtime = _runtime(tmp_path, model)
    started = time.monotonic()
    with thinking.working_on(target_id="t-1"):
        result = runtime.start("Walk it")
    elapsed = time.monotonic() - started
    assert result.status is RunStatus.COMPLETED
    assert elapsed < 0.9, elapsed  # the door takes a second per batch; the run never waited
    assert _wait_for(lambda: door.calls >= 1)
    model.thinking.close(0)


def test_recording_is_cheap_while_the_sender_is_stuck():
    release = threading.Event()
    door = FakeDoor(hold=release)
    stream = _stream(door, batch_size=1)
    stream.note("first", place=HERE)
    assert door.entered.wait(5)
    started = time.monotonic()
    for number in range(500):
        stream.note(f"n{number}", place=HERE)
    assert time.monotonic() - started < 0.5
    release.set()
    stream.close(0.1)


def test_the_queue_is_bounded_and_drops_the_oldest_counted(caplog):
    release = threading.Event()
    door = FakeDoor(hold=release)
    stream = _stream(door, batch_size=1, queue_cap=5)
    stream.note("0", place=HERE)
    assert door.entered.wait(5)  # the sender holds "0" and waits on the door
    caplog.set_level(logging.WARNING, logger="toll_harness.thinking")
    for number in range(1, 9):
        stream.note(str(number), place=HERE)
    assert stream.stats()["queued"] == 5
    assert stream.stats()["dropped_full"] == 3
    release.set()
    assert stream.drain(5)
    assert [entry["text"] for entry in door.entries()] == ["0", "4", "5", "6", "7", "8"]
    assert any("queue was full" in r.getMessage() for r in caplog.records)


def test_a_404_backs_off_quietly_for_that_want_only(caplog):
    class NotThere(Exception):
        status = 404

    door = FakeDoor(fail=lambda target: NotThere() if target == "t-gone" else None)
    stream = _stream(door)
    caplog.set_level(logging.WARNING, logger="toll_harness.thinking")
    stream.note("one", place={"target_id": "t-gone", "run_id": "r"})
    assert stream.drain(5)
    stream.note("two", place={"target_id": "t-gone", "run_id": "r"})
    stream.note("three", place={"target_id": "t-here", "run_id": "r"})
    assert stream.drain(5)
    assert door.calls == 2  # one knock on t-gone, then quiet; t-here still sent
    assert [entry["text"] for entry in door.entries("t-here")] == ["three"]
    assert stream.stats()["dropped_not_there"] == 2
    assert not [r for r in caplog.records if r.name == "toll_harness.thinking"]


def test_a_model_call_that_fails_is_a_note_and_the_error_is_the_callers():
    class Broken(ScriptedModelAdapter):
        def invoke(self, **_kwargs):
            raise ValueError("provider said no")

    door = FakeDoor()
    model = Broken([])
    model.thinking = _stream(door)
    with pytest.raises(ValueError, match="provider said no"):
        thinking.invoke(model, system="s", messages=[], tools=[], place=HERE)
    assert model.thinking.drain(5)
    [entry] = door.entries()
    assert entry["kind"] == "note" and entry["text"].startswith("model call failed after")
    assert "ValueError" in entry["text"] and "provider said no" not in entry["text"]


# ---------------------------------------------------------------------------
# 4. Secrets stay home; everything is cut at 20,000 characters
# ---------------------------------------------------------------------------
def test_secrets_are_masked_before_they_leave():
    door = FakeDoor()
    stream = _stream(door)
    response = _turn(
        "Using Bearer abcdefghijklmnopqrstuv and sk-ant-api03-abc123def456ghi and my own "
        f"token {OWN_TOKEN}.",
        ("http.request", {
            "api_key": "AKIAsomething-plain", "password": "hunter22",
            "headers": {"Authorization": "Bearer zzzzzzzzzzzzzzzz", "Accept": "json"},
            "note": f"the bench token is {OWN_TOKEN}", "max_tokens": 10,
            "clientSecret": "s3cr3t-value", "body": 'client_secret="abcd1234efgh"',
        }),
    )
    stream.model_turn(response, place=HERE)
    stream.tool_result("http.request", {
        "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        "data": "charge with rk_live_51Habc123def456 or pk_test_9xYz12345678",
        "stripe": "sk_live_51Hxyz0987654321abc",
        "count": 3,
    }, place=HERE)
    assert stream.drain(5)
    sent = json.dumps(door.posts)
    for secret in ("abcdefghijklmnopqrstuv", "sk-ant-api03", OWN_TOKEN, "hunter22",
                   "AKIAsomething", "zzzzzzzzzzzzzzzz", "s3cr3t-value", "abcd1234efgh",
                   "eyJhbGciOiJIUzI1NiJ9", "rk_live_51H", "pk_test_9xYz", "sk_live_51H"):
        assert secret not in sent, secret
    assert "[REDACTED]" in sent
    call = door.entries()[2]
    assert call["arguments"]["max_tokens"] == 10
    assert call["arguments"]["headers"]["Accept"] == "json"
    assert door.entries()[3]["result"]["count"] == 3


def test_the_runtime_sends_the_audit_view_of_an_http_request(tmp_path):
    door = FakeDoor()
    model = ScriptedModelAdapter([
        _turn("", ("result.complete", {"summary": "done"}))])
    stream = model.thinking = _stream(door)
    runtime = _runtime(tmp_path, model)
    stream.model_turn(
        _turn("", ("http.request", {"method": "GET", "url": "https://x.example/a?k={{secret:K}}",
                                    "headers": {"X-Api": "v"}, "body": "hello"})),
        place=HERE, audit=runtime._audit_arguments,
    )
    assert stream.drain(5)
    assert door.entries()[1]["arguments"] == {
        "method": "GET", "domain": "x.example", "header_names": ["X-Api"], "body_bytes": 5}


def test_text_results_and_arguments_are_cut_at_20000_characters():
    door = FakeDoor()
    stream = _stream(door)
    stream.model_turn(_turn("w" * 50_000, ("files.write", {"content": "c" * 30_000})),
                      place=HERE)
    stream.tool_result("files.read", {"content": "r" * 40_000}, place=HERE)
    stream.tool_result("files.read", "s" * 25_000, place=HERE)
    stream.note("n" * 21_000, place=HERE)
    assert stream.drain(5)
    _call, think, call, result, words, note = door.entries()
    assert len(think["text"]) <= TEXT_CAP
    kept = think["text"].index("... (")
    assert think["text"].endswith(f"... ({50_000 - kept} more characters)")
    assert set(call["arguments"]) == {"cut"}
    assert len(json.dumps(call["arguments"], ensure_ascii=False, separators=(",", ":"))) \
        <= TEXT_CAP
    assert call["arguments"]["cut"].endswith("more characters)")
    assert isinstance(result["result"], str) and len(result["result"]) <= TEXT_CAP
    assert result["result"].startswith('{"content":"rrr')
    assert len(words["result"]) <= TEXT_CAP and words["result"].endswith("more characters)")
    assert len(note["text"]) <= TEXT_CAP
    # something that fits rides as it is
    assert thinking.cap_text("short") == "short"
    assert thinking.prepare_result({"a": 1}) == {"a": 1}
    assert thinking.prepare_arguments({"a": 1}) == {"a": 1}
    # a five-megabyte result costs the run no more than a small one, and the
    # count of what was left out stays true
    huge = "z" * 5_000_000
    started = time.monotonic()
    cut = thinking.prepare_text(huge)
    assert time.monotonic() - started < 0.5
    kept = cut.index("... (")
    assert cut.endswith(f"... ({5_000_000 - kept} more characters)")


# ---------------------------------------------------------------------------
# 5. The switch
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("word", ["0", "off", "false", "FALSE", " no "])
def test_the_switch_off_sends_nothing(monkeypatch, word):
    monkeypatch.setenv("TOLL_HARNESS_THINKING", word)
    door = FakeDoor()
    model = ScriptedModelAdapter([_turn("hello", ("state.load", {}))])
    model.thinking = _stream(door, flush_seconds=0.01)
    response = thinking.invoke(model, system="s", messages=[], tools=[], place=HERE)
    thinking.tool_result(model, "state.load", {"ok": True}, place=HERE)
    thinking.note(model, "a note", place=HERE)
    assert response.text == "hello"  # the plain call, untouched
    time.sleep(0.1)
    assert door.calls == 0 and model.thinking.stats()["recorded"] == 0


def test_the_config_switch_reads_true_false_and_refuses_a_typo():
    assert thinking.configured_switch(None) is True
    assert thinking.configured_switch(True) is True
    assert thinking.configured_switch(False) is False
    assert thinking.configured_switch("off") is False
    with pytest.raises(ValueError, match="toll_bench.thinking"):
        thinking.configured_switch("maybe")


def _connected_config(tmp_path, **toll_bench):
    path = create_configuration(tmp_path / "rt", _answers(connected=False))
    config = yaml.safe_load(path.read_text())
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    config["toll_bench"].update({"connected": True, "token_secret": "bench_token",
                                 "base_url": "http://127.0.0.1:9"}, **toll_bench)
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    FileSecretStore(path.parent / config["secrets"]["directory"]).set("bench_token", OWN_TOKEN)
    return path


def _fake_model(monkeypatch):
    monkeypatch.setattr(
        config_module, "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )


def test_build_runtime_hangs_the_stream_on_the_model(tmp_path, monkeypatch):
    _fake_model(monkeypatch)
    resources = config_module.build_runtime(_connected_config(tmp_path))
    try:
        assert isinstance(resources.thinking, ThinkingStream)
        assert resources.runtime.model.thinking is resources.thinking
        assert resources.thinking._secrets == (OWN_TOKEN,)
    finally:
        resources.close()


@pytest.mark.parametrize("how", ["config", "env"])
def test_build_runtime_with_the_switch_off_has_no_stream(tmp_path, monkeypatch, how):
    _fake_model(monkeypatch)
    if how == "env":
        monkeypatch.setenv("TOLL_HARNESS_THINKING", "off")
        path = _connected_config(tmp_path)
    else:
        path = _connected_config(tmp_path, thinking=False)
    resources = config_module.build_runtime(path)
    try:
        assert resources.thinking is None
        assert getattr(resources.runtime.model, "thinking", None) is None
    finally:
        resources.close()


def test_a_harness_that_is_not_connected_has_no_stream(tmp_path, monkeypatch):
    _fake_model(monkeypatch)
    path = create_configuration(tmp_path / "rt", _answers(connected=False))
    config = yaml.safe_load(path.read_text())
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    resources = config_module.build_runtime(path)
    try:
        assert resources.thinking is None
    finally:
        resources.close()


# ---------------------------------------------------------------------------
# 6. The other two roads: the step ask and the draft loop
# ---------------------------------------------------------------------------
def test_the_step_ask_streams_its_turn_and_the_benchs_answer():
    door = FakeDoor()
    bench = FakeBench()
    model = _model(CAFES)
    model.thinking = _stream(door)
    ask = StepAsk(model, bench)
    with cli._thinking_scope(OBLIGATION, _payload()):
        out = ask.run(OBLIGATION, _payload(), brief=BRIEF, act_kinds=bench.list_act_kinds())
    assert out["ok"] is True
    assert _wait_for(lambda: len(door.entries()) == 3)  # the step's end sent it
    kinds = [entry["kind"] for entry in door.entries()]
    assert kinds == ["model_call", "tool_call", "tool_result"]
    call, result = door.entries()[1:]
    assert call["tool"] == result["tool"] == "submit_step_outcome"
    assert result["result"] == {"ok": True, "outcome_id": "o-1"} and result["is_error"] is False
    [(target, payload)] = door.posts
    assert target == "t1"
    assert (payload["deal_id"], payload["step_id"], payload["run_id"]) == ("d1", "s-1", ask.run_id)


def test_the_step_ask_says_why_a_reply_was_not_used():
    door = FakeDoor()
    model = _model("I think I will just chat about it.", "Still chatting.")
    model.thinking = _stream(door)
    StepAsk(model, FakeBench()).run(OBLIGATION, _payload(), brief=BRIEF)
    assert _wait_for(lambda: len(door.entries()) >= 3)
    notes = [entry["text"] for entry in door.entries() if entry["kind"] == "note"]
    assert notes and notes[0].startswith("the reply made no usable tool call")


def test_the_draft_loop_streams_its_turn_under_the_want():
    door = FakeDoor()
    answer = json.dumps({"decline": "Not a fit for this agent."})
    model = ScriptedModelAdapter([_turn(answer)])
    model.thinking = _stream(door)
    loop = DraftLoop(model, provider=None)
    loop.target_id = "t-9"
    loop._call("the tail", "bid")
    assert model.thinking.drain(5)
    [(target, payload)] = door.posts
    assert target == "t-9" and payload["run_id"] == loop.run_id
    assert [entry["kind"] for entry in payload["entries"]] == ["model_call", "think"]
    assert payload["entries"][1]["text"] == answer


def test_the_cli_scope_names_the_want_deal_and_step():
    with cli._thinking_scope({"kind": "deal_step", "target_id": "t", "deal_id": "d",
                              "step_id": "s"}):
        assert thinking.here(run_id="r") == {"target_id": "t", "deal_id": "d",
                                             "step_id": "s", "run_id": "r"}
        # a turn about another want drops the old deal and step
        assert thinking.here(target_id="t2") == {"target_id": "t2"}
    with cli._thinking_scope({"kind": "deal_step"}, {"deal": {"id": "d2",
                                                               "target_goal_id": "t3"},
                                                      "current_step": {"id": "s3"}}):
        assert thinking.here() == {"target_id": "t3", "deal_id": "d2", "step_id": "s3"}
    assert thinking.here() == {}


# ---------------------------------------------------------------------------
# 7. What a rail reports: tokens, cost, its reasoning, its native work
# ---------------------------------------------------------------------------
def test_tokens_and_cost_come_from_what_the_adapter_billed():
    class Billed(ScriptedModelAdapter):
        def invoke(self, **kwargs):
            self.count_spend(fresh_in=1000, cache_read=500, cache_write=100, out=200,
                             model="claude-haiku-4-5")
            return super().invoke(**kwargs)

    door = FakeDoor()
    model = Billed([_turn("ok"), _turn("ok")], model_id="claude_code/cli-default")
    model.thinking = _stream(door)
    thinking.invoke(model, system="s", messages=[], tools=[], place=HERE)
    assert model.thinking.drain(5)
    call = door.entries()[0]
    assert call["model"] == "claude-haiku-4-5"
    assert (call["tokens_in"], call["tokens_out"]) == (1600, 200)
    dollars = price_usd("claude-haiku-4-5", fresh_in=1000, cache_read=500, cache_write=100,
                        out=200)
    assert call["cost_cents"] == pytest.approx(round(dollars * 100, 4))
    assert getattr(model, "_spend_meter", None) is None  # the meter is put back

    class Unpriced(Billed):
        def invoke(self, **kwargs):
            self.count_spend(fresh_in=10, out=5, model="some-model-nobody-listed")
            return ScriptedModelAdapter.invoke(self, **kwargs)

    model = Unpriced([_turn("ok")])
    model.thinking = _stream(door)
    thinking.invoke(model, system="s", messages=[], tools=[], place=HERE)
    assert model.thinking.drain(5)
    assert door.entries()[-2]["cost_cents"] is None


CLAUDE_STREAM = "\n".join(json.dumps(event) for event in [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "The person wants cafes; search first."},
        {"type": "text", "text": "Searching the web."},
        {"type": "tool_use", "id": "tu1", "name": "WebSearch",
         "input": {"query": "quiet cafes Alberta St"}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu1",
         "content": [{"type": "text", "text": "Barista, Proud Mary"}]},
    ]}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": '{"text": "Done.", "tool_calls": []}'},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": '{"text": "Done.", "tool_calls": []}',
     "usage": {"input_tokens": 10, "output_tokens": 5}},
])


def test_the_claude_rail_hands_over_what_it_did_inside_the_call(tmp_path):
    trail = claude_trail(CLAUDE_STREAM, '{"text": "Done.", "tool_calls": []}')
    assert trail == [
        {"kind": "think", "text": "The person wants cafes; search first.", "reasoning": True},
        {"kind": "think", "text": "Searching the web."},
        {"kind": "tool_call", "tool": "WebSearch",
         "arguments": {"query": "quiet cafes Alberta St"}},
        {"kind": "tool_result", "tool": "WebSearch", "result": "Barista, Proud Mary",
         "is_error": False},
    ]
    runs = []

    def runner(argv, *, input, capture_output, text, timeout, cwd):
        runs.append(argv)
        return SimpleNamespace(returncode=0, stdout=CLAUDE_STREAM, stderr="")

    tools = [ToolDefinition("state.save", "Save", {"type": "object"})]
    messages = [ModelMessage.text("user", "Walk the deal")]
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)
    plain = adapter.invoke(system="s", messages=messages, tools=tools)
    assert plain.raw_metadata == {"model": adapter.model_id}  # no stream, as before

    door = FakeDoor()
    adapter.thinking = _stream(door)
    thinking.invoke(adapter, system="s", messages=messages, tools=tools, place=HERE)
    assert adapter.thinking.drain(5)
    assert [(entry["kind"], entry.get("text") or entry.get("tool") or entry.get("model"))
            for entry in door.entries()] == [("model_call", "claude_code/cli-default"),
            ("think", "(reasoning) The person wants cafes; search first."),
            ("think", "Searching the web."), ("tool_call", "WebSearch"),
            ("tool_result", "WebSearch"), ("think", "Done.")]


def test_the_codex_rail_reads_the_items_it_can_name():
    stdout = "\n".join(json.dumps(event) for event in [
        {"type": "item.completed", "item": {"type": "reasoning", "text": "Plan the search."}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls",
                                            "aggregated_output": "a.txt", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "something_new", "x": 1}},
        "not json",
        {"type": "item.completed", "item": {"type": "agent_message", "text": "final"}},
    ] if not isinstance(event, str)) + "\nnot json"
    assert codex_trail(stdout, "final") == [
        {"kind": "think", "text": "Plan the search.", "reasoning": True},
        {"kind": "tool_call", "tool": "shell", "arguments": {"command": "ls"}},
        {"kind": "tool_result", "tool": "shell", "result": "a.txt", "is_error": False},
    ]


def test_an_openai_compatible_rail_sends_its_reasoning_words():
    from tests.unit.test_openai_adapter import (
        FakeOpenAIClient,
        _Choice,
        _Message,
        _Response,
        _Usage,
    )

    message = _Message("Here is the answer.", None)
    message.reasoning = "First I weigh the two options."
    response = _Response([_Choice(message, "stop")], _Usage(100, 20, 120))
    adapter = OpenAIModelAdapter("gpt-example", client=FakeOpenAIClient(response))
    plain = adapter.invoke(system="s", messages=[ModelMessage.text("user", "hi")], tools=[])
    assert "reasoning_text" not in plain.raw_metadata  # no stream, as before

    door = FakeDoor()
    adapter.thinking = _stream(door)
    thinking.invoke(adapter, system="s", messages=[ModelMessage.text("user", "hi")], tools=[],
                    place=HERE)
    assert adapter.thinking.drain(5)
    assert [entry.get("text") for entry in door.entries()[1:]] == [
        "(reasoning) First I weigh the two options.", "Here is the answer."]


# ---------------------------------------------------------------------------
# 8. The door itself: the agent's bearer, its own timeout, no redirects
# ---------------------------------------------------------------------------
def test_post_thinking_knocks_on_the_wants_door_with_the_agents_bearer(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, timeout):
            seen.update(url=request.full_url, method=request.get_method(), timeout=timeout,
                        auth=request.get_header("Authorization"),
                        body=json.loads(request.data))
            return io.BytesIO(b'{"ok": true, "written": 1}')

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Opener())
    client = BookOfHousesApiClient(base_url="https://bench.example/", token=OWN_TOKEN)
    payload = {"run_id": "r", "entries": [{"at": "x", "kind": "note", "text": "hi"}]}
    assert client.post_thinking("t-1", payload) == {"ok": True, "written": 1}
    assert seen == {"url": "https://bench.example/api/bench/targets/t-1/thinking",
                    "method": "POST", "timeout": 10.0, "auth": f"Bearer {OWN_TOKEN}",
                    "body": payload}

    class Missing:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                         io.BytesIO(b'{"error": "not_found"}'))

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: Missing())
    with pytest.raises(BookOfHousesApiError) as raised:
        client.post_thinking("t-1", payload)
    assert raised.value.status == 404
