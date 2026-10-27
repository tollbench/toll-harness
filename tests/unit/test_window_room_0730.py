"""THE ROOM FOLLOWS THE MODEL'S WINDOW, AND THE MENU IS SHED LAST (0.73.0).

WHAT FORCED IT: lab agent Nia (DJ 4-Fun, claude-opus-5-5 on the claude_code
rail), 2026-10-26. Steven asked her to revise a song
workflow and her log read:

    Brief for target 8d06fe06 ran to 41352 characters; shed bid_template,
    block_templates to fit the window (budget 60000)
    draft loop tail ran to 14000 characters (budget 8000) with nothing left
    to shed but the question
    draft loop tail was 21379 characters (budget 16000); dropped `example`
    ... dropped `tools_on_this_want` ... dropped `forecast_context`

So she bid and planned without the block menu, the bid template, the example,
her tools on this want and the forecast context. Every budget was a number set
against a 131,072-token window, and the block menu was the second thing shed.

Tested here:
  1. the window is a setting: agent.yaml, then the environment, then the
     models this package can name, then 131,072 -- and a plain Claude id is
     200,000, only a `[1m]` id 1,000,000;
  2. every budget grows with the window and never shrinks below today's;
  3. a large-window model gets the whole 64k brief with nothing shed;
  4. a small-window model still sheds, in the new order: copies first, then
     the example, the bid template, the tools, and the block menu LAST;
  5. the draft loop's asks: Nia's fix round and proposal ask pass whole at
     200,000, and on a small window the example and the tools go last;
  6. the 0.70.0 board preflight confirms the brief WHOLE, since that is the
     brief the draft loop then works from, and the step ask grows too.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import sys
from types import SimpleNamespace

import pytest

from tests.unit.test_draft_loop_r241 import _says
from toll_harness import cli
from toll_harness.config import build_model_adapter
from toll_harness.core import window
from toll_harness.core.budget import measure
from toll_harness.core.window import (
    CONTEXT_WINDOW_ENV,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    REFERENCE_WINDOW_TOKENS,
    known_window,
    resolve_context_window,
    room,
    window_of,
)
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.book_of_houses import (
    BRIEF_CHAR_BUDGET,
    BRIEF_LAST_TO_SHED,
    BRIEF_SHED_ORDER,
    BookOfHousesTollBenchProvider,
)
from toll_harness.toll_bench.draft import (
    FORM_CHAR_BUDGET,
    PROMPT_CHAR_BUDGET,
    DraftLoop,
)
from toll_harness.toll_bench.step import StepAsk

NIA_MODEL = "claude-opus-5-5"  # her agent.yaml model_id: a plain id, 200,000
NIA_1M = "claude-opus-5-5[1m]"
SMALL_WINDOW = 32_768


@pytest.fixture(autouse=True)
def _no_fleet_window(monkeypatch):
    monkeypatch.delenv(CONTEXT_WINDOW_ENV, raising=False)
    # A CLI test earlier in the run configures the package logger to stop at
    # its own handler; these tests read the lines through caplog.
    monkeypatch.setattr(logging.getLogger("toll_harness"), "propagate", True)


# ---------------------------------------------------------------------------
# 1. THE WINDOW IS A SETTING
# ---------------------------------------------------------------------------
def test_the_window_is_the_config_then_the_environment_then_the_table_then_131k(monkeypatch):
    assert resolve_context_window(300_000, NIA_MODEL) == 300_000
    monkeypatch.setenv(CONTEXT_WINDOW_ENV, "500000")
    assert resolve_context_window(None, NIA_MODEL) == 500_000
    assert resolve_context_window(250_000, NIA_MODEL) == 250_000
    monkeypatch.delenv(CONTEXT_WINDOW_ENV)
    assert resolve_context_window(None, NIA_MODEL) == 200_000
    assert resolve_context_window(None, NIA_1M) == 1_000_000
    # A model this package cannot name is never guessed at.
    assert resolve_context_window(None, "gpt-6-sol") == DEFAULT_CONTEXT_WINDOW_TOKENS
    assert resolve_context_window(None, None) == REFERENCE_WINDOW_TOKENS == 131_072
    # Unreadable settings fall through rather than fail a call.
    assert resolve_context_window("lots", NIA_MODEL) == 200_000
    assert resolve_context_window(0, "gpt-6-sol") == 131_072


@pytest.mark.parametrize(
    ("model_id", "tokens"),
    [
        # A PLAIN CLAUDE ID IS 200,000, whatever the model can do.
        ("claude-opus-5-5", 200_000),
        ("claude-opus-5", 200_000),
        ("claude-fable-5-1", 200_000),
        ("claude-sonnet-4-6", 200_000),
        ("us.anthropic.claude-opus-4-6-v1:0", 200_000),
        # ...and 1,000,000 only when the id itself carries the [1m] tag.
        ("claude-opus-5-5[1m]", 1_000_000),
        ("claude-fable-5-1[1m]", 1_000_000),
        ("CLAUDE-SONNET-4-6[1M]", 1_000_000),
        ("us.anthropic.claude-opus-4-6-v1:0[1m]", 1_000_000),
        ("claude-haiku-4-5", 200_000),
        ("claude-sonnet-4-5-20250929", 200_000),
        ("anthropic/claude-opus-4-5", 200_000),
        ("google/gemini-2.5-flash", 1_048_576),
        ("deepseek/deepseek-v4.1-flash", None),
        ("gpt-6-sol", None),
        ("", None),
    ],
)
def test_the_table_names_only_windows_it_can_source(model_id, tokens):
    assert known_window(model_id) == tokens


def test_a_built_adapter_carries_its_window(tmp_path):
    config = {"model": {"adapter": "claude_code", "model_id": NIA_MODEL,
                        "binary": sys.executable}}
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)
    assert adapter.context_window_tokens == 200_000
    assert window_of(adapter) == 200_000

    config["model"]["model_id"] = NIA_1M
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)
    assert adapter.context_window_tokens == 1_000_000

    config["model"]["model_id"] = NIA_MODEL
    config["model"]["context_window_tokens"] = 1_000_000
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)
    assert adapter.context_window_tokens == 1_000_000

    config["model"]["context_window_tokens"] = 400_000
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)
    assert adapter.context_window_tokens == 400_000

    for bad in (0, -5, "big", True):
        config["model"]["context_window_tokens"] = bad
        with pytest.raises(ValueError, match="context_window_tokens"):
            build_model_adapter(config, root=tmp_path, data_dir=tmp_path)


def test_an_adapter_nobody_set_a_window_on_reads_its_model_id():
    assert window_of(ScriptedModelAdapter([], model_id=NIA_MODEL)) == 200_000
    assert window_of(ScriptedModelAdapter([], model_id=NIA_1M)) == 1_000_000
    assert window_of(ScriptedModelAdapter([])) == 131_072
    assert window_of(SimpleNamespace()) == 131_072


# ---------------------------------------------------------------------------
# 2. THE ROOM GROWS WITH THE WINDOW AND NEVER SHRINKS
# ---------------------------------------------------------------------------
def test_every_budget_grows_with_the_window_and_never_below_todays():
    for base in (PROMPT_CHAR_BUDGET, FORM_CHAR_BUDGET, BRIEF_CHAR_BUDGET):
        assert room(base, None) == base
        assert room(base, SMALL_WINDOW) == base
        assert room(base, 131_072) == base
        assert room(base, 200_000) > base
    assert room(BRIEF_CHAR_BUDGET, 200_000) == 91_552
    assert room(FORM_CHAR_BUDGET, 200_000) == 24_414
    assert room(PROMPT_CHAR_BUDGET, 200_000) == 12_207
    assert room(BRIEF_CHAR_BUDGET, 1_000_000) == 457_763
    assert room(FORM_CHAR_BUDGET, 1_000_000) == 122_070
    assert room(PROMPT_CHAR_BUDGET, 1_000_000) == 61_035


# ---------------------------------------------------------------------------
# 3 + 4. THE BRIEF
# ---------------------------------------------------------------------------
def _steps(label: str, count: int, size: int) -> list[dict]:
    return [
        {"ask": "APPROVE", "title": f"{label} step {index}", "statement": label[0] * size}
        for index in range(count)
    ]


def _tools(count: int) -> list[dict]:
    rows = [
        {
            "family": "music",
            "provider": f"service{index}",
            "tool": f"service{index}.song.make",
            "required": ["prompt", "style"],
            "fields": {"prompt": "the words", "style": "the sound", "length": "seconds"},
            "shapes": {"prompt": "string up to 3000", "style": "string", "length": "int"},
            "one_line": f"Makes a song on service {index}.",
        }
        for index in range(count)
    ]
    rows.append({"family": "any", "provider": "composio", "tool": "composio:<service>/<TOOL>",
                 "one_line": "Any of ~1,500 other services."})
    return rows


def _brief(catalog_steps: int = 4) -> dict:
    """A brief the size of Nia's: about 64,000 characters once it is read."""
    return {
        "target_id": "8d06fe06",
        "want": "Revise my song workflow so the duet feels homemade",
        "want_in_own_words": "Warmer, less polished, keep the duet.",
        "the_person_said": ["Keep the duet and the steady groove."],
        "plan_template": {"steps": _steps("Plan", 3, 200)},
        "bid_template": {"pitch_title": "", "plan_template": {"steps": _steps("Bid", 10, 1150)}},
        "bid_template_notes": ["steps.N.title: name the step in your own words"],
        "block_templates": {
            kind: _steps(kind, catalog_steps, 1200)
            for kind in ("song", "meeting", "research", "email", "post")
        },
        "tools": _tools(40),
        "nearest_program": {
            "key": "04", "title": "Write and record a song",
            "why": "The nearest worked program is the song one.",
            "proposal": {"steps": _steps("Example", 5, 1000)},
        },
        "plan_examples": [
            {"key": f"{index:02d}", "title": f"Program {index}", "wants_like": ["a song"],
             "proposal": {"steps": _steps("Program", 2, 100)}}
            for index in range(12)
        ],
        "past_attempts": [{"summary": "An earlier deal ended. " * 30} for _ in range(10)],
        "prior_attempts": [{"summary": "Ten deals tried and ended."}],
    }


