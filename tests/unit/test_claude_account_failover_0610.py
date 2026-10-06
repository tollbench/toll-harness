"""Claude account failover on the claude_code rail (0.61.0).

WHAT FORCED IT (Steven, 2026-10-06): the lab's Claude-brained agents ride one
Claude subscription, and when it hits its usage limit every one of them stops
until the reset while a second signed-in account on the same machine sits
idle. The harness must never hobble an agent, and a general gap is fixed in
the harness. The rail now takes an ordered list of Claude config directories
and moves a call that comes back at a usage limit to the next account.

The streams below are what Claude Code 2.1.280 printed under `claude -p
--output-format stream-json --verbose --max-turns 1` against an API stand-in
that answered each way (the init line is dropped; every other field is
verbatim apart from the session ids).
"""

from __future__ import annotations

import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

from toll_harness.config import build_model_adapter
from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.base import ModelInvocationError
from toll_harness.models.claude_accounts import (
    CONFIG_DIRS_ENV,
    AccountTable,
    detect_usage_limit,
    resolve_config_dirs,
)
from toll_harness.models.cli_rail import ClaudeCodeCliAdapter, _read_claude_output

NOW = 1791319731.0  # 2026-10-06T20:48:51Z, 13:48:51 Pacific
RESETS_AT = 1791326931  # two hours later, what the stand-in's reset header said
A = "/accounts/one/.claude"
B = "/accounts/two/.claude-b"

TOOLS = [
    ToolDefinition("state.save", "Persist a checkpoint", {"type": "object"}),
    ToolDefinition("result.complete", "Finish the run", {"type": "object"}),
]
MESSAGES = [ModelMessage.text("user", "Walk the target briefing")]
ENVELOPE = json.dumps({"text": "Saving.", "tool_calls": [{"name": "state.save", "arguments": {}}]})


def _stream(*events):
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _result(**fields):
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "stop_reason": "end_turn",
        "num_turns": 1,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }
    base.update(fields)
    return base


def _api_error(error, text):
    return {
        "type": "assistant",
        "message": {
            "model": "<synthetic>",
            "role": "assistant",
            "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": text}],
        },
        "error": error,
        "is_api_error_message": True,
    }


SESSION_LIMIT_TEXT = "You've hit your session limit · resets 3:48pm (America/Los_Angeles)"

# A subscription sign-in answered with 429 + anthropic-ratelimit-unified-status:
# rejected. The CLI does not retry; it exits 1.
LIMIT_STREAM = _stream(
    {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "resetsAt": RESETS_AT,
            "rateLimitType": "five_hour",
            "isUsingOverage": False,
            "unifiedWindows": {"five_hour": {"utilization": 1, "resetsAt": RESETS_AT}},
        },
    },
    _api_error("rate_limit", SESSION_LIMIT_TEXT),
    _result(
        is_error=True,
        stop_reason="stop_sequence",
        terminal_reason="api_error",
        api_error_status=429,
        result=SESSION_LIMIT_TEXT,
        usage={"input_tokens": 0, "output_tokens": 0},
    ),
)

# A plain 429 with no unified headers, after the CLI's own ten retries: a
# server-side throttle the CLI itself says is NOT the usage limit.
THROTTLE_TEXT = (
    "API Error: Server is temporarily limiting requests (not your usage limit) · "
    "Number of request tokens has exceeded your per-minute rate limit"
)
THROTTLE_STREAM = _stream(
    {"type": "system", "subtype": "api_retry", "attempt": 10, "error_status": 429,
     "error": "rate_limit"},
    {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected",
                                                     "isUsingOverage": False}},
    _api_error("rate_limit", THROTTLE_TEXT),
    _result(is_error=True, stop_reason="stop_sequence", terminal_reason="api_error",
            api_error_status=429, result=THROTTLE_TEXT),
)

OVERLOADED_TEXT = (
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary — try "
    "again in a moment."
)
OVERLOADED_STREAM = _stream(
    _api_error("server_error", OVERLOADED_TEXT),
    _result(is_error=True, stop_reason="stop_sequence", terminal_reason="api_error",
            api_error_status=529, result=OVERLOADED_TEXT),
)

TOO_LONG_TEXT = (
    "Prompt is too long · this conversation is a single exchange and cannot be compacted"
)
TOO_LONG_STREAM = _stream(
    _api_error("invalid_request", TOO_LONG_TEXT),
    _result(is_error=True, stop_reason="stop_sequence", terminal_reason="prompt_too_long",
            api_error_status=400, result=TOO_LONG_TEXT),
)


def _ok_stream(text=ENVELOPE):
    return _stream(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        _result(result=text),
    )


