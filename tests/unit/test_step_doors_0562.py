"""THE STEP DOORS (0.56.2): a form the bench publishes reaches the model.

WHAT FORCED THIS FILE (lab agent Rick, 2026-09-25 08:08 UTC, a loop step:
"send each person you pick a follow-up from your Gmail"). The bench published
the propose_act form for the open loop item, `repeat_item` const in its
schema. The onboarding tool list never carried toll_bench.propose_act, so
`cli._permitted_step_state` dropped the form, the file_act ask offered one
tool (chat), the model wrote "the act-filing tool was not offered" three
times, and the loop guard parked the step.

And the drop (lab agent Ali, want 97502496): the plan door's drop counts from
ONE; the harness sent the 0-based index, so a drop of step 1 read "no step 0
to drop" five times and every other drop took out the step before it.

The payload below is the live GET's shape with the person's names and ids
replaced.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from tests.unit.plan_door import Door
from tests.unit.test_step_ask import ACTIONS, FakeBench, OBLIGATION, _calls, _model, _payload
from tests.unit.test_whole_step_fix_r238 import _model as _draft_model
from toll_harness import cli
from toll_harness.config import STEP_DOOR_TOOLS, with_step_doors
from toll_harness.loop_guard import LoopGuard
from toll_harness.onboarding import TOLL_BENCH_TOOLS
from toll_harness.toll_bench.draft import DraftLoop
from toll_harness.toll_bench.step import FORMS_TURNED_OFF, StepAsk, step_tools

ITEM = "11111111-2222-4333-8444-555555555555"

# The loop step's act form, as the live bench published it (trimmed of prose).
LOOP_ACT_FORM = {
    "action": "propose_act",
    "endpoint": "/api/bench/deals/d1/steps/s-1/acts",
    "method": "POST",
    "item_label": "Test Person",
    "repeat_item": ITEM,
    "result": "Files the act for Test Person, one of the open items, for approval.",
    "template": {"kind": "email", "contact_ref": ITEM, "repeat_item": ITEM,
                 "subject": "", "body_text": ""},
    "schema": {
        "type": "object",
        "properties": {
            "kind": {"const": "email"},
            "body_text": {"type": "string", "minLength": 1, "maxLength": 4000},
            "contact_ref": {"type": "string"},
            "subject": {"type": "string"},
            "in_reply_to": {"type": "string"},
            "repeat_item": {"const": ITEM},
        },
        "required": ["kind", "body_text", "repeat_item"],
        "if": {"required": ["in_reply_to"]},
        "else": {"required": ["subject"],
                 "anyOf": [{"required": ["contact_ref"]}, {"required": ["to"]}]},
    },
}
LOOP_ACTIONS = [a for a in ACTIONS if a["action"] in ("post_step_message", "post_work_pulse")]
LOOP_ACTIONS.append(LOOP_ACT_FORM)


def _loop_payload():
    return _payload(
        declared_acts=[{
            "kind": "email", "filed": 0, "in_loop": True, "item_status": "open",
            "repeat_item": ITEM, "item_label": "Test Person",
        }],
        submission={
            "recommended_action": "propose_act",
            "prerequisites": [{"code": "repeats_not_ended"}],
            "actions": LOOP_ACTIONS,
        },
    )


# The list onboarding wrote before 0.56.2 (Rick's, Ali's, every lab agent's).
OLD_ONBOARDED = [
    "state.load", "state.save", "result.complete", "result.fail",
    "toll_bench.protocol", "toll_bench.guide", "toll_bench.proposal_schema",
    "toll_bench.capability_taxonomy", "toll_bench.status",
    "toll_bench.ensure_reachable", "toll_bench.attention", "toll_bench.events",
    "toll_bench.list_targets", "toll_bench.read_brief", "toll_bench.list_proposals",
    "toll_bench.validate_proposal", "toll_bench.submit_proposal",
    "toll_bench.withdraw_proposal", "toll_bench.read_finalist_answers",
    "toll_bench.submit_informed_plan", "toll_bench.current_step",
    "toll_bench.reply_step_message", "toll_bench.post_check_in",
    "toll_bench.file_outcome", "toll_bench.deliver_file",
    "toll_bench.deliver_hosted_file",
]


def _resources(tools):
    return SimpleNamespace(runtime=SimpleNamespace(enabled_tools=tools))


# ---------------------------------------------------------------------------
# 1. The loop step's act form becomes a tool, repeat_item const and all
# ---------------------------------------------------------------------------
def test_the_loop_act_form_reaches_the_model_as_a_tool():
    move = {"move": "file_act", "calls": ["propose_act", "reply_step_message"]}
    permitted = cli._permitted_step_state(
        _resources(with_step_doors(OLD_ONBOARDED)), _loop_payload())
    tools = step_tools(move, permitted)
    assert list(tools) == ["propose_act", "reply_step_message"]
    assert tools["propose_act"]["schema"]["properties"]["repeat_item"] == {"const": ITEM}


def test_the_old_list_drops_the_form_and_says_which_tool_is_off():
    permitted = cli._permitted_step_state(_resources(OLD_ONBOARDED), _loop_payload())
    actions = [a["action"] for a in permitted["submission"]["actions"]]
    assert "propose_act" not in actions
    assert permitted["submission"][FORMS_TURNED_OFF] == [
        {"action": "propose_act", "tools": ["toll_bench.propose_act"]}]


# ---------------------------------------------------------------------------
# 2. Onboarding and loading carry the step doors
# ---------------------------------------------------------------------------
def test_onboarding_writes_the_step_doors():
    for tool in STEP_DOOR_TOOLS:
        assert tool in TOLL_BENCH_TOOLS


def test_an_onboarded_list_gets_the_step_doors_and_a_hand_cut_one_does_not():
    upgraded = with_step_doors(OLD_ONBOARDED)
    assert upgraded[: len(OLD_ONBOARDED)] == OLD_ONBOARDED
    assert all(tool in upgraded for tool in STEP_DOOR_TOOLS)
    hand_cut = ["state.load", "toll_bench.current_step", "toll_bench.file_outcome"]
    assert with_step_doors(hand_cut) == hand_cut
    assert with_step_doors(None) is None


def test_doctor_names_the_missing_step_tools():
    cut = {"runtime": {"tools": ["toll_bench.current_step"]}}
    check = cli.step_form_tools_check(cut)
    assert check["ok"] is False
    assert "toll_bench.propose_act" in check["fix"]
    onboarded = cli.step_form_tools_check({"runtime": {"tools": OLD_ONBOARDED}})
    assert onboarded["ok"] is True and "toll_bench.propose_act" in onboarded["missing_in_file"]
    whole = cli.step_form_tools_check(
        {"runtime": {"tools": OLD_ONBOARDED + STEP_DOOR_TOOLS}})
    assert whole == {"ok": True, "missing_in_file": []}


# ---------------------------------------------------------------------------
# 3. The move's own door missing: a warning, and no model call
# ---------------------------------------------------------------------------
def test_a_missing_act_door_is_named_and_the_model_is_not_asked(caplog):
    bench = FakeBench()
    model = _model({"call": "reply_step_message", "reply": "the tool was not offered"})
    permitted = cli._permitted_step_state(_resources(OLD_ONBOARDED), _loop_payload())
    with caplog.at_level(logging.WARNING):
        out = StepAsk(model, bench).run(OBLIGATION, permitted)
    assert out["ok"] is True and out["call"] is None
    assert out["missing_form"] == "propose_act"
    assert out["model_calls"] == 0 and model.invocations == []
    assert bench.replies == [] and bench.acts == []
    assert "add toll_bench.propose_act to runtime.tools" in caplog.text


def test_with_the_door_the_act_is_filed_for_the_loop_item():
    bench = FakeBench()
    model = _model({"call": "propose_act", "kind": "email", "contact_ref": ITEM,
                    "repeat_item": ITEM, "subject": "Good to meet you",
                    "body_text": "Hi, following up."})
    permitted = cli._permitted_step_state(
        _resources(with_step_doors(OLD_ONBOARDED)), _loop_payload())
    out = StepAsk(model, bench).run(OBLIGATION, permitted)
    assert out["ok"] is True and out["call"] == "propose_act"
    assert bench.acts and bench.acts[0][2]["repeat_item"] == ITEM


# ---------------------------------------------------------------------------
# 4. A paused plan door is not a loop-guard attempt
# ---------------------------------------------------------------------------
def test_release_hands_back_one_reserved_attempt(tmp_path):
    guard = LoopGuard(tmp_path / "g.sqlite3")
    for _ in range(3):
        assert guard.reserve("k", "s")
    assert guard.held("k", "s")
    guard.release("k", "s")
    assert not guard.held("k", "s")
    guard.release("never", "seen")  # no row: nothing happens


# ---------------------------------------------------------------------------
# 5. The drop counts from ONE
# ---------------------------------------------------------------------------
def test_a_drop_of_step_one_sends_step_one():
    # form.steps.0 is step 1; the door refused {"step": 0} as "no step 0".
    door = Door({"ok": True, "ready": True})
    loop = DraftLoop(_draft_model(), door)
    loop._drop_step("t-1", "plan", 0, "form.steps.0", "who_reaches_nobody")
    assert door.drops == [1]
    assert loop._sent == [("form.steps.0", {"drop": {"step": 1}})]


def test_the_api_client_sends_the_number_it_is_given():
    from toll_harness.email.book_of_houses import BookOfHousesApiClient

    sent = {}

    class Api(BookOfHousesApiClient):
        def __init__(self):
            pass

        def patch_proposal_draft(self, target_id, patches, kind="plan", document=None):
            sent["document"] = document
            return {"ok": True}

    Api().drop_proposal_draft_step("t-1", 1)
    assert sent["document"] == {"drop": {"step": 1}}
