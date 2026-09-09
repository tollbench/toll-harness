"""THE RUN STOPS BEFORE THE PROVIDER DOES, AND EVERY TOOL HANDS BACK LESS.

WHAT FORCED THIS FILE (production fleet, 2026-09-09). Peter's run on "find a
researcher's email and reach out" reached 129,025 input tokens against a
131,072-token context and died inside Bedrock; Greg hit the same wall twelve
times that day, Marcia ten, Bobby six. Three things fed it and all three are
tested here: a brief carrying twelve worked programs, a validate door echoing
the whole submitted plan back on every one of fourteen calls in three minutes,
and a harness that never measured what a tool handed back. The fourth thing --
that nobody could read WHY a bid failed -- is the refusal log at the bottom.
"""

import copy
import json
import logging

import pytest

from toll_harness.core import budget as budget_module
from toll_harness.core.budget import (
    DEFAULT_CONTEXT_BUDGET_TOKENS,
    ContextBudget,
    resolve_context_budget,
)
from toll_harness.core.runtime import HarnessRuntime
from toll_harness.core.types import (
    ModelMessage,
    ModelResponse,
    ModelUsage,
    RunStatus,
    ToolCall,
)
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.toll_bench import programs
from toll_harness.toll_bench.book_of_houses import (
    BRIEF_CHAR_BUDGET,
    MAX_VALIDATE_ATTEMPTS,
    STEP_ACT_LIMIT,
    STEP_THREAD_MESSAGE_LIMIT,
    BookOfHousesTollBenchProvider,
)
from toll_harness.tools.registry import build_standard_registry


# ---------------------------------------------------------------------------
# 1. THE BUDGET. Read off the provider's own usage, never guessed at.
# ---------------------------------------------------------------------------
def _response(name, arguments, *, input_tokens):
    call = ToolCall("call-1", name, arguments)
    return ModelResponse(
        message=ModelMessage(
            "assistant",
            [{"type": "tool_call", "id": call.id, "name": name, "arguments": arguments}],
        ),
        text="",
        tool_calls=[call],
        usage=ModelUsage(
            input_tokens=input_tokens, output_tokens=8, total_tokens=input_tokens + 8
        ),
        stop_reason="tool_use",
    )


def _runtime(tmp_path, model, *, budget):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    return HarnessRuntime(
        model=model,
        state_store=store,
        event_store=store,
        artifact_store=FilesystemArtifactStore(tmp_path / "artifacts"),
        tools=build_standard_registry(),
        enabled_tools=["state.save", "result.complete", "result.fail"],
        context_budget_tokens=budget,
    ), store


def test_a_run_stops_itself_before_the_next_call_would_overflow(tmp_path):
    model = ScriptedModelAdapter(
        [
            _response("state.save", {"checkpoint": {"status": "working"}}, input_tokens=1200),
            _response("result.complete", {"summary": "never reached"}, input_tokens=10),
        ]
    )
    runtime, store = _runtime(tmp_path, model, budget=1000)

    result = runtime.start("Reach out to the researcher")

    assert result.status is RunStatus.FAILED
    assert result.result["error"] == "context_budget_exceeded"
    assert result.result["budget_input_tokens"] == 1000
    assert result.result["last_call_input_tokens"] == 1200
    assert result.result["model_calls"] == 1
    # The provider was never asked a second time: the run stopped, it was not
    # stopped. This is the whole point -- a 400 records nothing.
    assert len(model.invocations) == 1
    assert "run.context_budget" in [
        event.kind for event in store.list_events(result.run_id)
    ]


def test_the_refusal_names_the_step_it_was_on_and_the_last_tool(tmp_path):
    model = ScriptedModelAdapter(
        [_response("state.save", {"checkpoint": {"status": "working"}}, input_tokens=99_000)]
    )
    runtime, _ = _runtime(tmp_path, model, budget=90_000)

    result = runtime.start("Reach out to the researcher")

    assert result.result["last_tool"] == "state.save"
    assert "stopped itself" in result.result["message"]


