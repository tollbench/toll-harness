"""THE OUTSIDE ACT (Steven, 2026-09-05) -- one block, one evidence door.

The platform executes what it has hands for: an email, a meeting, a post, a
record, a calendar event. Everything else -- a phone call, a purchase, a
visit, a form on somebody else's site -- used to be nothing at all, so an
agent either promised it in prose or said the want could not be done. Now it
is ONE generic block: declared at bid time, allowed by the person with a tap,
done by the agent in its own name, and closed by the evidence it files here.
"""

import json
import urllib.request

import pytest

from toll_harness.core.types import AutonomyMode
from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import (
    ToolContext,
    add_toll_bench_tools,
    build_standard_registry,
)

SUMMARY = (
    "Called Ridgeway Auto at 555-0142 and spoke to Dana in service. They hold "
    "the part and can fit the person in Thursday at 9."
)


# --------------------------------------------------------------------------
# The transport, mocked at the one place the client actually calls out.
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _transport(monkeypatch, *, body=None, error=None):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        if error is not None:
            raise error
        return _Response(json.dumps(body or {}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _http_error(status, payload):
    import urllib.error

    class _Fake(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("u", status, "refused", {}, None)
            self._payload = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._payload

    return _Fake()


def _provider():
    return BookOfHousesTollBenchProvider(
        BookOfHousesApiClient(
            base_url="https://bench.example", token="agent-token", maker_id="maker-1"
        )
    )


# --------------------------------------------------------------------------
# 1. The happy filing: the right door, the agent's own auth, the body.
# --------------------------------------------------------------------------


def test_file_evidence_posts_the_evidence_door_and_returns_the_body(monkeypatch):
    captured = _transport(
        monkeypatch,
        body={
            "ok": True,
            "act_id": "act-9",
            "kind": "outside",
            "state": "executed",
            "witness": "asked",
            "next": "Dana was asked whether it happened. Nothing is owed by you.",
        },
    )
    provider = _provider()

    result = provider.file_evidence(
        "deal-1",
        "step-2",
        summary=SUMMARY,
        links=["https://ridgeway.example/receipt/88"],
        receipt_ids=["receipt-1"],
    )

    assert result["ok"] is True
    assert result["act_id"] == "act-9"
    assert result["state"] == "executed"
    assert result["witness"] == "asked"

    assert captured["method"] == "POST"
    assert captured["url"] == (
        "https://bench.example/api/bench/deals/deal-1/steps/step-2/acts/evidence"
    )
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert headers["authorization"] == "Bearer agent-token"
    assert headers["x-maker-id"] == "maker-1"
    assert headers["content-type"] == "application/json"
    # One filing however many times a cycle retries it.
    assert headers["idempotency-key"].startswith("evidence-step-2-")

    assert json.loads(captured["body"]) == {
        "summary": SUMMARY,
        "links": ["https://ridgeway.example/receipt/88"],
        "receipt_ids": ["receipt-1"],
    }


def test_file_evidence_sends_only_the_summary_when_that_is_all_there_is(monkeypatch):
    captured = _transport(monkeypatch, body={"ok": True, "act_id": "act-9"})

    _provider().file_evidence("deal-1", "step-2", summary=SUMMARY)

    assert json.loads(captured["body"]) == {"summary": SUMMARY}


# --------------------------------------------------------------------------
# 2. Checked at home, so a refusal the harness can see costs no call.
# --------------------------------------------------------------------------


def _never_called(monkeypatch):
    def fake_urlopen(request, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("the door was called on a body checked at home")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


@pytest.mark.parametrize("summary", ["", "   ", "did it"])
def test_a_summary_too_short_to_read_never_reaches_the_door(monkeypatch, summary):
    _never_called(monkeypatch)

    result = _provider().file_evidence("deal-1", "step-2", summary=summary)

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence"
    assert result["field"] == "summary"
    assert "plain words" in result["message"]


def test_a_summary_over_the_cap_never_reaches_the_door(monkeypatch):
    _never_called(monkeypatch)

    result = _provider().file_evidence("deal-1", "step-2", summary="a" * 2001)

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence"
    assert result["field"] == "summary"


def test_a_sixth_link_never_reaches_the_door(monkeypatch):
    _never_called(monkeypatch)

    result = _provider().file_evidence(
        "deal-1",
        "step-2",
        summary=SUMMARY,
        links=[f"https://ridgeway.example/{index}" for index in range(6)],
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence"
    assert result["field"] == "links"
    assert "at most 5" in result["message"]


def test_a_link_that_is_not_a_web_address_never_reaches_the_door(monkeypatch):
    _never_called(monkeypatch)

    result = _provider().file_evidence(
        "deal-1", "step-2", summary=SUMMARY, links=["ridgeway.example/receipt/88"]
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence"
    assert result["field"] == "links"
    assert "http" in result["message"]


def test_a_sixth_receipt_id_never_reaches_the_door(monkeypatch):
    _never_called(monkeypatch)

    result = _provider().file_evidence(
        "deal-1",
        "step-2",
        summary=SUMMARY,
        receipt_ids=[f"receipt-{index}" for index in range(6)],
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence"
    assert result["field"] == "receipt_ids"


def test_an_empty_receipt_id_names_nothing_and_never_reaches_the_door(monkeypatch):
    _never_called(monkeypatch)

    result = _provider().file_evidence(
        "deal-1", "step-2", summary=SUMMARY, receipt_ids=["  "]
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_evidence"
    assert result["field"] == "receipt_ids"


# --------------------------------------------------------------------------
# 3. The door's refusals come back as plain results, in its own words.
# --------------------------------------------------------------------------


def test_not_allowed_yet_comes_back_as_a_plain_result(monkeypatch):
    _transport(
        monkeypatch,
        error=_http_error(
            409,
            {
                "ok": False,
                "error": "not_allowed_yet",
                "message": "The person has not tapped Allow yet. Poll current_step.",
            },
        ),
    )

    result = _provider().file_evidence("deal-1", "step-2", summary=SUMMARY)

    assert result["ok"] is False
    assert result["error"] == "not_allowed_yet"
    assert result["status"] == 409
    # VERBATIM: the door knows whether the person tapped Allow.
    assert result["message"] == (
        "The person has not tapped Allow yet. Poll current_step."
    )
    assert result["terminal"] is False


@pytest.mark.parametrize(
    "code", ["no_outside_act", "already_done", "invalid_evidence"]
)
def test_every_evidence_refusal_comes_back_readable(monkeypatch, code):
    _transport(
        monkeypatch, error=_http_error(409, {"error": code, "message": "no."})
    )

    result = _provider().file_evidence("deal-1", "step-2", summary=SUMMARY)

    assert result["ok"] is False
    assert result["error"] == code


def test_file_evidence_never_swallows_an_unrelated_refusal(monkeypatch):
    from toll_harness.email.book_of_houses import BookOfHousesApiError

    _transport(monkeypatch, error=_http_error(500, {"error": "server_error"}))

    with pytest.raises(BookOfHousesApiError):
        _provider().file_evidence("deal-1", "step-2", summary=SUMMARY)


# --------------------------------------------------------------------------
# 4. The tool a raw model actually sees.
# --------------------------------------------------------------------------


class _RecordingProvider:
    def __init__(self):
        self.calls = []

    def file_evidence(self, deal_id, step_id, *, summary, links=None, receipt_ids=None):
        self.calls.append((deal_id, step_id, summary, links, receipt_ids))
        return {"ok": True, "act_id": "act-9", "state": "executed"}


def _context(tmp_path, provider=None):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    return ToolContext(
        run.id,
        store,
        store,
        FilesystemArtifactStore(tmp_path / "artifacts"),
        0,
        toll_bench_provider=provider,
    )


def test_the_tool_is_registered_with_the_args_the_door_takes():
    registry = add_toll_bench_tools(build_standard_registry())
    definitions = {item.name: item for item in registry.definitions()}

    assert "toll_bench.file_evidence" in definitions
    schema = definitions["toll_bench.file_evidence"].input_schema
    assert sorted(schema["properties"]) == [
        "deal_id",
        "links",
        "receipt_ids",
        "step_id",
        "summary",
    ]
    assert schema["required"] == ["deal_id", "step_id", "summary"]
    assert schema["properties"]["links"]["maxItems"] == 5
    assert schema["properties"]["receipt_ids"]["maxItems"] == 5

    # A tool nobody can read is a tool nobody uses: the words have to say when
    # it is the move, what it closes, and what each refusal means.
    words = definitions["toll_bench.file_evidence"].description
    assert "outside" in words
    assert "Allow" in words
    assert "do NOT call toll_bench.file_outcome" in words
    for code in ("no_outside_act", "not_allowed_yet", "already_done", "invalid_evidence"):
        assert code in words


def test_the_tool_hands_every_argument_to_the_provider(tmp_path):
    registry = add_toll_bench_tools(build_standard_registry())
    provider = _RecordingProvider()

    result = registry.execute(
        _context(tmp_path, provider),
        "call-1",
        "toll_bench.file_evidence",
        {
            "deal_id": "deal-1",
            "step_id": "step-2",
            "summary": SUMMARY,
            "links": ["https://ridgeway.example/receipt/88"],
            "receipt_ids": ["receipt-1"],
        },
    )

    assert not result.is_error
    assert result.output["act_id"] == "act-9"
    assert provider.calls == [
        (
            "deal-1",
            "step-2",
            SUMMARY,
            ["https://ridgeway.example/receipt/88"],
            ["receipt-1"],
        )
    ]


def test_the_tool_is_in_the_hand_dealt_on_a_deal_step():
    from toll_harness.cli import _OBLIGATION_DISPATCH

    entry = _OBLIGATION_DISPATCH["deal_step"]
    assert "toll_bench.file_evidence" in entry["tools"]
    # The cue: an approved outside act is the agent's turn to go and do it.
    assert "toll_bench.file_evidence" in entry["instruction"]
    assert "OUTSIDE ACT" in entry["instruction"]


# --------------------------------------------------------------------------
# 5. An outside block is the platform's step: the agent files no outcome on it.
# --------------------------------------------------------------------------


def test_an_approved_outside_act_makes_the_step_the_platforms(monkeypatch):
    """The generic block needed no new code, and this is why.

    `outside` publishes a template in the act catalog, so it reads as a block
    like any other: rule 229's existing guard refuses an agent-written outcome
    on its step, and the platform writes that outcome itself once the evidence
    lands.
    """
    provider = _provider()
    provider._act_kinds = {
        "kinds": {
            "outside": {
                "declaration": {"who": "", "what": "", "how": "", "when": ""},
                "template": [{"title": "", "acts": [{"kind": "outside"}]}],
            }
        }
    }
    monkeypatch.setattr(
        provider.api,
        "current_step",
        lambda deal_id: {
            "ok": True,
            "deal": {"id": deal_id},
            "current_step": {"id": "step-2", "state": "agent_working"},
            "acts": [{"act_id": "act-9", "kind": "outside", "state": "approved"}],
            "declared_acts": [{"kind": "outside"}],
        },
    )

    step = provider.current_step("deal-1")
    assert step["acts"][0]["state"] == "approved"

    refused = provider.file_outcome(
        "target-1", {"note": "Called the shop.", "text": "Done."}, "idem-1"
    )

    assert refused["ok"] is False
    assert refused["error"] == "platform_owned_block"
    assert refused["kinds"] == ["outside"]
