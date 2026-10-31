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

from types import SimpleNamespace

from toll_harness import cli
from toll_harness.core.types import Checkpoint, ModelUsage, RunResult, RunStatus


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


# ---------------------------------------------------------------------------
# 2. The dispatch: no model run, one log line, the next obligation gets the cycle
# ---------------------------------------------------------------------------
def _completed_run(goal, mode):
    return RunResult(
        run_id="run-x", status=RunStatus.COMPLETED, result={"summary": "Handled."},
        checkpoint=Checkpoint(
            run_id="run-x",
            goal=goal,
            data={},
            event_cursor=0,
            revision=0,
            updated_at="2026-09-09T00:00:00Z",
        ),
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


def test_the_plan_request_behind_a_step_that_is_not_the_agents_turn_gets_the_cycle():
    """0.74.0: whose move a step is, is the bench's `turn`. A step whose turn
    is the person's starts no model run, and the plan request behind it gets
    the cycle."""
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    payload = _payload(turn={"whose_turn": "human", "why": "The person holds the Allow"})
    plan = {"kind": "file_informed_plan", "proposal_id": "p-free", "target_id": "t-1"}
    resources = _resources([DEAL_STEP, plan], payload, observed)
    answers = iter([{"attention": [DEAL_STEP, plan]}, {"attention": [DEAL_STEP]}])
    resources.toll_bench.attention = lambda wait: next(answers)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert result["platform_steps"] == 1
    assert '"p-free"' in observed["goal"]
    assert '"s-1"' not in observed["goal"]
