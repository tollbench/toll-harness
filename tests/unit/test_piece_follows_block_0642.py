"""THE PIECE FOLLOWS A BLOCK THAT RAN (0.64.2, bench contract 4.1.26).

WHAT FORCED IT: prod heat, 2026-10-20 02:15Z. Lab agents Opus (deal a90bd449)
and Fable (deal 3dc988f9) were picked; step 3 of each was a `calls` block
(platform.draft) on an APPROVE step that promised cards (subject, body). The
platform ran the draft at 02:27Z and the act read `executed`, but the cards
were still owed, so the bench kept the step the agent's: turn `agent`,
"Hand in the work: ...", and `submission` recommending submit_step_outcome
with "The step stays yours until it lands". The provider's rule-229 memo read
the executed block as the platform's, so every cycle logged

    step 3: the platform's move (calls block executed (rule 229)); no model call

and the draft never came. These tests hold the line both ways: a block act
that has RUN gives the step back when the bench hands the outcome to the
agent on its turn, and a STANDING block act (or an older bench with no
`submission`) is still the platform's.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.core.types import Checkpoint, ModelUsage, RunResult, RunStatus
from toll_harness.toll_bench.book_of_houses import (
    BookOfHousesTollBenchProvider,
    bench_hands_back_to_agent,
)
from toll_harness.toll_bench.step import platform_move, the_move

DRAFT_TEXT = "Write a short, heartfelt email to my friend with a wink about his extra lung joke."
CALLS_DECLARATION = {
    "kind": "calls",
    "title": "Write a heartfelt email with a wink about his extra lung joke",
    "runs": [{"name": "platform_draft", "tool": "platform.draft", "args": {"text": DRAFT_TEXT}}],
}


def _submission(turn="agent", actions=("post_step_message", "post_work_pulse",
                                       "wait_outside", "submit_step_outcome")):
    return {
        "version": "1",
        "turn": turn,
        "recommended_action": actions[-1] if actions else None,
        "instruction": (
            "Your words are filed on this step, and the cards promised for this "
            "step (subject, body) is still owed: hand it back with "
            "submit_step_outcome. The step stays yours until it lands."
        ),
        "actions": [{"action": name, "endpoint": "/x", "schema": {"type": "object"}}
                    for name in actions],
    }


def _step(act_state="executed", *, submission=None, channel="text"):
    """A current_step answer shaped like prod's for deal a90bd449 step 3."""
    payload = {
        "ok": True,
        "deal": {"id": "d-1", "target_goal_id": "t-1", "status": "signed"},
        "current_step": {
            "id": "s-3", "number": 3, "state": "agent_working", "ask": "APPROVE",
            "title": CALLS_DECLARATION["title"],
            "outcome_promise": "A subject line and a short email body ready for you to approve",
            "deliverable": {"channel": channel, "family": None, "fields": ["subject", "body"],
                            "min_count": 1, "types": []},
        },
        "turn": {"party": "agent", "whose_turn": "agent", "state": "agent_working",
                 "next": "Hand in the work: Write a heartfelt email with a wink."},
        "step_thread": {"messages": [], "unread_from_person": 0, "unanswered_elsewhere": []},
        "latest_work_pulse": {"overdue": False, "next_due_at": "2999-01-01T00:00:00Z"},
        "acts": [{"act_id": "a-1", "kind": "calls", "state": act_state}],
        "declared_acts": [{"kind": "calls", "declaration": CALLS_DECLARATION,
                           "discharged": False}],
        "owed_replies": [],
        "inbound_replies": [],
        "waiting_outside": None,
        "released_materials": [],
        "released_materials_count": 0,
        "access": {"grants": []},
    }
    if submission is not None:
        payload["submission"] = submission
    return payload


class _Api:
    """The bench, as far as these tests need it: `calls` publishes a
    declaration and a template, exactly as production's act registry does,
    so the provider counts it among the block kinds (rule 229)."""

    def __init__(self, step):
        self.step = step
        self.outcomes = []
        self.acts_filed = []

    def act_kinds(self):
        return {"kinds": {
            "calls": {"declaration": {"kind": "calls"}, "template": [{"ask": "APPROVE"}]},
            "meeting": {"declaration": {"kind": "meeting"}, "template": [{"ask": "APPROVE"}]},
        }}

    def current_step(self, deal_id):
        return self.step

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        self.acts_filed.append(payload)
        return {"ok": True, "act_id": "a-2"}

    def file_outcome(self, target_id, payload, idempotency_key):
        self.outcomes.append(payload)
        return {"ok": True}


# ---------------------------------------------------------------------------
# 1. The memo, on the provider
# ---------------------------------------------------------------------------
def test_an_executed_block_with_the_piece_owed_is_the_agents_to_hand_back():
    api = _Api(_step("executed", submission=_submission()))
    provider = BookOfHousesTollBenchProvider(api)
    payload = provider.current_step("d-1")

    assert provider.platform_owned_block("s-3") is None
    assert platform_move(payload, provider.platform_owned_block("s-3")) is None
    assert the_move(payload)["move"] == "hand_back"

    out = provider.file_outcome(
        "t-1",
        {"note": "Here is the email.", "step_ref": "s-3",
         "document": {"title": "Email", "blocks": [
             {"type": "cards", "items": [{"subject": "You", "body": "Hi"}]}]}},
        "k-1",
    )
    assert out.get("error") != "platform_owned_block"
    assert len(api.outcomes) == 1


