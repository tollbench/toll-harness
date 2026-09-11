"""THE STEP ASK IS SMALL (0.36.0, part two).

WHAT FORCED THESE TESTS (Steven, 2026-09-09: "It's just stepping through the
plan. why would that cost so much?"). A deal step went down the whole
agentic road: the runtime's instruction sheet, 32 tools, every tool result
glued into the conversation and re-sent on every call -- 12,675 input tokens
to open, 261,749 over twenty calls, on one fleet unit that morning, and nothing filed.
A bid by then cost ~9,000 input tokens for a whole ten-call draft loop. So a
step is asked the way a plan is written: the bid loop's cacheable prefix,
byte for byte, and a tail carrying only this step. Each test holds one line
of that.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.core.runtime import TOLL_BENCH_SYSTEM_INSTRUCTION
from toll_harness.core.types import (
    Checkpoint,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunResult,
    RunStatus,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.step import (
    NEED_TOOLS,
    ROAD_AGENTIC,
    StepAsk,
    step_tail,
    the_move,
    what_changed,
)
from toll_harness.tools.registry import build_standard_registry


# ---------------------------------------------------------------------------
# Fixtures: a step, a bench, a model that answers with JSON
# ---------------------------------------------------------------------------
def _says(text):
    return ModelResponse(message=ModelMessage.text("assistant", text), text=text, tool_calls=[],
                         usage=ModelUsage(input_tokens=1500, output_tokens=200, total_tokens=1700))


class CachingModel(ScriptedModelAdapter):
    def caches_a_stable_prefix(self) -> bool:
        return True


def _model(*answers, caching=True):
    cls = CachingModel if caching else ScriptedModelAdapter
    return cls([_says(json.dumps(a) if not isinstance(a, str) else a) for a in answers])


BRIEF = {
    "want": "I want three quiet cafes near Alberta St with their hours",
    "block_templates": {"meeting": [], "research": []},
    "tools": [
        {
            "tool": "platform.research",
            "provider": "",
            "one_line": "the platform looks something up",
        },
        {"tool": "gmail.message.send", "provider": "google-gmail", "one_line": "send an email"},
        {
            "tool": "composio:<service>/<TOOL>",
            "provider": "composio",
            "one_line": "any of 1,500 services",
        },
    ],
}


def _payload(**over):
    base = {
        "ok": True,
        "deal": {
            "id": "d1",
            "proposal_id": "p1",
            "target_goal_id": "t1",
            "status": "signed",
            "is_free": True,
        },
        "current_step": {
            "id": "s-1", "number": 2, "state": "agent_working", "ask": "APPROVE",
            "title": "Find three quiet cafes with their hours",
            "outcome_promise": "A short list: name, address, hours, why it is quiet.",
            "deliverable": {
                "channel": "text",
                "fields": ["name", "address", "hours"],
                "min_count": 3,
            },
            "file_receipts": [],
        },
        "person_sees_control": False,
        "open_ask_move": "File the outcome to open the APPROVE.",
        "latest_work_pulse": None,
        "step_thread": {
            "messages": [],
            "unread_from_person": 0,
            "unanswered_elsewhere": [],
            "post_reply": "/x",
        },
        "released_materials": [], "released_materials_count": 0,
        "access": {"grants": []},
        "acts": [], "declared_acts": [], "drafts_sent_back": [],
        "owed_replies": [], "inbound_replies": [],
        "waiting_outside": None,
    }
    base.update(over)
    return base


class FakeBench:
    """The bench doors the step ask can knock on, recording every knock."""

    def __init__(self, *, refuse_first=None):
        self.acts, self.replies, self.pulses = [], [], []
        self.outcomes, self.waits, self.dismissals = [], [], []
        self.refuse_first = refuse_first
        self.briefs = 0

    def read_brief(self, target_id):
        self.briefs += 1
        return {"ok": True, "brief": dict(BRIEF, target_id=target_id)}

    def list_act_kinds(self):
        return {"kinds": {"meeting": {"declaration": {}}}}

    def propose_act(self, deal_id, step_id, act, key):
        self.acts.append((deal_id, step_id, act, key))
        return {"ok": True, "act_id": "ap-1"}

    def dismiss_reply(self, deal_id, step_id, reply_id, dismissal, key):
        self.dismissals.append((reply_id, dismissal))
        return {"ok": True}

    def reply_step_message(self, deal_id, step_id, reply, key):
        self.replies.append((deal_id, step_id, reply, key))
        return {"ok": True, "message_id": "m-9"}

    def post_check_in(self, deal_id, pulse, key):
        self.pulses.append((deal_id, pulse, key))
        return {"ok": True, "work_pulse": {"progress_percent": pulse.get("progress_percent")}}

    def wait_outside(self, deal_id, step_id, wait, key):
        self.waits.append(wait)
        return {"ok": True}

    def file_outcome(self, target_id, outcome, key):
        self.outcomes.append((target_id, outcome, key))
        if self.refuse_first and len(self.outcomes) == 1:
            return self.refuse_first
        return {"ok": True, "outcome_id": "o-1"}


OBLIGATION = {
    "kind": "deal_step",
    "deal_id": "d1",
    "proposal_id": "p1",
    "step_id": "s-1",
    "target_id": "t1",
}

CAFES = {
    "call": "file_outcome",
    "pulse": {"changed": "Picked three cafes", "now": "Filing the list", "next": "Your review"},
    "outcome": {
        "note": "Three quiet cafes with hours. Approve to close the step.",
        "document": {"title": "Three quiet cafes", "blocks": [
            {"type": "cards", "items": [
                {"name": "Barista", "address": "1725 NE Alberta", "hours": "7-5"},
                {"name": "Proud Mary", "address": "2012 NE Alberta", "hours": "7-4"},
                {"name": "Case Study", "address": "1422 NE Alberta", "hours": "7-6"},
            ]},
        ]},
    },
}


# ---------------------------------------------------------------------------
# 1. The move ladder
# ---------------------------------------------------------------------------
def test_the_move_is_read_off_the_step_in_the_persons_order():
    assert the_move(_payload(owed_replies=[{"id": "r1"}]))["move"] == "answer_reply"
    spoke = _payload(step_thread={
            "messages": [{"id": "m1", "who": "person", "text": "hi"}],
            "unread_from_person": 1,
            "unanswered_elsewhere": [],
        })
    assert the_move(spoke)["move"] == "answer_person"
    back = _payload(acts=[{
                "act_id": "a1",
                "kind": "email",
                "state": "sent_back",
                "note": "wrong time",
            }])
    assert the_move(back)["move"] == "refile_act"
    declared = _payload(declared_acts=[{"kind": "email", "filed": 0, "held": 0, "executed": 0}])
    assert the_move(declared)["move"] == "file_act"
    assert the_move(_payload())["move"] == "hand_back"


def test_the_moves_the_ask_cannot_shape_name_the_old_road():
    bytes_step = _payload()
    bytes_step["current_step"]["deliverable"] = {
        "channel": "file",
        "family": "video",
        "types": ["mp4"],
    }
    assert the_move(bytes_step)["road"] == ROAD_AGENTIC
    outside = _payload(acts=[{"act_id": "a1", "kind": "outside", "state": "approved"}])
    assert the_move(outside)["road"] == ROAD_AGENTIC
    elsewhere = _payload(step_thread={
            "messages": [],
            "unread_from_person": 0,
            "unanswered_elsewhere": [{"step_id": "s-0", "unread_from_person": 1}],
        })
    assert "not on this payload" in the_move(elsewhere)["why"]


def test_what_changed_names_the_new_words_and_the_moved_acts():
    before = json.loads(cli._deal_step_fingerprint(_payload()))
    after = json.loads(cli._deal_step_fingerprint(_payload(
        step_thread={
            "messages": [{"id": "m1", "who": "person"}],
            "unread_from_person": 1,
            "unanswered_elsewhere": [],
        },
        acts=[{"act_id": "a1", "kind": "email", "state": "sent_back", "note": "no"}],
    )))
    lines = what_changed(before, after)
    assert any("m1" in line for line in lines)
    assert "act a1 is now sent_back" in lines
    assert what_changed(None, after) == ["first look at this step"]
    assert what_changed(before, before) == ["nothing new from the person since the last look"]


# ---------------------------------------------------------------------------
# 2. One small ask, one call: the hand-back
# ---------------------------------------------------------------------------
def test_a_hand_back_is_one_small_ask_and_one_call_with_nothing_carried():
    bench = FakeBench()
    model = _model(CAFES)
    ask = StepAsk(model, bench)

    out = ask.run(
        OBLIGATION,
        _payload(),
        brief=BRIEF,
        act_kinds=bench.list_act_kinds(),
        changed=["first look"],
    )

    assert out["ok"] is True and out["road"] == "step_ask" and out["call"] == "file_outcome"
    assert out["model_calls"] == 1
    # ONE user message, no tools, no tool result from any earlier call.
    [invocation] = model.invocations
    assert invocation["tools"] == []
    assert len(invocation["messages"]) == 1
    assert all(block.get("type") == "text" for block in invocation["messages"][0].content)
    prompt_chars = len(invocation["system"]) + sum(
        len(b["text"]) for b in invocation["messages"][0].content)
    assert prompt_chars < 8_000, prompt_chars
    # The bench got the 100% pulse and then the outcome, on this step.
    assert bench.pulses[0][1]["progress_percent"] == 100
    assert bench.outcomes[0][0] == "t1"
    assert bench.outcomes[0][1]["step_ref"] == "s-1"
    assert bench.outcomes[0][1]["document"]["blocks"][0]["type"] == "cards"


def test_the_step_prefix_is_the_bid_loops_prefix_byte_for_byte():
    bench = FakeBench()
    model = _model(CAFES)
    kinds = bench.list_act_kinds()
    StepAsk(model, bench).run(OBLIGATION, _payload(), brief=BRIEF, act_kinds=kinds)
    assert model.invocations[0]["system"] == draft.stable_prefix(BRIEF, kinds, with_tools=True)
    # ...and a provider that caches nothing gets the short door, as the bid loop does.
    plain = _model(CAFES, caching=False)
    StepAsk(plain, FakeBench()).run(OBLIGATION, _payload(), brief=BRIEF, act_kinds=kinds)
    assert plain.invocations[0]["system"] == draft.SHORT_FRONT_DOOR


def test_the_tail_carries_only_this_step():
    tail = step_tail(_payload(), OBLIGATION, the_move(_payload()), ["first look"])
    assert tail["ids"] == {"deal_id": "d1", "step_id": "s-1", "target_id": "t1"}
    assert tail["step"]["title"] == "Find three quiet cafes with their hours"
    assert tail["step"]["deliverable"]["fields"] == ["name", "address", "hours"]
    assert tail["your_move"] == "hand_back"
    assert "file_outcome" in tail["calls_you_may_make"]
    assert tail["changed_since_last_look"] == ["first look"]
    # Nothing of the deal's other steps, no brief, no plan.
    assert "steps" not in tail and "brief" not in tail and "plan" not in tail


def test_a_pulse_already_at_100_is_not_repeated():
    bench = FakeBench()
    payload = _payload(latest_work_pulse={
            "progress_percent": 100,
            "next_due_at": "2999-01-01T00:00:00Z",
        })
    StepAsk(_model(CAFES), bench).run(OBLIGATION, payload, brief=BRIEF)
    assert bench.pulses == [] and len(bench.outcomes) == 1


def test_a_refused_call_is_asked_once_more_with_the_refusal_verbatim():
    refusal = {"ok": False, "error": "deliverable_count_short", "message": "3 promised, 2 filed"}
    bench = FakeBench(refuse_first=refusal)
    model = _model(CAFES, CAFES)
    out = StepAsk(model, bench).run(OBLIGATION, _payload(), brief=BRIEF)
    assert out["ok"] is True and out["model_calls"] == 2
    second = model.invocations[1]["messages"][0].content[0]["text"]
    assert "the_bench_refused" in second and "3 promised, 2 filed" in second
    assert len(bench.outcomes) == 2


def test_two_refusals_end_the_ask_with_the_benchs_error():
    refusal = {"ok": False, "error": "invalid_delivery_note", "message": "note too long"}

    class AlwaysRefuses(FakeBench):
        def file_outcome(self, target_id, outcome, key):
            self.outcomes.append((target_id, outcome, key))
            return refusal

    out = StepAsk(_model(CAFES, CAFES), AlwaysRefuses()).run(OBLIGATION, _payload(), brief=BRIEF)
    assert out["ok"] is False and out["error"] == "invalid_delivery_note"
    assert out["model_calls"] == 2


def test_a_call_that_is_not_the_move_is_refused_before_the_bench_sees_it():
    bench = FakeBench()
    wrong = {"call": "file_outcome", "outcome": CAFES["outcome"]}
    out = StepAsk(_model(wrong, wrong), bench).run(
        OBLIGATION,
        _payload(owed_replies=[{"id": "r1", "from": "ruby@x"}]),
        brief=BRIEF,
    )
    assert out["ok"] is False and out["error"] == "call_not_the_move"
    assert bench.outcomes == []


def test_need_tools_hands_the_step_to_the_old_road():
    out = StepAsk(
        _model({"call": NEED_TOOLS, "why": "I need to search the web"}),
        FakeBench(),
    ).run(OBLIGATION, _payload(), brief=BRIEF)
    assert out["road"] == ROAD_AGENTIC and "search the web" in out["why"]


# ---------------------------------------------------------------------------
# 3. The other moves, each one call
# ---------------------------------------------------------------------------
def test_answering_the_person_is_one_reply_call():
    bench = FakeBench()
    spoke = _payload(step_thread={
            "messages": [{"id": "m1", "who": "person", "text": "Tuesday works?"}],
            "unread_from_person": 1,
            "unanswered_elsewhere": [],
        })
    out = StepAsk(
        _model({"call": "reply_step_message", "reply": "Tuesday works, I'll book it."}),
        bench,
    ).run(OBLIGATION, spoke, brief=BRIEF)
    assert out["ok"] and out["move"] == "answer_person"
    assert bench.replies[0][:3] == ("d1", "s-1", "Tuesday works, I'll book it.")


def test_an_owed_reply_is_answered_in_its_thread():
    bench = FakeBench()
    owed = _payload(owed_replies=[{
                "id": "r1",
                "from": "ruby@studio.example",
                "text": "Which day?",
            }])
    out = StepAsk(
        _model({"call": "propose_act", "act": {"in_reply_to": "r1", "body_text": "Tuesday at 2."}}),
        bench,
    ).run(OBLIGATION, owed, brief=BRIEF)
    assert out["ok"] and out["move"] == "answer_reply"
    assert bench.acts[0][2] == {"in_reply_to": "r1", "body_text": "Tuesday at 2."}


def test_a_returned_act_is_refiled_once_changed():
    bench = FakeBench()
    back = _payload(acts=[{
                "act_id": "a1",
                "kind": "email",
                "state": "sent_back",
                "note": "make it 11-11:30",
            }])
    act = {
        "kind": "email",
        "to": "ruby@x",
        "subject": "11-11:30?",
        "body_text": "Does 11 work?",
        "purpose": "book",
    }
    out = StepAsk(
        _model({"call": "propose_act", "act": act}),
        bench,
    ).run(OBLIGATION, back, brief=BRIEF)
    assert out["ok"] and out["move"] == "refile_act"
    assert bench.acts[0][2] == act
    # The tail carried the person's own words on the dead act.
    tail = step_tail(back, OBLIGATION, the_move(back), [])
    assert tail["acts"][0]["note"] == "make it 11-11:30"


# ---------------------------------------------------------------------------
# 4. The dispatch takes the step-ask road, and says when it does not
# ---------------------------------------------------------------------------
def _clean():
    cli._IDLE_STEP_MEMO.clear()
    cli._STEP_REFUSALS.clear()
    cli._OBLIGATION_FAILURES.clear()


def _completed_run(goal, mode):
    return RunResult(run_id="run-x", status=RunStatus.COMPLETED, result={"summary": "Handled."},
                     checkpoint=Checkpoint(
                         run_id="run-x",
                         goal=goal,
                         data={},
                         event_cursor=0,
                         revision=0,
                         updated_at="2026-09-09T00:00:00Z",
                     ),
                     usage=ModelUsage(total_tokens=10), iterations=1, observed_mode=mode)


class _MailClient:
    def configure_send_context(self, **_kwargs):
        pass

    def resume_pending_send(self):
        return None


def _resources(payload, model, *, bench=None, attention=None):
    bench = bench or FakeBench()
    bench.ensure_reachable = lambda: {"ok": True}
    bench.attention = lambda wait: {"attention": attention or [OBLIGATION]}
    bench.list_proposals = lambda: {"proposals": []}
    bench.current_step = lambda deal_id: payload
    bench.platform_owned_block = lambda step_id: None
    observed = {}
    runtime = SimpleNamespace(
        model=model,
        email_provider=SimpleNamespace(client=_MailClient()),
        enabled_tools=[
            "state.load",
            "state.save",
            "result.complete",
            "result.fail",
            "toll_bench.current_step",
            "toll_bench.file_outcome",
        ],
    )

    def start(goal, mode):
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime.start = start
    return SimpleNamespace(toll_bench=bench, runtime=runtime, agent_identity=None), observed


def test_the_dispatch_files_a_hand_back_through_the_step_ask(caplog):
    _clean()
    bench = FakeBench()
    model = _model(CAFES)
    resources, observed = _resources(_payload(), model, bench=bench)

    with caplog.at_level(logging.INFO):
        result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True and result["run"] is None
    assert result["dispatch"]["kind"] == "deal_step_step_ask"
    assert result["dispatch"]["move"] == "hand_back"
    assert result["dispatch"]["call"] == "file_outcome"
    assert "goal" not in observed  # the old road never ran
    assert len(bench.outcomes) == 1
    assert cli._IDLE_STEP_MEMO["s-1"] == cli._deal_step_fingerprint(_payload())
    assert "model call 1: prompt 1500 input tokens" in caplog.text
    cli._IDLE_STEP_MEMO.clear()


def test_a_file_deliverable_takes_the_old_road_without_a_model_call(caplog):
    _clean()
    payload = _payload()
    payload["current_step"]["deliverable"] = {
        "channel": "file",
        "family": "video",
        "types": ["mp4"],
    }
    model = _model(CAFES)
    resources, observed = _resources(payload, model)

    with caplog.at_level(logging.INFO, logger="toll_harness.cli"):
        result = cli._process_market_attention(resources, wait=20)

    assert result["dispatch"]["kind"] == "deal_step"
    assert '"s-1"' in observed["goal"]
    assert model.invocations == []
    assert "hands back a file (rule 230)" in caplog.text and "the old road" in caplog.text
    cli._IDLE_STEP_MEMO.clear()


def test_need_tools_takes_the_old_road_after_one_small_call():
    _clean()
    model = _model({"call": NEED_TOOLS, "why": "need a live search"})
    resources, observed = _resources(_payload(), model)
    result = cli._process_market_attention(resources, wait=20)
    assert result["dispatch"]["kind"] == "deal_step" and '"s-1"' in observed["goal"]
    assert len(model.invocations) == 1
    cli._IDLE_STEP_MEMO.clear()


def test_three_refused_asks_on_one_state_hand_the_step_to_the_old_road():
    # THREE, not two (Steven, 2026-09-11: "two seems odd, how about 3, in case
    # of a mistake"), and the same three the old road's brake counts.
    _clean()
    refusal = {"ok": False, "error": "invalid_delivery_note", "message": "no"}

    class AlwaysRefuses(FakeBench):
        def file_outcome(self, target_id, outcome, key):
            self.outcomes.append((target_id, outcome, key))
            return refusal

    model = _model(*([CAFES] * 8))
    resources, observed = _resources(_payload(), model, bench=AlwaysRefuses())
    for _ in range(3):
        asked = cli._process_market_attention(resources, wait=20)
        assert asked["ok"] is False and asked["dispatch"]["kind"] == "deal_step_step_ask"
        cli._OBLIGATION_FAILURES.clear()
    fourth = cli._process_market_attention(resources, wait=20)
    assert fourth["dispatch"]["kind"] == "deal_step" and '"s-1"' in observed["goal"]
    _clean()


def test_the_benchs_refusal_rides_the_next_ask_and_the_next_dispatch():
    # A refusal the model never sees is a refusal it earns again from a blank
    # slate. The bench's own words ride the first ask of the next cycle, and
    # the old road's goal when the step gets there.
    _clean()
    refusal = {
        "ok": False,
        "error": "stand_in",
        "message": "card 1 reads a made-up address: a stand-in is not a value.",
    }

    class AlwaysRefuses(FakeBench):
        def file_outcome(self, target_id, outcome, key):
            self.outcomes.append((target_id, outcome, key))
            return refusal

    model = _model(*([CAFES] * 8))
    resources, observed = _resources(_payload(), model, bench=AlwaysRefuses())
    cli._process_market_attention(resources, wait=20)
    cli._OBLIGATION_FAILURES.clear()
    model.invocations.clear()
    cli._process_market_attention(resources, wait=20)
    # The FIRST ask of the second cycle already carries the bench's sentence.
    assert "the_bench_refused" in model.invocations[0]["messages"][0].content[0]["text"]
    assert "a stand-in is not a value" in model.invocations[0]["messages"][0].content[0]["text"]
    cli._OBLIGATION_FAILURES.clear()
    cli._process_market_attention(resources, wait=20)
    cli._OBLIGATION_FAILURES.clear()
    cli._process_market_attention(resources, wait=20)
    assert '"the_bench_refused"' in observed["goal"]
    assert "a stand-in is not a value" in observed["goal"]
    _clean()


def test_a_message_debt_on_another_step_takes_the_old_road():
    _clean()
    debt = {"kind": "unanswered_message", "deal_id": "d1", "step_id": "s-0", "target_id": "t1"}
    model = _model(CAFES)
    resources, observed = _resources(_payload(), model, attention=[debt])
    result = cli._process_market_attention(resources, wait=20)
    assert result["dispatch"]["kind"] == "unanswered_message"
    assert model.invocations == [] and '"s-0"' in observed["goal"]


# ---------------------------------------------------------------------------
# 5. Before and after, measured on the same step
# ---------------------------------------------------------------------------
def test_the_step_ask_is_a_fraction_of_the_old_roads_opening_prompt(capsys):
    """The old road's FIRST call, on this same step, versus the whole step ask."""
    _clean()
    payload = _payload()
    # The old road: instruction + goal JSON + the system sheet + every tool's schema.
    resources, observed = _resources(payload, None)
    cli._process_market_attention(resources, wait=20)
    tools = build_standard_registry().definitions(
        cli._OBLIGATION_DISPATCH["deal_step"]["tools"]
        & set(build_standard_registry()._tools))
    old_chars = len(observed["goal"]) + len(TOLL_BENCH_SYSTEM_INSTRUCTION) + sum(
        len(json.dumps({
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                })) for t in tools
    )
    # The step ask: prefix + tail, one call.
    model = _model(CAFES)
    StepAsk(model, FakeBench()).run(
        OBLIGATION,
        payload,
        brief=BRIEF,
        act_kinds={"kinds": {"meeting": {"declaration": {}}}},
    )
    [invocation] = model.invocations
    new_chars = len(invocation["system"]) + len(invocation["messages"][0].content[0]["text"])
    print(f"\nfile-outcome move, opening prompt: old road ~{old_chars // 4} tokens "
          f"({old_chars} chars, before any tool result) -> step ask ~{new_chars // 4} tokens "
          f"({new_chars} chars: prefix {len(invocation['system'])} cacheable + tail "
          f"{len(invocation['messages'][0].content[0]['text'])})")
    assert new_chars < 8_000
    assert new_chars * 3 < old_chars
    cli._IDLE_STEP_MEMO.clear()