def test_a_market_step_is_named_when_the_run_was_holding_one():
    place = {}
    HarnessRuntime._note_place(
        place,
        "toll_bench.current_step",
        {"deal_id": "d-1"},
        {"current_step": {"id": "s-9", "number": 3, "title": "Send the email"}},
    )
    assert place == {
        "last_tool": "toll_bench.current_step",
        "deal_id": "d-1",
        "step_id": "s-9",
        "step_number": 3,
        "step_title": "Send the email",
    }


def test_a_budget_of_zero_turns_the_guard_off(tmp_path):
    model = ScriptedModelAdapter(
        [
            _response("state.save", {"checkpoint": {"status": "working"}}, input_tokens=9_000_000),
            _response("result.complete", {"summary": "done"}, input_tokens=10),
        ]
    )
    runtime, _ = _runtime(tmp_path, model, budget=0)

    assert runtime.start("Reach out").status is RunStatus.COMPLETED


def test_one_log_line_per_model_call_carries_the_cumulative_input(tmp_path, caplog):
    model = ScriptedModelAdapter(
        [
            _response("state.save", {"checkpoint": {"status": "working"}}, input_tokens=400),
            _response("result.complete", {"summary": "done"}, input_tokens=700),
        ]
    )
    runtime, _ = _runtime(tmp_path, model, budget=90_000)
    with caplog.at_level(logging.INFO, logger="toll_harness.runtime"):
        runtime.start("Reach out")

    lines = [
        record.getMessage()
        for record in caplog.records
        if "model call" in record.getMessage()
    ]
    assert len(lines) == 2
    assert "prompt 400 input tokens, cumulative input 400" in lines[0]
    assert "prompt 700 input tokens, cumulative input 1100" in lines[1]
    assert "budget 90000" in lines[1]


def test_the_budget_is_the_config_then_the_environment_then_ninety_thousand(monkeypatch):
    monkeypatch.delenv(budget_module.CONTEXT_BUDGET_ENV, raising=False)
    assert resolve_context_budget(None) == DEFAULT_CONTEXT_BUDGET_TOKENS
    assert resolve_context_budget(12_000) == 12_000
    assert resolve_context_budget(0) == 0
    monkeypatch.setenv(budget_module.CONTEXT_BUDGET_ENV, "40000")
    assert resolve_context_budget(None) == 40_000
    assert resolve_context_budget(12_000) == 12_000
    # A typo in a fleet-wide lever must never stop seven agents.
    monkeypatch.setenv(budget_module.CONTEXT_BUDGET_ENV, "ninety thousand")
    assert resolve_context_budget(None) == DEFAULT_CONTEXT_BUDGET_TOKENS


def test_the_estimate_is_the_last_prompt_plus_what_was_appended_since():
    meter = ContextBudget(limit=1000)
    meter.record(ModelUsage(input_tokens=500, output_tokens=10), conversation_chars=2000)
    # Nothing appended: the next call is the same prompt.
    assert meter.estimate(2000) == 500
    # 400 characters of tool result is about 100 tokens.
    assert meter.estimate(2400) == 600
    assert meter.would_exceed(2400) is False
    assert meter.would_exceed(4404) is True


# ---------------------------------------------------------------------------
# 2. ONE PROGRAM, AND AN INDEX OF THE REST.
# ---------------------------------------------------------------------------
def _proposal(title):
    return {
        "pitch_title": "A plan",
        "pitch_body": "words " * 200,
        "steps": [{"title": title, "outcome_promise": "done", "declared_odds": 0.4}],
    }


def _programs(count=12):
    return [
        {
            "key": f"{index:02d}",
            "title": f"Program {index}",
            "wants_like": ["send an email to somebody"] if index == 4 else ["something else"],
            "proposal": _proposal(f"Step of {index}"),
        }
        for index in range(1, count + 1)
    ]


def _brief(**fields):
    brief = {
        "target_id": "t-1",
        "round": 1,
        "want": "I want an email sent to somebody who studies this",
        "required_blocks": [],
        "plan_template": [],
        "block_templates": {},
        "plan_examples": _programs(),
        "your_bid": None,
    }
    brief.update(fields)
    return brief


