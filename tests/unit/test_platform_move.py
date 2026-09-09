"""THE PLATFORM'S MOVE STARTS NO MODEL RUN (0.36.0, part one).

WHAT FORCED THESE TESTS (Steven, 2026-09-09: "It's just stepping through the
plan. why would that cost so much?"). A deal step whose act was held for the
person's Allow, or whose block the platform was running, still went down the
whole agentic road every cycle to discover it had no move -- one fleet unit spent
261,749 input tokens over twenty calls on one such step that morning and
filed nothing. Each test holds one line of the rule: whose move it is, and
that the platform's move costs zero model calls.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.core.types import Checkpoint, ModelUsage, RunResult, RunStatus
from toll_harness.toll_bench.step import platform_move


# ---------------------------------------------------------------------------
# 1. platform_move: the judgement, on payloads shaped like current_step
# ---------------------------------------------------------------------------
def _payload(**over):
    base = {
        "ok": True,
        "deal": {"id": "d1", "target_goal_id": "t1", "status": "signed"},
        "current_step": {"id": "s-1", "number": 2, "state": "agent_working", "ask": "APPROVE"},
        "step_thread": {"messages": [], "unread_from_person": 0, "unanswered_elsewhere": []},
        "latest_work_pulse": {"overdue": False, "next_due_at": "2999-01-01T00:00:00Z"},
        "acts": [],
        "declared_acts": [],
        "owed_replies": [],
        "inbound_replies": [],
        "waiting_outside": None,
        "released_materials_count": 0,
        "access": {"grants": []},
    }
    base.update(over)
    return base


def test_a_block_the_platform_is_running_is_the_platforms_move():
    why = platform_move(
        _payload(acts=[{"act_id": "a1", "kind": "meeting", "state": "held"}]),
        {"kinds": {"meeting": "held"}},
    )
    assert why == "meeting block held (rule 229)"


def test_an_act_waiting_on_the_persons_allow_is_not_the_agents_move():
    why = platform_move(_payload(acts=[{"act_id": "a1", "kind": "email", "state": "held"}]))
    assert why == "email act held, waiting on the person's Allow"
    why = platform_move(_payload(acts=[{"act_id": "a1", "kind": "email", "state": "pending"}]))
    assert "waiting on the person's Allow" in why


def test_an_approved_act_the_platform_carries_out_is_not_the_agents_move():
    why = platform_move(_payload(acts=[{"act_id": "a1", "kind": "email", "state": "approved"}]))
    assert why == "email act approved, the platform is carrying it out"


def test_an_approved_outside_act_is_the_agents_to_go_and_do():
    assert platform_move(_payload(acts=[{"act_id": "a1", "kind": "outside", "state": "approved"}])) is None


def test_a_step_the_person_holds_is_the_persons_move():
    payload = _payload()
    payload["current_step"]["state"] = "waiting_on_you"
    assert platform_move(payload) == "step state waiting_on_you, the person's"


def test_the_persons_words_are_always_the_agents_move():
    held = [{"act_id": "a1", "kind": "meeting", "state": "held"}]
    spoke = _payload(acts=held, step_thread={"messages": [{"id": "m1", "who": "person"}], "unread_from_person": 1, "unanswered_elsewhere": []})
    assert platform_move(spoke, {"kinds": {"meeting": "held"}}) is None
    owed = _payload(acts=held, owed_replies=[{"id": "r1"}])
    assert platform_move(owed, {"kinds": {"meeting": "held"}}) is None
    elsewhere = _payload(acts=held, step_thread={"messages": [], "unread_from_person": 0, "unanswered_elsewhere": [{"step_id": "s-0"}]})
    assert platform_move(elsewhere, {"kinds": {"meeting": "held"}}) is None


def test_an_act_that_came_back_is_the_agents_move_again():
    for state in ("sent_back", "denied", "failed"):
        payload = _payload(acts=[
            {"act_id": "a1", "kind": "email", "state": "held"},
            {"act_id": "a2", "kind": "email", "state": state, "note": "wrong time"},
        ])
        assert platform_move(payload) is None, state


def test_a_sent_act_leaves_the_outcome_to_the_agent():
    assert platform_move(_payload(acts=[{"act_id": "a1", "kind": "email", "state": "sent"}])) is None


def test_a_standing_wait_on_the_outside_world_is_not_the_agents_move():
    waiting = _payload(waiting_outside={"on": "email_reply", "who": "Ruby at the studio", "what": "her answer"})
    assert platform_move(waiting) == "waiting outside on Ruby at the studio (rule 216)"
    landed = _payload(
        waiting_outside={"on": "email_reply", "who": "Ruby", "what": "her answer"},
        inbound_replies=[{"id": "in-1"}],
    )
    assert platform_move(landed) is None
    lapsed = _payload(waiting_outside={"on": "email_reply", "who": "Ruby", "what": "x", "until": "2000-01-01T00:00:00Z"})
    assert platform_move(lapsed) is None


def test_a_plain_working_step_is_the_agents_move():
    assert platform_move(_payload()) is None
    assert platform_move(None) is None


# ---------------------------------------------------------------------------
# 2. The dispatch: no model run, one log line, the next obligation gets the cycle
# ---------------------------------------------------------------------------
def _completed_run(goal, mode):
    return RunResult(
        run_id="run-x", status=RunStatus.COMPLETED, result={"summary": "Handled."},
        checkpoint=Checkpoint(run_id="run-x", goal=goal, data={}, event_cursor=0, revision=0, updated_at="2026-09-09T00:00:00Z"),
        usage=ModelUsage(total_tokens=10), iterations=1, observed_mode=mode,
    )


class _MailClient:
    def configure_send_context(self, **_kwargs):
        pass

    def resume_pending_send(self):
        return None


def _resources(attention_items, payload, observed, *, owned=None):
    fetched = []

    def current_step(deal_id):
        fetched.append(deal_id)
        return payload

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {"attention": attention_items},
        list_proposals=lambda: {"proposals": [{"id": "p-free", "total_ask_cents": 0}]},
        current_step=current_step,
        platform_owned_block=lambda step_id: owned,
        fetched=fetched,
    )
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=_MailClient()),
        enabled_tools=["state.load", "state.save", "result.complete", "result.fail",
                       "toll_bench.current_step", "toll_bench.file_outcome",
                       "toll_bench.read_finalist_answers", "toll_bench.submit_informed_plan"],
    )

    def start(goal, mode):
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime.start = start
    return SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)


DEAL_STEP = {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"}


def test_a_platform_run_step_starts_no_model_run_and_logs_one_line(caplog):
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    payload = _payload(acts=[{"act_id": "a1", "kind": "meeting", "state": "held"}])
    resources = _resources([DEAL_STEP], payload, observed, owned={"kinds": {"meeting": "held"}})

    with caplog.at_level(logging.INFO, logger="toll_harness.cli"):
        result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert result["run"] is None
    assert result["platform_steps"] == 1
    assert "goal" not in observed  # the model was never started
    assert resources.toll_bench.fetched == ["d1"]  # one read, the one the dispatch makes anyway
    assert "step 2: the platform's move (meeting block held (rule 229)); no model call" in caplog.text


def test_an_act_held_for_allow_starts_no_model_run_even_when_a_pulse_is_due():
    # Before 0.36.0 a due pulse re-dispatched every step, held or not. The
    # ball is the person's while an act waits on Allow; the pulse is not owed.
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    payload = _payload(
        acts=[{"act_id": "a1", "kind": "email", "state": "held"}],
        latest_work_pulse={"overdue": True, "next_due_at": "2000-01-01T00:00:00Z"},
    )
    resources = _resources([DEAL_STEP], payload, observed)

    result = cli._process_market_attention(resources, wait=20)

    assert result["run"] is None and result["platform_steps"] == 1
    assert "goal" not in observed


def test_the_plan_request_behind_a_platform_step_gets_the_cycle():
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    payload = _payload(acts=[{"act_id": "a1", "kind": "email", "state": "approved"}])
    plan = {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"}
    resources = _resources([DEAL_STEP, plan], payload, observed)
    # The plan postcondition re-reads attention: the second read shows it filed.
    answers = iter([{"attention": [DEAL_STEP, plan]}, {"attention": [DEAL_STEP]}])
    resources.toll_bench.attention = lambda wait: next(answers)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert result["platform_steps"] == 1
    assert '"p-free"' in observed["goal"]
    assert '"s-1"' not in observed["goal"]


def test_the_persons_words_on_a_platform_step_still_dispatch():
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    payload = _payload(
        acts=[{"act_id": "a1", "kind": "meeting", "state": "held"}],
        step_thread={"messages": [{"id": "m1", "who": "person", "text": "can we do Tuesday?"}],
                     "unread_from_person": 1, "unanswered_elsewhere": []},
    )
    resources = _resources([DEAL_STEP], payload, observed, owned={"kinds": {"meeting": "held"}})

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert '"s-1"' in observed["goal"]
    assert "platform_steps" not in result


def test_a_working_step_with_no_act_standing_still_dispatches_once():
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    resources = _resources([DEAL_STEP], _payload(), observed)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert '"s-1"' in observed["goal"]
    # The prefilter read is the H6 prefetch: the step was read once, not twice.
    assert resources.toll_bench.fetched == ["d1"]
    cli._IDLE_STEP_MEMO.clear()