def _runner(replies):
    """Fake CLI: each reply is (returncode, stdout) or an exception to raise."""
    calls = []

    def run(argv, *, input, capture_output, text, timeout, cwd, **kwargs):
        calls.append({"argv": argv, "prompt": input, "kwargs": kwargs})
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        returncode, stdout = reply
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


def _account(call):
    env = call["kwargs"].get("env")
    return None if env is None else env.get("CLAUDE_CONFIG_DIR")


@pytest.fixture(autouse=True)
def _no_ambient_accounts(monkeypatch):
    monkeypatch.delenv(CONFIG_DIRS_ENV, raising=False)
    monkeypatch.delenv("TOLL_HARNESS_CLAUDE_ACCOUNTS_FILE", raising=False)


def _adapter(tmp_path, runner, dirs=(A, B), clock=lambda: NOW):
    return ClaudeCodeCliAdapter(
        "claude-opus-5-5",
        workdir=tmp_path / "rail",
        runner=runner,
        config_dirs=list(dirs) if dirs is not None else None,
        accounts_file=tmp_path / "claude-accounts.json",
        clock=clock,
    )


def _table(tmp_path):
    path = tmp_path / "claude-accounts.json"
    return json.loads(path.read_text())["accounts"] if path.exists() else {}


# --- one account (or none): the pre-0.61 call, byte for byte ---------------


def test_no_accounts_configured_is_the_old_call_exactly(tmp_path):
    runner = _runner([(0, _ok_stream())])
    adapter = _adapter(tmp_path, runner, dirs=None)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["state.save"]
    assert adapter.config_dirs == []
    # No environment handed to the CLI: it inherits the harness's own.
    assert runner.calls[0]["kwargs"] == {}
    assert runner.calls[0]["argv"][:7] == [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1"
    ]


def test_one_account_runs_on_it_and_a_limit_is_todays_error(tmp_path):
    runner = _runner([(1, LIMIT_STREAM)])
    adapter = _adapter(tmp_path, runner, dirs=[A])

    with pytest.raises(ModelInvocationError) as raised:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert raised.value.code == "exit_1"  # what 0.60.0 raised for this stream
    assert len(runner.calls) == 1
    assert _account(runner.calls[0]) == A
    assert not (tmp_path / "claude-accounts.json").exists()


# --- the failover ------------------------------------------------------------


def test_limit_on_the_first_account_retries_the_same_prompt_on_the_next(tmp_path, caplog):
    runner = _runner([(1, LIMIT_STREAM), (0, _ok_stream())])
    adapter = _adapter(tmp_path, runner)

    with caplog.at_level(logging.INFO, logger="toll_harness"):
        response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["state.save"]
    assert [_account(call) for call in runner.calls] == [A, B]
    assert runner.calls[0]["prompt"] == runner.calls[1]["prompt"]
    assert runner.calls[0]["argv"] == runner.calls[1]["argv"]
    entry = _table(tmp_path)[A]
    assert entry["rejected_until"] == RESETS_AT
    assert entry["reason"] == "usage_limit:five_hour"
    assert entry["label"] == ".claude"
    assert B not in _table(tmp_path)
    switches = [r.getMessage() for r in caplog.records if "claude_account_switch" in r.getMessage()]
    assert len(switches) == 1
    assert switches[0].startswith(
        "claude_account_switch from=.claude to=.claude-b reason=usage_limit:five_hour "
        "resets_at=2026-10-06T22:48:51Z source=this_call"
    )
    assert "You've hit your session limit" in switches[0]


def test_the_next_call_starts_on_the_account_that_works(tmp_path, caplog):
    runner = _runner([(1, LIMIT_STREAM), (0, _ok_stream()), (0, _ok_stream())])
    adapter = _adapter(tmp_path, runner)
    adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="toll_harness"):
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [_account(call) for call in runner.calls] == [A, B, B]
    assert not [r for r in caplog.records if "claude_account_switch" in r.getMessage()]


