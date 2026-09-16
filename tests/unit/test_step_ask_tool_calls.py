"""THE STEP ASK SPEAKS TOOL CALLS, END TO END (0.43.2).

WHAT FORCED THESE TESTS: `StepAsk._ask` invoked the model with `tools=[]` and
read only `response.text` for `{"call": ...}`, while the CLI rail tells the
model to answer `{"text": ..., "tool_calls": [...]}` and splits the calls off
the text. An explicit propose_act came back as a tool call with empty text and
was read as no call at all. Each test here runs a real adapter's parse (the
CLI rail's envelope, the Anthropic adapter's tool_use blocks) into the step
ask and on to the bench door, with nothing mocked in between.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.test_step_ask import ACTIONS, BRIEF, CAFES, OBLIGATION, FakeBench, _form, _payload
from toll_harness.models.anthropic import AnthropicModelAdapter
from toll_harness.models.cli_rail import ClaudeCodeCliAdapter
from toll_harness.toll_bench.step import ROAD_STEP_ASK, StepAsk

ACT = {
    "kind": "email",
    "contact_ref": "c-1",
    "subject": "Meeting Jane",
    "body_text": "Hi John, Jane would like to meet.",
    "purpose": "introduce",
}
DECLARED = {"declared_acts": [{"kind": "email", "filed": 0, "held": 0, "executed": 0}]}


def _cli(tmp_path, *envelopes):
    """The Claude Code rail with its subprocess faked: one reply per call."""
    replies = [
        json.dumps({"type": "result", "subtype": "success", "is_error": False,
                    "result": e if isinstance(e, str) else json.dumps(e),
                    "usage": {"input_tokens": 100, "output_tokens": 20}})
        for e in envelopes
    ]
    prompts = []

    def run(argv, *, input, capture_output, text, timeout, cwd):
        prompts.append(input)
        return SimpleNamespace(returncode=0, stdout=replies.pop(0), stderr="")

    adapter = ClaudeCodeCliAdapter(workdir=tmp_path, runner=run)
    adapter.prompts = prompts
    return adapter


def _call(name, arguments, text=""):
    return {"text": text, "tool_calls": [{"name": name, "arguments": arguments}]}


def test_a_propose_act_tool_call_with_empty_text_is_filed(tmp_path):
    bench = FakeBench()
    model = _cli(tmp_path, _call("propose_act", ACT))
    out = StepAsk(model, bench).run(OBLIGATION, _payload(**DECLARED), brief=BRIEF)
    assert out["ok"] is True and out["road"] == ROAD_STEP_ASK and out["call"] == "propose_act"
    assert bench.acts == [("d1", "s-1", ACT, bench.acts[0][3])]
    assert bench.acts[0][3].startswith("step-s-1-propose_act-")
    # The rail showed the model the bench's form, whole, and no JSON-in-text rule.
    prompt = model.prompts[0]
    assert '"name": "propose_act"' in prompt and '"else"' in prompt
    assert '{"call"' not in prompt


def test_the_same_answer_twice_carries_the_same_idempotency_key(tmp_path):
    first, second = FakeBench(), FakeBench()
    StepAsk(_cli(tmp_path, _call("propose_act", ACT)), first).run(
        OBLIGATION, _payload(**DECLARED), brief=BRIEF)
    StepAsk(_cli(tmp_path, _call("propose_act", ACT)), second).run(
        OBLIGATION, _payload(**DECLARED), brief=BRIEF)
    assert first.acts[0][3] == second.acts[0][3]


def test_an_outcome_is_submitted_through_the_tool_call(tmp_path):
    bench = FakeBench()
    arguments = {key: value for key, value in CAFES.items() if key != "call"}
    out = StepAsk(_cli(tmp_path, _call("file_outcome", arguments)), bench).run(
        OBLIGATION, _payload(), brief=BRIEF)
    assert out["ok"] is True and out["call"] == "file_outcome"
    assert bench.outcomes[0][0] == "t1" and bench.outcomes[0][1] == arguments
    assert bench.pulses == []  # this bench records completion on the outcome


def test_a_message_reply_is_one_tool_call(tmp_path):
    bench = FakeBench()
    spoke = _payload(step_thread={"messages": [{"id": "m1", "who": "person", "text": "Tue?"}],
                                  "unread_from_person": 1, "unanswered_elsewhere": []})
    model = _cli(tmp_path, _call("reply_step_message", {"reply": "Tuesday works."}))
    out = StepAsk(model, bench).run(OBLIGATION, spoke, brief=BRIEF)
    assert out["ok"] and out["move"] == "answer_person"
    assert bench.replies[0][:3] == ("d1", "s-1", "Tuesday works.")


def test_anthropic_tool_use_with_no_text_is_dispatched_and_only_its_view_is_adapted():
    sent = {}

    class Messages:
        def create(self, **request):
            sent.update(request)
            block = SimpleNamespace(type="tool_use", id="tu1", name="propose_act", input=ACT)
            return SimpleNamespace(content=[block], stop_reason="tool_use", model="m",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    bench = FakeBench()
    model = AnthropicModelAdapter("m", client=SimpleNamespace(messages=Messages()))
    out = StepAsk(model, bench).run(OBLIGATION, _payload(**DECLARED), brief=BRIEF)
    assert out["ok"] is True and bench.acts[0][2] == ACT
    [tool] = [t for t in sent["tools"] if t["name"] == "propose_act"]
    assert "anyOf" not in tool["input_schema"]
    assert tool["input_schema"]["else"]["anyOf"]  # nested rules are left as the bench wrote them


def test_arguments_are_checked_against_the_full_schema_before_anything_is_filed(tmp_path):
    bench = FakeBench()
    no_recipient = {k: v for k, v in ACT.items() if k != "contact_ref"}
    model = _cli(tmp_path, _call("propose_act", no_recipient), _call("propose_act", no_recipient))
    out = StepAsk(model, bench).run(OBLIGATION, _payload(**DECLARED), brief=BRIEF)
    assert out["ok"] is False and out["failure"] == "invalid_arguments"
    assert bench.acts == []
    assert "Hi John" not in out["message"]  # the diagnostic names the rule, not the words
    assert out["model_calls"] == 2  # asked once more with the refusal, as before


def test_each_failure_is_named_for_what_it_is(tmp_path):
    cases = [
        ("", "empty_response"),
        ("I would file the email now.", "malformed_response"),
        ({"text": "", "tool_calls": [{"name": "propose_act", "arguments": ACT},
                                     {"name": "propose_act", "arguments": ACT}]},
         "malformed_response"),
        (_call("file_outcome", {"note": "done", "step_ref": "s-1"}), "disallowed_tool"),
    ]
    for reply, failure in cases:
        bench = FakeBench()
        model = _cli(tmp_path, reply, reply, reply, reply)
        out = StepAsk(model, bench).run(OBLIGATION, _payload(**DECLARED), brief=BRIEF)
        assert out["ok"] is False and out["failure"] == failure, (reply, out)
        assert bench.acts == [] and bench.outcomes == []


def test_a_bench_refusal_is_a_server_rejection(tmp_path):
    class Refuses(FakeBench):
        def propose_act(self, deal_id, step_id, act, key):
            self.acts.append((deal_id, step_id, act, key))
            return {"ok": False, "error": "contact_required", "message": "pick a contact"}

    bench = Refuses()
    model = _cli(tmp_path, _call("propose_act", ACT), _call("propose_act", ACT))
    out = StepAsk(model, bench).run(OBLIGATION, _payload(**DECLARED), brief=BRIEF)
    assert out["failure"] == "server_rejected" and out["error"] == "contact_required"
    assert len(bench.acts) == 2


def test_every_published_form_is_its_own_tool_mapped_to_its_own_door(tmp_path):
    bench = FakeBench()
    owed = [{"id": "r1", "text": "Which day?"}, {"id": "r2", "text": "Unsubscribe"}]
    actions = ACTIONS + [
        _form("dismiss_reply", {"type": "object", "properties": {"reason": {"type": "string"}},
                                "required": ["reason"]},
              "/api/bench/deals/d1/steps/s-1/replies/r2/dismiss"),
    ]
    payload = _payload(owed_replies=owed, submission={"actions": actions})
    model = _cli(tmp_path, _call("dismiss_reply_2", {"reason": "An unsubscribe request."}))
    out = StepAsk(model, bench).run(OBLIGATION, payload, brief=BRIEF)
    assert out["ok"] and out["call"] == "dismiss_reply_2"
    assert bench.dismissals == [("r2", {"reason": "An unsubscribe request."})]
    assert '"name": "dismiss_reply"' in model.prompts[0]


def test_a_bench_that_publishes_no_forms_takes_the_old_road_without_a_model_call(tmp_path):
    model = _cli(tmp_path)
    out = StepAsk(model, FakeBench()).run(OBLIGATION, _payload(submission=None), brief=BRIEF)
    assert out["road"] == "agentic" and out["model_calls"] == 0 and model.prompts == []
