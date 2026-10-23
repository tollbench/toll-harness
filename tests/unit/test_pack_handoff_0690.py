"""A resolved action-pack card can hand metadata to its next selected agent."""

from __future__ import annotations

from types import SimpleNamespace

from toll_harness import cli
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import ToolContext, add_toll_bench_tools, build_standard_registry

RECEIPT = "02b2c15f-9a90-4c59-85fe-f1fda7f739be"
HANDOFF = {"summary": "The flyer is approved.", "labels": ["design"],
           "artifacts": [RECEIPT]}


def _provider():
    calls = []

    def file_outcome(target, outcome, key):
        calls.append((target, outcome, key))
        return {"ok": True, "receipt": "handoff-receipt"}

    return BookOfHousesTollBenchProvider(SimpleNamespace(file_outcome=file_outcome)), calls


def test_pack_only_handoff_uses_existing_tool_and_posts_exact_metadata():
    provider, calls = _provider()
    registry = add_toll_bench_tools(build_standard_registry())
    schema = next(tool.input_schema for tool in registry.definitions()
                  if tool.name == "toll_bench.file_outcome")
    outcome_schema = schema["properties"]["outcome"]
    assert outcome_schema["additionalProperties"] is False
    assert outcome_schema["properties"]["pack_handoff"]["required"] == [
        "summary", "labels", "artifacts"]
    context = ToolContext("run", None, None, None, 0, toll_bench_provider=provider)
    arguments = {"target_id": "target-1", "outcome": {"pack_handoff": HANDOFF},
                 "idempotency_key": "same-handoff"}
    result = registry.execute(context, "call", "toll_bench.file_outcome", arguments)
    assert not result.is_error
    assert calls == [("target-1", {"pack_handoff": HANDOFF}, "same-handoff")]


def test_normal_file_outcome_still_requires_note_and_one_content_field():
    provider, calls = _provider()
    assert provider.file_outcome("target-1", {"text": "Done"}, "key")["error"] == (
        "invalid_delivery_note")
    assert provider.file_outcome("target-1", {"note": "Done"}, "key")["error"] == (
        "exactly_one_outcome_content_required")
    assert provider.file_outcome("target-1", {"note": "Done", "text": "Delivered"},
                                 "key")["ok"]
    assert calls == [("target-1", {"note": "Done", "text": "Delivered"}, "key")]


def test_active_deal_outcome_can_carry_handoff_with_ordinary_delivery():
    provider, calls = _provider()
    payload = {"note": "Done", "text": "Delivered", "pack_handoff": HANDOFF}
    assert provider.file_outcome("target-1", payload, "key")["ok"]
    assert calls == [("target-1", payload, "key")]


def test_invalid_handoff_never_reaches_the_server():
    provider, calls = _provider()
    bad = [
        {"summary": "", "labels": [], "artifacts": []},
        {"summary": "x" * 1201, "labels": [], "artifacts": []},
        {"summary": "okay", "labels": ["x" * 41], "artifacts": []},
        {"summary": "okay", "labels": [], "artifacts": ["not-a-uuid"]},
        {"summary": "okay", "labels": [], "artifacts": [], "extra": "private"},
    ]
    for handoff in bad:
        assert provider.file_outcome("target-1", {"pack_handoff": handoff}, "key")[
            "error"] == "invalid_pack_handoff"
    assert calls == []


def test_handoff_attention_is_actionable_and_uses_only_brief_and_outcome():
    item = {"kind": "pack_handoff", "target_id": "target-1", "deal_id": "deal-1",
            "pack_id": "pack-1", "number": 1}
    assert cli._select_obligation([item]) == item
    assert "pack_handoff" in cli._OBLIGATION_PRIORITY
    dispatch = cli._OBLIGATION_DISPATCH["pack_handoff"]
    assert {"toll_bench.read_brief", "toll_bench.file_outcome"} <= dispatch["tools"]
    assert "toll_bench.propose_act" not in dispatch["tools"]
    assert "email.send" not in dispatch["tools"]
    assert "receipt UUID" in dispatch["instruction"]


def test_watch_dispatches_handoff_instead_of_classifying_it_as_notice(monkeypatch):
    item = {"kind": "pack_handoff", "target_id": "target-1", "deal_id": "deal-1",
            "pack_id": "pack-1", "number": 1}
    started = []

    def start(goal, _mode):
        started.append((goal, tuple(runtime.enabled_tools)))
        return SimpleNamespace(status=SimpleNamespace(value="completed"))

    runtime = SimpleNamespace(enabled_tools=["toll_bench.read_brief",
                                              "toll_bench.file_outcome", "email.send"],
                              start=start, model=None, email_provider=None)
    bench = SimpleNamespace(ensure_reachable=lambda: {"ok": True},
                            attention=lambda wait: {"attention": [item]},
                            list_proposals=lambda: {"proposals": []})
    resources = SimpleNamespace(toll_bench=bench, runtime=runtime,
                                agent_identity=None, store=None)
    monkeypatch.setattr(cli, "_result_payload", lambda result: {"status": result.status.value})
    monkeypatch.setattr(cli, "_dispatch_meter", lambda *args: {})
    result = cli._process_market_attention(resources, wait=0)
    assert result["ok"] and result["attention_count"] == 1
    assert not result.get("notices")
    assert len(started) == 1
    assert "brief.action_pack" in started[0][0]
    assert "email.send" not in started[0][1]