def test_every_account_limited_fails_naming_each_account_and_its_reset(tmp_path, caplog):
    later = RESETS_AT + 3 * 86400
    b_limit = LIMIT_STREAM.replace(str(RESETS_AT), str(later)).replace(
        '"five_hour"', '"seven_day"'
    )
    runner = _runner([(1, LIMIT_STREAM), (1, b_limit)])
    adapter = _adapter(tmp_path, runner)

    with caplog.at_level(logging.INFO, logger="toll_harness"):
        with pytest.raises(ModelInvocationError) as raised:
            adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    error = raised.value
    assert error.code == "usage_limit"
    assert ".claude (/accounts/one/.claude) until 2026-10-06T22:48:51Z" in error.message
    assert "[usage_limit:five_hour]" in error.message
    assert ".claude-b (/accounts/two/.claude-b) until 2026-10-09T22:48:51Z" in error.message
    assert "[usage_limit:seven_day]" in error.message
    assert "first one resets at 2026-10-06T22:48:51Z" in error.message
    assert set(_table(tmp_path)) == {A, B}
    assert any("claude_account_all_limited" in r.getMessage() for r in caplog.records)

    # While both are held, a call fails at once without running the CLI.
    with pytest.raises(ModelInvocationError) as again:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
    assert again.value.code == "usage_limit"
    assert len(runner.calls) == 2


def test_another_agents_hit_is_honored_from_the_shared_table(tmp_path, caplog):
    AccountTable(tmp_path / "claude-accounts.json").reject(A, RESETS_AT, now=NOW)
    runner = _runner([(0, _ok_stream())])
    adapter = _adapter(tmp_path, runner)

    with caplog.at_level(logging.INFO, logger="toll_harness"):
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [_account(call) for call in runner.calls] == [B]
    switches = [r.getMessage() for r in caplog.records if "claude_account_switch" in r.getMessage()]
    assert len(switches) == 1 and "source=shared_table" in switches[0]


def test_an_expired_rejection_is_tried_again(tmp_path, caplog):
    table = AccountTable(tmp_path / "claude-accounts.json")
    table.reject(A, NOW - 1, now=NOW - 3600)  # held until one second ago
    runner = _runner([(0, _ok_stream())])
    adapter = _adapter(tmp_path, runner)

    adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [_account(call) for call in runner.calls] == [A]


def test_the_rail_goes_back_to_the_first_account_when_its_hold_runs_out(tmp_path, caplog):
    clock = {"now": NOW}
    runner = _runner([(1, LIMIT_STREAM), (0, _ok_stream()), (0, _ok_stream())])
    adapter = _adapter(tmp_path, runner, clock=lambda: clock["now"])
    adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
    caplog.clear()

    clock["now"] = RESETS_AT + 1
    with caplog.at_level(logging.INFO, logger="toll_harness"):
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [_account(call) for call in runner.calls] == [A, B, A]
    switches = [r.getMessage() for r in caplog.records if "claude_account_switch" in r.getMessage()]
    assert switches == ["claude_account_switch from=.claude-b to=.claude reason=reset_passed"]


def test_a_limit_with_no_named_reset_is_held_an_hour(tmp_path):
    bare = _stream(
        _result(is_error=True, result="Claude AI usage limit reached", api_error_status=429)
    )
    runner = _runner([(1, bare), (0, _ok_stream())])
    adapter = _adapter(tmp_path, runner)

    adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert _table(tmp_path)[A]["rejected_until"] == NOW + 3600


# --- what is NOT a limit -----------------------------------------------------


@pytest.mark.parametrize(
    "stream",
    [THROTTLE_STREAM, OVERLOADED_STREAM, TOO_LONG_STREAM],
    ids=["server-throttle-429", "overloaded-529", "prompt-too-long-400"],
)
def test_ordinary_failures_mark_no_account_and_keep_todays_error(tmp_path, stream):
    runner = _runner([(1, stream)])
    adapter = _adapter(tmp_path, runner)

    with pytest.raises(ModelInvocationError) as raised:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert raised.value.code == "exit_1"
    assert [_account(call) for call in runner.calls] == [A]
    assert _table(tmp_path) == {}


def test_a_timeout_marks_no_account(tmp_path):
    runner = _runner([subprocess.TimeoutExpired(["claude"], 600)])
    adapter = _adapter(tmp_path, runner)

    with pytest.raises(ModelInvocationError) as raised:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert raised.value.code == "timeout"
    assert len(runner.calls) == 1
    assert _table(tmp_path) == {}


