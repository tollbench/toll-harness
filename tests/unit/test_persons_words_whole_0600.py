"""THE PERSON'S WORDS RIDE THE STEP ASK WHOLE, AND ALL OF THEM (0.60.0).

WHAT FORCED THESE TESTS: Steven, 2026-10-06, "I dont want the harness to
hobble, we want to make sure the agents have full capabilites." After 0.59.0
stopped SHEDDING the person's material, fixed clips were still cutting it on
every step ask: only the newest 6 of their messages rode, each cut at 600
characters; the step's promise was cut at 600; only 4 send-back reasons rode.
The bench already caps a message on its side, so the harness cuts none of it.
Machinery (acts, outside replies) keeps its limits, except an act's `note`,
which is the person's own reason for sending it back.
"""
from __future__ import annotations

from tests.unit.test_step_ask import BRIEF, OBLIGATION, FakeBench, _payload
from tests.unit.test_step_ask import _model as _step_model
from toll_harness.toll_bench.step import StepAsk, step_tail, the_move

LONG = "She laughs at her own jokes before the punchline, every time. " * 40


def _tail(payload, obligation=OBLIGATION):
    return step_tail(payload, obligation, the_move(payload, obligation), [])


def test_every_message_of_the_persons_rides_whole():
    said = [f"{i}: " + LONG for i in range(10)]
    payload = _payload(step_thread={
        "messages": (
            [{"id": f"m{i}", "who": "person", "text": text} for i, text in enumerate(said)]
            + [{"id": "a1", "who": "agent", "text": "My own words."}]
        ),
        "unread_from_person": 0,
        "unanswered_elsewhere": [],
    })

    rows = _tail(payload)["from_the_person"]

    assert [row["text"] for row in rows] == said
    assert len(said[0]) > 2_000


def test_the_steps_promise_rides_whole():
    step = dict(_payload()["current_step"], outcome_promise=LONG)
    assert _tail(_payload(current_step=step))["step"]["outcome_promise"] == LONG


def test_every_send_back_reason_rides_whole():
    reasons = [f"{i}: " + LONG for i in range(6)]
    payload = _payload(drafts_sent_back=[
        {"approval_id": f"ap-{i}", "sent_back_reason": reason}
        for i, reason in enumerate(reasons)
    ])
    rows = _tail(payload)["drafts_sent_back"]
    assert [row["sent_back_reason"] for row in rows] == reasons

    obligation = dict(OBLIGATION, kind="draft_sent_back", sent_back_reason=LONG)
    assert _tail(_payload(), obligation)["sent_back_reason"] == LONG


def test_an_acts_note_is_the_persons_and_rides_whole_while_the_act_stays_clipped():
    acts = [{"act_id": f"a{i}", "kind": "email", "state": "sent_back", "note": LONG,
             "words": LONG} for i in range(6)]
    rows = _tail(_payload(acts=acts))["acts"]

    assert len(rows) == 4                     # machinery keeps its count
    assert all(row["note"] == LONG for row in rows)
    assert all(len(row["words"]) == 400 for row in rows)


def test_outside_replies_keep_their_limits():
    replies = [{"id": f"r{i}", "body": LONG} for i in range(4)]
    rows = _tail(_payload(inbound_replies=replies))["inbound_replies"]
    assert len(rows) == 2 and all(len(row["body"]) == 800 for row in rows)


def test_the_whole_thread_reaches_the_model():
    said = [f"{i}: " + LONG for i in range(8)]
    payload = _payload(step_thread={
        "messages": [{"id": f"m{i}", "who": "person", "text": t} for i, t in enumerate(said)],
        "unread_from_person": 1,
        "unanswered_elsewhere": [],
    })
    model = _step_model({"call": "reply_step_message", "reply": "Got all of it."})
    StepAsk(model, FakeBench()).run(OBLIGATION, payload, brief=BRIEF)

    text = model.invocations[0]["messages"][0].content[0]["text"]
    assert all(message in text for message in said)
