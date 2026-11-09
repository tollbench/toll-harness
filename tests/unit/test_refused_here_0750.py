"""A REFUSAL MADE ON THIS MACHINE SAYS SO (0.75.0, 2026-11-08).

WHAT FORCED THESE TESTS: Sam (want 9b2fc180, step 3) filed an outcome naming
both a link and words. The provider refused it in 0 ms, before anything left
the machine, and the step stamped `failure: server_rejected` on it and told
the model "the bench's words are in `the_bench_refused`". Every refusal the
provider makes before sending now carries `refused_by: "harness"`; the step
labels it `harness_rejected` and the one more try says the bench never saw it.
A refusal the bench sends keeps `server_rejected` and the bench's wording.
"""
from __future__ import annotations

from tests.unit.test_deliver_file_r230 import _OutcomeApi
from tests.unit.test_step_ask import BRIEF, CAFES, OBLIGATION, FakeBench, _payload
from tests.unit.test_step_ask_tool_calls import _call, _cli
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import StepAsk

ARGUMENTS = {key: value for key, value in CAFES.items() if key != "call"}


def test_the_providers_own_outcome_refusals_are_marked_and_never_sent():
    api = _OutcomeApi()
    provider = BookOfHousesTollBenchProvider(api)
    both = provider.file_outcome(
        "t", {"note": "n", "url": "https://x/y", "text": "words"}, "k")
    assert both["error"] == "exactly_one_outcome_content_required"
    assert both["refused_by"] == "harness"
    stray = provider.file_outcome("t", {"note": "n", "text": "w", "colour": "red"}, "k")
    assert stray["error"] == "invalid_outcome_fields" and stray["refused_by"] == "harness"
    assert provider.file_outcome("t", {"text": "w"}, "k")["refused_by"] == "harness"
    assert api.filed == []  # none of them reached the bench


def test_a_bench_refusal_is_not_marked():
    from toll_harness.toll_bench.book_of_houses import BookOfHousesApiError
    error = BookOfHousesApiError(422, "deliverable_missing", "a file was promised")
    provider = BookOfHousesTollBenchProvider(_OutcomeApi(error=error))
    refused = provider.file_outcome("t", {"note": "n", "text": "words"}, "k")
    assert refused["ok"] is False and "refused_by" not in refused


def _two_tries(tmp_path, refusal):
    bench = FakeBench(refuse_first=refusal)
    model = _cli(tmp_path, _call("submit_step_outcome", ARGUMENTS),
                 _call("submit_step_outcome", ARGUMENTS))
    out = StepAsk(model, bench).run(OBLIGATION, _payload(), brief=BRIEF)
    return out, model


def test_a_harness_refusal_is_labelled_harness_rejected_and_the_retry_says_so(tmp_path):
    refusal = BookOfHousesTollBenchProvider(_OutcomeApi()).file_outcome(
        "t", {"note": "n", "url": "https://x/y", "text": "words"}, "k")
    out, model = _two_tries(tmp_path, refusal)
    assert out["ok"] is True
    assert out["trail"][0]["failure"] == "harness_rejected"
    assert "before it was sent; the bench never saw it" in model.prompts[1]
    assert "the bench's words are in" not in model.prompts[1]


def test_a_bench_refusal_keeps_server_rejected_and_the_benchs_words(tmp_path):
    out, model = _two_tries(tmp_path, {"ok": False, "error": "deliverable_missing",
                                       "status": 422, "message": "a file was promised"})
    assert out["ok"] is True
    assert out["trail"][0]["failure"] == "server_rejected"
    assert "the bench's words are in" in model.prompts[1]
