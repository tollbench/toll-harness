"""RULE 230 -- the typed deliverable, and the two doors that hand back bytes.

WHAT FORCED IT (production, 2026-09-05): agent Greg filed three `document`
outcomes on a video want, every one of them text sections naming
"stan_animation.mp4". No file was ever uploaded and none could have been: the
harness's file tool wrote UTF-8 text into a folder on the operator's machine
and its outcome call knew only note, text and document.
"""

import base64
import json
import urllib.request

import pytest

from toll_harness.core.types import AutonomyMode
from toll_harness.email.book_of_houses import (
    BookOfHousesApiClient,
    BookOfHousesApiError,
    _multipart_body,
)
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools import sniff as sniffer
from toll_harness.tools.registry import (
    ToolContext,
    add_toll_bench_tools,
    build_standard_registry,
)

# A twelve-byte MP4 header: the box length, `ftyp`, then the isom brand. Enough
# for the sniffer, which reads the bytes and never the name.
MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24
HTML_BYTES = b"<!DOCTYPE html><html><body>an animation, allegedly</body></html>"


# --------------------------------------------------------------------------
# The transport, mocked at the one place the client actually calls out.
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _HttpError(Exception):
    """Stands in for urllib.error.HTTPError, which the client catches."""


def _transport(monkeypatch, *, status=201, body=None, error=None):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
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


def _client():
    return BookOfHousesApiClient(
        base_url="https://bench.example", token="agent-token", maker_id="maker-1"
    )


# --------------------------------------------------------------------------
# 1. deliver_file: the multipart body, the headers, the 201 receipt.
# --------------------------------------------------------------------------


def test_deliver_file_posts_one_multipart_part_and_parses_the_receipt(monkeypatch):
    captured = _transport(
        monkeypatch,
        body={
            "ok": True,
            "receipt_id": "receipt-1",
            "kind": "file",
            "sha256": "abc123",
            "size_bytes": len(MP4_BYTES),
            "filename": "stan_animation.mp4",
            "filed_at": "2026-09-05T18:00:00Z",
        },
    )
    provider = BookOfHousesTollBenchProvider(_client())

    result = provider.deliver_file(
        "deal-1",
        filename="stan_animation.mp4",
        content=MP4_BYTES,
        title="Stan animation",
        step_ref="step-2",
    )

    assert result["ok"] is True
    assert result["receipt_id"] == "receipt-1"
    assert result["sha256"] == "abc123"
    assert result["filename"] == "stan_animation.mp4"
    assert result["sniffed"] == {
        "type": "mp4",
        "family": "video",
        "media_type": "video/mp4",
    }
    # It does NOT hand the ball over; only the outcome does (one-ball law).
    assert "file this step's outcome" in result["message"]

    assert captured["method"] == "POST"
    assert captured["url"] == "https://bench.example/api/bench/deals/deal-1/artifacts"
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert headers["authorization"] == "Bearer agent-token"
    assert headers["x-maker-id"] == "maker-1"
    assert headers["content-type"].startswith("multipart/form-data; boundary=")
    # The idempotency key is the step plus the file's own fingerprint, so the
    # same bytes on the same step are one delivery however many times a cycle
    # retries them.
    assert headers["idempotency-key"].startswith("artifact-step-2-")

    body = captured["body"]
    assert body.count(b'name="file"') == 1
    assert b'filename="stan_animation.mp4"' in body
    assert b"Content-Type: video/mp4" in body
    assert b'name="step_ref"' in body and b"step-2" in body
    assert b'name="title"' in body and b"Stan animation" in body
    assert MP4_BYTES in body


def test_multipart_body_quotes_a_hostile_filename():
    body, content_type = _multipart_body(
        {}, filename='ev"il\nname.mp4', content=b"x", media_type="video/mp4"
    )
    boundary = content_type.split("boundary=")[1]
    assert boundary.encode() in body
    assert b'filename="ev_il_name.mp4"' in body


# --------------------------------------------------------------------------
# 2. The platform is the scanner: its refusals come back as plain results.
# --------------------------------------------------------------------------


