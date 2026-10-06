"""A native tool_use on the claude_code rail is retried, never read as a crash (0.58.1).

WHAT FORCED IT: on 2026-10-05/06 lab agent Sam (claude_code rail, Opus 5.5)
went to hand back a file and the Claude Code CLI exited 1 fourteen times in
one night. `--tools ''` turns off the CLI's built-in tools only; the account's
claude.ai connectors still rode every request, so the model called the harness
tools natively (`files.list`, `toll_bench.post_check_in`), the CLI answered "No
such tool available", and the second turn broke `--max-turns 1`:
error_max_turns, stop_reason tool_use, exit 1. The adapter raised exit_1, the
run failed "Model invocation failed", and after three of those the loop guard
parked the step. The stream below is that failure's shape.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.base import ModelInvocationError
from toll_harness.models.cli_rail import ClaudeCodeCliAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.tools.registry import build_standard_registry

TOOLS = [
    ToolDefinition("files.write", "Write a file into the run folder", {"type": "object"}),
    ToolDefinition("result.complete", "Finish the run", {"type": "object"}),
    ToolDefinition("result.fail", "End the run", {"type": "object"}),
]
MESSAGES = [ModelMessage.text("user", "Make the flyer PDF and hand it back")]
ENVELOPE = json.dumps(
    {
        "text": "Writing the flyer.",
        "tool_calls": [
            {
                "name": "files.write",
                "arguments": {"path": "flyer.pdf", "content_base64": "JVBERi0="},
            }
        ],
    }
)


def _native_tool_stream(*names, usage=None):
    """What `claude -p --output-format stream-json --verbose --max-turns 1`
    printed when the model called tools natively: the assistant's tool_use,
    the CLI's own "No such tool" answer, then the error_max_turns result."""
    usage = usage or {"input_tokens": 2, "output_tokens": 650, "cache_read_input_tokens": 91151}
    lines = [
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": ""}]
                + [
                    {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": {}}
                    for i, name in enumerate(names)
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"toolu_{i}",
                        "is_error": True,
                        "content": f"<tool_use_error>Error: No such tool available: {name}"
                        "</tool_use_error>",
                    }
                    for i, name in enumerate(names)
                ]
            },
        },
        {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "stop_reason": "tool_use",
            "terminal_reason": "max_turns",
            "num_turns": 2,
            "errors": ["Reached maximum number of turns (1)"],
            "permission_denials": [],
            "session_id": "fb880e4a-6bb5-4670-86a4-86c91cee16b4",
            "usage": usage,
        },
    ]
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def _reply_stream(result, usage=None):
    usage = usage or {"input_tokens": 100, "output_tokens": 20}
    lines = [
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": result}]}},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "stop_reason": "end_turn",
            "num_turns": 1,
            "result": result,
            "usage": usage,
        },
    ]
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def _runner(replies):
    """Fake CLI: each reply is (returncode, stdout, stderr); one per call."""
    calls = []

    def run(argv, *, input, capture_output, text, timeout, cwd):
        calls.append({"argv": argv, "prompt": input})
        returncode, stdout, stderr = replies.pop(0)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