class BriefApi:
    def __init__(self, brief, program=None, door=None):
        self._brief = brief
        self._program = program
        self._door = door
        self.fetched = []
        self.door_calls = []

    def target_brief(self, target_id):
        return {"ok": True, "brief": copy.deepcopy(self._brief)}

    def plan_example(self, key):
        self.fetched.append(key)
        if self._program is None:
            raise RuntimeError("no such program")
        return {"ok": True, "plan_example": self._program}

    def protocol(self):
        return {"contract_version": "3.8"}

    def validate_proposal(self, target_id, payload):
        self.door_calls.append((target_id, payload))
        return self._door

    def proposal_schema(self):
        return {"type": "object"}


def test_the_brief_carries_one_program_and_an_index_of_the_rest():
    api = BriefApi(_brief())
    brief = BookOfHousesTollBenchProvider(api).read_brief("t-1")["brief"]

    index = brief["plan_examples"]
    assert len(index) == 12
    assert sorted(index[0]) == ["approx_tokens", "key", "steps", "title", "wants_like"]
    # Exactly one proposal is inline, and it is the pick.
    assert not any("proposal" in row for row in index)
    assert brief["nearest_program"]["key"] == "04"
    assert brief["nearest_program"]["proposal"]["steps"][0]["title"] == "Step of 4"
    assert "Copy its proposal WHOLE" in brief["program_to_copy"]
    assert brief["program_shelf"] == programs.SHELF_SENTENCE


def test_the_benchs_own_pick_wins_and_is_never_overwritten():
    """W15's bench (contract 3.8) chooses server-side and publishes `why` as an
    object. The local scorer must not overwrite that pick with its own -- or,
    over an index with no proposals, with None."""
    served = {
        "key": "09",
        "title": "The bench's pick",
        "why": {"score": 6, "shared_words": ["email"], "sentence": "Copy its proposal WHOLE."},
        "proposal": _proposal("The bench's step"),
    }
    api = BriefApi(_brief(nearest_program=served))
    brief = BookOfHousesTollBenchProvider(api).read_brief("t-1")["brief"]

    assert brief["nearest_program"]["key"] == "09"
    assert brief["program_to_copy"] == "Copy its proposal WHOLE."
    assert api.fetched == []


def test_an_index_only_brief_fetches_the_chosen_program_by_key():
    """The slim bench sends the index and no proposals. The pick is scored over
    the index and the winner is fetched -- one program, never twelve."""
    index = [
        {"key": "01", "title": "Buy a thing", "wants_like": ["buy me"], "steps": 3,
         "approx_tokens": 900},
        {"key": "04", "title": "Send an email", "wants_like": ["send an email to somebody"],
         "steps": 2, "approx_tokens": 800, "url": "/api/bench/plan-examples/04"},
    ]
    api = BriefApi(
        _brief(plan_examples=index, nearest_program=None),
        program={"key": "04", "title": "Send an email", "proposal": _proposal("Send it")},
    )
    brief = BookOfHousesTollBenchProvider(api).read_brief("t-1")["brief"]

    assert api.fetched == ["04"]
    assert brief["nearest_program"]["proposal"]["steps"][0]["title"] == "Send it"
    assert brief["plan_examples"][1]["url"] == "/api/bench/plan-examples/04"
    assert brief["plan_examples"][1]["steps"] == 2


def test_a_program_that_cannot_be_fetched_is_never_a_failed_read():
    api = BriefApi(
        _brief(
            plan_examples=[{"key": "04", "title": "Send an email",
                            "wants_like": ["send an email to somebody"], "steps": 2}],
            nearest_program=None,
        ),
        program=None,
    )
    brief = BookOfHousesTollBenchProvider(api).read_brief("t-1")["brief"]
    assert brief["nearest_program"]["key"] == "04"
    assert brief["nearest_program"].get("proposal") is None
    assert brief["program_to_copy"]


def test_a_brief_over_the_budget_sheds_its_own_copies_of_itself():
    fat = {"meeting": [{"title": "x" * 4000} for _ in range(16)]}
    api = BriefApi(
        _brief(
            block_templates=fat,
            bid_template={"steps": [{"title": "y" * 8000}]},
            bid_template_notes=["title: name the step"],
        )
    )
    brief = BookOfHousesTollBenchProvider(api).read_brief("t-1")["brief"]

    assert brief["bid_template"] is None
    assert brief["bid_template_note"]
    assert brief["bid_template_notes"] == ["title: name the step"]
    assert brief["block_templates"] == {"meeting": 16}
    assert brief["block_templates_note"]
    assert len(json.dumps(brief)) < BRIEF_CHAR_BUDGET