def test_an_executed_block_on_a_file_step_hands_back_through_deliver_file():
    submission = _submission(actions=("post_step_message", "post_work_pulse",
                                      "wait_outside", "deliver_file"))
    api = _Api(_step("executed", submission=submission, channel="file"))
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d-1")
    assert provider.platform_owned_block("s-3") is None


def test_an_older_bench_with_no_submission_keeps_rule_229():
    api = _Api(_step("executed", submission=None))
    provider = BookOfHousesTollBenchProvider(api)
    payload = provider.current_step("d-1")

    assert provider.platform_owned_block("s-3") == {"kinds": {"calls": "executed"}}
    assert platform_move(payload, provider.platform_owned_block("s-3")) == (
        "calls block executed (rule 229)")
    out = provider.file_outcome("t-1", {"note": "x", "text": "y", "step_ref": "s-3"}, "k-1")
    assert out["error"] == "platform_owned_block"
    assert api.outcomes == []


def test_a_standing_block_act_is_still_the_platforms_even_if_the_outcome_is_offered():
    for state in ("pending", "held", "approved"):
        api = _Api(_step(state, submission=_submission()))
        provider = BookOfHousesTollBenchProvider(api)
        provider.current_step("d-1")
        assert provider.platform_owned_block("s-3") == {"kinds": {"calls": state}}, state
        out = provider.propose_act("d-1", "s-3", {"kind": "calls", "runs": []}, "k-1")
        assert out["error"] == "platform_owned_block", state
        assert api.acts_filed == [], state


def test_the_persons_turn_or_no_outcome_door_keeps_the_block_the_platforms():
    for submission in (
        _submission(turn="human"),
        _submission(actions=("post_step_message", "post_work_pulse")),
        {"turn": "agent", "actions": "not a list of forms"},
    ):
        api = _Api(_step("executed", submission=submission))
        provider = BookOfHousesTollBenchProvider(api)
        provider.current_step("d-1")
        assert provider.platform_owned_block("s-3") == {"kinds": {"calls": "executed"}}


def test_bench_hands_back_to_agent_reads_only_the_benchs_own_forms():
    assert bench_hands_back_to_agent({"submission": _submission()}) is True
    assert bench_hands_back_to_agent({"submission": _submission(turn="Agent")}) is True
    assert bench_hands_back_to_agent({"submission": _submission(turn="outside")}) is False
    assert bench_hands_back_to_agent({}) is False
    assert bench_hands_back_to_agent(None) is False


# ---------------------------------------------------------------------------
# 2. The dispatch: the step reaches the model, once, with no rule-229 line
# ---------------------------------------------------------------------------
class _MailClient:
    def configure_send_context(self, **_kwargs):
        pass

    def resume_pending_send(self):
        return None


def _completed_run(goal, mode):
    return RunResult(
        run_id="run-x", status=RunStatus.COMPLETED, result={"summary": "Handled."},
        checkpoint=Checkpoint(run_id="run-x", goal=goal, data={}, event_cursor=0,
                              revision=0, updated_at="2026-10-20T00:00:00Z"),
        usage=ModelUsage(total_tokens=10), iterations=1, observed_mode=mode,
    )


def _resources(provider, observed):
    deal_step = {"kind": "deal_step", "deal_id": "d-1", "proposal_id": "p-1",
                 "step_id": "s-3", "target_id": "t-1"}
    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {"attention": [deal_step]},
        list_proposals=lambda: {"proposals": [{"id": "p-1", "total_ask_cents": 0}]},
        current_step=provider.current_step,
        platform_owned_block=provider.platform_owned_block,
    )
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=_MailClient()),
        enabled_tools=["state.load", "state.save", "result.complete", "result.fail",
                       "toll_bench.current_step", "toll_bench.file_outcome"],
    )

    def start(goal, mode):
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime.start = start
    return SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)


def test_the_owed_piece_reaches_the_model_instead_of_the_rule_229_line(caplog):
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    provider = BookOfHousesTollBenchProvider(_Api(_step("executed", submission=_submission())))
    resources = _resources(provider, observed)

    with caplog.at_level(logging.INFO, logger="toll_harness.cli"):
        result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert "platform_steps" not in result
    assert '"s-3"' in observed["goal"]  # the model was asked about the step
    assert "the platform's move" not in caplog.text
    cli._IDLE_STEP_MEMO.clear()


def test_the_same_step_on_an_older_bench_still_starts_no_model_run(caplog):
    cli._IDLE_STEP_MEMO.clear()
    observed = {}
    provider = BookOfHousesTollBenchProvider(_Api(_step("executed", submission=None)))
    resources = _resources(provider, observed)

    with caplog.at_level(logging.INFO, logger="toll_harness.cli"):
        result = cli._process_market_attention(resources, wait=20)

    assert result["run"] is None and result["platform_steps"] == 1
    assert "goal" not in observed
    assert "the platform's move (calls block executed (rule 229))" in caplog.text
    cli._IDLE_STEP_MEMO.clear()
