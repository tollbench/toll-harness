import json
from types import SimpleNamespace

import pytest

from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.base import ModelInvocationError
from toll_harness.models.cli_rail import (
    ClaudeCodeCliAdapter,
    CodexCliAdapter,
    ExternalAgentAdapter,
)

TOOLS = [
    ToolDefinition(
        name="state.save",
        description="Persist a checkpoint",
        input_schema={"type": "object", "properties": {"data": {"type": "object"}}},
    ),
    ToolDefinition(
        name="result.complete",
        description="Finish the run",
        input_schema={"type": "object"},
    ),
]

MESSAGES = [ModelMessage.text("user", "Walk the target briefing")]


def _claude_payload(result, *, is_error=False, usage=None):
    return json.dumps(
        {
            "type": "result",
            "subtype": "success" if not is_error else "error_during_execution",
            "is_error": is_error,
            "result": result,
            "usage": usage or {"input_tokens": 100, "output_tokens": 20},
        }
    )


def _claude_runner(replies):
    """Fake subprocess.run for the claude CLI; pops one reply per call."""
    calls = []

    def run(argv, *, input, capture_output, text, timeout, cwd):
        calls.append({"argv": argv, "prompt": input, "cwd": cwd})
        return SimpleNamespace(returncode=0, stdout=replies.pop(0), stderr="")

    run.calls = calls
    return run


def test_claude_code_parses_envelope_into_canonical_tool_calls(tmp_path):
    envelope = json.dumps(
        {
            "text": "Saving progress.",
            "tool_calls": [{"name": "state.save", "arguments": {"data": {"step": 1}}}],
        }
    )
    runner = _claude_runner([_claude_payload(envelope)])
    adapter = ClaudeCodeCliAdapter(
        "claude-opus-4-8", workdir=tmp_path, runner=runner
    )

    response = adapter.invoke(system="Be exact.", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["state.save"]
    assert response.tool_calls[0].arguments == {"data": {"step": 1}}
    assert response.text == "Saving progress."
    assert response.stop_reason == "tool_use"
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 20
    argv = runner.calls[0]["argv"]
    assert argv[:5] == ["claude", "-p", "--output-format", "json", "--max-turns"]
    assert "--model" in argv and "claude-opus-4-8" in argv
    prompt = runner.calls[0]["prompt"]
    assert "Be exact." in prompt
    assert "state.save" in prompt
    assert "Walk the target briefing" in prompt
    # The CLI runs in the isolated scratch dir, never the project checkout.
    assert runner.calls[0]["cwd"] == str(tmp_path)


def test_claude_code_retries_once_on_prose_then_parses(tmp_path):
    envelope = json.dumps({"tool_calls": [{"name": "result.complete", "arguments": {}}]})
    runner = _claude_runner(
        [_claude_payload("Sure! I will now save my progress."), _claude_payload(envelope)]
    )
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["result.complete"]
    assert len(runner.calls) == 2
    assert "could not be parsed" in runner.calls[1]["prompt"]
    # Usage from both calls is summed.
    assert response.usage.input_tokens == 200


def test_claude_code_degrades_to_text_when_envelope_never_parses(tmp_path):
    runner = _claude_runner(
        [_claude_payload("prose the first"), _claude_payload("prose the second")]
    )
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert response.tool_calls == []
    assert response.stop_reason == "envelope_unparsed"
    assert response.text == "prose the second"


def test_claude_code_accepts_fenced_envelope(tmp_path):
    fenced = "```json\n" + json.dumps(
        {"tool_calls": [{"name": "state.save", "arguments": {}}]}
    ) + "\n```"
    runner = _claude_runner([_claude_payload(fenced)])
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["state.save"]


def test_claude_code_surfaces_cli_error_payload(tmp_path):
    runner = _claude_runner([_claude_payload("credit exhausted", is_error=True)])
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=runner)

    with pytest.raises(ModelInvocationError) as excinfo:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert excinfo.value.provider == "claude_code"


