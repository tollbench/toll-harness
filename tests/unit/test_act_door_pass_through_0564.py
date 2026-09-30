"""THE ACT DOOR IS THE BENCH'S (0.56.4): the harness passes an act through.

WHAT FORCED THIS FILE (lab agents Rick and Ali, prod, 2026-09-25 09:32 UTC).
Rick's deal 40b6df58 step 5 ("Next morning: send each person you pick a
short personal follow-up") is a loop step: `repeats: {cadence: for_each,
of_step: who}`. The bench published one act form per open item, each with
`repeat_item` const and required, and current_step said "file it at file_at
now with repeat_item". The model did exactly that. The harness's own
`propose_act` kept a private allow-list of act fields that had never heard of
`repeat_item`, and answered `invalid_act_fields` before anything reached the
server. On the retry the model dropped `repeat_item` as told, and the bench's
own schema check answered `arguments fails required`. Twice a cycle, three
cycles, and the step was parked; Ali's step 7 the same. The harness had
become a second wall in front of the one door.

So: the act goes to the server as filed. An unknown key rides through, an
unknown kind is the server's to answer with its `kinds` list, and whatever
the door refuses comes back in the door's own words.
"""
from __future__ import annotations

import pytest

from tests.unit.test_step_ask import OBLIGATION, _model, _payload
from tests.unit.test_step_doors_0562 import ITEM, _loop_payload
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import StepAsk


class _Api:
    """The bench's act door, recording what reached the wire."""

    def __init__(self, refusal: BookOfHousesApiError | None = None):
        self.calls: list[tuple[str, str, dict, str]] = []
        self.refusal = refusal

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        self.calls.append((deal_id, step_id, payload, idempotency_key))
        if self.refusal is not None:
            raise self.refusal
        return {"ok": True, "act_id": "ap-1", "kind": payload.get("kind"),
                "status": "held"}


def _provider(refusal=None):
    api = _Api(refusal)
    return BookOfHousesTollBenchProvider(api), api


# ---------------------------------------------------------------------------
# 1. The loop step: repeat_item reaches the bench
# ---------------------------------------------------------------------------
def test_a_loop_act_naming_its_item_reaches_the_bench():
    provider, api = _provider()
    act = {"kind": "email", "to": "friend@example.com",
           "subject": "Good to meet you", "body_text": "Hi, following up.",
           "repeat_item": ITEM}
    out = provider.propose_act("d1", "s-1", act, "k-1")
    assert out["ok"] is True, out
    assert api.calls == [("d1", "s-1", act, "k-1")]


def test_the_loop_form_files_on_the_first_ask_through_the_real_provider():
    """Rick's road, whole: the bench's loop form, the model's one call, the
    real provider. One model call, and the item is on the wire."""
    provider, api = _provider()
    model = _model({"call": "propose_act", "kind": "email", "contact_ref": ITEM,
                    "repeat_item": ITEM, "subject": "Good to meet you",
                    "body_text": "Hi, following up."})
    out = StepAsk(model, provider).run(OBLIGATION, _loop_payload())
    assert out["ok"] is True, out
    assert out["model_calls"] == 1
    assert api.calls and api.calls[0][2]["repeat_item"] == ITEM
    assert api.calls[0][2]["contact_ref"] == ITEM


def test_an_email_the_bench_fills_rides_through_unrefused():
    """On a loop over people the bench fills the recipient from repeat_item,
    and on a follow-up it fills the subject from the thread. The harness
    refused both as contact_required / missing_act_field; it asks neither."""
    provider, api = _provider()
    act = {"kind": "email", "repeat_item": ITEM, "body_text": "Hi again."}
    out = provider.propose_act("d1", "s-1", act, "k-2")
    assert out["ok"] is True
    assert api.calls[0][2] == act


def test_every_key_the_bench_takes_rides_through():
    provider, api = _provider()
    act = {"kind": "email", "contact_ref": "c-1", "subject": "Hello",
           "body_text": "Hi", "attachment_file_ids": ["f-1"], "seat": 2,
           "purpose": "the intro", "a_field_from_next_week": {"x": 1}}
    assert provider.propose_act("d1", "s-1", act, "k-3")["ok"] is True
    assert api.calls[0][2] == act


def test_a_calendar_event_on_a_schedule_carries_its_date():
    """Contract 3.28: a calendar_event on a schedule names its day with
    repeat_item. The kind's shaping stays; the key the shaping does not know
    rides along."""
    provider, api = _provider()
    start = {"dateTime": "2026-09-26T18:00:00-07:00", "timeZone": "America/Los_Angeles"}
    end = {"dateTime": "2026-09-26T19:00:00-07:00", "timeZone": "America/Los_Angeles"}
    out = provider.propose_act("d1", "s-1", {
        "kind": "calendar_event", "summary": "Practice", "start": start,
        "end": end, "repeat_item": "2026-09-26"}, "k-4")
    assert out["ok"] is True
    assert api.calls[0][2]["repeat_item"] == "2026-09-26"
    assert api.calls[0][2]["summary"] == "Practice"


def test_a_meeting_keeps_its_shaping_and_passes_what_it_does_not_know():
    provider, api = _provider()
    provider.propose_act("d1", "s-1", {
        "kind": "meeting", "with": "r@x.co", "start": {"dateTime": "x"},
        "body_text": "When: Friday 11", "repeat_item": ITEM}, "k-5")
    payload = api.calls[0][2]
    assert payload["repeat_item"] == ITEM
    # still never a slot or a body on a meeting (rule 223)
    assert "start" not in payload and "body_text" not in payload


