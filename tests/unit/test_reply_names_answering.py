"""A REPLY NAMES WHAT IT ANSWERS (0.56.1).

WHAT FORCED THESE TESTS: 2026-09-25 07:20 UTC, lab agent Rick, deal 40b6df58,
step 17fbac0e. The step loop answered the person's message 871c5dbb six times
in 70 seconds. The bench pays only the message a reply NAMES on `answering`,
and the harness never sent the key: every 200 read `answered: []`,
`answering_key_read: null`, `unread_from_person: 1`, so the next poll owed the
same answer again. Each test holds one line of the fix.
"""
from __future__ import annotations

import logging

import pytest

from tests.unit.test_step_ask import BRIEF, OBLIGATION, FakeBench, _model, _payload
from toll_harness.core.types import AutonomyMode
from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.toll_bench import step as step_module
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import (
    StepAsk,
    answering_for,
    note_unpaid_reply,
    unanswered_on_step,
)
from toll_harness.tools.registry import (
    ToolContext,
    add_toll_bench_tools,
    build_standard_registry,
)


@pytest.fixture(autouse=True)
def _no_held_replies():
    step_module._UNPAID_REPLIES.clear()
    yield
    step_module._UNPAID_REPLIES.clear()


def _row(mid):
    return {"id": mid, "posted_at": "2026-09-25T07:19:00Z",
            "answer_with": {"call": "POST /x", "body": {"reply": "<your answer>",
                                                         "answering": mid}}}


def _spoke(*ids):
    return _payload(step_thread={
        "messages": [{"id": mid, "who": "person", "text": "when?"} for mid in ids],
        "unread_from_person": len(ids),
        "unanswered_elsewhere": [],
        "unanswered_messages": [_row(mid) for mid in ids],
        "post_reply": "/x",
    })


class PaidBench(FakeBench):
    """A bench that pays what a reply names, the way the live one does."""

    def __init__(self, *, pays=True):
        super().__init__()
        self.pays = pays

    def reply_step_message(self, deal_id, step_id, reply, key, answering=None):
        super().reply_step_message(deal_id, step_id, reply, key, answering=answering)
        named = [answering] if isinstance(answering, str) else list(answering or [])
        paid = named if self.pays else []
        return {"ok": True, "message": {"id": "m-9"}, "answered": paid,
                "answering_key_read": "answering" if paid else None,
                "unread_from_person": 0 if paid else 1,
                "unanswered_messages": [] if paid else [_row("871c5dbb")]}


# ---------------------------------------------------------------------------
# 1. The API door and the provider send the key
# ---------------------------------------------------------------------------
class _Recorder(BookOfHousesApiClient):
    def __init__(self):  # noqa: D401 - no network, just the body
        self.sent = []

    def _request(self, method, path, payload=None, authenticated=False,
                 idempotency_key=None, **_):
        self.sent.append((method, path, payload))
        return {"ok": True}


def test_the_api_door_sends_answering_only_when_there_is_one():
    api = _Recorder()
    api.post_step_message("d1", "s1", "Tuesday.", "k1", answering="871c5dbb")
    api.post_step_message("d1", "s1", "Status.", "k2")
    api.post_step_message("d1", "s1", "Both.", "k3", answering=["a", "b"])
    assert api.sent[0][2] == {"reply": "Tuesday.", "answering": "871c5dbb"}
    assert api.sent[1][2] == {"reply": "Status."}
    assert api.sent[2][2] == {"reply": "Both.", "answering": ["a", "b"]}


class _StepApi:
    def __init__(self, unanswered):
        self.unanswered = unanswered
        self.posts = []

    def current_step(self, deal_id):
        return {"ok": True, "deal": {"id": deal_id},
                "current_step": {"id": "s1", "number": 2},
                "step_thread": {"unread_from_person": len(self.unanswered),
                                "messages": [], "unanswered_messages": self.unanswered}}

    def post_step_message(self, deal_id, step_id, reply, idempotency_key, answering=None):
        self.posts.append((reply, answering))
        return {"ok": True, "answered": [answering] if answering else [],
                "answering_key_read": "answering" if answering else None,
                "unread_from_person": 0, "unanswered_messages": []}


def test_the_provider_keeps_the_list_and_names_the_latest_when_the_caller_names_nothing():
    api = _StepApi([_row("m-old"), _row("871c5dbb")])
    provider = BookOfHousesTollBenchProvider(api)
    read = provider.current_step("d1")
    assert [row["id"] for row in read["step_thread"]["unanswered_messages"]] == [
        "m-old", "871c5dbb"]
    provider.reply_step_message("d1", "s1", "Tuesday at 2.", "k1")
    assert api.posts[-1] == ("Tuesday at 2.", "871c5dbb")
    # The 200 said nothing is owed now: the next status line claims nothing.
    provider.reply_step_message("d1", "s1", "Booked.", "k2")
    assert api.posts[-1] == ("Booked.", None)


def test_the_provider_passes_the_callers_own_pick_through():
    api = _StepApi([_row("m-old"), _row("871c5dbb")])
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d1")
    provider.reply_step_message("d1", "s1", "On the first one.", "k1", answering="m-old")
    assert api.posts[-1] == ("On the first one.", "m-old")


# ---------------------------------------------------------------------------
# 2. The step loop names it; the model keeps no book
# ---------------------------------------------------------------------------
def test_answering_for_takes_a_valid_pick_else_the_latest():
    owed = ["m1", "m2", "m3"]
    assert answering_for([], "m1") is None
    assert answering_for(owed) == "m3"
    assert answering_for(owed, "m2") == "m2"
    assert answering_for(owed, "nope") == "m3"
    assert answering_for(owed, ["m1", "m2"]) == ["m1", "m2"]
    assert answering_for(owed, ["m1", "gone"]) == "m3"
    assert answering_for(owed, ["m2"]) == "m2"