# ---------------------------------------------------------------------------
# LAW A (Steven, 2026-09-09): what the person said rides the tail, never the prefix
# ---------------------------------------------------------------------------
THE_PERSON_SAID = [
    {"question": "What do you want?", "answer": "Connect two people by email"},
    {"question": "How formal should the introduction be?", "answer": "Warm and casual"},
    {"question": "May I mention that you two met at the fair?", "answer": "yes"},
    {"question": "Anything they should know before they meet?",
     "answer": "John is only free on weekday evenings."},
    {"question": "Who are the two people?", "answer": "Jane Real, John Actual",
     "people": [{"name": "Jane Real", "contact_ref": "c-1"},
                {"name": "John Actual", "contact_ref": "c-2"}],
     "finding": []},
]


def test_the_tail_carries_what_the_person_said_verbatim():
    from toll_harness.toll_bench import step as step_module
    with_it = _payload(the_person_said=THE_PERSON_SAID)
    tail = step_tail(with_it, OBLIGATION, the_move(with_it), [])
    assert tail["the_person_said"] == THE_PERSON_SAID
    # an older bench that sends nothing changes nothing
    bare = step_tail(_payload(), OBLIGATION, the_move(_payload()), [])
    assert "the_person_said" not in bare
    assert step_module.PERSON_SAID_INSTRUCTION == draft.PERSON_SAID_INSTRUCTION


def test_the_prefix_is_byte_identical_with_and_without_it_and_the_line_rides_the_tail():
    from toll_harness.toll_bench import step as step_module
    kinds = FakeBench().list_act_kinds()
    with_it = _model(CAFES)
    StepAsk(with_it, FakeBench()).run(
        OBLIGATION, _payload(the_person_said=THE_PERSON_SAID), brief=BRIEF, act_kinds=kinds)
    without = _model(CAFES)
    StepAsk(without, FakeBench()).run(OBLIGATION, _payload(), brief=BRIEF, act_kinds=kinds)
    assert with_it.invocations[0]["system"] == without.invocations[0]["system"]
    said = with_it.invocations[0]["messages"][0].content[0]["text"]
    plain = without.invocations[0]["messages"][0].content[0]["text"]
    assert step_module.PERSON_SAID_INSTRUCTION in said
    assert step_module.PERSON_SAID_INSTRUCTION not in plain
    assert '"contact_ref":"c-1"' in said and "Jane Real" in said
    assert "the_person_said" not in plain
