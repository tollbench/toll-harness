"""THE HEADLINE IS A SLOT (bench contract 4.0.12, Steven 2026-09-24).

WHAT FORCED THESE TESTS: the new card designs put a five-word headline on
every want card, and the proposal had no short slot for one ("it's another
slot"). The bench added `headline`: the agent's own quick-hit title, up to 40
characters, that the person's card wears once the agent is picked. Longer is
TRIMMED at a word and reported on `trimmed`, never refused; empty is REJ-21,
one problem naming `headline`, in one sentence.

So the harness must: tell the model what the slot is, read it, send it in the
body it files, ask once more when it is empty (and file nothing if it is still
empty), and never cut or refuse a long one.
"""
from __future__ import annotations

import json
import logging

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_two_stages_r243_r245 import (
    PROPOSAL,
    SMALL,
    _door_provider,
    _DoorApi,
    _FilingBench,
)
from toll_harness.toll_bench import programs
from toll_harness.toll_bench.book_of_houses import (
    BookOfHousesTollBenchProvider,
    _is_a_missing_field,
)
from toll_harness.toll_bench.draft import (
    HEADLINE_EMPTY_PROBLEM,
    HEADLINE_EXAMPLE,
    HEADLINE_INSTRUCTION,
    HEADLINE_MAX,
    PROPOSAL_FIELDS,
    PROPOSAL_INSTRUCTION,
    DraftLoop,
    headline_problems,
    headline_warnings,
    mend_the_small_proposal,
    read_proposal,
)
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

LONG = "A little heat, a lot of friends, and a great night in"  # 53 characters
BRIEF = {"want": "A hot-sauce tasting night for eight friends"}


def _ask_text(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


# ---------------------------------------------------------------------------
# The prompt and the schema
# ---------------------------------------------------------------------------
def test_the_bench_sentence_is_the_benchs_own_words():
    """bid_validator._check_headline, word for word, with its cap and example."""
    assert HEADLINE_MAX == 40
    assert HEADLINE_EXAMPLE == "A little heat. A great night."
    assert HEADLINE_EMPTY_PROBLEM == (
        "headline is empty: write the short quick-hit title the person's card "
        'wears once you are picked, up to 40 characters, e.g. "A little heat. '
        'A great night."'
    )


def test_headline_is_one_of_the_eight_fields():
    assert "headline" in PROPOSAL_FIELDS
    assert len(PROPOSAL_FIELDS) == 8
    assert PROPOSAL_FIELDS.index("headline") == PROPOSAL_FIELDS.index("pitch_title") + 1


def test_the_proposal_ask_describes_the_headline_as_the_bench_does():
    assert "eight fields" in PROPOSAL_INSTRUCTION
    assert "`headline` -- REQUIRED" in PROPOSAL_INSTRUCTION
    assert "up to 40 characters (about five words)" in PROPOSAL_INSTRUCTION
    assert "card wears at the top once you are picked" in PROPOSAL_INSTRUCTION
    assert '"A little heat. A great night."' in PROPOSAL_INSTRUCTION
    assert '"Five emails. One surprise."' in PROPOSAL_INSTRUCTION
    assert "Longer is trimmed at a word" in PROPOSAL_INSTRUCTION
    assert '"headline": "..."' in PROPOSAL_INSTRUCTION
    # No em-dashes in words the model reads.
    assert "\u2014" not in PROPOSAL_INSTRUCTION
    assert "\u2014" not in HEADLINE_INSTRUCTION


def test_the_proposal_ask_the_model_sees_carries_the_headline_slot():
    bench = _FilingBench()
    model = _model(PROPOSAL)

    DraftLoop(model, bench).run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")

    ask = _ask_text(model)
    assert "`headline`" in ask and "40 characters" in ask


def test_the_old_road_tool_description_names_the_headline():
    registry = add_toll_bench_tools(build_standard_registry())
    submit = next(
        definition
        for definition in registry.definitions()
        if definition.name == "toll_bench.submit_proposal"
    )
    assert "headline is REQUIRED" in submit.description
    assert "up to 40 characters" in submit.description


def test_the_headline_is_a_word_the_program_diff_expects_to_change():
    assert "headline" in programs.EXPECTED_TO_CHANGE


# ---------------------------------------------------------------------------
# Reading it
# ---------------------------------------------------------------------------
def test_read_proposal_keeps_the_headline_stripped_and_never_cut():
    read = read_proposal(dict(PROPOSAL, headline="  " + LONG + "  "))
    assert read["headline"] == LONG
    assert len(read["headline"]) > HEADLINE_MAX
    # A blank one is simply not there: the door's hole, not a value.
    assert "headline" not in read_proposal(dict(PROPOSAL, headline="   "))
    assert "headline" not in read_proposal(dict(PROPOSAL, headline=7))


def test_the_mend_never_cuts_or_invents_a_headline():
    long_one = dict(SMALL, headline=LONG)
    fixed, mended = mend_the_small_proposal(long_one)
    assert fixed["headline"] == LONG and mended == []
    bare = {k: v for k, v in SMALL.items() if k != "headline"}
    fixed, mended = mend_the_small_proposal(bare)
    assert "headline" not in fixed and mended == []


def test_empty_is_the_one_problem_and_long_is_only_a_warning():
    assert headline_problems(PROPOSAL) == []
    assert headline_warnings(PROPOSAL) == []
    for empty in ({}, {"headline": ""}, {"headline": "   "}, {"headline": None}):
        assert headline_problems(empty) == [
            {"path": "headline", "code": "REJ-21", "message": HEADLINE_EMPTY_PROBLEM}
        ]
    assert headline_problems({"headline": LONG}) == []
    (warning,) = headline_warnings({"headline": LONG})
    assert "53 characters" in warning and "trim it at a word" in warning
    # Exactly at the cap, counted the door's way (stripped), says nothing.
    assert headline_warnings({"headline": " " + "x" * HEADLINE_MAX + " "}) == []


# ---------------------------------------------------------------------------
# The wire: the draft loop files it
# ---------------------------------------------------------------------------
def test_the_filed_body_carries_the_headline():
    bench = _FilingBench()
    out = DraftLoop(_model(PROPOSAL), bench).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k"
    )

    assert out["ok"] is True and out["model_calls"] == 1
    filed = bench.filed[0][1]
    assert filed["headline"] == PROPOSAL["headline"]
    assert out["proposal"]["headline"] == PROPOSAL["headline"]


