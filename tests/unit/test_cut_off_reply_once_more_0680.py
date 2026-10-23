"""A CUT-OFF REPLY GETS ONE RETRY, EMPTY OR SLICED (0.68.0).

WHAT FORCED THESE TESTS. 0.64.3 asked a reply once more only when the output
limit stopped it before it said ANYTHING. The lab's thinking models also came
back sliced: most of the allowance spent reasoning, then the plan's JSON
stopped part way, which read as no plan at all and lost the attempt on one
reply. Now a reply the output limit cut off gets exactly one retry, empty or
sliced: in the OpenAI adapter on OpenRouter (the reasoning turned down) when
the model reasoned, otherwise in the draft loop (the same question, asking for
the object alone and short). Never both, and never a third call.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from tests.unit.plan_door import a_row, only, reply
from tests.unit.test_cut_off_reply_0643 import ANSWER, FakeClient, _ask, _reply
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.base import (
    asked_again_over_a_cut_off,
    empty_reply_note,
    stop_reason_of,
    was_cut_off,
)
from toll_harness.models.openai import OpenAIModelAdapter
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.draft import CUT_OFF_AGAIN_INSTRUCTION, DraftLoop

FORM = {
    "overview": "Three places that take a booking for four, offered to you.",
    "odds": 0.4,
    "span_days": 7,
    "steps": [
        {"verb": "finds", "do_line": "Find three places for four",
         "hand_over_line": "Three places that take a booking", "need_line": "",
         "declared_odds": 0.5, "proof": "text", "who": "agent"},
    ],
}
SLICED = json.dumps(FORM)[:90]
NO_DRAFT = {"ok": False, "error": "no_draft", "status": 404}


def _sliced_reply(**over):
    return _reply(SLICED, finish="length", completion=5000, **over)


# -- the adapter ------------------------------------------------------------
def test_a_sliced_reply_from_a_model_that_reasoned_is_asked_once_more():
    client = FakeClient(_sliced_reply(reasoning_tokens=3900, reasoning="thinking " * 40),
                        _reply(ANSWER, completion=700))
    response = _ask(OpenAIModelAdapter("deepseek/deepseek-v4.1-flash", max_tokens=5000,
                                       client=client))
    assert response.text == ANSWER
    assert len(client.requests) == 2
    assert client.requests[1]["extra_body"] == {"reasoning": {"effort": "none"}}
    assert response.raw_metadata["retry"] == {"reasoning": {"effort": "none"}}
    assert response.raw_metadata["cut_off_try"]["finish_reason"] == "length"
    assert not was_cut_off(response)


def test_a_sliced_reply_with_no_reasoning_is_marked_for_its_caller_not_retried_here():
    client = FakeClient(_sliced_reply())
    response = _ask(OpenAIModelAdapter("some/model", max_tokens=5000, client=client))
    assert len(client.requests) == 1  # nothing to turn down
    note = response.raw_metadata["sliced"]
    assert note.startswith("cut off: finish_reason length")
    assert f"{len(SLICED)} characters came back and stopped mid-reply" in note
    # `cut_off` / empty_reply_note mean EMPTY, and this reply is not.
    assert "cut_off" not in response.raw_metadata and empty_reply_note(response) == ""
    assert was_cut_off(response) and not asked_again_over_a_cut_off(response)


def test_sliced_twice_is_never_asked_a_third_time():
    thinking = {"reasoning_tokens": 4000, "reasoning": "hmm " * 30}
    client = FakeClient(_sliced_reply(**thinking), _sliced_reply(**thinking))
    response = _ask(OpenAIModelAdapter("z-ai/glm-5.3", max_tokens=5000, client=client))
    assert len(client.requests) == 2
    assert "the second try with reasoning none was cut off too" in (
        response.raw_metadata["sliced"])
    assert was_cut_off(response) and asked_again_over_a_cut_off(response)


def test_what_stopped_a_reply_is_read_the_same_way_for_every_provider():
    def said(stop, **meta):
        return SimpleNamespace(stop_reason=stop, raw_metadata=meta)

    for stop in ("length", "max_tokens", "max_output_tokens", "MAX_TOKENS"):
        assert was_cut_off(said(stop))
    assert not was_cut_off(said("stop")) and not was_cut_off(said("end_turn"))
    assert was_cut_off(said(None, cut_off="cut off: ..."))
    assert stop_reason_of(said(None, finish_reason="length")) == "length"
    assert stop_reason_of(None) == ""


# -- the draft loop -----------------------------------------------------------
def _said(text, stop="end_turn", **meta):
    return ModelResponse(message=ModelMessage.text("assistant", text), text=text,
                         tool_calls=[], stop_reason=stop, raw_metadata=meta)


class Door:
    fleet = None

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[str] = []

    def read_draft(self, target_id, *, kind="bid"):
        self.calls.append("GET")
        return dict(NO_DRAFT)

    def put_draft(self, target_id, outline, *, kind="bid"):
        self.calls.append("PUT")
        if not outline:
            return reply("outline_round")
        return self.answers.pop(0) if self.answers else reply("clean")

    def file_plan_from_draft(self, target_id, proposal_id, idempotency_key):
        self.calls.append("FILE")
        return {"ok": True}


def _plan(model, door, already_read=None):
    return DraftLoop(model, door).run(
        "t-1", kind="plan", proposal_id="p-1", idempotency_key="k",
        brief={"want": "A table for four"}, already_read=already_read or dict(NO_DRAFT),
    )


def _asked(model, n):
    return model.invocations[n]["messages"][0].content[0]["text"]


def test_a_plan_sliced_mid_json_is_asked_once_more_and_files():
    model = ScriptedModelAdapter([_said(SLICED, "max_tokens"), _said(json.dumps(FORM))])
    door = Door()
    out = _plan(model, door)
    assert out["filed"] is True, out
    assert len(model.invocations) == 2
    assert CUT_OFF_AGAIN_INSTRUCTION in _asked(model, 1)
    assert CUT_OFF_AGAIN_INSTRUCTION not in _asked(model, 0)
    assert door.calls == ["PUT", "PUT", "FILE"]


def test_an_empty_cut_off_the_adapter_only_marked_is_asked_once_more():
    empty = _said("", "length", cut_off="cut off: finish_reason length after 0 "
                  "reasoning tokens (2048 output tokens), no text came back")
    model = ScriptedModelAdapter([empty, _said(json.dumps(FORM))])
    out = _plan(model, Door())
    assert out["filed"] is True and len(model.invocations) == 2


def test_cut_off_twice_is_never_asked_a_third_time():
    model = ScriptedModelAdapter([_said(SLICED, "length"), _said(SLICED, "length")])
    out = _plan(model, Door())
    assert out["filed"] is False and out["error"] == "no_form"
    assert len(model.invocations) == 2


def test_a_cut_off_the_adapter_already_asked_again_is_not_asked_a_third_time():
    spent = _said("", "length", cut_off="cut off: ...; the second try with reasoning "
                  "none was cut off too", retry={"reasoning": {"effort": "none"}})
    model = ScriptedModelAdapter([spent])
    out = _plan(model, Door())
    assert out["filed"] is False and len(model.invocations) == 1


def test_a_whole_object_before_the_limit_is_not_asked_again():
    model = ScriptedModelAdapter([_said(json.dumps(FORM) + "\n\nAnd here is why I", "length")])
    out = _plan(model, Door())
    assert out["filed"] is True and len(model.invocations) == 1


def test_a_red_round_does_not_spend_a_second_retry_after_a_cut_off():
    """The whole-form round asks once more over an empty answer -- but not
    when that ask already spent its one retry on a cut-off."""
    row = a_row("form.overview", slot="overview", say="The plan needs its pitch.")
    standing = only(reply("clean"), row)
    model = ScriptedModelAdapter([_said(SLICED, "length"), _said(SLICED, "length")])
    door = Door()
    out = _plan(model, door, already_read=copy.deepcopy(standing))
    assert out["filed"] is False
    assert len(model.invocations) == 2
    assert door.calls == []
