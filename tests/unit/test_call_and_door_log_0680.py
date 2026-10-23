"""EVERY MODEL CALL AND EVERY DOOR ANSWER, ONE LINE EACH (0.68.0).

WHAT FORCED THESE TESTS. Reading a lab model's failed plan back off the run
log, nothing said how big each prompt was, how big each answer was, what
stopped it (a cut-off read the same as a model that said nothing), or what
the door said back. Each model call -- the draft loop's, the step ask's and
the runtime's -- now writes one line with the want, deal or run it is about,
the prompt's size, the answer's size and the stop reason; each bench door
answer writes one line at the HTTP client every road goes through, with the
want/deal id, the status, the answer's size and the door's own sentence.
Sizes, counts and sentences only: never a body, a header or a token (a model
reply that could not be used is the one exception, as an excerpt).
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.request

import pytest

from tests.unit.test_context_budget import _response, _runtime
from tests.unit.test_one_read_before_any_write_0680 import FORM, NO_DRAFT, Door
from toll_harness.core.types import ModelMessage, ModelResponse, ModelUsage
from toll_harness.email.book_of_houses import BookOfHousesApiClient, door_line
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import DraftLoop
from toll_harness.toll_bench.step import StepAsk

WANT = "0f8e2a6c-1d2b-4c3a-9e8f-7a6b5c4d3e2f"


def _said(text, stop="end_turn", out=40):
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[],
        usage=ModelUsage(input_tokens=1200, output_tokens=out, total_tokens=1200 + out),
        stop_reason=stop,
    )


def _lines(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def test_every_draft_loop_model_call_is_one_line_with_its_sizes_and_stop(caplog):
    sliced = json.dumps(FORM)[:80]
    model = ScriptedModelAdapter([_said(sliced, "max_tokens", out=2048),
                                  _said(json.dumps(FORM), out=310)])
    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        DraftLoop(model, Door()).run(
            WANT, kind="plan", proposal_id="p-1", idempotency_key="k",
            brief={"want": "A table for four"}, already_read=dict(NO_DRAFT))

    lines = _lines(caplog, "draft loop model call")
    assert len(lines) == len(model.invocations) == 2
    first, second = lines
    assert f"want={WANT}" in first and "kind=plan" in first and "ask=form" in first
    assert "prompt " in first and " chars (prefix " in first and "in 1200" in first
    assert f"answer {len(sliced)} chars" in first and "out 2048 tokens" in first
    assert "stop max_tokens (cut off by the output limit)" in first
    assert "reply malformed_json" in first
    assert "(cut off, again)" in second and "stop end_turn" in second
    assert "reply object" in second
    # A reply that was used never reaches the log; only a reply that could
    # not be used leaves an excerpt (test_unreadable_plan_reply_0680).
    assert "Find three places" not in caplog.text


def test_a_failed_model_call_is_one_line_with_the_exception_class_only(caplog):
    class Broken(ScriptedModelAdapter):
        def invoke(self, **kwargs):
            raise RuntimeError("Authorization: Bearer sk-secret")

    with caplog.at_level(logging.INFO, logger="toll_harness.draft"), \
            pytest.raises(RuntimeError):
        DraftLoop(Broken([]), Door()).run(
            WANT, kind="plan", proposal_id="p-1", idempotency_key="k",
            brief={"want": "x"}, already_read=dict(NO_DRAFT))
    lines = _lines(caplog, "draft loop model call")
    assert len(lines) == 1 and "no answer: api_error RuntimeError" in lines[0]
    assert "sk-secret" not in caplog.text


def test_every_step_ask_model_call_is_one_line_with_the_deal_and_step(caplog):
    model = ScriptedModelAdapter([_said('{"ok": true}', out=12)])
    ask = StepAsk(model, provider=None)
    ask.ids = {"deal_id": "d-7", "step_id": "s-3"}
    with caplog.at_level(logging.INFO, logger="toll_harness.step"):
        ask._ask("Do the move.", {"step": 2}, "hand_back step 2", [])
    lines = _lines(caplog, "step ask hand_back step 2")
    assert len(lines) == 1
    line = lines[0]
    assert "deal=d-7 step=s-3" in line
    assert "model call 1: prompt 1200 input tokens" in line
    assert "answer 12 chars, 0 tool call(s), out 12 tokens, stop end_turn" in line


def test_every_runtime_model_call_is_one_line_with_the_run_and_the_stop(tmp_path, caplog):
    model = ScriptedModelAdapter([
        _response("state.save", {"checkpoint": {"status": "working"}}, input_tokens=400),
        _response("result.complete", {"summary": "done"}, input_tokens=700),
    ])
    runtime, _store = _runtime(tmp_path, model, budget=90_000)
    with caplog.at_level(logging.INFO, logger="toll_harness.runtime"):
        runtime.start("Reach out")
    lines = _lines(caplog, "model call")
    assert len(lines) == 2
    assert "prompt 400 input tokens" in lines[0] and "; run " in lines[0]
    assert "1 tool call(s)" in lines[0] and "stop tool_use" in lines[0]


class _Response:
    def __init__(self, payload, status=200):
        self.raw = json.dumps(payload).encode()
        self.status = status
        self.headers = {}

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_every_door_answer_is_one_line_with_the_want_the_size_and_the_words(
        monkeypatch, caplog):
    answer = {"ok": True, "ready": False, "remaining": 2,
              "next_fix": {"say": "Step 2 needs\nits subject line.", "path": "x"},
              "problems": [{"who_fixes": "agent"}, {"who_fixes": "platform"}],
              "token": "never-logged"}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=None: _Response(answer))
    client = BookOfHousesApiClient(base_url="https://bench.test", token="tok")
    with caplog.at_level(logging.INFO, logger="toll_harness.door"):
        client.put_proposal_draft(WANT, {"form": {"steps": []}}, kind="plan")
    lines = _lines(caplog, "door answer")
    assert len(lines) == 1
    line = lines[0]
    raw = len(json.dumps(answer).encode())
    assert line.startswith(
        f"door answer PUT /api/bench/targets/{WANT}/proposals/draft -> 200 ({raw} chars)")
    assert f"want={WANT}" in line and "kind=plan" in line
    assert "ok=True ready=False remaining=2" in line and "red=1/2" in line
    assert 'says "Step 2 needs its subject line."' in line
    assert "never-logged" not in caplog.text and "Bearer" not in caplog.text


def test_a_refusal_is_one_line_with_its_status_and_the_doors_sentence(monkeypatch, caplog):
    body = json.dumps({"ok": False, "error": "draft_paused",
                       "message": "The plan door is resting until 10:00."}).encode()

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = BookOfHousesApiClient(base_url="https://bench.test", token="tok")
    with caplog.at_level(logging.INFO, logger="toll_harness.door"):
        try:
            client.get_proposal_draft(WANT, kind="plan")
        except Exception as error:  # noqa: BLE001 - the refusal is the point
            assert getattr(error, "status", None) == 409
    lines = _lines(caplog, "door answer")
    assert len(lines) == 1
    assert f"GET /api/bench/targets/{WANT}/proposals/draft -> 409 ({len(body)} chars)" in (
        lines[0])
    assert "error=draft_paused" in lines[0]
    assert 'says "The plan door is resting until 10:00."' in lines[0]


def test_a_plain_reads_body_never_reaches_the_log():
    line = door_line("GET", "/api/bench/me/mail", 200, 90,
                     {"ok": True, "message": "Dear Sam, the private note"})
    assert line == "door answer GET /api/bench/me/mail -> 200 (90 chars) ok=True"