def test_an_empty_headline_gets_one_more_ask_in_the_benchs_words_then_files():
    bench = _FilingBench()
    bare = {k: v for k, v in PROPOSAL.items() if k != "headline"}
    model = _model(bare, {"headline": "Three shows. Your voice."})

    out = DraftLoop(model, bench).run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")

    assert out["ok"] is True and out["model_calls"] == 2
    ask = _ask_text(model, 1)
    assert HEADLINE_EMPTY_PROBLEM in ask
    assert '{"headline": "..."}' in ask
    assert bench.filed[0][1]["headline"] == "Three shows. Your voice."


def test_a_headline_still_empty_after_one_more_ask_files_nothing():
    bench = _FilingBench()
    bare = dict(PROPOSAL, headline="")
    out = DraftLoop(_model(bare, {"headline": "  "}), bench).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k"
    )

    assert out["ok"] is False and out["error"] == "no_headline"
    assert HEADLINE_EMPTY_PROBLEM in out["message"]
    assert bench.filed == []


def test_a_long_headline_is_sent_whole_with_a_warning_and_the_trim_rides_back(caplog):
    class TrimmingBench(_FilingBench):
        def submit_proposal(self, target_id, proposal, idempotency_key):
            super().submit_proposal(target_id, proposal, idempotency_key)
            return {
                "ok": True,
                "proposal_id": "p-1",
                "trimmed": [
                    {
                        "path": "headline",
                        "from": LONG,
                        "to": "A little heat, a lot of friends, and a",
                        "from_chars": 53,
                        "to_chars": 38,
                    }
                ],
            }

    bench = TrimmingBench()
    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        out = DraftLoop(_model(dict(PROPOSAL, headline=LONG)), bench).run(
            "t-1", kind="bid", brief=BRIEF, idempotency_key="k"
        )

    # Not refused, not cut here, not asked about again: the door trims it.
    assert out["ok"] is True and out["model_calls"] == 1
    assert bench.filed[0][1]["headline"] == LONG
    assert out["trimmed"][0]["path"] == "headline"
    assert out["trimmed"][0]["to_chars"] == 38
    assert any("trim it at a word" in record.getMessage() for record in caplog.records)
    assert any("the bench trimmed headline" in record.getMessage() for record in caplog.records)


def test_a_dry_run_carries_the_headline_too():
    bench = _FilingBench()
    out = DraftLoop(_model(PROPOSAL), bench).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k", file=False
    )
    assert out["dry_run"] is True and bench.filed == []
    assert out["proposal"]["headline"] == PROPOSAL["headline"]


# ---------------------------------------------------------------------------
# The provider: the small road and the local mirror
# ---------------------------------------------------------------------------
def test_the_small_road_sends_the_headline_to_both_doors():
    api = _DoorApi()
    out = _door_provider(api).submit_proposal("t-1", dict(SMALL), "k-1")

    assert out["ok"] is True
    assert api.asked[0]["headline"] == SMALL["headline"]
    assert api.filed[0][1]["headline"] == SMALL["headline"]


