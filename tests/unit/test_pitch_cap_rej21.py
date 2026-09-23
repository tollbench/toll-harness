"""THE PARAGRAPH CAP IS KEPT BEFORE THE DOOR (REJ-21, 0.50.0).

WHAT FORCED IT (Rick, 2026-09-23): the draft loop produced a 617-character
pitch_body. The free validate door named REJ-21, the fix round had nothing
for a length problem, the harness "filed anyway", and the filing door refused
it: "pitch_body is 617 characters and the cap is 600: cut at least 17 and send
the proposal again. Nothing was filed." The want was lost for that cycle.

The door counts `len(pitch_body.strip())` (bid_validator._check_pitch,
the bench's own validator) and has no length rule
on the title at all.
"""

from __future__ import annotations

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_two_stages_r243_r245 import (
    PROPOSAL,
    SMALL,
    TOOLS_BRIEF,
    _door_provider,
    _DoorApi,
    _FilingBench,
)
from toll_harness.toll_bench.draft import (
    PROPOSAL_BODY_MAX,
    DraftLoop,
    mend_the_small_proposal,
    pitch_length,
    trim_to_cap,
)

LONG_BODY = " ".join(["I find the shows that take guests like you."] * 14)  # 615 chars


def test_the_count_is_the_doors_count_after_strip():
    # Leading and trailing whitespace is not counted, exactly as the door does.
    body = "  " + "a" * PROPOSAL_BODY_MAX + " \n"
    assert pitch_length(body) == PROPOSAL_BODY_MAX
    assert pitch_length(None) == 0
    # Code points, not bytes: an accented letter is one character.
    assert pitch_length("é" * 10) == 10


def test_a_body_at_exactly_the_cap_is_untouched():
    body = ("word " * 200)[: PROPOSAL_BODY_MAX - 1] + "."
    assert len(body) == PROPOSAL_BODY_MAX
    assert trim_to_cap(body) == body
    fixed, mended = mend_the_small_proposal(dict(SMALL, pitch_body=body), TOOLS_BRIEF)
    assert mended == []
    assert fixed["pitch_body"] == body


def test_an_over_cap_body_is_cut_at_a_sentence_boundary():
    assert len(LONG_BODY) > PROPOSAL_BODY_MAX
    cut = trim_to_cap(LONG_BODY)
    assert len(cut) <= PROPOSAL_BODY_MAX
    assert cut.endswith(".")
    assert LONG_BODY.startswith(cut)
    assert "..." not in cut and "…" not in cut


def test_with_no_sentence_end_the_cut_is_at_a_word_and_never_mid_word():
    body = " ".join(["podcast"] * 100)  # 799 chars, no sentence ends
    cut = trim_to_cap(body)
    assert len(cut) <= PROPOSAL_BODY_MAX
    assert cut.split(" ")[-1] == "podcast"
    assert body.startswith(cut)


def test_a_newline_is_a_sentence_boundary_too():
    body = "a" * 300 + "\n" + " ".join(["word"] * 100)
    cut = trim_to_cap(body)
    assert cut == "a" * 300


def test_the_model_trim_round_is_used_first():
    bench = _FilingBench()
    short = "I find the shows that take guests like you, and book three."
    model = _model(dict(PROPOSAL, pitch_body=LONG_BODY), {"pitch_body": short})

    out = DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                      idempotency_key="k")

    assert out["filed"] is True
    assert len(model.invocations) == 2
    ask = model.invocations[1]["messages"][0].content[0]["text"]
    excess = len(LONG_BODY) - PROPOSAL_BODY_MAX
    assert f"cut at least {excess} characters" in ask
    assert f"cap is {PROPOSAL_BODY_MAX}" in ask
    assert bench.filed[0][1]["pitch_body"] == short


def test_the_deterministic_trim_is_the_fallback_when_the_model_stays_over():
    bench = _FilingBench()
    model = _model(dict(PROPOSAL, pitch_body=LONG_BODY), {"pitch_body": LONG_BODY + " More."})

    out = DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                      idempotency_key="k")

    assert out["filed"] is True
    filed = bench.filed[0][1]["pitch_body"]
    assert filed == trim_to_cap(LONG_BODY)
    assert pitch_length(filed) <= PROPOSAL_BODY_MAX


def test_the_deterministic_trim_is_the_fallback_when_the_model_answers_empty():
    bench = _FilingBench()
    model = _model(dict(PROPOSAL, pitch_body=LONG_BODY), {"pitch_body": ""})

    DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                idempotency_key="k")

    assert bench.filed[0][1]["pitch_body"] == trim_to_cap(LONG_BODY)


def test_a_body_inside_the_cap_costs_no_trim_round():
    bench = _FilingBench()
    model = _model(PROPOSAL)
    DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                idempotency_key="k")
    assert len(model.invocations) == 1


def test_the_title_is_never_cut_the_door_has_no_length_rule_on_it():
    title = "T" * 200
    fixed, mended = mend_the_small_proposal(dict(SMALL, pitch_title=title), TOOLS_BRIEF)
    assert fixed["pitch_title"] == title
    assert mended == []


def test_the_fix_round_changes_a_length_problem_instead_of_filing_it():
    """The fix-round block: REJ-21 for length is a problem this package CAN
    change, so the door is asked again about the cut proposal and nothing is
    filed over the cap."""
    api = _DoorApi({"ok": True, "problems": []})
    provider = _door_provider(api)
    rej21 = [{"code": "REJ-21",
              "detail": "pitch_body is 615 characters and the cap is 600: cut at "
                        "least 15 and send the proposal again. Nothing was filed."}]
    fixed, left = provider._one_fix_round(
        "t-1", dict(SMALL, pitch_body=LONG_BODY), TOOLS_BRIEF,
        {"corrected_ok": False}, rej21,
    )
    assert left == []
    assert pitch_length(fixed["pitch_body"]) <= PROPOSAL_BODY_MAX
    assert len(api.asked) == 1
    assert pitch_length(api.asked[0]["pitch_body"]) <= PROPOSAL_BODY_MAX


def test_an_over_cap_pitch_never_reaches_the_door_or_the_filing(caplog):
    api = _DoorApi({"ok": True, "problems": [], "trimmed": []})
    out = _door_provider(api).submit_proposal(
        "t-1", dict(SMALL, pitch_body=LONG_BODY), "k"
    )
    assert out["ok"] is True
    assert pitch_length(api.asked[0]["pitch_body"]) <= PROPOSAL_BODY_MAX
    assert pitch_length(api.filed[0][1]["pitch_body"]) <= PROPOSAL_BODY_MAX
    assert "filing anyway" not in caplog.text