def test_deliver_file_surfaces_a_type_mismatch_as_a_plain_result(monkeypatch):
    _transport(
        monkeypatch,
        error=_http_error(
            422,
            {
                "error": "deliverable_type_mismatch",
                "message": "This step promised an MP4; these bytes are HTML.",
                "promised": ["mp4"],
                "found": "html",
            },
        ),
    )
    provider = BookOfHousesTollBenchProvider(_client())

    result = provider.deliver_file(
        "deal-1",
        filename="stan_animation.mp4",
        content=HTML_BYTES,
        title="Stan animation",
        step_ref="step-2",
    )

    assert result["ok"] is False
    assert result["error"] == "deliverable_type_mismatch"
    assert result["status"] == 422
    # VERBATIM: the platform's sentence is the one that tells the model what
    # to do next.
    assert result["message"] == "This step promised an MP4; these bytes are HTML."
    assert result["detail"]["found"] == "html"
    # The harness read the same bytes and agrees they are not a video.
    assert result["sniffed"]["type"] == "html"


def test_deliver_file_surfaces_out_of_turn_filing(monkeypatch):
    _transport(
        monkeypatch,
        error=_http_error(
            422,
            {
                "error": "out_of_turn_filing",
                "message": "No step of yours is working; the ball is with the person.",
            },
        ),
    )
    provider = BookOfHousesTollBenchProvider(_client())

    result = provider.deliver_file(
        "deal-1", filename="a.mp4", content=MP4_BYTES, title="A", step_ref="step-2"
    )

    assert result == {
        "ok": False,
        "error": "out_of_turn_filing",
        "status": 422,
        "message": "No step of yours is working; the ball is with the person.",
        "detail": {
            "error": "out_of_turn_filing",
            "message": "No step of yours is working; the ball is with the person.",
        },
        "terminal": False,
        "sniffed": {"type": "mp4", "family": "video", "media_type": "video/mp4"},
    }


def test_deliver_file_never_swallows_an_unrelated_refusal(monkeypatch):
    _transport(monkeypatch, error=_http_error(500, {"error": "server_error"}))
    provider = BookOfHousesTollBenchProvider(_client())

    with pytest.raises(BookOfHousesApiError):
        provider.deliver_file(
            "deal-1", filename="a.mp4", content=MP4_BYTES, title="A", step_ref="s"
        )


def test_deliver_file_refuses_over_the_platform_cap_before_the_wire(monkeypatch):
    captured = _transport(monkeypatch, body={})
    provider = BookOfHousesTollBenchProvider(_client())

    result = provider.deliver_file(
        "deal-1",
        filename="huge.mp4",
        content=b"\x00" * (50 * 1024 * 1024 + 1),
        title="Huge",
        step_ref="s",
    )

    assert result["ok"] is False
    assert result["error"] == "file_too_large"
    assert "50 MB per file" in result["message"]
    assert "deliver_hosted_file" in result["message"]
    assert captured == {}  # nothing went up the wire


def test_deliver_file_names_the_card_title_it_needs():
    provider = BookOfHousesTollBenchProvider(_client())

    assert provider.deliver_file(
        "d", filename="a.mp4", content=MP4_BYTES, title=" "
    )["error"] == "missing_title"
    assert provider.deliver_file(
        "d", filename="a.mp4", content=MP4_BYTES, title="x" * 81
    )["error"] == "title_too_long"
    assert provider.deliver_file(
        "d", filename="a.mp4", content=b"", title="Empty"
    )["error"] == "empty_file"


# --------------------------------------------------------------------------
# 3. deliver_hosted_file: the agent-hosted lane rides the outcome.
# --------------------------------------------------------------------------


class _OutcomeApi:
    def __init__(self, response=None, error=None):
        self.filed = []
        self.response = response or {"ok": True, "receipt_id": "r-1"}
        self.error = error

    def file_outcome(self, target_id, payload, idempotency_key):
        self.filed.append((target_id, payload, idempotency_key))
        if self.error is not None:
            raise self.error
        return self.response

    def current_step(self, deal_id):
        return {"ok": True}