def test_the_doors_empty_headline_problem_is_a_missing_field_and_files_nothing():
    row = {"code": "REJ-21", "field": "headline", "detail": HEADLINE_EMPTY_PROBLEM,
           "fix": "Write a headline."}
    assert _is_a_missing_field(row) is True
    # The pitch problem on the same code is not about a missing field.
    assert _is_a_missing_field({"code": "REJ-21", "field": "pitch_body",
                                "detail": "pitch_body is 617 characters"}) is False

    api = _DoorApi({"ok": False, "problems": [row], "trimmed": []})
    bare = {k: v for k, v in SMALL.items() if k != "headline"}
    out = _door_provider(api).submit_proposal("t-1", bare, "k-1")

    assert out["ok"] is False and out["error"] == "proposal_incomplete"
    assert HEADLINE_EMPTY_PROBLEM in out["message"]
    assert api.filed == []


def test_the_validate_tool_hands_back_the_doors_trims():
    trim = {"path": "headline", "from": LONG, "to": "A little heat, a lot of friends, and a",
            "from_chars": 53, "to_chars": 38}
    class DoorWithSchema(_DoorApi):
        def proposal_schema(self):
            return _schema(with_headline=True)

    api = DoorWithSchema({"ok": True, "problems": [], "trimmed": [trim]})
    out = _door_provider(api).validate_proposal(dict(SMALL, headline=LONG), "t-1")

    assert out["ok"] is True
    # A long headline is a trim, never a problem -- not at the door and not
    # in the offline mirror the answer also carries.
    assert not any(row.get("field") == "headline" for row in out["problems"])
    assert out["trimmed"] == [trim]


class _SchemaApi:
    def __init__(self, schema):
        self.schema = schema

    def proposal_schema(self):
        return self.schema


def _schema(*, with_headline):
    properties = {
        "pitch_title": {"type": "string"},
        "pitch_body": {"type": "string"},
        "smart_goals": {"type": "array"},
        "strategy": {"type": "string"},
        "capabilities": {"type": "array"},
        "wins": {"type": "array"},
        "research_links": {"type": "array"},
        "skill_research": {"type": "string"},
    }
    required = ["pitch_title", "pitch_body"]
    if with_headline:
        properties["headline"] = {"type": "string", "minLength": 1}
        required.insert(1, "headline")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


# The whole-plan mirror's other required fields, filled, so the only thing
# these tests move is the headline.
BASE = {
    "pitch_title": "A",
    "pitch_body": "B",
    "smart_goals": ["one"],
    "strategy": "How.",
    "capabilities": ["research"],
    "wins": [],
    "research_links": [{"url": "https://example.test", "note": "What it said."}],
    "skill_research": "What I learned.",
}


def _mirror(schema, proposal):
    return BookOfHousesTollBenchProvider(_SchemaApi(schema)).validate_proposal(proposal)


def test_the_mirror_names_an_empty_headline_once_in_the_benchs_sentence():
    for proposal in (dict(BASE), dict(BASE, headline=""), dict(BASE, headline="  ")):
        out = _mirror(_schema(with_headline=True), proposal)
        headline_rows = [row for row in out["problems"] if "headline" in row["message"]
                         or row["path"] == "headline"]
        assert headline_rows == [{"path": "headline", "message": HEADLINE_EMPTY_PROBLEM}]
        assert out["ok"] is False


def test_the_mirror_warns_about_a_long_headline_and_does_not_refuse_it():
    out = _mirror(_schema(with_headline=True), dict(BASE, headline=LONG))
    assert out["ok"] is True and out["problems"] == []
    assert len(out["warnings"]) == 1 and "trim it at a word" in out["warnings"][0]

    fine = _mirror(_schema(with_headline=True), dict(BASE, headline="Four seats."))
    assert fine["ok"] is True and fine["warnings"] == []


def test_a_bench_that_does_not_name_the_headline_is_not_told_about_one():
    """An older bench (contract 4.0.10 and before) ignores the key, so the
    mirror neither asks for it nor calls it an unexpected property."""
    sent = _mirror(_schema(with_headline=False), dict(BASE, headline="Four seats."))
    assert sent["ok"] is True, sent["problems"]
    missing = _mirror(_schema(with_headline=False), dict(BASE))
    assert missing["ok"] is True and missing["warnings"] == []


def test_the_body_on_the_wire_is_plain_json_with_the_headline():
    """What the api client posts is the proposal dict itself: the headline is
    a plain top-level string key, next to pitch_title."""
    api = _DoorApi()
    _door_provider(api).submit_proposal("t-1", dict(SMALL), "k-1")
    body = json.loads(json.dumps(api.filed[0][1]))
    assert list(body).index("headline") == list(body).index("pitch_title") + 1
