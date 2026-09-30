"""0.57.1: the bench's `issues` and `failure` reach the model, and a calendar
event carries every slot the bench lists.

WHAT FORCED THIS FILE: the harness check of 2026-09-30 against bench contract
4.1.6 and 4.1.7. The bench answers an act-door refusal with `issues`
[{slot, problem, accepted, example}], puts `failure` and `lines` on act views,
and a calendar slot takes whatever kind the service's own form takes. The
harness kept its own copy of a calendar form and dropped the bench's words.
"""
from __future__ import annotations

from toll_harness import cli
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import _act_row


class _Api:
    def __init__(self, refusal=None):
        self.calls = []
        self.refusal = refusal

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        self.calls.append(payload)
        if self.refusal is not None:
            raise self.refusal
        return {"ok": True, "act_id": "ap-1", "kind": payload.get("kind")}


def test_a_calendar_event_carries_with_and_any_bench_slot():
    api = _Api()
    provider = BookOfHousesTollBenchProvider(api)
    act = {"kind": "calendar_event", "summary": "Coffee",
           "start": {"dateTime": "2026-10-01T09:00:00", "timeZone": "UTC"},
           "end": {"dateTime": "2026-10-01T10:00:00", "timeZone": "UTC"},
           "with": "contact-1", "room": "B"}
    assert provider.propose_act("d1", "s-1", act, "k")["ok"] is True
    assert api.calls[0]["with"] == "contact-1"
    assert api.calls[0]["room"] == "B"
    assert api.calls[0]["summary"] == "Coffee"


def test_a_string_start_is_not_refused_locally():
    api = _Api()
    provider = BookOfHousesTollBenchProvider(api)
    act = {"kind": "calendar_event", "summary": "Coffee",
           "start": "2026-10-01T09:00:00", "end": "2026-10-01T10:00:00"}
    assert provider.propose_act("d1", "s-1", act, "k")["ok"] is True
    assert api.calls[0]["start"] == "2026-10-01T09:00:00"


def test_a_missing_calendar_slot_is_the_benchs_to_answer_with_issues():
    issues = [{"slot": "start", "problem": "missing",
               "accepted": "ISO string", "example": "2026-10-01T09:00:00"}]
    refusal = BookOfHousesApiError(
        422, "invalid_act", "no",
        body={"error": "invalid_act", "issues": issues})
    api = _Api(refusal)
    provider = BookOfHousesTollBenchProvider(api)
    out = provider.propose_act(
        "d1", "s-1", {"kind": "calendar_event", "summary": "x"}, "k")
    assert api.calls, "the bench was never asked"
    assert out["ok"] is False and out["issues"] == issues


def test_a_standing_refusal_keeps_issues_and_failure():
    cli._STEP_REFUSALS.pop("s-x", None)
    issues = [{"slot": "start", "problem": "wrong kind",
               "accepted": "ISO string", "example": "2026-10-01T09:00:00"}]
    failure = {"code": "vendor_rejected", "vendor_message": "bad time",
               "slot": "start"}
    cli._refusal_note(
        "s-x", "fp", {"error": "invalid_act", "message": "fix start",
                      "issues": issues, "failure": failure},
        road="ask", number=2)
    body = cli._standing_refusal("s-x", "fp")
    cli._STEP_REFUSALS.pop("s-x", None)
    assert body["issues"] == issues
    assert body["failure"] == failure


def test_an_act_row_keeps_failure_and_lines():
    row = _act_row({"act_id": "a1", "kind": "calendar_event",
                    "state": "failed",
                    "failure": {"code": "x", "vendor_message": "m", "slot": "start"},
                    "lines": ["one", "two"]})
    assert row["failure"]["slot"] == "start"
    assert row["lines"] == ["one", "two"]