# ---------------------------------------------------------------------------
# 3. THE DOOR ANSWERS THREE TIMES. AND IT NEVER HANDS THE PLAN BACK.
# ---------------------------------------------------------------------------
DOOR_REFUSAL = {
    "ok": False,
    "problem_count": 2,
    "problems": [
        {"code": "REJ-16", "detail": "step 1 declares 35", "step_index": 1,
         "field": "declared_odds", "fix": "Give every step a declared_odds between 0 and 1."},
        {"code": "REJ-41", "detail": "run 2 quotes a run below it", "step_index": 2,
         "field": "acts", "fix": "Every argument comes from a run above it."},
    ],
    "corrected_plan": None,
    "corrected_ok": False,
    "corrections": [],
}


def _door_provider(door=DOOR_REFUSAL):
    provider = BookOfHousesTollBenchProvider(BriefApi(_brief(), door=door))
    provider._local_validation = lambda proposal: {"ok": True, "problems": []}
    return provider


def test_the_validate_door_hands_back_problems_and_never_the_plan():
    provider = _door_provider(
        {**DOOR_REFUSAL, "corrected_plan": _proposal("mechanically fixed"), "corrected_ok": True}
    )
    answer = provider.validate_proposal(_proposal("One"), "t-1")

    assert answer["ok"] is False
    assert "corrected_plan" not in answer
    assert [problem["code"] for problem in answer["problems"]] == ["REJ-16", "REJ-41"]
    assert answer["problem_count"] == 2
    assert answer["summary"] == "2 problem(s): REJ-16 (step 1), REJ-41 (step 2)"
    assert answer["corrected_ok"] is True
    assert answer["attempt"] == 1
    assert answer["attempts_left"] == MAX_VALIDATE_ATTEMPTS - 1


def test_the_door_answers_three_times_and_the_fourth_is_the_honest_refusal():
    provider = _door_provider()
    api = provider.api

    for attempt in range(1, MAX_VALIDATE_ATTEMPTS + 1):
        answer = provider.validate_proposal(_proposal("One"), "t-1")
        assert answer["attempt"] == attempt
    assert len(api.door_calls) == MAX_VALIDATE_ATTEMPTS

    fourth = provider.validate_proposal(_proposal("One"), "t-1")
    assert fourth["ok"] is False
    assert fourth["error"] == "validate_attempts_exhausted"
    assert fourth["terminal"] is True
    assert fourth["attempts_left"] == 0
    # The door's own last problems come back -- nothing invented, nothing new
    # asked of the bench.
    assert [problem["code"] for problem in fourth["problems"]] == ["REJ-16", "REJ-41"]
    assert len(api.door_calls) == MAX_VALIDATE_ATTEMPTS
    assert "file" in fourth["message"].lower()


def test_the_cap_is_per_want_and_the_filing_path_is_not_capped():
    provider = _door_provider()
    for _ in range(MAX_VALIDATE_ATTEMPTS):
        provider.validate_proposal(_proposal("One"), "t-1")
    # Another want is another bid.
    assert provider.validate_proposal(_proposal("One"), "t-2")["attempt"] == 1
    # And the dry-run path, which validates on the harness's behalf rather than
    # the model's, never spends an attempt.
    provider.validate_proposal(_proposal("One"), "t-2", enforce_cap=False)
    assert provider._validate_attempts["t-2"] == 1


def test_every_validate_attempt_is_one_line_and_every_refusal_is_verbatim(caplog):
    provider = _door_provider()
    with caplog.at_level(logging.INFO, logger="toll_harness.toll_bench"):
        provider.validate_proposal(_proposal("One"), "t-1")

    messages = [record.getMessage() for record in caplog.records]
    assert any("validate attempt 1/3 on target t-1: 2 problem(s)" in line for line in messages)
    verbatim = [line for line in messages if line.startswith("REFUSAL validate door")]
    assert len(verbatim) == 1
    assert "REJ-16" in verbatim[0] and "REJ-41" in verbatim[0]
    # Verbatim means the whole door answer, corrected plan and all: the tool
    # result is trimmed, the run log is not.
    assert json.dumps(DOOR_REFUSAL["problems"][0]["fix"])[1:-1] in verbatim[0]