def test_deliver_hosted_file_files_the_outcome_carrying_the_link():
    api = _OutcomeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.deliver_hosted_file(
        "target-1",
        {
            "note": "Your 8 second animation. Tap download, and use the keep link within a day.",
            "file_url": "https://here.now/abc/stan_animation.mp4",
            "claim_url": "https://here.now/abc/claim/xyz",
            "filename": "stan_animation.mp4",
            "step_ref": "step-2",
        },
        "idem-1",
    )

    assert result == {"ok": True, "receipt_id": "r-1"}
    target_id, payload, key = api.filed[0]
    assert target_id == "target-1"
    assert key == "idem-1"
    assert payload["file_url"] == "https://here.now/abc/stan_animation.mp4"
    assert payload["claim_url"] == "https://here.now/abc/claim/xyz"
    assert payload["filename"] == "stan_animation.mp4"
    assert "text" not in payload and "document" not in payload


def test_deliver_hosted_file_needs_the_url_and_takes_no_other_fields():
    provider = BookOfHousesTollBenchProvider(_OutcomeApi())

    assert provider.deliver_hosted_file("t", {"note": "n"}, "k")["error"] == "missing_file_url"
    refused = provider.deliver_hosted_file(
        "t", {"note": "n", "file_url": "https://x/y", "text": "words"}, "k"
    )
    assert refused["error"] == "invalid_outcome_fields"
    assert refused["unexpected_fields"] == ["text"]


def test_a_claim_link_only_rides_a_hosted_file():
    provider = BookOfHousesTollBenchProvider(_OutcomeApi())

    refused = provider.file_outcome(
        "t", {"note": "n", "text": "words", "claim_url": "https://here.now/claim"}, "k"
    )

    assert refused["error"] == "claim_url_without_file_url"


def test_file_outcome_takes_exactly_one_of_text_document_or_file_url():
    provider = BookOfHousesTollBenchProvider(_OutcomeApi())

    refused = provider.file_outcome(
        "t", {"note": "n", "text": "words", "file_url": "https://x/y"}, "k"
    )

    assert refused["error"] == "exactly_one_outcome_content_required"
    assert refused["allowed"] == ["text", "document", "file_url"]


# --------------------------------------------------------------------------
# 4. A step that promised a file does not close on words.
# --------------------------------------------------------------------------


class _StepApi(_OutcomeApi):
    def __init__(self, deliverable, receipts=None, **kwargs):
        super().__init__(**kwargs)
        self.deliverable = deliverable
        self.receipts = receipts or []

    def current_step(self, deal_id):
        return {
            "ok": True,
            "deal": {"id": deal_id},
            "current_step": {
                "id": "step-2",
                "state": "agent_working",
                "ask": "APPROVE",
                "deliverable": self.deliverable,
                "file_receipts": self.receipts,
            },
        }


def test_current_step_carries_the_promise_and_the_files_always():
    provider = BookOfHousesTollBenchProvider(
        _StepApi({"channel": "file", "family": "video", "types": ["mp4"]})
    )

    payload = provider.current_step("deal-1")

    assert payload["current_step"]["deliverable"] == {
        "channel": "file",
        "family": "video",
        "types": ["mp4"],
    }
    # Always present, including zero: an empty hand-over and no visibility
    # must be tellable apart.
    assert payload["current_step"]["file_receipts"] == []


def test_an_outcome_over_a_promised_file_with_nothing_attached_is_refused():
    api = _StepApi({"channel": "file", "family": "video", "types": ["mp4"]})
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")

    refused = provider.file_outcome(
        "target-1",
        {
            "note": "Files delivered: stan_animation.mp4, clear audio and lip-sync.",
            "document": {"blocks": [{"type": "paragraph", "text": "Files Delivered"}]},
        },
        "idem-1",
    )

    assert refused["ok"] is False
    assert refused["error"] == "deliverable_missing"
    assert refused["message"].startswith("This step promised an MP4; nothing attached.")
    assert "deliver_file" in refused["message"]
    assert api.filed == []  # the filing was never spent on it


