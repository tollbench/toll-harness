"""THE STEP IS THE BENCH'S FORMS (0.74.0, harness thinness audit item 9).

WHAT FORCED IT: lane 3 of the 2026-10-27 heat-log readout. The bench publishes
sixteen step forms and the step ask mapped seven (no send_out, extend, fail,
skip_repeat_item, end_wait ...); a declared act of any kind but email or
calendar_event got no "file this act" move; and the harness decided whose move
a step was off its own reading of acts and states while the bench said it on
`turn`. Now: the turn is read, every form is a tool posted to its own door,
and the tail is the bench's payload whole.
"""
from __future__ import annotations

import json
from pathlib import Path

from toll_harness.core.types import ModelMessage, ModelResponse, ModelUsage, ToolCall
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import (
    NEED_TOOLS,
    REPORT_PARKED,
    ROAD_AGENTIC,
    StepAsk,
    not_the_agents_move,
    step_tail,
    step_tools,
    whose_turn,
)

TEXT = {"type": "string", "minLength": 1}


def _obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


def _form(action, schema, endpoint="/api/bench/deals/d1/steps/s1/x"):
    return {"action": action, "endpoint": endpoint, "method": "POST", "schema": schema,
            "template": {}, "result": f"the {action} door"}


MESSAGE = _form("post_step_message", _obj({"reply": TEXT}, ("reply",)))
OUTCOME = _form("submit_step_outcome", _obj({"note": TEXT, "text": TEXT}, ("note",)))
SKIP = _form("skip_repeat_item", _obj({"item": TEXT, "why": TEXT}, ("item", "why")),
             "/api/bench/deals/d1/steps/s1/repeat/skip")


def _payload(*forms, turn="agent", **over):
    base = {
        "ok": True,
        "deal": {"id": "d1", "target_goal_id": "t1"},
        "current_step": {"id": "s1", "number": 3, "state": "agent_working"},
        "step_thread": {"messages": [], "unread_from_person": 0, "unanswered_elsewhere": []},
        "turn": {"whose_turn": turn, "why": f"{turn} holds it", "next": "Hand in the work."},
        "submission": {"instruction": "Hand in the cards.",
                       "recommended_action": "submit_step_outcome",
                       "completion_recorded_on_outcome": True,
                       "actions": list(forms) or [MESSAGE, OUTCOME]},
        "earlier_steps": [{"number": 1, "hand_back": {"text": "name: Ada"}}],
        "rewound": None,
        "sent_out": None,
    }
    base.update(over)
    return base


def _says(*calls, text=""):
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text,
        tool_calls=[ToolCall(id=f"tc-{i}", name=name, arguments=args)
                    for i, (name, args) in enumerate(calls)],
        usage=ModelUsage(input_tokens=100, output_tokens=10, total_tokens=110))


class Bench:
    def __init__(self, refuse_first=False):
        self.calls = []
        self.refuse_first = refuse_first

    def reply_step_message(self, deal_id, step_id, reply, key, answering=None):
        self.calls.append(("reply", reply, answering))
        return {"ok": True}

    def file_outcome(self, target_id, outcome, key):
        if self.refuse_first:
            self.refuse_first = False
            return {"ok": False, "error": "stand_in", "fix": "Write the real name."}
        self.calls.append(("outcome", outcome))
        return {"ok": True}

    def submit_step_form(self, form, body, key, *, deal_id="", step_id=""):
        self.calls.append(("form", form["action"], body, deal_id, step_id))
        return {"ok": True}

    def report_worker_status(self, deal_id, step_id, state, number, reason=None):
        self.calls.append(("parked", reason))
        return {"ok": True}

    def file_evidence(self, deal_id, step_id, **kwargs):
        self.calls.append(("evidence", kwargs))
        return {"ok": False, "error": "invalid_evidence"}

    def deliver_file(self, deal_id, *, filename, content, title, step_ref=None):
        self.calls.append(("file", filename, content, title, step_ref))
        return {"ok": True, "receipt_id": "r1"}


def _run(payload, *responses, bench=None, workdir=None, refused=None):
    model = ScriptedModelAdapter(list(responses))
    if workdir is not None:
        model.workdir = workdir
    bench = bench or Bench()
    out = StepAsk(model, bench).run({"kind": "deal_step"}, payload, refused=refused)
    return out, bench, model