class _BriefApi:
    def __init__(self, brief: dict):
        self._brief = brief

    def target_brief(self, target_id):
        return {"ok": True, "brief": copy.deepcopy(self._brief)}


def _provider(brief: dict, window_tokens: int | None) -> BookOfHousesTollBenchProvider:
    provider = BookOfHousesTollBenchProvider(_BriefApi(brief))
    provider.context_window_tokens = window_tokens
    return provider


def _shed_named(caplog) -> list[str]:
    for record in caplog.records:
        found = re.search(r"; shed (.+) to fit the window", record.getMessage())
        if found:
            return found.group(1).split(", ")
    return []


def test_the_test_brief_is_nias_size_and_over_the_old_budget():
    whole = _provider(_brief(), None).read_whole_brief("8d06fe06")["brief"]
    assert 62_000 <= measure(whole) <= 66_000
    assert measure(whole) > BRIEF_CHAR_BUDGET


@pytest.mark.parametrize("window_tokens", [200_000, 1_000_000])
def test_a_large_window_gets_the_whole_64k_brief_with_nothing_shed(caplog, window_tokens):
    caplog.set_level(logging.WARNING, logger="toll_harness.toll_bench")
    whole = _provider(_brief(), None).read_whole_brief("8d06fe06")["brief"]

    read = _provider(_brief(), window_tokens).read_brief("8d06fe06")["brief"]

    assert read == whole
    assert measure(read) > 62_000
    assert read["block_templates"] == _brief()["block_templates"]
    assert read["bid_template"] == _brief()["bid_template"]
    assert read["tools"] == _brief()["tools"]
    assert read["nearest_program"]["proposal"] == _brief()["nearest_program"]["proposal"]
    assert not set(read) & {f"{name}_note" for name in BRIEF_SHED_ORDER}
    assert _shed_named(caplog) == []