# ---------------------------------------------------------------------------
# 2. The kinds are the bench's
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["record", "post", "outside", "calls"])
def test_every_kind_the_bench_lists_reaches_the_bench(kind):
    provider, api = _provider()
    act = {"kind": kind, "summary": "what the act is", "repeat_item": ITEM}
    out = provider.propose_act("d1", "s-1", act, "k-6")
    assert out["ok"] is True
    assert api.calls[0][2] == act


def test_an_unknown_kind_is_answered_by_the_bench_with_its_kinds():
    kinds = ["email", "calendar_event", "meeting", "record", "post", "outside", "calls"]
    sentence = "unknown act kind 'carrier_pigeon'; kinds today: " + ", ".join(kinds)
    refusal = BookOfHousesApiError(
        422, sentence, "UNPROCESSABLE ENTITY",
        body={"ok": False, "error": sentence, "kinds": kinds})
    provider, api = _provider(refusal)
    out = provider.propose_act("d1", "s-1", {"kind": "carrier_pigeon"}, "k-7")
    assert len(api.calls) == 1, "the harness answered for the bench"
    assert out["ok"] is False and out["status"] == 422
    assert out["kinds"] == kinds
    assert out["message"] == sentence


# ---------------------------------------------------------------------------
# 3. A refusal comes back in the door's own words
# ---------------------------------------------------------------------------
def _item_not_open():
    sentence = ("repeat_item 1111 is not open on this step: it is held for the "
                "person's approval. File the next open item.")
    return BookOfHousesApiError(
        422, "repeat_item_not_open", sentence,
        body={"ok": False, "error": sentence, "code": "repeat_item_not_open",
              "fields": ["repeat_item"], "open_items": ["2222"]}), sentence


def test_the_door_refusal_is_returned_verbatim():
    refusal, sentence = _item_not_open()
    provider, _api = _provider(refusal)
    out = provider.propose_act("d1", "s-1", {"kind": "email", "repeat_item": "1111",
                                             "body_text": "Hi"}, "k-8")
    assert out["ok"] is False
    assert out["error"] == "repeat_item_not_open"
    assert out["message"] == sentence
    assert out["fields"] == ["repeat_item"] and out["open_items"] == ["2222"]


def test_the_retry_sees_the_door_s_own_words(monkeypatch):
    refusal, sentence = _item_not_open()
    provider, api = _provider(refusal)
    answer = {"call": "propose_act", "kind": "email", "contact_ref": ITEM,
              "repeat_item": ITEM, "subject": "Hi", "body_text": "Hi."}
    model = _model(answer, answer)
    tails = []
    from toll_harness.toll_bench import step as step_module
    real_tail = step_module.step_tail

    def recording_tail(*args, **kwargs):
        tail = real_tail(*args, **kwargs)
        tails.append(tail)
        return tail

    monkeypatch.setattr(step_module, "step_tail", recording_tail)
    out = StepAsk(model, provider).run(OBLIGATION, _loop_payload())
    assert len(api.calls) == 2  # both asks reached the door
    refused = tails[-1]["the_bench_refused"]
    assert refused["error"] == "repeat_item_not_open"
    assert refused["message"] == sentence and refused["fields"] == ["repeat_item"]
    assert out["failure"] == "server_rejected"


def test_a_server_fault_is_still_raised():
    provider, _api = _provider(BookOfHousesApiError(500, "server_on_fire", "boom"))
    with pytest.raises(BookOfHousesApiError):
        provider.propose_act("d1", "s-1", {"kind": "email", "body_text": "x"}, "k-9")


# ---------------------------------------------------------------------------
# 4. The tool the old road hands the model says the same
# ---------------------------------------------------------------------------
def _act_tool():
    from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry
    registry = add_toll_bench_tools(build_standard_registry())
    return registry, next(d for d in registry.definitions()
                          if d.name == "toll_bench.propose_act")


def test_the_tool_names_the_bench_s_kinds_and_repeat_item():
    _registry, tool = _act_tool()
    assert "list_act_kinds" in tool.description
    assert "repeat_item" in tool.description
    assert "three kinds" not in tool.description
    act = tool.input_schema["properties"]["act"]
    assert "repeat_item" in act["properties"]
    assert act["required"] == ["kind"]


def test_the_tool_takes_a_key_it_does_not_list():
    from toll_harness.tools.registry import _validate
    _registry, tool = _act_tool()
    _validate({"deal_id": "d1", "step_id": "s-1", "idempotency_key": "k",
               "act": {"kind": "record", "repeat_item": ITEM,
                       "contact_ref": "c-1", "window": {"start": "a", "end": "b"},
                       "something_new": 1}},
              tool.input_schema)


def test_the_payload_fixture_is_unchanged():
    # guard: the loop fixture this file borrows still carries the const item
    form = next(a for a in _loop_payload()["submission"]["actions"]
                if a["action"] == "propose_act")
    assert form["schema"]["properties"]["repeat_item"] == {"const": ITEM}
    assert "repeat_item" in form["schema"]["required"]
    assert _payload()["current_step"]["id"] == "s-1"
