"""Paid-pack agent doors use the signed order, never a free-form payment path."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.config import PAID_PACK_TOOLS, with_paid_pack_tools
from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.onboarding import TOLL_BENCH_TOOLS
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import ToolContext, add_toll_bench_tools, build_standard_registry

ORDER = "a6dd8070-7849-4dcc-a694-538e93289122"
RECEIPT = "2b0a20a9-ccac-45a7-ab94-a0e691e928a9"
SUBMIT = "5769e688-3c6d-4643-a513-76b26bcf53e9"


def registry():
    return add_toll_bench_tools(build_standard_registry())


def test_paid_attention_is_actionable_and_distinguishes_orders_and_cards():
    work = {"kind": "paid_pack_work", "order_id": ORDER, "card_number": 1}
    next_card = {**work, "card_number": 2}
    review = {"kind": "paid_pack_review", "order_id": ORDER,
              "card_number": 1, "receipt_id": RECEIPT}
    assert cli._select_obligation([work]) == work
    assert cli._obligation_key(work) != cli._obligation_key(next_card)
    assert cli._obligation_key(work) != cli._obligation_key(review)
    assert {"toll_bench.paid_pack_job", "toll_bench.submit_paid_pack_result"} <= (
        cli._OBLIGATION_DISPATCH["paid_pack_work"]["tools"])
    assert {"toll_bench.paid_pack_receipt", "toll_bench.paid_pack_handoff"} <= (
        cli._OBLIGATION_DISPATCH["paid_pack_review"]["tools"])
    configured = {"toll_bench.paid_pack_job", "toll_bench.submit_paid_pack_result",
                  "web.search", "browser.open", "files.write", "files.read",
                  "email.send", "toll_bench.paid_pack_handoff"}
    work_tools = configured & cli._OBLIGATION_DISPATCH["paid_pack_work"]["tools"]
    review_tools = configured & cli._OBLIGATION_DISPATCH["paid_pack_review"]["tools"]
    assert {"web.search", "browser.open", "files.write"} <= work_tools
    assert "email.send" not in work_tools
    assert "files.read" in review_tools
    assert "toll_bench.submit_paid_pack_result" not in review_tools
    assert set(PAID_PACK_TOOLS) <= set(TOLL_BENCH_TOOLS)
    assert with_paid_pack_tools(["toll_bench.attention"]) == ["toll_bench.attention"]


def test_paid_retry_state_changes_only_for_meaningful_rejection_or_handoff():
    resources = SimpleNamespace(toll_bench=None)
    work = {"kind": "paid_pack_work", "order_id": ORDER, "card_number": 1,
            "work_revision": 1, "parent_deal_id": RECEIPT}
    first = cli._loop_state(resources, work)
    assert cli._loop_state(resources, {**work, "polled_at": "later"}) == first
    assert cli._loop_state(resources, {**work, "work_revision": 2,
                                       "rejected_receipt_id": RECEIPT}) != first
    review = {"kind": "paid_pack_review", "order_id": ORDER, "card_number": 1,
              "receipt_id": RECEIPT, "receipt_status": "submitted"}
    accepted = {**review, "receipt_status": "accepted", "handoff_state": "filing"}
    assert cli._loop_state(resources, accepted) != cli._loop_state(resources, review)


def test_paid_cost_scope_uses_parent_deal_without_step_probe():
    seen = []

    class Ledger:
        def deal(self, deal_id, goal_id):
            seen.append((deal_id, goal_id))
            return SimpleNamespace(__enter__=lambda self: self,
                                   __exit__=lambda self, *_args: None)

    resources = SimpleNamespace(cost_ledger=Ledger())
    cli._cost_scope(resources, {"kind": "paid_pack_work", "order_id": ORDER,
                                "parent_deal_id": RECEIPT, "target_id": ORDER})
    assert seen == [(RECEIPT, ORDER)]


def test_actual_watch_dispatch_intersects_configured_paid_work_tools(monkeypatch):
    attention = {"kind": "paid_pack_work", "order_id": ORDER, "card_number": 1,
                 "target_id": ORDER, "parent_deal_id": RECEIPT,
                 "work_revision": "initial"}
    observed = []
    configured = ["toll_bench.paid_pack_job", "toll_bench.read_paid_pack_input",
                  "toll_bench.submit_paid_pack_result", "web.search", "browser.open",
                  "files.write", "email.send", "toll_bench.paid_pack_handoff"]
    runtime = SimpleNamespace(enabled_tools=list(configured), model=None,
                              email_provider=None)

    def start(goal, _mode):
        observed.append((goal, tuple(runtime.enabled_tools)))
        return SimpleNamespace(status=SimpleNamespace(value="completed"))

    runtime.start = start
    bench = SimpleNamespace(ensure_reachable=lambda: {"ok": True},
                            attention=lambda wait: {"attention": [attention]},
                            list_proposals=lambda: {"proposals": []})
    resources = SimpleNamespace(toll_bench=bench, runtime=runtime,
                                agent_identity=None, store=None)
    monkeypatch.setattr(cli, "_result_payload", lambda result: {"status": result.status.value})
    monkeypatch.setattr(cli, "_dispatch_meter", lambda *_args: {})
    result = cli._process_market_attention(resources, wait=0)
    assert result["ok"] and len(observed) == 1
    assert {"web.search", "browser.open", "files.write",
            "toll_bench.read_paid_pack_input"} <= set(observed[0][1])
    assert "email.send" not in observed[0][1]
    assert "toll_bench.paid_pack_handoff" not in observed[0][1]
    assert runtime.enabled_tools == configured


def test_text_and_file_result_use_exact_private_door_without_redirect(monkeypatch):
    calls = []

    class Response:
        status = 201
        headers = {}

        def read(self):
            return b'{"ok":true,"receipt_id":"same"}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request)
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    api.submit_paid_pack_result(ORDER, card_number=1, submit_key=SUBMIT,
                                title="Research", note="Done")
    api.submit_paid_pack_result(ORDER, card_number=1, submit_key=SUBMIT,
                                title="Research", note="Audio", content=b"sound",
                                filename="song.wav", content_type="audio/wav")
    assert len(calls) == 2
    assert all(request.full_url ==
               f"https://bench.example/api/bench/paid-packs/{ORDER}/results"
               for request in calls)
    assert all(request.get_header("Authorization") == "Bearer agent-token"
               for request in calls)
    assert json.loads(calls[0].data)["submit_key"] == SUBMIT
    assert b'filename="song.wav"' in calls[1].data
    assert b"sound" in calls[1].data
    assert "multipart/form-data" in calls[1].get_header("Content-type")


def test_paid_file_redirect_refused_before_bearer_can_leave(monkeypatch):
    seen = []

    class Opener:
        def open(self, request, timeout=None):
            seen.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, io.BytesIO())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    try:
        api.paid_pack_receipt_file(ORDER, RECEIPT)
    except BookOfHousesApiError as error:
        assert error.status == 302 and error.code == "paid_pack_file_refused"
    else:
        raise AssertionError("Paid file redirect was followed")
    assert seen == [f"https://bench.example/api/bench/paid-packs/{ORDER}/"
                    f"receipts/{RECEIPT}/file"]


def test_checked_receipt_download_has_safe_local_filename_and_no_file_bytes():
    content = b"approved-song"
    digest = hashlib.sha256(content).hexdigest()
    provider = SimpleNamespace(
        paid_pack_receipt=lambda *_: {"ok": True, "receipt": {
            "kind": "file", "filename": "../song?.wav", "sha256": digest,
            "size_bytes": len(content), "status": "accepted"}},
        paid_pack_receipt_file=lambda *_: content,
    )
    writes = []
    store = SimpleNamespace(write=lambda run, path, data:
                            writes.append((run, path, data)) or {"path": path})
    context = ToolContext("run", None, None, store, 0, toll_bench_provider=provider)
    result = registry().execute(context, "call", "toll_bench.paid_pack_receipt",
                                {"order_id": ORDER, "receipt_id": RECEIPT})
    assert not result.is_error
    assert result.output["receipt"]["artifact_path"] == (
        f"paid-pack-inputs/{RECEIPT}/song_.wav")
    assert b"approved-song" not in repr(result.output).encode()
    assert writes == [("run", f"paid-pack-inputs/{RECEIPT}/song_.wav", content)]


def test_changed_receipt_file_refused_before_local_write():
    provider = SimpleNamespace(
        paid_pack_receipt=lambda *_: {"ok": True, "receipt": {
            "kind": "file", "filename": "song.wav",
            "sha256": hashlib.sha256(b"approved").hexdigest(), "size_bytes": 7}},
        paid_pack_receipt_file=lambda *_: b"changed",
    )
    writes = []
    store = SimpleNamespace(write=lambda *args: writes.append(args))
    context = ToolContext("run", None, None, store, 0, toll_bench_provider=provider)
    result = registry().execute(context, "call", "toll_bench.paid_pack_receipt",
                                {"order_id": ORDER, "receipt_id": RECEIPT})
    assert result.is_error and "differs" in result.output["error"]
    assert writes == []


def test_local_result_path_uploads_only_bounded_artifact_bytes():
    calls = []
    provider = SimpleNamespace(submit_paid_pack_result=lambda *args, **kwargs:
                               calls.append((args, kwargs)) or {"ok": True})
    store = SimpleNamespace(read=lambda run, path, limit=None: b"wave"[:limit])
    context = ToolContext("run", None, None, store, 0, toll_bench_provider=provider)
    result = registry().execute(context, "call", "toll_bench.submit_paid_pack_result",
                                {"order_id": ORDER, "card_number": 1,
                                 "submit_key": SUBMIT, "title": "Song",
                                 "file_path": "song.wav", "content_type": "audio/wav"})
    assert not result.is_error and result.output["ok"]
    assert calls[0][1]["content"] == b"wave"
    assert calls[0][1]["filename"] == "song.wav"
    assert b"wave" not in repr(result.output).encode()


def test_optional_draft_suggestion_survives_existing_handoff_tool():
    slot = "1ac4929a-13e0-472d-952a-aaac77c3e172"
    source = "043f66ec-68e5-4db9-88e1-2192fb910241"
    handoff = {"summary": "Done", "labels": [], "artifacts": [RECEIPT],
               "draft_suggestions": [{"slot_id": slot, "title": "Another card",
                                      "brief": "Optional follow-up", "artifacts": [RECEIPT]}]}
    calls = []
    provider = BookOfHousesTollBenchProvider(SimpleNamespace(
        file_outcome=lambda target, outcome, key:
        calls.append((target, outcome, key)) or {"ok": True}))
    context = ToolContext("run", None, None, None, 0, toll_bench_provider=provider)
    result = registry().execute(context, "call", "toll_bench.file_outcome",
                                {"target_id": source, "outcome": {"pack_handoff": handoff},
                                 "idempotency_key": SUBMIT})
    assert not result.is_error and result.output["ok"]
    assert calls == [(source, {"pack_handoff": handoff}, SUBMIT)]
    assert {"toll_bench.get_pack_drafts", "toll_bench.suggest_pack_draft"} <= (
        cli._OBLIGATION_DISPATCH["pack_handoff"]["tools"])
    assert "same task" in cli._OBLIGATION_DISPATCH["pack_handoff"]["instruction"]
    assert "draft_suggestions" in cli._OBLIGATION_DISPATCH["deal_step"]["instruction"]


def test_after_handoff_draft_tools_use_exact_scoped_door_and_key(monkeypatch):
    source = "043f66ec-68e5-4db9-88e1-2192fb910241"
    slot = "1ac4929a-13e0-472d-952a-aaac77c3e172"
    calls = []

    class Response:
        status = 200
        headers = {}

        def read(self):
            return b'{"ok":true,"slots":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request)
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    api.get_pack_drafts(source)
    api.suggest_pack_draft(source, slot,
                           {"title": "Next", "brief": "Optional", "artifacts": [RECEIPT]},
                           SUBMIT)
    assert [request.full_url for request in calls] == [
        f"https://bench.example/api/bench/targets/{source}/pack-drafts"] * 2
    assert all(request.get_header("Authorization") == "Bearer agent-token"
               for request in calls)
    assert calls[1].get_header("Idempotency-key") == SUBMIT
    assert json.loads(calls[1].data)["slot_id"] == slot


def test_paid_buyer_input_pages_are_explicit_and_scoped(monkeypatch):
    calls = []

    class Response:
        status = 200
        headers = {}

        def read(self):
            return b'{"ok":true,"input":{"index":2,"text":"bounded","next_offset":4}}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request)
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
    api = BookOfHousesApiClient(base_url="https://bench.example", token="agent-token")
    result = api.read_paid_pack_input(ORDER, 2, 0)
    assert result["input"]["text"] == "bounded"
    assert calls[0].full_url == (
        f"https://bench.example/api/bench/paid-packs/{ORDER}/inputs/2?offset=0")
    assert calls[0].get_header("Authorization") == "Bearer agent-token"
    try:
        api.read_paid_pack_input(ORDER, True)
    except ValueError:
        pass
    else:
        raise AssertionError("bool index must be refused")
    assert len(calls) == 1