def test_the_same_step_closes_once_the_bytes_are_delivered(monkeypatch):
    api = _StepApi({"channel": "file", "family": "video", "types": ["mp4"]})
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")
    provider._step_receipts["step-2"] = [{"receipt_id": "receipt-1"}]

    result = provider.file_outcome(
        "target-1",
        {"note": "The animation is on your card.", "text": "Delivered."},
        "idem-1",
    )

    assert result == {"ok": True, "receipt_id": "r-1"}
    assert api.filed


def test_the_mirror_warns_once_and_then_lets_the_door_decide():
    """`current-step` publishes the promise but NOT the step's file receipts.

    A harness restarted between the delivery and the outcome cannot see a file
    that is genuinely on the step, so the local check speaks once -- which is
    what the model needs, because the usual case is that nothing was ever
    delivered -- and any second attempt goes to the bench, whose
    `deliverable_missing` is the refusal that counts.
    """
    api = _StepApi({"channel": "file", "family": "video", "types": ["mp4"]})
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")
    outcome = {"note": "Delivered.", "text": "The animation is live."}

    first = provider.file_outcome("target-1", outcome, "idem-1")
    second = provider.file_outcome("target-1", outcome, "idem-2")

    assert first["error"] == "deliverable_missing"
    assert second == {"ok": True, "receipt_id": "r-1"}
    assert len(api.filed) == 1


def test_the_files_this_run_delivered_show_on_the_step():
    api = _StepApi({"channel": "file", "family": "video", "types": ["mp4"]})
    provider = BookOfHousesTollBenchProvider(api)
    provider._step_receipts["step-2"] = [{"receipt_id": "receipt-1"}]

    payload = provider.current_step("deal-1")

    assert payload["current_step"]["file_receipts"] == [{"receipt_id": "receipt-1"}]


def test_a_step_that_promised_words_draws_no_new_refusal():
    api = _StepApi({"channel": "text"})
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")

    result = provider.file_outcome(
        "target-1", {"note": "The findings.", "text": "Three venues."}, "idem-1"
    )

    assert result == {"ok": True, "receipt_id": "r-1"}


def test_the_servers_deliverable_missing_reaches_the_model_verbatim():
    # The client turns the 422 into this before the provider ever sees it.
    api = _StepApi(
        None,
        error=BookOfHousesApiError(
            422,
            "deliverable_missing",
            "This step promised an MP4; nothing attached.",
            body={
                "error": "deliverable_missing",
                "message": "This step promised an MP4; nothing attached.",
            },
        ),
    )
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.file_outcome(
        "target-1", {"note": "Done.", "text": "Delivered."}, "idem-1"
    )

    assert result["ok"] is False
    assert result["error"] == "deliverable_missing"
    assert result["message"] == "This step promised an MP4; nothing attached."
    assert result["status"] == 422


# --------------------------------------------------------------------------
# 5. The bid: the deliverable blank is the agent's word or nothing.
# --------------------------------------------------------------------------


def test_the_validate_mirror_names_an_empty_deliverable_blank():
    problems = blocks.deliverable_problems(
        [
            {"title": "Research", "outcome_promise": "Three venues"},
            {"title": "Deliver", "deliverable": "<name what you hand back>"},
        ]
    )

    assert len(problems) == 1
    assert problems[0]["path"] == "steps.1.deliverable"
    assert "for example an MP4" in problems[0]["message"]
    assert "do not promise it" in problems[0]["message"]


def test_a_file_deliverable_must_name_its_family_and_its_types():
    problems = blocks.deliverable_problems(
        [{"deliverable": {"channel": "file", "family": "clip", "types": []}}]
    )
    paths = [problem["path"] for problem in problems]

    assert paths == ["steps.0.deliverable.family", "steps.0.deliverable.types"]