def test_native_tool_use_retries_once_with_the_reminder_then_succeeds(tmp_path):
    runner = _runner(
        [(1, _native_tool_stream("files.list", "state.load"), ""), (0, _reply_stream(ENVELOPE), "")]
    )
    adapter = ClaudeCodeCliAdapter("claude-opus-5-5", workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["files.write"]
    assert response.stop_reason == "tool_use"
    assert len(runner.calls) == 2
    first, retry = runner.calls[0]["prompt"], runner.calls[1]["prompt"]
    # The SAME call, with the reminder appended and nothing else changed.
    assert retry.startswith(first)
    reminder = retry[len(first):]
    assert "'files.list', 'state.load'" in reminder
    assert "There are no native tools on this call." in reminder
    assert "writing the JSON envelope" in reminder
    assert "files.write (base64 for binary) into the run folder" in reminder
    # Both calls are paid for, so both are counted.
    assert response.usage.output_tokens == 650 + 20


def test_two_native_tool_uses_in_a_row_fail_clearly_naming_the_tool(tmp_path):
    runner = _runner(
        [
            (1, _native_tool_stream("files.list"), ""),
            (1, _native_tool_stream("toll_bench.post_check_in"), ""),
        ]
    )
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert len(runner.calls) == 2  # one retry, never a third
    assert response.stop_reason == "native_tool_use"
    assert [call.name for call in response.tool_calls] == ["result.fail"]
    reason = response.tool_calls[0].arguments["reason"]
    assert "'toll_bench.post_check_in'" in reason
    assert "native tool call" in reason and "JSON envelope" in reason
    assert response.tool_calls[0].arguments["blocker"] == "native_tool_use"
    assert response.raw_metadata["native_tools"] == ["toll_bench.post_check_in"]
    assert response.usage.output_tokens == 1300


def test_two_native_tool_uses_on_a_call_without_result_fail_raise_a_named_error(tmp_path):
    runner = _runner(
        [(1, _native_tool_stream("files.list"), ""), (1, _native_tool_stream("files.list"), "")]
    )
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    with pytest.raises(ModelInvocationError) as excinfo:
        adapter.invoke(system="s", messages=MESSAGES, tools=[])

    assert excinfo.value.code == "native_tool_use"
    assert "'files.list'" in excinfo.value.message
    # A tools-free call is reminded there are no tools at all.
    reminder = runner.calls[1]["prompt"][len(runner.calls[0]["prompt"]):]
    assert "There are no tools on this call, native or otherwise." in reminder
    assert "files.write" not in reminder


def test_a_plain_exit_1_is_still_a_crash_and_is_not_retried(tmp_path):
    runner = _runner([(1, "", "Error: Claude Code process crashed")])
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    with pytest.raises(ModelInvocationError) as excinfo:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert excinfo.value.code == "exit_1"
    assert excinfo.value.message == "Error: Claude Code process crashed"
    assert len(runner.calls) == 1


def test_an_exit_1_error_result_that_is_not_a_tool_call_is_unchanged(tmp_path):
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "stop_reason": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        }
    )
    runner = _runner([(1, stdout, "")])
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    with pytest.raises(ModelInvocationError) as excinfo:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert excinfo.value.code == "exit_1"
    assert len(runner.calls) == 1


def test_a_json_format_tool_use_stop_is_retried_without_a_name(tmp_path):
    # An operator who puts --output-format json back in extra_args gets the
    # same retry; that format never names the tool.
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 2, "output_tokens": 5},
        }
    )
    runner = _runner([(1, stdout, ""), (0, _reply_stream(ENVELOPE), "")])
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["files.write"]
    assert (
        "Your previous reply ended on a native tool call, so nothing ran."
        in runner.calls[1]["prompt"]
    )


def test_native_tool_use_on_the_corrective_retry_is_retried_the_same_way(tmp_path):
    runner = _runner(
        [
            (0, _reply_stream("Sure, I will write the flyer now."), ""),
            (1, _native_tool_stream("files.write"), ""),
            (0, _reply_stream(ENVELOPE), ""),
        ]
    )
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["files.write"]
    assert len(runner.calls) == 3
    assert "could not be parsed" in runner.calls[2]["prompt"]
    assert "native tool call to 'files.write'" in runner.calls[2]["prompt"]


def test_strict_mcp_config_rides_once(tmp_path):
    runner = _runner([(0, _reply_stream(ENVELOPE), ""), (0, _reply_stream(ENVELOPE), "")])
    adapter = ClaudeCodeCliAdapter(
        workdir=tmp_path, runner=runner, extra_args=["--tools", "", "--effort", "high"]
    )
    adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
    argv = runner.calls[0]["argv"]
    assert argv.count("--strict-mcp-config") == 1
    assert argv[-4:] == ["--tools", "", "--effort", "high"]

    explicit = ClaudeCodeCliAdapter(
        workdir=tmp_path, runner=runner, extra_args=["--strict-mcp-config"]
    )
    explicit.invoke(system="s", messages=MESSAGES, tools=TOOLS)
    assert runner.calls[1]["argv"].count("--strict-mcp-config") == 1


def test_a_run_whose_model_stays_on_native_tools_fails_with_the_named_reason(tmp_path):
    # What the loop guard and the breaker read: the run's own failure words,
    # naming the tool, not "Model invocation failed" over an exit code.
    runner = _runner(
        [(1, _native_tool_stream("files.list"), ""), (1, _native_tool_stream("files.list"), "")]
    )
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    runtime = HarnessRuntime(
        model=adapter,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["files.write", "result.complete", "result.fail"],
    )

    result = runtime.start("Make the flyer PDF and hand it back")

    assert result.status.value == "failed"
    assert "'files.list'" in result.result["reason"]
    assert result.result["reason"] != "Model invocation failed"
    assert result.result["blocker"] == "native_tool_use"