# ---------------------------------------------------------------------------
# 4. THE OTHER BIG PAYLOADS.
# ---------------------------------------------------------------------------
class StepApi:
    def __init__(self, payload=None, proposals=None):
        self._payload = payload or {}
        self._proposals = proposals or []

    def current_step(self, deal_id):
        return copy.deepcopy(self._payload)

    def proposals(self):
        return copy.deepcopy(self._proposals)


def test_a_long_step_thread_hands_back_the_newest_and_says_how_many():
    messages = [{"id": str(index), "body": "hello"} for index in range(60)]
    api = StepApi(
        {
            "ok": True,
            "deal": {"id": "d-1"},
            "current_step": {"id": "s-1", "number": 1, "title": "Send it"},
            "step_thread": {"messages": messages, "unread_from_person": 0},
            "acts": [{"id": str(index)} for index in range(40)],
        }
    )
    payload = BookOfHousesTollBenchProvider(api).current_step("d-1")

    thread = payload["step_thread"]
    assert len(thread["messages"]) == STEP_THREAD_MESSAGE_LIMIT
    assert thread["messages"][-1]["id"] == "59"
    assert thread["messages_total"] == 60
    assert thread["messages_omitted"] == 40
    assert len(payload["acts"]) == STEP_ACT_LIMIT
    assert payload["acts_total"] == 40


def test_a_short_step_thread_is_untouched_and_still_says_zero():
    api = StepApi(
        {
            "ok": True,
            "deal": {"id": "d-1"},
            "current_step": {"id": "s-1"},
            "step_thread": {"messages": [{"id": "1"}], "unread_from_person": 1},
        }
    )
    thread = BookOfHousesTollBenchProvider(api).current_step("d-1")["step_thread"]
    assert thread["messages"] == [{"id": "1"}]
    assert thread["messages_total"] == 1
    assert thread["messages_omitted"] == 0


def test_a_bid_with_no_move_hands_back_its_row_and_not_its_plan():
    settled = {
        "id": "p-1",
        "target_goal_id": "t-1",
        "status": "closed",
        "total_ask_cents": 0,
        "your_move": None,
        "deal": {"deal_id": None},
        "steps": [{"title": "One"}, {"title": "Two"}],
        "pitch_body": "words " * 500,
        "finalist_answers": ["duplicate"],
        "selection_answers": ["duplicate"],
    }
    working = {
        **settled,
        "id": "p-2",
        "your_move": {"kind": "file_informed_plan"},
    }
    rows = BookOfHousesTollBenchProvider(StepApi(proposals=[settled, working])).list_proposals()
    settled_row, working_row = rows["proposals"]

    assert "steps" not in settled_row
    assert "pitch_body" not in settled_row
    assert settled_row["steps_count"] == 2
    assert settled_row["plan_omitted"]
    assert settled_row["total_ask_cents"] == 0
    # The bid with a move keeps its plan: the revision is written from it.
    assert working_row["steps"] == [{"title": "One"}, {"title": "Two"}]
    # The bench emits the answers twice under both vocabularies; one is enough.
    assert "finalist_answers" not in settled_row
    assert settled_row["selection_answers"] == ["duplicate"]


def test_a_large_tool_result_is_named_in_the_run_log(caplog):
    registry = build_standard_registry()
    with caplog.at_level(logging.WARNING, logger="toll_harness.tools"):
        from toll_harness.tools.registry import _warn_on_a_large_result

        _warn_on_a_large_result("toll_bench.read_brief", {"brief": "x" * 30_000})
    assert any("toll_bench.read_brief returned" in r.getMessage() for r in caplog.records)
    assert registry is not None


@pytest.mark.parametrize("size", [0, 100])
def test_a_small_tool_result_says_nothing(size, caplog):
    from toll_harness.tools.registry import _warn_on_a_large_result

    with caplog.at_level(logging.WARNING, logger="toll_harness.tools"):
        _warn_on_a_large_result("state.save", {"ok": True, "note": "x" * size})
    assert caplog.records == []
