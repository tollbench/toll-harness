"""Native research stays in the vendor agent; only bench actions cross the bridge."""
import json
from types import SimpleNamespace

import pytest

from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.base import ModelInvocationError
from toll_harness.models.cli_rail import ClaudeCodeCliAdapter


def test_native_research_then_bench_handback_is_one_agent_run(tmp_path):
    calls = []
    envelope = {"tool_calls": [{"name": "toll_bench.post_check_in", "arguments": {}}]}
    def run(argv, **kwargs):
        calls.append((argv, kwargs["input"]))
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "WebSearch", "input": {"query": "AI podcasts"}}]}},
            {"type": "result", "subtype": "success", "is_error": False,
             "num_turns": 4, "result": json.dumps(envelope),
             "usage": {"input_tokens": 1000, "output_tokens": 200}},
        ]
        return SimpleNamespace(returncode=0, stdout="\n".join(map(json.dumps, events)), stderr="")
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=run)
    response = adapter.invoke(system="Find podcasts.", messages=[ModelMessage.text("user", "Research")],
        tools=[ToolDefinition("toll_bench.post_check_in", "Report progress", {"type": "object"})])
    assert len(calls) == 1
    assert response.tool_calls[0].name == "toll_bench.post_check_in"
    assert response.usage.input_tokens == 1000
    argv, prompt = calls[0]
    assert not {"--tools", "--strict-mcp-config", "--max-turns"}.intersection(argv)
    assert "normal tools" in prompt
    assert "ONLY tools" not in prompt
    assert "do not call their names as native tools" in prompt


def test_native_runtime_stop_does_not_fabricate_agent_failure(tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stderr="", stdout=json.dumps({
            "type": "result", "subtype": "error_max_turns", "is_error": True,
            "stop_reason": "tool_use", "result": "Operator-configured turn limit reached"}))
    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=run, extra_args=["--max-turns", "2"])
    with pytest.raises(ModelInvocationError, match="turn limit reached"):
        adapter.invoke(system="Research", messages=[],
            tools=[ToolDefinition("result.fail", "Declare failure", {"type": "object"})])
    assert len(calls) == 1
    assert calls[0][-2:] == ["--max-turns", "2"]