def test_codex_reads_last_message_file_and_cleans_up(tmp_path):
    envelope = json.dumps({"tool_calls": [{"name": "state.save", "arguments": {}}]})
    calls = []

    def run(argv, *, input, capture_output, text, timeout, cwd):
        calls.append(argv)
        path = argv[argv.index("--output-last-message") + 1]
        with open(path, "w") as handle:
            handle.write(envelope)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    adapter = CodexCliAdapter("gpt-5.2-codex", workdir=tmp_path, runner=run)

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["state.save"]
    argv = calls[0]
    assert argv[:2] == ["codex", "exec"]
    assert "--sandbox" in argv and "read-only" in argv
    assert "--skip-git-repo-check" in argv
    assert argv[-1] == "-"  # prompt rides stdin, never argv (128KB argv limit)
    assert "--model" in argv and "gpt-5.2-codex" in argv
    assert list(tmp_path.glob("last-message-*.txt")) == []


def test_codex_nonzero_exit_raises_with_stderr(tmp_path):
    def run(argv, *, input, capture_output, text, timeout, cwd):
        return SimpleNamespace(returncode=1, stdout="", stderr="not logged in; run codex login")

    adapter = CodexCliAdapter(workdir=tmp_path, runner=run)

    with pytest.raises(ModelInvocationError) as excinfo:
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert "codex login" in excinfo.value.message


def test_missing_binary_fails_fast_with_sign_in_hint(tmp_path):
    with pytest.raises(RuntimeError, match="claude setup-token"):
        ClaudeCodeCliAdapter(binary="definitely-not-on-path-xyz", workdir=tmp_path)
    with pytest.raises(RuntimeError, match="codex login"):
        CodexCliAdapter(binary="definitely-not-on-path-xyz", workdir=tmp_path)


def test_external_adapter_rails_any_command(tmp_path):
    # The whole "any agent" contract: prompt on stdin, envelope on stdout.
    envelope = json.dumps({"tool_calls": [{"name": "result.complete", "arguments": {}}]})
    calls = []

    def run(argv, *, input, capture_output, text, timeout, cwd):
        calls.append({"argv": argv, "prompt": input})
        return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

    adapter = ExternalAgentAdapter(
        "my-lab/my-agent-v2",
        command=["my-agent-wrapper", "--flag"],
        workdir=tmp_path,
        runner=run,
    )

    response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)

    assert [call.name for call in response.tool_calls] == ["result.complete"]
    assert adapter.model_id == "my-lab/my-agent-v2"
    assert calls[0]["argv"] == ["my-agent-wrapper", "--flag"]
    assert "result.complete" in calls[0]["prompt"]  # tool catalog rendered


def test_external_adapter_requires_a_command(tmp_path):
    with pytest.raises(ValueError, match="non-empty model.command"):
        ExternalAgentAdapter(workdir=tmp_path, runner=lambda *a, **k: None, command=[])


def test_config_builds_cli_rail_adapters(tmp_path):
    from toll_harness.config import _build_model

    config = {
        "model": {"adapter": "claude_code", "binary": "sh", "timeout_seconds": 120}
    }
    adapter = _build_model(config, root=tmp_path, data_dir=tmp_path / "data")
    assert isinstance(adapter, ClaudeCodeCliAdapter)
    assert adapter.timeout_seconds == 120
    assert adapter.workdir == tmp_path / "data" / "cli-rail"

    config = {"model": {"adapter": "codex", "binary": "sh"}}
    assert isinstance(
        _build_model(config, root=tmp_path, data_dir=tmp_path / "data"), CodexCliAdapter
    )

    config = {"model": {"adapter": "external", "command": ["sh", "-c", "cat"]}}
    external = _build_model(config, root=tmp_path, data_dir=tmp_path / "data")
    assert isinstance(external, ExternalAgentAdapter)
    assert external.command == ["sh", "-c", "cat"]