@pytest.mark.parametrize(("model_id", "budget"), [(NIA_MODEL, 91_552), (NIA_1M, 457_763)])
def test_nias_own_adapter_sizes_the_brief(tmp_path, caplog, model_id, budget):
    """Her plain id is 200,000 tokens, and her ~64k brief still passes whole."""
    caplog.set_level(logging.WARNING, logger="toll_harness.toll_bench")
    adapter = build_model_adapter(
        {"model": {"adapter": "claude_code", "model_id": model_id, "binary": sys.executable}},
        root=tmp_path, data_dir=tmp_path,
    )
    provider = _provider(_brief(), adapter.context_window_tokens)
    assert provider.brief_char_budget() == budget
    read = provider.read_brief("8d06fe06")["brief"]
    assert read == provider.read_whole_brief("8d06fe06")["brief"]
    assert _shed_named(caplog) == []


def test_a_small_window_sheds_only_the_copies_when_that_is_enough(caplog):
    caplog.set_level(logging.WARNING, logger="toll_harness.toll_bench")
    read = _provider(_brief(), SMALL_WINDOW).read_brief("8d06fe06")["brief"]

    assert measure(read) <= BRIEF_CHAR_BUDGET
    shed = _shed_named(caplog)
    assert shed and not set(shed) & set(BRIEF_LAST_TO_SHED)
    # The four things a plan is built from are untouched. Before 0.73.0 this
    # brief lost its bid template first.
    assert read["block_templates"] == _brief()["block_templates"]
    assert read["bid_template"] == _brief()["bid_template"]
    assert read["tools"] == _brief()["tools"]
    assert read["nearest_program"]["proposal"] == _brief()["nearest_program"]["proposal"]