def test_a_type_the_platform_cannot_check_cannot_be_promised():
    """REJ-36 at the bid door, so the mirror names it before the round is spent.

    A promise nothing can check can never be kept, and freezing one would mean
    refusing the delivery forever.
    """
    problems = blocks.deliverable_problems(
        [{"deliverable": {"channel": "file", "family": "video", "types": ["gz"]}}]
    )

    assert [problem["path"] for problem in problems] == ["steps.0.deliverable.types"]
    assert "cannot check gz" in problems[0]["message"]
    assert "mp4" in problems[0]["message"]


def test_a_family_that_does_not_carry_its_own_types_is_named():
    problems = blocks.deliverable_problems(
        [{"deliverable": {"channel": "file", "family": "video", "types": ["png"]}}]
    )

    assert [problem["path"] for problem in problems] == ["steps.0.deliverable.family"]
    assert "does not carry png" in problems[0]["message"]


def test_plain_text_is_one_thing_to_the_scanner():
    """Nothing in the bytes separates markdown from a note from a python file.

    So a promise of any plain-text type is kept by any other -- but html, svg
    and json are positively detected and never satisfy one.
    """
    assert sniffer.matches(b"the venue notes", ["md"]) is True
    assert sniffer.matches(b"print('hi')", ["txt"], filename="run.py") is True
    assert sniffer.matches(HTML_BYTES, ["md"]) is False
    assert sniffer.matches(b'{"a": 1}', ["txt"]) is False


def test_a_filled_deliverable_and_a_text_channel_both_pass():
    assert (
        blocks.deliverable_problems(
            [
                {"deliverable": {"channel": "file", "family": "video", "types": ["mp4"]}},
                {"deliverable": {"channel": "text"}},
                {"title": "Research", "outcome_promise": "Three venues"},
            ]
        )
        == []
    )


def test_the_copied_blank_is_removed_rather_than_filed_as_a_promise():
    proposal = {
        "steps": [
            {"title": "Deliver", "deliverable": "<name what you hand back>"},
            {"title": "Approve", "deliverable": {"channel": "file", "types": ["mp4"]}},
        ]
    }

    trimmed, cleared = blocks.clear_blank_deliverables(proposal)

    assert cleared == [0]
    assert "deliverable" not in trimmed["steps"][0]
    # The model's own words are never touched.
    assert trimmed["steps"][1]["deliverable"] == {"channel": "file", "types": ["mp4"]}
    # And a plan with nothing copied comes back as it was.
    assert blocks.clear_blank_deliverables({"steps": [{"title": "x"}]}) == (
        {"steps": [{"title": "x"}]},
        [],
    )


def test_the_promise_is_named_the_way_the_refusal_says_it():
    assert blocks.promise_words({"channel": "file", "types": ["mp4"]}) == "an MP4"
    assert blocks.promise_words({"channel": "file", "family": "image"}) == "a image file"
    assert blocks.promise_words(None) == "a file"


# --------------------------------------------------------------------------
# 6. RULE 231: what the person already connected, in one line.
# --------------------------------------------------------------------------


class _BriefApi:
    def __init__(self, person_connected):
        self.person_connected = person_connected

    def target_brief(self, target_id):
        return {
            "ok": True,
            "brief": {"target_id": target_id, "person_connected": self.person_connected},
        }


def test_the_brief_says_in_one_line_what_the_person_already_connected():
    provider = BookOfHousesTollBenchProvider(_BriefApi(["google-calendar", "google-gmail"]))

    brief = provider.read_brief("target-1")["brief"]

    assert brief["person_already_connected"] == "The person already connected: Calendar, Gmail."


def test_nothing_connected_is_said_out_loud_too():
    provider = BookOfHousesTollBenchProvider(_BriefApi([]))

    brief = provider.read_brief("target-1")["brief"]

    assert brief["person_already_connected"] == "The person has connected nothing yet."
    assert blocks.connected_sentence(None) == "The person has connected nothing yet."


