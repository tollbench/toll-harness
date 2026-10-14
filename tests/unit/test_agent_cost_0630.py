"""WHAT THE AGENT SPENT (agent_cost hand-back slot, 0.63.0).

WHAT FORCED THESE TESTS (bench contract 4.1.20, 2026-10-11). Every public deal
carried C_usd 0.0 because the only cost the ledger knew was money that moved
between the person and the agent; what the agent SPENT on the model was nowhere.
The bench now takes an optional `agent_cost` {usd, tokens_in, tokens_out, model,
basis} on the outcome. The harness counts what each billed model call used,
keeps it per deal, and sends it with the hand-back. Absent means absent: a deal
where nothing was counted sends no field at all, never a zero.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from tests.unit.test_onboarding import _answers
from tests.unit.test_toll_bench import FakeApi
from toll_harness import cli
from toll_harness import config as config_module
from toll_harness.core.cost_ledger import CostLedger
from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.models.anthropic import AnthropicModelAdapter
from toll_harness.models.bedrock import BedrockModelAdapter
from toll_harness.models.cli_rail import (
    ClaudeCodeCliAdapter,
    CodexCliAdapter,
    ExternalAgentAdapter,
)
from toll_harness.models.openai import OpenAIModelAdapter
from toll_harness.models.pricing import PRICES, normalize_model, price_usd
from toll_harness.onboarding import create_configuration
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

MESSAGES = [ModelMessage.text("user", "Walk the deal")]
TOOLS = [ToolDefinition("state.save", "Save", {"type": "object"})]
ENVELOPE = json.dumps({"text": "ok", "tool_calls": []})
OUTCOME = {"note": "Here it is.", "text": "three cafes"}


def _slot(ledger, deal="d1", target="t1"):
    with ledger.deal(deal, target):
        return ledger.slot(target)


# ---------------------------------------------------------------------------
# 1. The price table
# ---------------------------------------------------------------------------
def test_the_named_models_are_in_the_table():
    for model in (
        "claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-4-5",
        "gpt-6.1-sol", "gpt-6-astra",
    ):
        assert price_usd(model, fresh_in=1_000_000) is not None, model


def test_a_model_spelled_the_way_a_provider_spells_it_finds_its_row():
    for spelled in (
        "claude-opus-5.5", "Claude-Opus-5-5", "us.anthropic.claude-opus-5-5-v1:0",
        "claude-opus-5-5-20261001", "claude-opus-5-5[1m]", "anthropic/claude-opus-5-5",
    ):
        assert normalize_model(spelled) == "claude-opus-5-5", spelled
    assert normalize_model("gpt-6.1-sol") in PRICES


def test_dollars_follow_the_four_rates():
    # Haiku 4.5: 1 in / 0.1 cached / 1.25 written / 5 out, per million tokens.
    dollars = price_usd(
        "claude-haiku-4-5", fresh_in=1_000_000, cache_read=1_000_000,
        cache_write=1_000_000, out=1_000_000,
    )
    assert dollars == pytest.approx(1.0 + 0.1 + 1.25 + 5.0)


def test_an_unknown_model_is_unpriced_not_guessed():
    assert price_usd("some-model-nobody-listed", fresh_in=1000, out=1000) is None


# ---------------------------------------------------------------------------
# 2. Per rail: a fake reply with usage yields the right agent_cost
# ---------------------------------------------------------------------------
class _Block:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _anthropic(usage, model="claude-sonnet-5-5"):
    response = SimpleNamespace(
        content=[_Block(type="text", text="ok")], usage=usage, stop_reason="end_turn", model=model
    )
    messages = SimpleNamespace(create=lambda **request: response)
    return AnthropicModelAdapter(
        "claude-sonnet-5-5", client=SimpleNamespace(messages=messages)
    )


def test_the_anthropic_rail_counts_cache_reads_and_writes_as_metered():
    ledger = CostLedger()
    adapter = _anthropic(
        SimpleNamespace(
            input_tokens=1000, output_tokens=200,
            cache_read_input_tokens=5000, cache_creation_input_tokens=2000,
        )
    )
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=[])
        slot = ledger.slot("t1")
    # 1000 fresh * 3 + 5000 read * 0.3 + 2000 written * 3.75 + 200 out * 15, per million
    assert slot == {
        "usd": round((3000 + 1500 + 7500 + 3000) / 1e6, 6),
        "tokens_in": 8000,
        "tokens_out": 200,
        "model": "claude-sonnet-5-5",
        "basis": "metered",
    }


def test_the_bedrock_rail_counts_as_metered():
    ledger = CostLedger()
    client = SimpleNamespace(
        converse=lambda **request: {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 400, "outputTokens": 100, "totalTokens": 500},
        }
    )
    adapter = BedrockModelAdapter("us.anthropic.claude-haiku-4-5-v1:0", client=client)
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=[])
        slot = ledger.slot("t1")
    assert slot["tokens_in"] == 400 and slot["tokens_out"] == 100
    assert slot["usd"] == round((400 * 1.0 + 100 * 5.0) / 1e6, 6)
    assert slot["basis"] == "metered"
    assert normalize_model(slot["model"]) == "claude-haiku-4-5"


def test_the_openai_rail_prices_the_cached_share_lower_and_is_metered():
    ledger = CostLedger()
    usage = SimpleNamespace(
        prompt_tokens=10_000, completion_tokens=500, total_tokens=10_500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=8000),
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
        usage=usage, model="gpt-6.1-sol",
    )
    completions = SimpleNamespace(create=lambda **request: response)
    adapter = OpenAIModelAdapter(
        "gpt-6.1-sol", client=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=[])
        slot = ledger.slot("t1")
    assert slot["tokens_in"] == 10_000 and slot["tokens_out"] == 500
    assert slot["usd"] == round((2000 * 1.25 + 8000 * 0.125 + 500 * 10.0) / 1e6, 6)
    assert slot["model"] == "gpt-6.1-sol" and slot["basis"] == "metered"


def _claude_result(usage, **extra):
    return json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "result": ENVELOPE,
         "usage": usage, **extra}
    )


def _claude_adapter(tmp_path, stdout, model="claude-opus-5-5"):
    def run(argv, *, input, capture_output, text, timeout, cwd):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    return ClaudeCodeCliAdapter(model, workdir=tmp_path, runner=run)


def test_the_claude_code_rail_is_a_subscription_estimate(tmp_path):
    ledger = CostLedger()
    stdout = _claude_result(
        {"input_tokens": 100, "output_tokens": 20,
         "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 1000},
        modelUsage={"claude-opus-5-5": {"outputTokens": 20}},
    )
    adapter = _claude_adapter(tmp_path, stdout)
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
        slot = ledger.slot("t1")
    assert slot == {
        "usd": round((100 * 5 + 4000 * 0.5 + 1000 * 6.25 + 20 * 25) / 1e6, 6),
        "tokens_in": 5100,
        "tokens_out": 20,
        "model": "claude-opus-5-5",
        "basis": "subscription_estimate",
    }


def test_the_claude_code_rail_names_the_model_the_cli_ran_when_none_was_configured(tmp_path):
    ledger = CostLedger()
    stdout = _claude_result(
        {"input_tokens": 10, "output_tokens": 5},
        modelUsage={"claude-haiku-4-5-20251001": {"outputTokens": 1},
                    "claude-sonnet-5-5": {"outputTokens": 5}},
    )
    adapter = _claude_adapter(tmp_path, stdout, model=None)
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
        slot = ledger.slot("t1")
    assert slot["model"] == "claude-sonnet-5-5"
    assert slot["usd"] == round((10 * 3 + 5 * 15) / 1e6, 6)


def _codex_adapter(tmp_path, stdout, model="gpt-6.1-sol"):
    def run(argv, *, input, capture_output, text, timeout, cwd):
        with open(argv[argv.index("--output-last-message") + 1], "w") as handle:
            handle.write(ENVELOPE)
        run.argv = argv
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    adapter = CodexCliAdapter(model, workdir=tmp_path, runner=run)
    adapter.argv = lambda: run.argv
    return adapter


def _codex_events(*turns):
    lines = [json.dumps({"type": "thread.started", "thread_id": "x"})]
    for input_tokens, cached, output in turns:
        lines.append(json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": input_tokens, "cached_input_tokens": cached,
                      "output_tokens": output},
        }))
    return "\n".join(lines)


def test_the_codex_rail_reads_token_usage_off_the_json_events(tmp_path):
    ledger = CostLedger()
    adapter = _codex_adapter(tmp_path, _codex_events((10_000, 6000, 300), (2000, 0, 100)))
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        response = adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
        slot = ledger.slot("t1")
    assert "--json" in adapter.argv()
    # Input includes the cached share: 12,000 in, 6,000 of it cached.
    assert slot == {
        "usd": round((6000 * 1.25 + 6000 * 0.125 + 400 * 10.0) / 1e6, 6),
        "tokens_in": 12_000,
        "tokens_out": 400,
        "model": "gpt-6.1-sol",
        "basis": "subscription_estimate",
    }
    # The run's own usage (which feeds the input-token budget) is what it was.
    assert response.usage.total_tokens == 0


def test_the_codex_rail_reads_an_older_builds_cumulative_total(tmp_path):
    ledger = CostLedger()
    event = json.dumps({"msg": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": 900, "cached_input_tokens": 400, "output_tokens": 50}}}})
    adapter = _codex_adapter(tmp_path, event)
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=TOOLS)
        slot = ledger.slot("t1")
    assert (slot["tokens_in"], slot["tokens_out"]) == (900, 50)


def test_codex_json_is_not_asked_for_twice(tmp_path):
    def run(argv, *, input, capture_output, text, timeout, cwd):
        with open(argv[argv.index("--output-last-message") + 1], "w") as handle:
            handle.write(ENVELOPE)
        run.argv = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    CodexCliAdapter(workdir=tmp_path, runner=run, extra_args=["--json"]).invoke(
        system="s", messages=MESSAGES, tools=TOOLS)
    assert run.argv.count("--json") == 1


@pytest.mark.parametrize("rail", ["anthropic", "bedrock", "openai", "claude", "codex", "external"])
def test_a_reply_with_no_usage_sends_no_field(rail, tmp_path):
    ledger = CostLedger()
    if rail == "anthropic":
        adapter = _anthropic(None)
    elif rail == "bedrock":
        adapter = BedrockModelAdapter("x", client=SimpleNamespace(converse=lambda **r: {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn"}))
    elif rail == "openai":
        response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
            usage=None, model="gpt-6.1-sol")
        adapter = OpenAIModelAdapter("gpt-6.1-sol", client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **r: response))))
    elif rail == "claude":
        adapter = _claude_adapter(tmp_path, _claude_result({}))
    elif rail == "codex":
        adapter = _codex_adapter(tmp_path, "")
    else:
        adapter = ExternalAgentAdapter(
            command=["agent"], workdir=tmp_path,
            runner=lambda argv, **kw: SimpleNamespace(returncode=0, stdout=ENVELOPE, stderr=""))
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=[] if rail != "claude" else TOOLS)
        assert ledger.slot("t1") is None


def test_spend_outside_a_deal_costs_no_deal_anything():
    ledger = CostLedger()
    adapter = _anthropic(SimpleNamespace(input_tokens=10, output_tokens=5))
    adapter.cost_ledger = ledger
    adapter.invoke(system="s", messages=MESSAGES, tools=[])  # no deal is current
    assert _slot(ledger) is None
    with ledger.deal("", "t1"):  # an obligation with no deal id
        adapter.invoke(system="s", messages=MESSAGES, tools=[])
    assert _slot(ledger) is None


def test_an_adapter_with_no_ledger_runs_exactly_as_before():
    adapter = _anthropic(SimpleNamespace(input_tokens=10, output_tokens=5))
    assert adapter.cost_ledger is None
    assert adapter.invoke(system="s", messages=MESSAGES, tools=[]).usage.total_tokens == 15


# ---------------------------------------------------------------------------
# 3. The tab: unknown models, mixed bases, persistence
# ---------------------------------------------------------------------------
def test_an_unknown_model_is_counted_in_tokens_and_sends_zero_dollars_never_null():
    # THE BENCH REQUIRES A NUMBER: a null `usd` is refused 422 agent_cost_slot
    # ("empty"), which would hold the hand-back. Tokens, model and basis still go.
    ledger = CostLedger()
    with ledger.deal("d1", "t1"):
        ledger.add("a-model-not-in-the-table", basis="metered", fresh_in=500, out=50)
        slot = ledger.slot("t1")
    assert slot == {"usd": 0.0, "tokens_in": 500, "tokens_out": 50,
                    "model": "a-model-not-in-the-table", "basis": "metered"}


def test_priced_calls_are_summed_beside_unpriced_ones():
    ledger = CostLedger()
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=1_000_000)
        ledger.add("unlisted", basis="metered", fresh_in=10, out=10)
        slot = ledger.slot("t1")
    assert slot["usd"] == 1.0 and slot["tokens_in"] == 1_000_010
    assert slot["model"] == "claude-haiku-4-5"  # the model that carried the tokens


def test_two_kinds_of_basis_in_one_tab_say_the_weaker_claim():
    ledger = CostLedger()
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
        ledger.add("claude-haiku-4-5", basis="subscription_estimate", fresh_in=10)
        assert ledger.slot("t1")["basis"] == "subscription_estimate"


def test_a_deal_is_billed_only_for_its_own_work():
    ledger = CostLedger()
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
    with ledger.deal("d2", "t2"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=7)
        assert ledger.slot("t2")["tokens_in"] == 7
        # a filing on another want never takes this deal's tab
        assert ledger.slot("t1") is None
    assert _slot(ledger, "d1", "t1")["tokens_in"] == 10


def test_the_tab_survives_a_restart(tmp_path):
    path = tmp_path / "agent-cost.json"
    first = CostLedger(path)
    with first.deal("d1", "t1"):
        first.add("claude-haiku-4-5", basis="metered", fresh_in=123, out=4)
    second = CostLedger(path)
    assert _slot(second)["tokens_in"] == 123
    with second.deal("d1", "t1"):
        second.paid()
    assert _slot(CostLedger(path)) is None


# ---------------------------------------------------------------------------
# 4. The hand-back carries it
# ---------------------------------------------------------------------------
def _provider(api=None):
    api = api or FakeApi()
    ledger = CostLedger()
    provider = BookOfHousesTollBenchProvider(api)
    provider.cost_ledger = ledger
    return api, ledger, provider


def test_an_outcome_carries_what_the_deal_spent_then_starts_a_new_tab():
    api, ledger, provider = _provider()
    adapter = _anthropic(SimpleNamespace(input_tokens=1000, output_tokens=100))
    adapter.cost_ledger = ledger
    with ledger.deal("d1", "t1"):
        adapter.invoke(system="s", messages=MESSAGES, tools=[])
        first = provider.file_outcome("t1", dict(OUTCOME), "k1")
        # Nothing spent since: the next filing carries no field at all.
        second = provider.file_outcome("t1", dict(OUTCOME), "k2")
    assert first["ok"] and second["ok"]
    sent = api.submissions[0][1]
    assert sent["agent_cost"] == {
        "usd": round((1000 * 3 + 100 * 15) / 1e6, 6), "tokens_in": 1000, "tokens_out": 100,
        "model": "claude-sonnet-5-5", "basis": "metered"}
    assert "agent_cost" not in api.submissions[1][1]


def test_nothing_counted_means_the_field_is_absent_not_zero():
    api, ledger, provider = _provider()
    with ledger.deal("d1", "t1"):
        provider.file_outcome("t1", dict(OUTCOME), "k1")
    assert "agent_cost" not in api.submissions[0][1]


def test_a_provider_with_no_ledger_files_exactly_what_it_always_did():
    api = FakeApi()
    BookOfHousesTollBenchProvider(api).file_outcome("t1", dict(OUTCOME), "k1")
    assert api.submissions[0][1] == OUTCOME


def test_a_model_may_not_write_its_own_cost():
    api, ledger, provider = _provider()
    refused = provider.file_outcome("t1", {**OUTCOME, "agent_cost": {"usd": 0}}, "k1")
    assert refused["error"] == "invalid_outcome_fields"
    assert refused["unexpected_fields"] == ["agent_cost"]


def test_a_filing_on_another_want_does_not_take_this_deals_tab():
    api, ledger, provider = _provider()
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
        provider.file_outcome("t-other", dict(OUTCOME), "k1")
        assert ledger.slot("t1") is not None
    assert "agent_cost" not in api.submissions[0][1]


def test_the_cost_slot_never_costs_the_person_a_filing():
    class Strict(FakeApi):
        def file_outcome(self, target_id, outcome, idempotency_key):
            self.submissions.append((target_id, outcome, idempotency_key))
            if "agent_cost" in outcome:
                raise BookOfHousesApiError(
                    422, "agent_cost_slot", "The agent_cost hole is empty.", {})
            return {"ok": True, "filed": True}

    api, ledger, provider = _provider(Strict())
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
        result = provider.file_outcome("t1", dict(OUTCOME), "k1")
        assert result == {"ok": True, "filed": True}
        # filed without it, same key; the tab waits for the next hand-back
        assert [("agent_cost" in sent[1]) for sent in api.submissions] == [True, False]
        assert {sent[2] for sent in api.submissions} == {"k1"}
        assert ledger.slot("t1")["tokens_in"] == 10


def test_a_bench_that_could_not_record_it_keeps_the_tab():
    class NotYet(FakeApi):
        def file_outcome(self, target_id, outcome, idempotency_key):
            self.submissions.append((target_id, outcome, idempotency_key))
            return {"ok": True, "agent_cost": None, "agent_cost_notes": ["not yet"]}

    api, ledger, provider = _provider(NotYet())
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
        provider.file_outcome("t1", dict(OUTCOME), "k1")
        assert ledger.slot("t1")["tokens_in"] == 10
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=5)
        provider.file_outcome("t1", dict(OUTCOME), "k2")
    assert api.submissions[1][1]["agent_cost"]["tokens_in"] == 15


def test_an_older_bench_that_ignores_the_field_still_files():
    # The echo is simply absent on a contract before 4.1.20: filed, tab cleared.
    api, ledger, provider = _provider()
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
        assert provider.file_outcome("t1", dict(OUTCOME), "k1")["ok"]
        assert ledger.slot("t1") is None


def test_a_hosted_file_hand_back_carries_it_too():
    api, ledger, provider = _provider()
    with ledger.deal("d1", "t1"):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=10)
        provider.deliver_hosted_file(
            "t1", {"note": "The file.", "file_url": "https://example.com/a.pdf"}, "k1")
    assert api.submissions[0][1]["agent_cost"]["tokens_in"] == 10


# ---------------------------------------------------------------------------
# 5. The wiring: the runtime builds one ledger, the cli scopes the work to a deal
# ---------------------------------------------------------------------------
def test_build_runtime_wires_one_ledger_to_the_model_and_the_provider(tmp_path, monkeypatch):
    path = create_configuration(tmp_path / "rt", _answers(connected=False))
    config = yaml.safe_load(path.read_text())
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    monkeypatch.setattr(
        config_module, "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )
    resources = config_module.build_runtime(path)
    try:
        assert isinstance(resources.cost_ledger, CostLedger)
        assert resources.runtime.model.cost_ledger is resources.cost_ledger
    finally:
        resources.close()


def test_the_cli_scopes_work_to_the_obligations_deal():
    ledger = CostLedger()
    resources = SimpleNamespace(cost_ledger=ledger)
    with cli._cost_scope(resources, {"deal_id": "d1", "target_id": "t1"}):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=3)
    # the deal and target may come from the step the dispatch read instead
    step = {"deal": {"id": "d2", "target_goal_id": "t2"}}
    with cli._cost_scope(resources, {"kind": "deal_step"}, step):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=4)
    # a bid or a plan has no deal: nothing is counted
    with cli._cost_scope(resources, {"kind": "file_informed_plan", "proposal_id": "p1"}):
        ledger.add("claude-haiku-4-5", basis="metered", fresh_in=99)
    assert _slot(ledger, "d1", "t1")["tokens_in"] == 3
    assert _slot(ledger, "d2", "t2")["tokens_in"] == 4


def test_a_harness_with_no_ledger_scopes_to_nothing():
    with cli._cost_scope(SimpleNamespace(), {"deal_id": "d1"}):
        pass