def test_a_small_window_sheds_in_the_new_order_and_the_block_menu_last(caplog):
    caplog.set_level(logging.WARNING, logger="toll_harness.toll_bench")
    big = _brief(catalog_steps=14)  # a catalog of seventy 1.2KB steps
    read = _provider(big, SMALL_WINDOW).read_brief("8d06fe06")["brief"]

    assert _shed_named(caplog) == list(BRIEF_SHED_ORDER)
    assert BRIEF_SHED_ORDER[-1] == "block_templates"
    assert BRIEF_SHED_ORDER[-len(BRIEF_LAST_TO_SHED):] == BRIEF_LAST_TO_SHED
    # Shed is not removed: the menu is still every kind and its step count,
    # and every tool is still listed by name.
    assert read["block_templates"] == {
        "song": 14, "meeting": 14, "research": 14, "email": 14, "post": 14,
    }
    assert read["block_templates_note"]
    assert [row["tool"] for row in read["tools"]] == [row["tool"] for row in big["tools"]]
    assert read["tools_note"]
    assert read["bid_template"] is None and read["bid_template_note"]
    # The example's pointer stays even when its proposal comes off, and no
    # note points at a proposal that is no longer there.
    assert read["nearest_program"]["key"] == "04"
    assert "proposal" not in read["nearest_program"]
    assert "nearest_program.proposal" not in read["bid_template_note"]
    assert "nearest_program.proposal" not in read["block_templates_note"]


def test_the_loop_reads_the_brief_whole_whatever_the_window():
    provider = _provider(_brief(catalog_steps=14), SMALL_WINDOW)
    resources = SimpleNamespace(toll_bench=provider)

    brief = cli._brief_for_the_loop(resources, "8d06fe06")

    assert brief["block_templates"] == _brief(catalog_steps=14)["block_templates"]
    assert brief["bid_template"] == _brief()["bid_template"]


