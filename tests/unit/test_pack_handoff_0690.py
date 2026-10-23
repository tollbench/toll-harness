"""A resolved action-pack card can hand metadata to its next selected agent."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.config import PACK_INPUT_TOOLS, with_pack_input_tools
from toll_harness.email.book_of_houses import (
    BookOfHousesApiClient,
    BookOfHousesApiError,
    _RefusePackInputRedirects,
)
from toll_harness.onboarding import TOLL_BENCH_TOOLS
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


def test_legacy_note_on_resolved_handoff_is_ignored_without_a_second_delivery():
    provider, calls = _provider()
    payload = {"note": "This card is complete", "pack_handoff": HANDOFF}
    assert provider.file_outcome("target-1", payload, "key")["ok"]
    assert calls == [("target-1", {"pack_handoff": HANDOFF}, "key")]


def test_revoked_handoff_refusal_is_structured_and_never_retried():
    calls = []

    def refused(*args):
        calls.append(args)
        raise BookOfHousesApiError(404, "not_found", "Result not found")

    provider = BookOfHousesTollBenchProvider(SimpleNamespace(file_outcome=refused))
    result = provider.file_outcome("target-1", {"pack_handoff": HANDOFF}, "key")
    assert result["ok"] is False and result["error"] == "not_found"
    assert result["status"] == 404 and len(calls) == 1


def test_invalid_handoff_never_reaches_the_server():
    provider, calls = _provider()
    bad = [
        {"summary": "", "labels": [], "artifacts": []},
        {"summary": "okay", "labels": [], "artifacts": [RECEIPT] * 9},
        {"summary": "okay", "labels": [], "artifacts": ["not-a-uuid"]},
        {"summary": "okay", "labels": [], "artifacts": [], "extra": "private"},
    ]
    for handoff in bad:
        assert provider.file_outcome("target-1", {"pack_handoff": handoff}, "key")[
            "error"] == "invalid_pack_handoff"
    assert calls == []


def test_long_summary_and_labels_reach_server_for_authoritative_trimming():
    provider, calls = _provider()
    handoff = {"summary": "  " + "x" * 1201 + "  ",
               "labels": ["x" * 41] * 9, "artifacts": []}
    assert provider.file_outcome("target-1", {"pack_handoff": handoff}, "key")["ok"]
    assert calls == [("target-1", {"pack_handoff": handoff}, "key")]


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


def test_authenticated_pack_input_read_is_scoped_to_successor_and_receipt(monkeypatch):
    calls = []

    class Response:
        status = 200

        def read(self):
            return json.dumps({"ok": True, "receipt_id": RECEIPT,
                               "kind": "text", "text": "Approved result"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_header("Authorization")))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    provider = BookOfHousesTollBenchProvider(api)
    registry = add_toll_bench_tools(build_standard_registry())
    context = ToolContext("run", None, None, None, 0, toll_bench_provider=provider)
    target = "4fa1b409-2d68-4811-8190-7abbdb448ab6"
    result = registry.execute(context, "call", "toll_bench.get_pack_input",
                              {"target_id": target, "receipt_id": RECEIPT})
    assert not result.is_error and result.output["text"] == "Approved result"
    assert calls == [(f"https://bench.example/api/bench/targets/{target}"
                      f"/pack-inputs/{RECEIPT}", "Bearer agent-token")]
    invalid = registry.execute(context, "bad", "toll_bench.get_pack_input",
                               {"target_id": "../other", "receipt_id": RECEIPT})
    assert invalid.is_error and len(calls) == 1


def test_existing_complete_tool_lists_gain_pack_input_but_hand_cut_lists_do_not():
    old = [name for name in TOLL_BENCH_TOOLS if name not in PACK_INPUT_TOOLS]
    assert with_pack_input_tools(old).count("toll_bench.get_pack_input") == 1
    assert with_pack_input_tools(with_pack_input_tools(old)).count(
        "toll_bench.get_pack_input") == 1
    assert with_pack_input_tools(["toll_bench.read_brief"]) == ["toll_bench.read_brief"]
    for kind in ("pack_handoff", "file_informed_plan", "deal_step", "draft_sent_back"):
        assert "toll_bench.get_pack_input" in cli._OBLIGATION_DISPATCH[kind]["tools"]


def test_file_input_is_authenticated_hash_checked_and_stored_outside_model_context(monkeypatch):
    content = b"approved source file"
    digest = hashlib.sha256(content).hexdigest()
    calls = []
    written = []

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def read(self, limit=None):
            return self.payload if limit is None else self.payload[:limit]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_header("Authorization")))
        if request.full_url.endswith("/file"):
            return Response(content)
        return Response(json.dumps({"ok": True, "receipt_id": RECEIPT,
                                    "kind": "file", "sha256": digest,
                                    "download_url": request.full_url + "/file"}).encode())

    class Opener:
        open = staticmethod(urlopen)

    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    api.get_pack_input = lambda *_: {"ok": True, "receipt_id": RECEIPT,
                                    "kind": "file", "sha256": digest}
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
    provider = BookOfHousesTollBenchProvider(api)
    store = SimpleNamespace(write=lambda run, path, data: (
        written.append((run, path, data)) or {"path": path, "size": len(data)}))
    registry = add_toll_bench_tools(build_standard_registry())
    context = ToolContext("run", None, None, store, 0, toll_bench_provider=provider)
    target = "4fa1b409-2d68-4811-8190-7abbdb448ab6"
    result = registry.execute(context, "call", "toll_bench.get_pack_input",
                              {"target_id": target, "receipt_id": RECEIPT})
    assert not result.is_error
    assert result.output["artifact_path"] == f"pack-inputs/{digest}.bin"
    assert result.output["size_bytes"] == len(content)
    assert "download_url" not in result.output
    assert written == [("run", f"pack-inputs/{digest}.bin", content)]
    assert len(calls) == 1 and calls[0][1] == "Bearer agent-token"


def test_pack_file_redirect_never_forwards_bearer_to_another_origin(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request.full_url)
            redirect = _RefusePackInputRedirects()
            forwarded = redirect.redirect_request(
                request, None, 302, "Found", {}, "https://foreign.invalid/receive"
            )
            assert forwarded is None
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    try:
        api.get_pack_input_file("4fa1b409-2d68-4811-8190-7abbdb448ab6", RECEIPT)
    except BookOfHousesApiError as exc:
        assert exc.status == 302 and exc.code == "pack_input_file_refused"
    else:
        raise AssertionError("cross-origin redirect followed")
    assert calls == [f"https://bench.example/api/bench/targets/"
                     f"4fa1b409-2d68-4811-8190-7abbdb448ab6/pack-inputs/{RECEIPT}/file"]


def test_changed_file_is_refused_before_artifact_write():
    expected = hashlib.sha256(b"approved").hexdigest()
    provider = SimpleNamespace(
        get_pack_input=lambda *_: {"ok": True, "kind": "file", "sha256": expected},
        get_pack_input_file=lambda *_: b"changed",
    )
    writes = []
    store = SimpleNamespace(write=lambda *args: writes.append(args))
    context = ToolContext("run", None, None, store, 0, toll_bench_provider=provider)
    registry = add_toll_bench_tools(build_standard_registry())
    result = registry.execute(context, "call", "toll_bench.get_pack_input",
                              {"target_id": "target", "receipt_id": RECEIPT})
    assert result.is_error and "differs" in result.output["error"]
    assert writes == []