def _asked(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


# ---------------------------------------------------------------------------
# 1. Whose turn
# ---------------------------------------------------------------------------
def test_the_turn_is_read_off_the_bench():
    assert whose_turn(_payload()) == "agent"
    assert whose_turn(_payload(turn="human")) == "human"
    assert whose_turn({"turn": {"party": "you"}}) == "human"
    assert whose_turn({"turn": {"whose_turn": None, "party": None}}) is None
    # Only a bench that sends no turn falls back to the step's state.
    assert whose_turn({"current_step": {"state": "waiting_on_you"}}) == "human"
    assert whose_turn({"current_step": {"state": "agent_working"}}) == "agent"


def test_a_turn_that_is_not_the_agents_starts_no_model_call():
    for turn in ("human", "outside"):
        assert not_the_agents_move(_payload(turn=turn)).startswith(f"turn {turn}")
        out, bench, model = _run(_payload(turn=turn))
        assert out["model_calls"] == 0 and model.invocations == [] and bench.calls == []


def test_the_persons_words_are_always_the_agents_to_answer():
    spoke = _payload(turn="human", step_thread={
        "messages": [{"id": "m1", "who": "person", "text": "Use the blue one"}],
        "unread_from_person": 1, "unanswered_messages": [{"id": "m1"}],
        "unanswered_elsewhere": []})
    assert not_the_agents_move(spoke) is None
    out, bench, _model = _run(spoke, _says(("post_step_message", {"reply": "Blue it is."})))
    assert out["ok"] is True
    assert bench.calls == [("reply", "Blue it is.", "m1")]


# ---------------------------------------------------------------------------
# 2. One tool per form, generated at run time
# ---------------------------------------------------------------------------
def test_every_form_is_a_tool_by_the_benchs_own_name():
    second = _form("post_step_message", _obj({"reply": TEXT}, ("reply",)))
    payload = _payload(MESSAGE, second, OUTCOME, SKIP)
    payload["submission"]["worker_status"] = {"action": "report_worker_status",
                                              "schema": _obj({"round": {"type": "integer"}})}
    payload["sent_out"] = {"out": False, "moves": [
        {"move": "extend", "call": "POST /api/bench/deals/d1/steps/s1/send-out/extend",
         "body": {"minutes": 30}}]}
    tools = step_tools(payload)
    assert list(tools) == ["post_step_message", "post_step_message_2", "submit_step_outcome",
                           "skip_repeat_item", REPORT_PARKED, "sent_out_extend", NEED_TOOLS]
    assert tools["skip_repeat_item"]["schema"] == SKIP["schema"]


def test_a_form_with_no_plumbing_posts_to_its_own_door():
    out, bench, _model = _run(
        _payload(MESSAGE, SKIP),
        _says(("skip_repeat_item", {"item": "i-4", "why": "That shop closed."})))
    assert out["ok"] is True and out["call"] == "skip_repeat_item"
    assert bench.calls == [("form", "skip_repeat_item",
                            {"item": "i-4", "why": "That shop closed."}, "d1", "s1")]


def test_a_send_out_move_carries_its_body_template():
    payload = _payload(MESSAGE, sent_out={"state": "late", "moves": [
        {"move": "extend", "call": "POST /api/bench/deals/d1/steps/s1/send-out/extend",
         "body": {"minutes": 30}}]})
    out, bench, _model = _run(payload, _says(("sent_out_extend", {})))
    assert out["ok"] is True
    assert bench.calls[0][:3] == ("form", "sent_out_extend", {"minutes": 30})


def test_the_provider_posts_a_form_only_to_a_bench_path(monkeypatch):
    sent = []

    class Api:
        def post_form(self, method, path, body, key):
            sent.append((method, path, body))
            return {"ok": True}

    provider = BookOfHousesTollBenchProvider(Api())
    form = {"action": "fail_step", "endpoint": "/api/bench/deals/{deal_id}/steps/{step_id}/fail",
            "method": "POST"}
    assert provider.submit_step_form(form, {"why": "x"}, "k", deal_id="d1", step_id="s1")["ok"]
    assert sent == [("POST", "/api/bench/deals/d1/steps/s1/fail", {"why": "x"})]
    away = provider.submit_step_form({"endpoint": "https://elsewhere.example/x"}, {}, "k")
    assert away["ok"] is False and away["error"] == "form_has_no_bench_endpoint"


# ---------------------------------------------------------------------------
# 3. The tail is the bench's payload, whole
# ---------------------------------------------------------------------------
def test_the_tail_is_the_payload_whole_minus_the_forms():
    payload = _payload(rewound={"note": "Go back to the cafe list", "message_id": "m9"})
    tail = step_tail(payload)
    assert tail["turn"] == payload["turn"]
    assert tail["earlier_steps"] == payload["earlier_steps"]
    assert tail["rewound"]["note"] == "Go back to the cafe list"
    assert tail["submission"]["instruction"] == "Hand in the cards."
    assert tail["submission"]["recommended_action"] == "submit_step_outcome"
    assert "actions" not in tail["submission"]
    assert tail["submission"]["forms_as_tools"] == ["post_step_message", "submit_step_outcome"]
    # An unseen key the bench adds tomorrow reaches the model too.
    assert step_tail(dict(payload, a_key_from_next_week=[1, 2]))["a_key_from_next_week"] == [1, 2]


def test_one_ask_carries_the_turn_and_the_instruction():
    out, _bench, model = _run(
        _payload(), _says(("submit_step_outcome", {"note": "Done", "text": "Ada"})))
    assert out["ok"] is True and out["call"] == "submit_step_outcome"
    asked = _asked(model)
    assert '"whose_turn":"agent"' in asked and "Hand in the cards." in asked


# ---------------------------------------------------------------------------
# 4. A refusal rides the one more ask; need_tools takes the old road
# ---------------------------------------------------------------------------
def test_a_refusal_rides_the_next_ask_in_the_benchs_words():
    out, bench, model = _run(
        _payload(), _says(("submit_step_outcome", {"note": "Done", "text": "TBD"})),
        _says(("submit_step_outcome", {"note": "Done", "text": "Ada"})),
        bench=Bench(refuse_first=True))
    assert out["ok"] is True and len(model.invocations) == 2
    second = _asked(model, 1)
    assert "the_bench_refused" in second and "Write the real name." in second


def test_need_tools_hands_the_step_to_the_old_road():
    out, bench, _model = _run(_payload(), _says((NEED_TOOLS, {"why": "a browser"})))
    assert out["road"] == ROAD_AGENTIC and bench.calls == []


def test_a_bench_with_no_forms_takes_the_old_road_without_a_call():
    payload = _payload()
    payload["submission"]["actions"] = []
    out, _bench, model = _run(payload)
    assert out["road"] == ROAD_AGENTIC and model.invocations == []


# ---------------------------------------------------------------------------
# 5. The outside step never ends in silence; a made file is handed back
# ---------------------------------------------------------------------------
def test_an_outside_step_that_lands_nothing_is_parked_out_loud():
    evidence = _form("file_outside_evidence", _obj({"summary": TEXT}, ("summary",)))
    out, bench, _model = _run(
        _payload(MESSAGE, evidence),
        _says(("file_outside_evidence", {"summary": "did it"})),
        _says(("file_outside_evidence", {"summary": "did it again"})))
    assert out["ok"] is False and out["result"]["parked"] is True
    assert ("parked", "I cannot do this one myself and I have stopped rather than sit on it: "
            "the work has to happen on a service I have no tool for. Nothing is moving on "
            "this step until you decide what to do with it.") in bench.calls


def test_deliver_file_reads_the_models_own_workdir(tmp_path):
    (tmp_path / "flyer.pdf").write_bytes(b"%PDF-1.4 flyer")
    deliver = _form("deliver_file", _obj({}))
    out, bench, _model = _run(
        _payload(MESSAGE, deliver),
        _says(("deliver_file", {"path": "flyer.pdf", "title": "Party flyer"})),
        workdir=tmp_path)
    assert out["ok"] is True
    assert bench.calls == [("file", "flyer.pdf", b"%PDF-1.4 flyer", "Party flyer", "s1")]


def test_deliver_file_reads_nothing_outside_the_workdir(tmp_path):
    inside = tmp_path / "work"
    inside.mkdir()
    (tmp_path / "secret.txt").write_text("no")
    deliver = _form("deliver_file", _obj({}))
    out, bench, _model = _run(
        _payload(MESSAGE, deliver),
        _says(("deliver_file", {"path": "../secret.txt", "title": "x"})),
        _says(("deliver_file", {"path": str(Path(tmp_path) / "secret.txt"), "title": "x"})),
        workdir=inside)
    assert out["ok"] is False and out["result"]["error"] == "path_outside_workdir"
    assert bench.calls == []


def test_the_whole_payload_reaches_the_model_as_json():
    payload = _payload()
    out, _bench, model = _run(payload, _says(("post_step_message", {"reply": "hi"})))
    asked = _asked(model)
    body = json.loads(asked[asked.index("\n\n{") + 2:])
    assert body["earlier_steps"] == payload["earlier_steps"]
    assert out["tool_count"] == 3