class _BoardApi(_BriefApi):
    """A board with a closed newest card and an open one under it (0.70.0)."""

    def open_targets(self):
        return {"targets": [
            {"target_id": "older-open", "want": "open", "round": 1,
             "posted_at": "2026-10-25T00:00:00Z"},
            {"target_id": "newer-closed", "want": "closed", "round": 1,
             "posted_at": "2026-10-26T00:00:00Z"},
        ]}

    def target_brief(self, target_id):
        if target_id == "newer-closed":
            raise BookOfHousesApiError(404, "not_found", "not open")
        return super().target_brief(target_id)


def test_the_board_preflight_confirms_the_brief_whole_for_the_loop():
    """The 0.70.0 preflight reads the brief the draft loop then works from, so
    it reads it whole: on a small window the loop still gets the block menu and
    the bid template, and a closed card is still skipped on its 404."""
    provider = BookOfHousesTollBenchProvider(_BoardApi(_brief(catalog_steps=14)))
    provider.context_window_tokens = SMALL_WINDOW
    resources = SimpleNamespace(toll_bench=provider, agent_identity=None)
    try:
        count, summaries, _keys = cli._market_scan_candidates(resources)
    finally:
        cli._MARKET_BRIEF_OFFSET = 0

    assert count == 2
    assert [row["target_id"] for row in summaries] == ["older-open"]
    verified = summaries[0]["_verified_brief"]
    assert verified == provider.read_whole_brief("older-open")["brief"]
    assert verified["block_templates"] == _brief(catalog_steps=14)["block_templates"]
    assert verified["bid_template"] == _brief()["bid_template"]
    # The tool read of the same brief is still the fitted one.
    assert provider.read_brief("older-open")["brief"] != verified


# ---------------------------------------------------------------------------
# 5. THE DRAFT LOOP'S ASKS
# ---------------------------------------------------------------------------
def _fix_round(loop: DraftLoop, caplog) -> tuple[str, list[str]]:
    """Nia's fix round: about 21,400 characters with the example on top."""
    head = [("example", "A FINISHED PLAN FOR A WANT LIKE THIS " + "E" * 3_000)]
    payload = {
        "want": "Revise my song workflow",
        "form": {"steps": [{"title": "W" * 9_000}]},
        "red": [{"step": 1, "say": "write the lyric"}],
        "forecast_context": {"rule": "F" * 1_400},
        "what_goes_in": [{"slot": "G" * 3_400}],
        "tools_on_this_want": [{"tool": "T" * 4_000}],
    }
    caplog.clear()
    loop._ask("Fix the red rows.", payload, "fix", head=head,
              budget=FORM_CHAR_BUDGET, shed_order=loop.FIX_SHED_ORDER)
    tail = loop.model.invocations[-1]["messages"][0].content[0]["text"]
    dropped = re.findall(r"dropped `([a-z_]+)`", caplog.text)
    return tail, dropped


def test_nias_fix_round_goes_whole_at_200k(caplog):
    caplog.set_level(logging.INFO, logger="toll_harness.draft")
    loop = DraftLoop(ScriptedModelAdapter([_says("{}")], model_id=NIA_MODEL), None)

    tail, dropped = _fix_round(loop, caplog)

    assert 21_000 < len(tail) < 22_000
    assert dropped == []
    for marker in ("EEEE", "TTTT", "FFFF", "GGGG"):
        assert marker in tail
    assert "tail ran to" not in caplog.text


def test_a_small_window_fix_round_keeps_the_example_and_the_tools_last(caplog):
    caplog.set_level(logging.INFO, logger="toll_harness.draft")
    model = ScriptedModelAdapter([_says("{}")])
    model.context_window_tokens = SMALL_WINDOW
    loop = DraftLoop(model, None)

    tail, dropped = _fix_round(loop, caplog)

    assert len(tail) <= FORM_CHAR_BUDGET
    # Before 0.73.0 this round dropped the example, then the tools.
    assert dropped == ["forecast_context", "what_goes_in", "example"]
    assert "TTTT" in tail