def test_an_unknown_provider_key_still_reads_as_words():
    assert blocks.connected_sentence(["dropbox"]) == "The person already connected: Dropbox."
    assert (
        blocks.connected_sentence(["notion-workspace"])
        == "The person already connected: Notion Workspace."
    )


# --------------------------------------------------------------------------
# 7. The run folder holds bytes now.
# --------------------------------------------------------------------------


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


def test_files_write_round_trips_binary_through_base64(tmp_path):
    registry = build_standard_registry()
    context = _context(tmp_path)

    written = registry.execute(
        context,
        "call-1",
        "files.write",
        {
            "path": "stan_animation.mp4",
            "content": base64.b64encode(MP4_BYTES).decode("ascii"),
            "encoding": "base64",
        },
    )

    assert not written.is_error
    assert written.output["size_bytes" if "size_bytes" in written.output else "size"] == len(
        MP4_BYTES
    )
    assert written.output["encoding"] == "base64"
    assert written.output["type"] == "mp4"
    assert written.output["family"] == "video"
    assert (
        context.artifact_store.read(context.run_id, "stan_animation.mp4") == MP4_BYTES
    )


def test_files_write_still_writes_plain_text_by_default(tmp_path):
    registry = build_standard_registry()
    context = _context(tmp_path)

    written = registry.execute(
        context, "call-1", "files.write", {"path": "notes.md", "content": "# Notes"}
    )

    assert not written.is_error
    assert written.output["encoding"] == "utf-8"
    assert context.artifact_store.read(context.run_id, "notes.md") == b"# Notes"


def test_files_write_names_bad_base64_instead_of_crashing(tmp_path):
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "files.write",
        {"path": "a.mp4", "content": "not base64!!", "encoding": "base64"},
    )

    assert result.is_error
    assert "not valid base64" in result.output["error"]


def test_files_write_keeps_the_run_directory_sandbox(tmp_path):
    registry = build_standard_registry()

    result = registry.execute(
        _context(tmp_path),
        "call-1",
        "files.write",
        {
            "path": "../../escape.mp4",
            "content": base64.b64encode(MP4_BYTES).decode("ascii"),
            "encoding": "base64",
        },
    )

    assert result.is_error
    assert "escapes the run directory" in result.output["error"]


def test_files_list_reports_the_type_read_out_of_the_bytes(tmp_path):
    registry = build_standard_registry()
    context = _context(tmp_path)
    context.artifact_store.write(context.run_id, "renamed.mp4", HTML_BYTES)
    context.artifact_store.write(context.run_id, "real.mp4", MP4_BYTES)

    listed = registry.execute(context, "call-1", "files.list", {})
    rows = {row["path"]: row for row in listed.output["files"]}

    # The name is a claim; the bytes are the file.
    assert rows["renamed.mp4"]["type"] == "html"
    assert rows["real.mp4"]["type"] == "mp4"
    assert rows["real.mp4"]["family"] == "video"
    assert rows["real.mp4"]["size"] == len(MP4_BYTES)


def test_the_sniffer_says_it_does_not_know_rather_than_guessing():
    assert sniffer.sniff(b"\x07\x08\x09\xfe\xff") == {
        "type": None,
        "family": None,
        "media_type": "application/octet-stream",
    }
    # And a promise it cannot check is a pass, never a refusal: the platform
    # is the scanner.
    assert sniffer.matches(b"\x07\x08\x09\xfe\xff", ["mp4"]) is None
    assert sniffer.matches(MP4_BYTES, ["mp4"]) is True
    assert sniffer.matches(HTML_BYTES, ["mp4"]) is False
    assert sniffer.matches(MP4_BYTES, []) is None


def test_the_sniffer_reads_the_families_the_rule_names():
    assert sniffer.sniff(b"\x89PNG\r\n\x1a\n")["family"] == "image"
    assert sniffer.sniff(b"%PDF-1.7")["family"] == "document"
    assert sniffer.sniff(b"ID3\x03\x00")["family"] == "audio"
    assert sniffer.sniff(b"RIFF\x00\x00\x00\x00WEBP")["type"] == "webp"
    assert sniffer.sniff(b"\x1aE\xdf\xa3" + b"\x42\x82\x84webm")["type"] == "webm"
    assert sniffer.sniff(b"print('hi')", filename="run.py")["family"] == "code"


