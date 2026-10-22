"""A due goal check reaches the existing watch worker through scoped Bench doors."""

from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.request

import pytest

from toll_harness import cli
from toll_harness.config import GOAL_CHECK_TOOLS, with_goal_check_tools
from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.onboarding import TOLL_BENCH_TOOLS
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import ToolContext, add_toll_bench_tools, build_standard_registry


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


RUN_ID = "24f1b8b8-ac64-4de4-a3ad-712fe0f5c5e9"


def test_authenticated_goal_doors_preserve_submit_key_and_body(monkeypatch):
    calls = []

    def urlopen(request, timeout=None):
        calls.append({
            "url": request.full_url, "method": request.get_method(),
            "authorization": request.get_header("Authorization"),
            "key": request.get_header("Idempotency-key"),
            "body": json.loads(request.data) if request.data else None,
        })
        return Response({"ok": True, "receipt": len(calls)})

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    provider = BookOfHousesTollBenchProvider(api)
    body = {"leaseToken": "lease", "assessment": "On track", "nextAction": "Ask owner",
            "strategy": "Review weekly", "blockers": []}

    provider.goal_check(RUN_ID)
    provider.claim_goal_check(RUN_ID)
    first = provider.submit_goal_check(RUN_ID, body, "same-run-retry")
    second = provider.submit_goal_check(RUN_ID, body, "same-run-retry")

    assert first["receipt"] == 3 and second["receipt"] == 4
    assert [call["method"] for call in calls] == ["GET", "POST", "POST", "POST"]
    assert all(f"/goal-checks/{RUN_ID}" in call["url"] for call in calls)
    assert all(call["authorization"] == "Bearer agent-token" for call in calls)
    assert calls[1]["body"] == {}
    assert calls[2]["key"] == calls[3]["key"] == "same-run-retry"
    assert calls[2]["body"] == calls[3]["body"] == body


def test_revoked_goal_door_returns_refusal_without_fallback(monkeypatch):
    def refused(_request, timeout=None):
        raise urllib.error.HTTPError(
            "https://bench.example", 404, "Not Found", {}, io.BytesIO(b'{"error":"not_found"}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    for operation in (api.goal_check, api.claim_goal_check):
        try:
            operation(RUN_ID)
        except BookOfHousesApiError as error:
            assert error.status == 404
            assert error.code == "not_found"
        else:
            raise AssertionError("revoked goal check was accepted")


@pytest.mark.parametrize("run_id", ["..", "../attention", "?query", "not-a-uuid", ""])
def test_malformed_run_id_is_rejected_before_any_network_call(monkeypatch, run_id):
    def forbidden_urlopen(_request, timeout=None):
        raise AssertionError("malformed run ID reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_urlopen)
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    body = {"leaseToken": "lease", "assessment": "On track", "nextAction": "Ask owner",
            "strategy": "Weekly review", "blockers": []}
    operations = (
        lambda: api.goal_check(run_id),
        lambda: api.claim_goal_check(run_id),
        lambda: api.submit_goal_check(run_id, body, "same-run-retry"),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="run_id must be a UUID"):
            operation()


def test_goal_tool_schema_and_submit_mapping():
    class Provider:
        def submit_goal_check(self, run_id, body, key):
            self.submitted = (run_id, body, key)
            return {"ok": True}

    provider = Provider()
    registry = add_toll_bench_tools(build_standard_registry())
    definitions = {item.name: item for item in registry.definitions()}
    assert set(GOAL_CHECK_TOOLS) <= set(definitions)
    for name in GOAL_CHECK_TOOLS:
        pattern = definitions[name].input_schema["properties"]["run_id"]["pattern"]
        assert re.fullmatch(pattern, RUN_ID)
        assert all(
            re.fullmatch(pattern, value) is None
            for value in ("..", "../attention", "?query")
        )
    schema = definitions["toll_bench.submit_goal_check"].input_schema
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["assessment"]["maxLength"] == 2000
    assert schema["properties"]["blockers"]["maxItems"] == 10
    context = ToolContext("run", None, None, None, 0, toll_bench_provider=provider)
    args = {"run_id": RUN_ID, "lease_token": "lease", "assessment": "On track",
            "next_action": "Ask owner", "strategy": "Weekly review", "blockers": [],
            "idempotency_key": "check-1-report"}
    result = registry.execute(context, "call", "toll_bench.submit_goal_check", args)
    assert not result.is_error
    assert provider.submitted == (
        RUN_ID, {"leaseToken": "lease", "assessment": "On track",
                    "nextAction": "Ask owner", "strategy": "Weekly review", "blockers": []},
        "check-1-report",
    )
    missing = registry.execute(
        context, "call-2", "toll_bench.submit_goal_check", {**args, "lease_token": None}
    )
    assert missing.is_error
    oversized = registry.execute(
        context, "call-3", "toll_bench.submit_goal_check", {**args, "assessment": "x" * 2001}
    )
    assert oversized.is_error
    body_too_large = registry.execute(
        context, "call-4", "toll_bench.submit_goal_check",
        {**args, "assessment": "é" * 2000, "strategy": "é" * 2000,
         "blockers": ["é" * 500] * 10},
    )
    assert body_too_large.is_error
    assert "8192-byte" in body_too_large.output["error"]
    assert provider.submitted[0] == RUN_ID


def test_goal_attention_has_focused_tools_and_distinct_run_keys():
    first = {"kind": "goal_check", "id": "run-1", "due_at": "2026-10-23T00:00:00Z"}
    second = {**first, "id": "run-2"}
    assert cli._select_obligation([first]) == first
    assert cli._obligation_key(first) != cli._obligation_key(second)
    assert cli._loop_key(first) != cli._loop_key(second)
    tools = cli._OBLIGATION_DISPATCH["goal_check"]["tools"]
    assert set(GOAL_CHECK_TOOLS) <= tools
    assert "toll_bench.submit_proposal" not in tools
    assert "http.request" not in tools
    assert "owner-confirmed" in cli._OBLIGATION_DISPATCH["goal_check"]["instruction"]


def test_complete_onboarded_tool_list_gets_goal_doors_but_hand_cut_list_does_not():
    old = [name for name in TOLL_BENCH_TOOLS if name not in GOAL_CHECK_TOOLS]
    upgraded = with_goal_check_tools(old)
    assert upgraded[:len(old)] == old
    assert upgraded[-len(GOAL_CHECK_TOOLS):] == GOAL_CHECK_TOOLS
    assert with_goal_check_tools(upgraded) == upgraded
    assert with_goal_check_tools(["toll_bench.attention"]) == ["toll_bench.attention"]