def test_a_reply_that_talks_about_limits_is_a_reply(tmp_path):
    words = json.dumps({"text": "You've hit your session limit, the email says.",
                        "tool_calls": [{"name": "state.save", "arguments": {}}]})
    runner = _runner([(0, _ok_stream(words))])
    adapter = _adapter(tmp_path, runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert response.text.startswith("You've hit your session limit")
    assert len(runner.calls) == 1
    assert _table(tmp_path) == {}


def test_a_native_tool_call_is_still_the_native_tool_retry_not_a_limit(tmp_path):
    native = _stream(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "files.list", "input": {}}]}},
        _result(subtype="error_max_turns", is_error=True, stop_reason="tool_use", num_turns=2),
    )
    runner = _runner([(1, native), (0, _ok_stream())])
    adapter = _adapter(tmp_path, runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["state.save"]
    assert [_account(call) for call in runner.calls] == [A, A]
    assert "native tool call" in runner.calls[1]["prompt"]
    assert _table(tmp_path) == {}


# --- detection and the table, directly -------------------------------------


def test_detection_reads_the_exact_limit_stream():
    payload, _ = _read_claude_output(LIMIT_STREAM)
    limit = detect_usage_limit(stdout=LIMIT_STREAM, stderr="", returncode=1, payload=payload,
                               now=NOW)
    assert limit is not None
    assert limit.resets_at == RESETS_AT
    assert limit.limit == "five_hour"
    assert limit.message == SESSION_LIMIT_TEXT


def test_detection_reads_the_reset_from_the_words_when_the_event_is_missing():
    text = "You've hit your weekly limit · resets Oct 9, 5pm (America/Los_Angeles)"
    stream = _stream(_result(is_error=True, result=text))
    payload, _ = _read_claude_output(stream)
    limit = detect_usage_limit(stdout=stream, stderr="", returncode=1, payload=payload, now=NOW)
    assert limit is not None
    # 5pm Pacific daylight time on 2026-10-09 is 00:00 UTC on the 10th.
    assert limit.resets_at == 1791590400.0

    clock_text = "You've hit your session limit · resets 3:48pm (America/Los_Angeles)"
    stream = _stream(_result(is_error=True, result=clock_text))
    payload, _ = _read_claude_output(stream)
    limit = detect_usage_limit(stdout=stream, stderr="", returncode=1, payload=payload, now=NOW)
    # NOW is 13:48:51 Pacific; the next 3:48pm Pacific is 22:48:00Z the same day.
    assert limit.resets_at == 1791326880.0


def test_detection_reads_the_older_pipe_epoch_on_plain_stdout():
    limit = detect_usage_limit(
        stdout="Claude AI usage limit reached|1791330000", stderr="", returncode=1,
        payload=None, now=NOW,
    )
    assert limit is not None and limit.resets_at == 1791330000.0


def test_a_successful_call_is_never_read_as_a_limit():
    stream = _stream(
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected",
                                                         "isUsingOverage": True}},
        _result(result="fine"),
    )
    payload, _ = _read_claude_output(stream)
    assert detect_usage_limit(stdout=stream, stderr="", returncode=0, payload=payload) is None


def test_a_missing_or_corrupt_table_reads_as_empty_and_heals(tmp_path):
    path = tmp_path / "nested" / "claude-accounts.json"
    table = AccountTable(path)
    assert table.entry(A, now=NOW) is None
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert table.entry(A, now=NOW) is None

    table.reject(A, RESETS_AT, now=NOW)

    stored = json.loads(path.read_text())
    assert stored["version"] == 1
    assert stored["accounts"][A]["rejected_until"] == RESETS_AT
    assert AccountTable(path).entry(A, now=NOW)["rejected_until"] == RESETS_AT
    assert AccountTable(path).entry(A, now=RESETS_AT) is None  # expired reads as absent


def test_writing_the_table_drops_expired_entries(tmp_path):
    path = tmp_path / "claude-accounts.json"
    AccountTable(path).reject(B, NOW - 10, now=NOW - 100)
    AccountTable(path).reject(A, RESETS_AT, now=NOW)
    assert set(json.loads(path.read_text())["accounts"]) == {A}


# --- configuration -----------------------------------------------------------


def test_config_dirs_come_from_agent_yaml_then_the_environment_then_nothing(monkeypatch):
    assert resolve_config_dirs(None, {}) == []
    assert resolve_config_dirs(None, {CONFIG_DIRS_ENV: f"{A}:{B}:{A}:"}) == [A, B]
    assert resolve_config_dirs([B], {CONFIG_DIRS_ENV: A}) == [B]
    assert resolve_config_dirs([], {CONFIG_DIRS_ENV: A}) == [A]
    monkeypatch.setenv("HOME", "/homes/op")
    assert resolve_config_dirs(["~/.claude"], {}) == ["/homes/op/.claude"]
    with pytest.raises(ValueError):
        resolve_config_dirs([1, 2], {})


def test_agent_yaml_key_reaches_the_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    config = {"model": {"adapter": "claude_code", "claude_config_dirs": [A, B]}}
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)
    assert adapter.config_dirs == [A, B]

    monkeypatch.setenv(CONFIG_DIRS_ENV, B)
    adapter = build_model_adapter({"model": {"adapter": "claude_code"}}, root=tmp_path,
                                  data_dir=tmp_path)
    assert adapter.config_dirs == [B]