# --------------------------------------------------------------------------
# 8. The tools a raw model actually sees.
# --------------------------------------------------------------------------


class _RecordingProvider:
    def __init__(self):
        self.calls = []

    def deliver_file(self, deal_id, *, filename, content, title, step_ref=None):
        self.calls.append(("deliver_file", deal_id, filename, content, title, step_ref))
        return {"ok": True, "receipt_id": "receipt-1", "filename": filename}

    def deliver_hosted_file(self, target_id, delivery, idempotency_key):
        self.calls.append(("deliver_hosted_file", target_id, delivery, idempotency_key))
        return {"ok": True}


def test_deliver_file_reads_the_run_folder_and_names_the_step(tmp_path):
    registry = add_toll_bench_tools(build_standard_registry())
    provider = _RecordingProvider()
    context = _context(tmp_path, provider)
    context.artifact_store.write(context.run_id, "out/stan.mp4", MP4_BYTES)

    result = registry.execute(
        context,
        "call-1",
        "toll_bench.deliver_file",
        {
            "deal_id": "deal-1",
            "path": "out/stan.mp4",
            "title": "Stan animation",
            "step_ref": "step-2",
        },
    )

    assert not result.is_error
    assert result.output["receipt_id"] == "receipt-1"
    assert provider.calls[0] == (
        "deliver_file",
        "deal-1",
        "stan.mp4",
        MP4_BYTES,
        "Stan animation",
        "step-2",
    )


def test_deliver_file_says_where_to_put_the_file_when_it_is_not_there(tmp_path):
    registry = add_toll_bench_tools(build_standard_registry())
    context = _context(tmp_path, _RecordingProvider())

    result = registry.execute(
        context,
        "call-1",
        "toll_bench.deliver_file",
        {"deal_id": "deal-1", "path": "missing.mp4", "title": "Missing"},
    )

    assert result.output["error"] == "file_not_found"
    assert "files.write" in result.output["message"]


def test_deliver_hosted_file_passes_only_the_fields_the_person_sees(tmp_path):
    registry = add_toll_bench_tools(build_standard_registry())
    provider = _RecordingProvider()

    result = registry.execute(
        _context(tmp_path, provider),
        "call-1",
        "toll_bench.deliver_hosted_file",
        {
            "target_id": "target-1",
            "file_url": "https://here.now/abc/stan.mp4",
            "claim_url": "https://here.now/abc/claim",
            "filename": "stan.mp4",
            "note": "Your animation. Use the keep link within a day.",
            "idempotency_key": "idem-1",
        },
    )

    assert not result.is_error
    _, target_id, delivery, key = provider.calls[0]
    assert target_id == "target-1"
    assert key == "idem-1"
    assert delivery == {
        "note": "Your animation. Use the keep link within a day.",
        "file_url": "https://here.now/abc/stan.mp4",
        "claim_url": "https://here.now/abc/claim",
        "filename": "stan.mp4",
    }


def test_both_doors_are_described_where_a_raw_model_reads_them():
    registry = add_toll_bench_tools(build_standard_registry())
    definitions = {item.name: item for item in registry.definitions()}

    assert "toll_bench.deliver_file" in definitions
    assert "toll_bench.deliver_hosted_file" in definitions
    # A tool nobody can read is a tool nobody uses: the description has to say
    # what the step will refuse and where the other door is.
    file_words = definitions["toll_bench.deliver_file"].description
    assert "does NOT close" in file_words
    assert "deliverable_type_mismatch" in file_words
    assert "deliver_hosted_file" in file_words
    hosted_words = definitions["toll_bench.deliver_hosted_file"].description
    assert "here.now" in hosted_words
    assert "fetches that address ONCE" in hosted_words
    assert "file_url" in definitions["toll_bench.file_outcome"].description