def _proposal_ask(model_id: str, caplog) -> str:
    caplog.set_level(logging.INFO, logger="toll_harness.draft")
    loop = DraftLoop(ScriptedModelAdapter([_says("{}")], model_id=model_id), None)
    payload = {"want": "w", "tools_on_this_want": [{"tool": "T" * 14_000}]}
    loop._ask("Write the proposal.", payload, "proposal")
    tail = loop.model.invocations[0]["messages"][0].content[0]["text"]
    assert len(tail) > 14_000 and "T" * 14_000 in tail
    return caplog.text


def test_nias_proposal_ask_goes_whole_at_200k_and_says_it_ran_long(caplog):
    """At 200,000 the proposal ask's budget is 12,207: her 14k tail goes whole
    -- nothing in a proposal ask is on a shed list -- and the long-tail line is
    still written, as it was before."""
    said = _proposal_ask(NIA_MODEL, caplog)
    assert "dropped `" not in said
    assert "tail ran to" in said and "budget 12207" in said


def test_a_1m_id_proposal_ask_goes_whole_with_no_warning(caplog):
    said = _proposal_ask(NIA_1M, caplog)
    assert "tail ran to" not in said and "dropped `" not in said


def test_every_shed_order_keeps_the_example_and_the_tools_for_last():
    for order in (DraftLoop.SHED_ORDER, DraftLoop.FIX_SHED_ORDER):
        protected = [index for index, name in enumerate(order) if name in DraftLoop.LAST_TO_SHED]
        others = [index for index, name in enumerate(order) if name not in DraftLoop.LAST_TO_SHED]
        assert protected and max(others) < min(protected)
    assert DraftLoop.FIX_SHED_ORDER[-1] == "tools_on_this_want"
    assert "tools_on_this_want" not in DraftLoop.SHED_ORDER


def test_the_prefix_stays_byte_identical_across_asks_on_a_large_window():
    """The cacheable prefix is built once per run from the window; growing the
    budgets must not make it vary between asks."""
    brief = {"want": "w", "tools": _tools(200), "block_templates": {"song": []}}
    model = ScriptedModelAdapter([_says("{}")] * 3, model_id=NIA_1M)
    model.caches_a_stable_prefix = lambda: True
    loop = DraftLoop(model, None)
    loop.run("t", kind="bid", brief=brief, file=False)
    systems = {json.dumps(call["system"], default=str) for call in model.invocations}
    assert len(systems) == 1
    # And on a 1M window the whole 200-tool index rides it, not the first 4KB.
    assert "service199.song.make" in loop.prefix
    assert window.room(4_000, 131_072) == 4_000


def _step_ask(model_id: str | None, window_tokens: int | None, caplog) -> dict:
    """A step ask of about 10,000 characters: five earlier acts and a reply."""
    caplog.set_level(logging.WARNING, logger="toll_harness")
    model = ScriptedModelAdapter([_says("{}")], model_id=model_id)
    model.context_window_tokens = window_tokens
    ask = StepAsk(model, None)
    tail = {
        "step": {"title": "Record the duet"},
        "acts": [{"kind": "calls", "say": "A" * 1_500} for _ in range(5)],
        "inbound_replies": [{"from": "studio", "text": "R" * 1_500}],
    }
    ask._ask("Take the next move.", tail, "move", [])
    return json.loads(model.invocations[-1]["messages"][0].content[0]["text"].rsplit("\n\n", 1)[1])


def test_a_step_ask_of_ten_thousand_goes_whole_at_200k_and_sheds_on_a_small_window(caplog):
    whole = _step_ask(NIA_MODEL, None, caplog)
    assert len(whole["acts"]) == 5 and whole["inbound_replies"]
    assert "step ask tail ran to" not in caplog.text

    caplog.clear()
    small = _step_ask(None, SMALL_WINDOW, caplog)
    assert len(small["acts"]) == 1
