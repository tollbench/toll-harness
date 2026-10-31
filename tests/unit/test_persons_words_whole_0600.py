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
from toll_harness.toll_bench.step import StepAsk

LONG = "She laughs at her own jokes before the punchline, every time. " * 40


def test_the_whole_thread_reaches_the_model():
    said = [f"{i}: " + LONG for i in range(8)]
    payload = _payload(step_thread={
        "messages": [{"id": f"m{i}", "who": "person", "text": t} for i, t in enumerate(said)],
        "unread_from_person": 1,
        "unanswered_elsewhere": [],
    })
    model = _step_model({"call": "post_step_message", "reply": "Got all of it."})
    StepAsk(model, FakeBench()).run(OBLIGATION, payload, brief=BRIEF)

    text = model.invocations[0]["messages"][0].content[0]["text"]
    assert all(message in text for message in said)
