"""0.63.1: the step tail carries no second copy of the bench's forms.

WHAT FORCED IT: lab agent DJ 4-Fun, 2026-10-12. Its lyrics step ask ran to
13,303 characters against the 8,000 budget; about 6,300 of them were
`submission.actions`, the same forms `step_tools` hands the model as tools.
"""
from toll_harness.toll_bench.step import step_tail, step_tools, submission_for_tail, the_move

FORM = {"action": "submit_step_outcome", "endpoint": "/api/bench/deals/d/steps/s/outcome",
        "method": "POST", "headers": {"Idempotency-Key": ""}, "result": "Files the outcome.",
        "template": {"words": ""}, "schema": {"type": "object", "required": ["words"],
                                              "properties": {"words": {"type": "string"}}}}
REPLY = {"action": "post_step_message", "endpoint": "/m", "method": "POST",
         "result": "Records your reply.", "schema": {"type": "object", "required": ["reply"],
                                                     "properties": {"reply": {"type": "string"}}}}
SUBMISSION = {"actions": [FORM, REPLY],
              "worker_status": {"action": "report_worker_status", "endpoint": "/w",
                                "schema": {"type": "object"}},
              "refresh": "/api/bench/deals/d/current-step",
              "completion_recorded_on_outcome": True, "template_rule": "Fill every slot."}


def _payload():
    return {"deal": {"id": "d", "target_goal_id": "t"},
            "current_step": {"id": "s", "number": 3, "title": "Write the lyrics",
                             "state": "agent_working"},
            "turn": {"whose_turn": "agent", "state": "agent_working"},
            "submission": SUBMISSION, "step_thread": {"messages": []}}


def test_the_tail_keeps_every_key_but_the_forms_and_names_them():
    out = submission_for_tail(SUBMISSION)
    assert "actions" not in out and "worker_status" not in out and "refresh" not in out
    assert out["actions_as_tools"] == ["submit_step_outcome", "post_step_message",
                                       "report_worker_status"]
    assert out["template_rule"] == "Fill every slot."
    assert out["completion_recorded_on_outcome"] is True
    assert SUBMISSION["actions"] == [FORM, REPLY]          # the payload is never changed


def test_the_forms_still_reach_the_model_whole_as_tools():
    payload = _payload()
    move = {"move": "hand_back", "calls": ["file_outcome", "reply_step_message"]}
    tools = step_tools(move, payload)
    assert tools["file_outcome"]["schema"] == FORM["schema"]
    assert tools["reply_step_message"]["schema"] == REPLY["schema"]
    tail = step_tail(payload, {}, move, [])
    assert '"endpoint"' not in str(tail) and "actions" not in tail["submission"]


def test_a_payload_without_actions_is_carried_as_it_was():
    payload = _payload()
    payload["submission"] = {"completion_recorded_on_outcome": True}
    tail = step_tail(payload, {}, the_move(payload, {}), [])
    assert tail["submission"] == {"completion_recorded_on_outcome": True}


def test_the_worker_status_form_still_becomes_report_parked_on_an_outside_move():
    payload = _payload()
    tools = step_tools({"move": "outside_work", "calls": ["report_parked"]}, payload)
    assert "report_parked" in tools