def test_unanswered_on_step_reads_the_list_then_the_older_thread():
    assert unanswered_on_step(_spoke("a", "b")) == ["a", "b"]
    older = _payload(step_thread={"messages": [{"id": "x", "who": "person"},
                                               {"id": "y", "who": "agent"}],
                                  "unread_from_person": 1})
    assert unanswered_on_step(older) == ["x"]
    assert unanswered_on_step(_payload()) == []


def test_ricks_step_names_the_message_the_model_did_not():
    bench = PaidBench()
    out = StepAsk(
        _model({"call": "reply_step_message", "reply": "Tuesday works, I'll book it."}),
        bench,
    ).run(OBLIGATION, _spoke("m-old", "871c5dbb"), brief=BRIEF)
    assert out["ok"] and out["move"] == "answer_person"
    assert bench.answering == ["871c5dbb"]


def test_a_park_with_nothing_owed_claims_nothing_and_with_a_debt_names_the_latest():
    bench = FakeBench()
    bench.report_worker_status = lambda *a, **k: {"ok": True}
    ask = StepAsk(_model(), bench)
    ask._park_the_step("d1", "s-1", 1, "I have stopped.", "k1")
    ask._park_the_step("d1", "s-1", 1, "I have stopped.", "k2",
                       answering=answering_for(unanswered_on_step(_spoke("a", "b"))))
    assert bench.answering == [None, "b"]


def test_a_reply_on_a_hand_back_with_nothing_owed_claims_nothing():
    bench = FakeBench()
    StepAsk(_model(), bench)._make_the_call(
        {"deal_id": "d1", "step_id": "s-1"},
        {"call": "reply_step_message", "action": {}},
        {"reply": "Working on it."},
        _payload(),
    )
    assert bench.answering == [None]


# ---------------------------------------------------------------------------
# 3. The registry tool takes it and passes it through
# ---------------------------------------------------------------------------
def test_the_reply_tool_takes_an_optional_answering(tmp_path):
    seen = []

    class Provider:
        def reply_step_message(self, deal_id, step_id, reply, key, answering=None):
            seen.append(answering)
            return {"ok": True}

    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    context = ToolContext(run.id, store, store, FilesystemArtifactStore(tmp_path / "a"), 0,
                          toll_bench_provider=Provider())
    registry = add_toll_bench_tools(build_standard_registry())
    base = {"deal_id": "d1", "step_id": "s1", "reply": "Yes.", "idempotency_key": "k"}
    assert not registry.execute(context, "c1", "toll_bench.reply_step_message", base).is_error
    assert not registry.execute(context, "c2", "toll_bench.reply_step_message",
                                dict(base, answering="871c5dbb")).is_error
    assert not registry.execute(context, "c3", "toll_bench.reply_step_message",
                                dict(base, answering=["a", "b"])).is_error
    assert seen == [None, "871c5dbb", ["a", "b"]]


# ---------------------------------------------------------------------------
# 4. An unpaid 200 is said out loud and not answered again
# ---------------------------------------------------------------------------
def test_an_unpaid_reply_is_logged_and_the_same_message_is_not_answered_again(caplog):
    bench = PaidBench(pays=False)
    with caplog.at_level(logging.WARNING, logger="toll_harness.step"):
        first = StepAsk(
            _model({"call": "reply_step_message", "reply": "Tuesday works."}), bench,
        ).run(OBLIGATION, _spoke("871c5dbb"), brief=BRIEF)
        # The next poll: same message still owed. Rick sent five more here.
        again = StepAsk(_model(), bench).run(OBLIGATION, _spoke("871c5dbb"), brief=BRIEF)
    assert first["ok"] and len(bench.replies) == 1
    assert again["ok"] and again["held"] == "871c5dbb" and again["model_calls"] == 0
    assert len(bench.replies) == 1
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("871c5dbb" in line and "paid nothing" in line for line in warned)


def test_a_new_message_is_answered_even_while_an_old_one_is_held():
    bench = PaidBench(pays=False)
    StepAsk(_model({"call": "reply_step_message", "reply": "Tuesday."}), bench).run(
        OBLIGATION, _spoke("871c5dbb"), brief=BRIEF)
    bench.pays = True
    out = StepAsk(_model({"call": "reply_step_message", "reply": "And Wednesday."}),
                  bench).run(OBLIGATION, _spoke("871c5dbb", "m-new"), brief=BRIEF)
    assert out["ok"] and bench.answering == ["871c5dbb", "m-new"]


def test_a_paid_reply_and_an_older_bench_hold_nothing():
    assert note_unpaid_reply({"ok": True, "answered": ["a"],
                              "answering_key_read": "answering"}, "a", "s1") == []
    # A 200 that carries neither key is an older bench and proves nothing.
    assert note_unpaid_reply({"ok": True, "message_id": "m"}, "a", "s1") == []
    assert note_unpaid_reply({"ok": True, "answered": []}, None, "s1") == []
    assert step_module._UNPAID_REPLIES == {}


def test_the_hold_runs_out():
    note_unpaid_reply({"ok": True, "answered": [], "answering_key_read": None,
                       "unanswered_messages": [_row("m1")]}, "m1", "s1")
    assert step_module.reply_is_held("m1")
    later = step_module._UNPAID_REPLIES["m1"] + step_module.UNPAID_REPLY_HOLD_SECONDS + 1
    assert not step_module.reply_is_held("m1", now=later)
