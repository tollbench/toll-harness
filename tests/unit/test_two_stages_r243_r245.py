"""TWO STAGES — the proposal is one call, the plan is a form (rules 243-245).

WHAT FORCED THESE TESTS. On 10 and 11 September 2026 the fleet spun. A bid was
an eighteen-step, ~33KB document checked by 44 refusal rules: GPT-6 Astra took
the workshop want from 139 problems down to 5 in 36 rounds and died on two
length caps; Llama, Nova and Mistral each burned the full 200-round ceiling and
filed nothing. Two of those deaths were the harness's own:

  * `tools: []` on a brief -- the bench saying "this want offers no tools" --
    was read as "this bench publishes no index", so the model was handed the
    platform's fallback four and planned rows the door refuses forever;
  * the stall guard hashed the whole draft, so a model that REWORDED the same
    bad field looked like progress and the guard never tripped.

The rest of the file holds the two stages themselves: a PROPOSAL is seven
fields and ONE model call with no draft at all, and a PLAN is a FORM the bench
hands the chosen agent, filled in one reply, with the bench's own questions,
its stance line and its worked example in front of the model.
"""
from __future__ import annotations

import json

from tests.unit.test_draft_loop_r241 import _model
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.draft import (
    FORM_CHAR_BUDGET,
    FORM_STEP_FIELDS,
    PROMPT_CHAR_BUDGET,
    PROPOSAL_QUESTIONS_MAX,
    DraftLoop,
    block_grammar_summary,
    example_plan,
    is_small_proposal,
    mend_the_small_proposal,
    pick_tools,
    read_form,
    read_proposal,
    read_questions,
    stance_of,
    the_form_is_blank,
    tool_slugs,
    tools_index,
)


# ---------------------------------------------------------------------------
# BUG ONE — an empty catalog is an answer, not a missing catalog
# ---------------------------------------------------------------------------
def test_an_empty_tools_list_means_no_tools_and_never_the_fallback():
    """`tools: []` is the bench saying this want offers NO tools. Reading it as
    "no index published" handed the model GRANT_MIN_ACTIONS and PLATFORM_TOOLS,
    so every plan on a want with no tools put a connection row on the front of
    a step that could never use it."""
    assert tools_index({"tools": []}) == []
    # And the fallback is real, so the test above is not passing by accident.
    assert tools_index({}) != []
    assert len(tools_index({})) >= len(blocks.PLATFORM_TOOLS)


def test_only_a_missing_index_falls_back_to_the_platforms_own_verbs():
    fallback = {row["tool"] for row in tools_index({})}
    assert fallback == {row["tool"] for row in tools_index({"tools": None})}
    assert "platform.research" in fallback
    # A list with nothing usable in it is still a list: the bench published it.
    assert tools_index({"tools": [{"no_tool_key": True}]}) == []


def test_an_empty_block_catalog_is_an_answer_too():
    """The same bug, one line away from it: `block_templates: {}` is a want
    with no blocks, and falling through to the act registry offered the model
    kinds this want does not have."""
    published = block_grammar_summary({"block_templates": {}}, {"act_kinds": {"meeting": {}}})
    assert "no blocks" in published
    assert "meeting" not in published
    # A catalog that IS published names its own kinds.
    named = block_grammar_summary({"block_templates": {"research": [], "email": []}}, None)
    assert "research" in named and "email" in named


# ---------------------------------------------------------------------------
# BUG TWO — the guard is the problem's NAME, not the draft's text
# ---------------------------------------------------------------------------
class _Naming:
    """A bench that names two problems in turn and never clears either."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.patches: list[list[dict]] = []

    def answer(self, index):
        path, code = self.paths[index % len(self.paths)]
        return {
            "ok": True,
            "draft": {"steps": [{"title": "A step"}]},
            "remaining": 2,
            "ready": False,
            "rounds": {"used": len(self.patches), "left": 100, "cap": 100},
            "next_fix": {"path": path, "code": code, "current": "whatever is there now"},
        }


def _reworded_loop(bench):
    loop = DraftLoop(None, None)
    words = iter("abcdefghij")

    def ask(*_args, **_kwargs):
        # THE TEXT IS DIFFERENT EVERY ROUND. A guard that hashed the draft saw
        # progress here for ever.
        return {"patches": [{"path": "form.steps.0.do_line", "value": next(words) * 20}]}

    def patch(_target, _kind, patches, _what):
        bench.patches.append(list(patches))
        return bench.answer(len(bench.patches))

    loop._ask = ask
    loop._patch = patch
    return loop


def test_the_same_problem_reworded_three_times_still_stops_the_draft():
    bench = _Naming([("form.steps.0.do_line", "does_not_address")])
    loop = _reworded_loop(bench)

    out = loop._answer_the_fixes("t-1", "plan", bench.answer(0), "a want")

    assert out["error"] == "draft_stalled"
    assert "does_not_address" in out["message"]
    # Named once, answered; named twice, answered; the third naming ends it.
    assert len(bench.patches) == 2


def test_two_problems_taking_turns_still_trip_the_guard():
    """The count is per (path, code), not per consecutive round: a door that
    alternates between two problems it never clears used to run for ever."""
    bench = _Naming([
        ("form.steps.0.do_line", "does_not_address"),
        ("form.steps.1.who", "person_does_the_work"),
    ])
    loop = _reworded_loop(bench)

    out = loop._answer_the_fixes("t-1", "plan", bench.answer(0), "a want")

    assert out["error"] == "draft_stalled"
    assert len(bench.patches) == 4


# ---------------------------------------------------------------------------
# STAGE ONE — the proposal: seven fields, one call, no draft
# ---------------------------------------------------------------------------
PROPOSAL = {
    "pitch_title": "Three podcast bookings, start to finish",
    "pitch_body": "I find the shows that take outside guests, write the pitch "
                  "you approve, send it, chase the quiet ones and book the "
                  "first interview.",
    "odds": 0.55,
    "total_ask_cents": 25_000,
    "research_links": [{"url": "https://example.test/shows", "note": "Who takes guests."}],
    "finalist_questions": ["What do you want to be known for?"],
    "tools_needed": ["gmail.message.send"],
}


class _FilingBench:
    """The proposal door and nothing else: no draft, no plan."""

    fleet = None

    def __init__(self):
        self.filed: list[tuple[str, dict, str]] = []

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.filed.append((target_id, json.loads(json.dumps(proposal)), idempotency_key))
        return {"ok": True, "proposal_id": "p-1",
                "bench_fixed": ["pitch_body trimmed to 600 characters"]}


def test_a_proposal_is_one_model_call_and_one_filing():
    bench = _FilingBench()
    model = _model(PROPOSAL)

    out = DraftLoop(model, bench).run(
        "t-1", kind="bid", brief={"want": "Get me on three podcasts"},
        idempotency_key="key-1",
    )

    assert out["ok"] is True and out["filed"] is True
    assert out["rounds"] == 0 and out["model_calls"] == 1
    assert len(model.invocations) == 1
    assert bench.filed[0][0] == "t-1"
    filed = bench.filed[0][1]
    assert set(filed) <= set(PROPOSAL)
    assert "steps" not in filed
    # A TRIM IS NOT A REFUSAL: the door says what it cut and the run carries it.
    assert out["bench_fixed"] == ["pitch_body trimmed to 600 characters"]


def test_the_proposal_ask_names_the_caps_and_asks_for_no_plan():
    bench = _FilingBench()
    model = _model(PROPOSAL)

    DraftLoop(model, bench).run(
        "t-1", kind="bid",
        brief={"want": "Get me on three podcasts", "budget_ceiling_cents": 30_000,
               "strategy": "Approach this like a master who moves fast."},
        idempotency_key="k",
    )

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    assert "120 characters" in ask and "600 characters" in ask
    assert "no steps" in ask
    assert "30000" in ask.replace(" ", "")
    # The person's own sentence about HOW opens the ask.
    assert "Approach this like a master who moves fast." in ask


def test_a_proposal_with_no_words_files_nothing():
    bench = _FilingBench()
    out = DraftLoop(_model({"odds": 0.5}), bench).run(
        "t-1", kind="bid", brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False and out["error"] == "no_proposal"
    assert bench.filed == []


def test_the_seven_fields_are_read_and_everything_else_is_dropped():
    read = read_proposal(dict(PROPOSAL, steps=[{"title": "no"}], strategy="no",
                              capabilities=["no"], odds=4))
    assert set(read) == set(PROPOSAL)
    # The one coercion: a probability is arithmetic, not an opinion.
    assert read["odds"] == 1.0
    assert read_proposal({"proposal": PROPOSAL})["pitch_title"] == PROPOSAL["pitch_title"]


def test_a_proposal_with_no_steps_is_the_small_one():
    assert is_small_proposal(PROPOSAL) is True
    assert is_small_proposal(dict(PROPOSAL, steps=[{"title": "A step"}])) is False
    assert is_small_proposal({}) is False


# ---------------------------------------------------------------------------
# THE WHOLE QUESTION, IN THE AGENT'S OWN WORDS (contract 3.16, 2026-09-11)
# ---------------------------------------------------------------------------
def test_a_question_is_a_block_carrying_the_whole_question_in_title():
    """THE FRAMES ARE DROPPED (Steven, 2026-09-11: "fine drop them"). The
    bench no longer writes half of anybody's sentence and no longer reads
    `fill`: "Write the whole question, in your own words, in `title`." The
    first fleet cycle on 0.38.1 was refused REJ-15 on every question of every
    bid because this package was still filing {id, format, fill}."""
    blocks_out = read_questions([
        "What do you want them to do next?",
        {"format": "yes_no", "title": "Should I include the background?"},
        {"format": "single_choice", "fill": "Which tone should it have?",
         "config": {"options": ["Warm", "Straight to the point"]}},
    ])

    assert [q["id"] for q in blocks_out] == ["q1", "q2", "q3"]
    assert blocks_out[0] == {"id": "q1", "format": "short_answer",
                             "title": "What do you want them to do next?"}
    # Nothing is taken off the front any more: the sentence is the agent's.
    assert blocks_out[1]["title"] == "Should I include the background?"
    # A model still writing the old shape keeps its question -- the words move
    # to `title` rather than the question being thrown away.
    assert blocks_out[2]["title"] == "Which tone should it have?"
    assert all("fill" not in q for q in blocks_out)
    # A choice's options are read as the door reads them, labels untouched.
    assert blocks_out[2]["config"] == {
        "options": [{"id": "o1", "label": "Warm"},
                    {"id": "o2", "label": "Straight to the point"}],
    }


def test_a_shape_the_door_does_not_take_comes_down_to_a_text_box():
    """A date, a number, a form or an upload is a control on a STEP of the
    plan. Asked here it is refused REJ-15 and the bid is the round, so the
    words are kept and the shape comes down."""
    out = read_questions([
        {"format": "date_time", "title": "Which dates work for you?"},
        {"format": "number", "title": "How many people?"},
        {"format": "written_response", "title": "Anything to avoid?"},
        {"format": "single_choice", "title": "Warm or formal?",
         "config": {"options": ["Warm"]}},
    ])
    assert [q["format"] for q in out[:3]] == ["short_answer"] * 3
    assert out[0]["title"] == "Which dates work for you?"
    # An empty dropdown is a text box wearing a control, and the door says so.
    assert read_questions([{"format": "single_choice", "title": "Warm or formal?",
                            "config": {"options": ["Warm"]}}])[0]["format"] == (
        "short_answer")


def test_a_contact_question_is_dropped_and_never_filed():
    """RULE 238 CORRECTED (2026-09-11): the contact book is not one of your
    questions. The person picks who this goes to on a step of the plan, after
    they have chosen you, out of their own private book -- so the bench
    refuses a picker here REJ-15, and one refused question costs the whole
    bid. Dropping it files the other two instead of nothing."""
    assert read_questions([{"format": "contact_picker", "title": "Who?",
                            "config": {"count": 2, "sneaky": "x"},
                            "contact_ref": "c-1"}]) == []
    assert read_questions([{"format": "contact_picker"}]) == []

    kept = read_questions([{"format": "contact_picker", "config": {"count": 2}},
                           {"format": "yes_no", "fill": "sign them from you"}])
    assert [q["format"] for q in kept] == ["yes_no"]


def test_three_questions_is_the_cap_and_ids_never_collide():
    many = read_questions(["one", "two", "three", "four"])
    assert len(many) == PROPOSAL_QUESTIONS_MAX
    same = read_questions([{"id": "q", "fill": "one"}, {"id": "q", "fill": "two"}])
    assert [q["id"] for q in same] == ["q", "q2"]


def test_a_proposal_that_asks_nothing_says_so_with_an_empty_list():
    """ALWAYS PRESENT, INCLUDING EMPTY: "I need nothing from you" and "the
    field never arrived" are different facts."""
    read = read_proposal(dict(PROPOSAL, finalist_questions=[]))
    assert read["finalist_questions"] == []
    assert "finalist_questions" in read_proposal({"pitch_title": "A", "pitch_body": "B"})


def test_the_proposal_ask_names_the_shapes_and_the_frames():
    bench = _FilingBench()
    model = _model(PROPOSAL)

    DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                idempotency_key="k")

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    for shape in ("yes_no", "single_choice", "short_answer"):
        assert shape in ask
    # And NOT the fourth shape: who this goes to is the bench's own step.
    assert "contact_picker" not in ask
    assert "DO NOT ASK WHO THIS GOES TO" in ask
    # The frames are gone, and so is the field the door stopped reading.
    assert "WRITE THE WHOLE QUESTION YOURSELF" in ask
    assert "Should I ___?" not in ask
    assert '"fill"' not in ask
    # The filed questions are blocks, not the strings the model wrote.
    assert bench.filed[0][1]["finalist_questions"][0]["format"] == "short_answer"
    assert bench.filed[0][1]["finalist_questions"][0]["title"]


# ---------------------------------------------------------------------------
# THE WANT'S TOOL LIST IS THE WANT'S (REJ-01, 0.38.2)
# ---------------------------------------------------------------------------
# A live brief's `tools`, as it arrives with ?tools=1: the same `tool` name
# repeats under several providers, and the last row is the wildcard.
TOOLS_BRIEF = {
    "want": "Run the ads and email the list",
    "tools": [
        {"tool": "gmail.message.send", "provider": "google-gmail",
         "family": "mailbox", "one_line": "send an email"},
        {"tool": "gmail.message.send", "provider": "composio:gmail",
         "family": "mailbox", "one_line": "send an email"},
        {"tool": "platform.research", "provider": "", "family": "platform",
         "one_line": "the platform looks something up"},
        {"tool": "composio:<service>/<TOOL>", "provider": "composio",
         "one_line": "any of 1,500 services"},
    ],
}


def test_the_catalog_is_the_briefs_own_slugs_and_never_the_wildcard():
    assert tool_slugs(TOOLS_BRIEF) == {"gmail.message.send", "platform.research"}
    # `composio:<service>/<TOOL>` is the door a plan step's run goes through,
    # not a slug the proposal door will match.
    assert "composio:<service>/<TOOL>" not in tool_slugs(TOOLS_BRIEF)


def test_a_tool_the_want_does_not_offer_is_dropped_before_filing():
    """WHAT FORCED IT: most bids of the first 0.38.1 fleet cycle were refused
    REJ-01 -- '"composio:facebook-ads/campaign.create" is not on this want's
    tool list'. One off-list name costs the whole bid."""
    kept, dropped = pick_tools(
        ["gmail.message.send", "composio:facebook-ads/campaign.create"], TOOLS_BRIEF
    )
    assert kept == ["gmail.message.send"]
    assert dropped == ["composio:facebook-ads/campaign.create"]


def test_a_provider_name_is_not_a_tool_and_neither_is_an_invented_slug():
    # The two shapes the models actually wrote.
    kept, dropped = pick_tools(["google-gmail", "slack.post.publish"], TOOLS_BRIEF)
    assert kept == []
    assert dropped == ["google-gmail", "slack.post.publish"]


def test_a_provider_prefixed_slug_is_read_as_the_slug_it_names():
    kept, dropped = pick_tools(
        ["google-gmail/gmail.message.send", "GMAIL.MESSAGE.SEND"], TOOLS_BRIEF
    )
    assert kept == ["gmail.message.send"]
    assert dropped == []


def test_a_want_that_publishes_no_list_refuses_nothing_of_our_own():
    kept, dropped = pick_tools(["anything.at.all"], {"want": "x"})
    assert kept == ["anything.at.all"] and dropped == []


def test_the_filed_proposal_carries_only_tools_the_want_offers():
    bench = _FilingBench()
    model = _model(dict(PROPOSAL, tools_needed=["google-gmail",
                                                "gmail.message.send",
                                                "composio:facebook-ads/campaign.create"]))

    DraftLoop(model, bench).run("t-1", kind="bid", brief=TOOLS_BRIEF,
                                idempotency_key="k")

    assert bench.filed[0][1]["tools_needed"] == ["gmail.message.send"]


def test_a_proposal_whose_tools_are_all_off_the_list_files_none():
    bench = _FilingBench()
    model = _model(dict(PROPOSAL, tools_needed=["composio:facebook-ads/campaign.create"]))

    DraftLoop(model, bench).run("t-1", kind="bid", brief=TOOLS_BRIEF,
                                idempotency_key="k")

    # A service off the list belongs on the step that uses it, as an outside
    # act -- never here, and never as a reason to lose the bid.
    assert "tools_needed" not in bench.filed[0][1]


def test_the_ask_tells_the_model_to_copy_the_slug_exactly():
    bench = _FilingBench()
    model = _model(PROPOSAL)

    DraftLoop(model, bench).run("t-1", kind="bid", brief=TOOLS_BRIEF,
                                idempotency_key="k")

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    assert "COPIED EXACTLY" in ask
    assert "tools_on_this_want" in ask
    assert "is not a tool" in ask


# ---------------------------------------------------------------------------
# RESEARCH LINKS ARE REQUIRED (REJ-31, 0.38.2)
# ---------------------------------------------------------------------------
def test_a_proposal_with_no_links_gets_one_more_ask_and_then_files():
    """WHAT FORCED IT: the models filed zero research_links on nearly every
    want, and the field is required -- so the free door refused the proposal
    and the filing door refused it again. One more small ask is cheaper than
    the round."""
    bench = _FilingBench()
    model = _model(
        dict(PROPOSAL, research_links=[]),
        {"research_links": [{"url": "https://example.test/a", "note": "Who takes guests."}]},
    )

    out = DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                      idempotency_key="k")

    assert out["filed"] is True
    assert len(model.invocations) == 2
    assert bench.filed[0][1]["research_links"] == [
        {"url": "https://example.test/a", "note": "Who takes guests."}
    ]


def test_the_links_ask_says_what_a_link_is_and_asks_for_nothing_else():
    bench = _FilingBench()
    model = _model(dict(PROPOSAL, research_links=[]),
                   {"research_links": [{"url": "https://example.test/a", "note": "x"}]})

    DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                idempotency_key="k")

    second = model.invocations[1]["messages"][0].content[0]["text"]
    assert "REJ-31" in second
    assert "research_links" in second


def test_no_links_twice_files_nothing_at_all():
    """A bid the door has already said it will refuse is the round. Nothing is
    filed, and the run says why."""
    bench = _FilingBench()
    model = _model(dict(PROPOSAL, research_links=[]), {"research_links": []})

    out = DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                      idempotency_key="k")

    assert out["ok"] is False and out["filed"] is False
    assert out["error"] == "no_research_links"
    assert bench.filed == []


def test_the_ask_says_the_links_are_required():
    bench = _FilingBench()
    model = _model(PROPOSAL)

    DraftLoop(model, bench).run("t-1", kind="bid", brief={"want": "x"},
                                idempotency_key="k")

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    assert "REQUIRED" in ask


# ---------------------------------------------------------------------------
# THE MEND: every fix this package can make with no model call
# ---------------------------------------------------------------------------
def test_the_mend_puts_the_questions_in_the_doors_shape():
    fixed, mended = mend_the_small_proposal(
        {"finalist_questions": [{"format": "yes_no", "fill": "include background"}]},
        TOOLS_BRIEF,
    )
    assert fixed["finalist_questions"] == [
        {"id": "q1", "title": "include background", "format": "yes_no"}
    ]
    assert mended and "finalist_questions" in mended[0]


def test_the_mend_drops_an_off_list_tool_and_says_so():
    fixed, mended = mend_the_small_proposal(
        {"tools_needed": ["gmail.message.send", "google-gmail"]}, TOOLS_BRIEF
    )
    assert fixed["tools_needed"] == ["gmail.message.send"]
    assert any("google-gmail" in line for line in mended)


def test_the_mend_leaves_a_whole_plan_bid_alone():
    """`[[...]]` is the old whole-plan shape -- one group of four -- and it has
    a road of its own. Reading it as a flat list would drop every question."""
    grouped = {"finalist_questions": [[{"id": "q1", "format": "yes_no",
                                        "title": "Ok?"}]]}
    fixed, mended = mend_the_small_proposal(grouped, TOOLS_BRIEF)
    assert mended == []
    assert fixed is grouped


def test_the_mend_invents_nothing():
    # A missing required field is not something this package can fill in.
    bare = {"pitch_title": "A", "pitch_body": "B"}
    fixed, mended = mend_the_small_proposal(bare, TOOLS_BRIEF)
    assert mended == [] and fixed is bare


# ---------------------------------------------------------------------------
# THE FREE DOOR, ONE FIX ROUND, THEN FILE (0.38.2)
# ---------------------------------------------------------------------------
# WHAT FORCED IT: the old policy filed every proposal "anyway so the door's own
# answer is the record". The validate door and the filing door run ONE
# pipeline, so a problem there is a refusal here, every time -- and on a
# one-bid-per-want board that spent the round to learn what we had just been
# told for free. The first 0.38.1 fleet cycle filed one bid in six minutes and
# every refusal had been named by the free door first.
from toll_harness.toll_bench.book_of_houses import (  # noqa: E402
    BookOfHousesTollBenchProvider,
)


class _DoorApi:
    """The proposals door and the free validate door, scripted."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[dict] = []
        self.filed: list[tuple[str, dict, str]] = []

    def protocol(self):
        return {"contract_version": "3.16"}

    def target_brief(self, target_id):
        return {"ok": True, "brief": dict(TOOLS_BRIEF, target_id=target_id)}

    def validate_proposal(self, target_id, proposal):
        self.asked.append(json.loads(json.dumps(proposal)))
        return self.answers.pop(0) if self.answers else {"ok": True, "problems": []}

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.filed.append((target_id, json.loads(json.dumps(proposal)), idempotency_key))
        return {"ok": True, "proposal_id": "p-1"}

    def me(self):
        return {"ok": True, "reachability_test": {"reachable": True}}


def _door_provider(api):
    provider = BookOfHousesTollBenchProvider(api)
    provider.fleet = None
    return provider


SMALL = {
    "pitch_title": "Three podcast bookings",
    "pitch_body": "I find the shows, write the pitch you approve and send it.",
    "odds": 0.55,
    "total_ask_cents": 25_000,
    "research_links": [{"url": "https://example.test/shows", "note": "Who takes guests."}],
    "finalist_questions": [{"id": "q1", "title": "Warm or formal?", "format": "yes_no"}],
}


def test_a_clean_proposal_is_filed_with_no_second_door_call():
    api = _DoorApi({"ok": True, "problems": [], "trimmed": []})
    out = _door_provider(api).submit_proposal("t-1", dict(SMALL), "k")
    assert out["ok"] is True
    assert len(api.asked) == 1
    assert len(api.filed) == 1


def test_an_off_list_tool_is_mended_before_the_door_ever_sees_it():
    api = _DoorApi({"ok": True, "problems": [], "trimmed": []})
    _door_provider(api).submit_proposal(
        "t-1", dict(SMALL, tools_needed=["google-gmail", "gmail.message.send"]), "k"
    )
    # The door is asked about the MENDED proposal, and that is what is filed.
    assert api.asked[0]["tools_needed"] == ["gmail.message.send"]
    assert api.filed[0][1]["tools_needed"] == ["gmail.message.send"]


def test_the_doors_own_corrected_plan_is_filed_when_it_passes_unchanged():
    corrected = dict(SMALL, pitch_title="Three podcast bookings, start to finish")
    api = _DoorApi({
        "ok": False,
        "problems": [{"code": "REJ-01", "detail": "pitch_title is thin"}],
        "corrected_plan": corrected,
        "corrected_ok": True,
        "trimmed": [],
    })
    _door_provider(api).submit_proposal("t-1", dict(SMALL), "k")
    assert api.filed[0][1]["pitch_title"] == "Three podcast bookings, start to finish"


def test_the_mend_runs_before_the_door_so_the_old_shape_never_reaches_it():
    """The fix round is EARLIER than the door, and free: a question still
    carrying `fill` is put in the door's shape at home, so the door is asked
    about -- and the filing carries -- the proposal we actually mean to file.
    On the first 0.38.1 cycle this exact shape was refused REJ-15 on every
    question of every bid."""
    api = _DoorApi({"ok": True, "problems": [], "trimmed": []})
    out = _door_provider(api).submit_proposal(
        "t-1",
        dict(SMALL, finalist_questions=[{"format": "yes_no", "fill": "include background"}]),
        "k",
    )
    assert out["ok"] is True
    assert len(api.asked) == 1
    assert api.asked[0]["finalist_questions"] == [
        {"id": "q1", "title": "include background", "format": "yes_no"}
    ]
    assert "fill" not in api.filed[0][1]["finalist_questions"][0]


def test_a_mend_the_door_makes_possible_gets_one_more_free_call():
    """The safety net: a proposal that reached the filing road already mended
    still gets one more free look when the door names a problem, and the door
    is asked again only when something actually changed."""
    api = _DoorApi({"ok": True, "problems": [], "trimmed": []})
    provider = _door_provider(api)
    mended, left = provider._one_fix_round(
        "t-1",
        dict(SMALL, finalist_questions=[{"format": "contact_picker", "config": {"count": 2}},
                                        {"format": "yes_no", "title": "Warm?"}]),
        TOOLS_BRIEF,
        {"corrected_ok": False},
        [{"code": "REJ-15", "detail": "question 1 is a contact_picker"}],
    )
    assert [q["format"] for q in mended["finalist_questions"]] == ["yes_no"]
    assert left == []
    assert len(api.asked) == 1


def test_a_fix_round_with_nothing_to_mend_asks_the_door_no_second_time():
    api = _DoorApi()
    provider = _door_provider(api)
    problems = [{"code": "REJ-09", "detail": "the price is over the want's budget"}]
    same, left = provider._one_fix_round(
        "t-1", dict(SMALL), TOOLS_BRIEF, {"corrected_ok": False}, problems
    )
    assert same == dict(SMALL)
    assert left == problems
    assert api.asked == []


def test_a_missing_required_field_is_never_filed():
    """A bid the free door has already refused for a field nothing here can
    fill in is a round spent on a certainty."""
    api = _DoorApi({
        "ok": False,
        "problems": [{"code": "REJ-31",
                      "detail": "research_links is required (1..3 entries of {url, note})"}],
        "trimmed": [],
    })
    out = _door_provider(api).submit_proposal("t-1", dict(SMALL, research_links=[]), "k")
    assert out["ok"] is False
    assert out["error"] == "proposal_incomplete"
    assert out["terminal"] is False
    assert "research_links is required" in out["message"]
    assert api.filed == []


def test_a_problem_this_package_cannot_change_is_still_filed_anyway():
    """The door's own answer is the record for anything we cannot touch -- a
    thin pitch, a price the person will not take -- as long as the proposal is
    complete."""
    api = _DoorApi({
        "ok": False,
        "problems": [{"code": "REJ-09", "detail": "the price is over the want's budget"}],
        "trimmed": [],
    })
    out = _door_provider(api).submit_proposal("t-1", dict(SMALL), "k")
    assert out["ok"] is True
    assert len(api.filed) == 1


# ---------------------------------------------------------------------------
# STAGE TWO — the plan: the bench's form, filled in one reply
# ---------------------------------------------------------------------------
VERBS = ("finds", "prepares", "does", "posts", "buys", "books", "checks",
         "emails", "calls", "meeting", "waits", "confirms", "reviews")
PROOFS = ("text", "file", "link", "number")
OPTIONAL_PICKS = [
    {"field": "only_if", "says": "Run this step only when an earlier step came out a certain way."},
    {"field": "do_ask", "says": 'Only when who is "person". What the person does.'},
    {"field": "tool", "says": "A tool from this want's list, or any service by name."},
    {"field": "repeats", "says": "How often this step runs."},
    {"field": "bid_step", "says": "The number of the step in your own proposal this came from."},
]
SEND = ('PUT this door again with {"kind": "plan", "form": {"span_days": 14, '
        '"steps": [...]}}: one entry per step, and on each one answer the '
        "seven questions in `blanks`.")


def _blank_step():
    return {"verb": "", "do_line": "", "hand_over_line": "", "need_line": "",
            "declared_odds": None, "proof": "", "who": "agent", "only_if": None,
            "do_ask": None, "tool": "", "repeats": None}


def _q(path, question, choices=(), example=None, required=True):
    return {"path": path, "question": question, "note": question,
            "choices": list(choices), "example": example, "required": bool(required)}


def _step_questions(index, step):
    """The bench's seven, in the bench's own words (app/services/plan_form.py)."""
    n = index + 1
    base = f"form.steps.{index}"
    rows = []
    if not step.get("verb"):
        rows.append(_q(base + ".verb",
                       "Step {}, what do you do? Choose one: {}.".format(n, ", ".join(VERBS)),
                       VERBS, "finds"))
    if not step.get("do_line"):
        rows.append(_q(base + ".do_line",
                       f"Step {n}, what do you do, in one line? Up to 140 "
                       "characters.",
                       example="Find three podcasts that take outside guests"))
    if not step.get("hand_over_line"):
        rows.append(_q(base + ".hand_over_line",
                       f"Step {n}, what do you hand over at the end of this step? "
                       "Up to 140 characters.",
                       example="A list of three shows with the host name for each"))
    if step.get("need_line") is None:
        rows.append(_q(base + ".need_line",
                       f"Step {n}, what do you need from the person here?",
                       example="", required=False))
    if step.get("declared_odds") is None:
        rows.append(_q(base + ".declared_odds",
                       f"Step {n}, after this step, what are the odds the person ends "
                       "up with the thing they asked for?", example=0.4))
    if not step.get("proof"):
        rows.append(_q(base + ".proof",
                       "Step {}, what comes back as proof? Choose one: {}."
                       .format(n, ", ".join(PROOFS)), PROOFS, "text"))
    if not step.get("who"):
        rows.append(_q(base + ".who",
                       f'Step {n}, who does it? Choose "agent" for you, "person" '
                       'for them, or "service:<name>".',
                       ["agent", "person", "service:<name>"], "agent"))
    return rows


def _form_questions(form):
    rows = []
    if form.get("span_days") in (None, "", 0):
        rows.append(_q("form.span_days",
                       "How long do you stay with this person? A whole number of days.",
                       example=14, required=False))
    for index, step in enumerate(form.get("steps") or []):
        rows.extend(_step_questions(index, step))
    return rows


EXAMPLE_PLAN = {
    "span_days": 30,
    "steps": [
        {"verb": "finds", "do_line": "Find five people on TaskRabbit who can hold a line",
         "hand_over_line": "Five names with their hourly rate", "proof": "text",
         "who": "agent", "declared_odds": 0.2, "tool": "TaskRabbit"}
        for _ in range(17)
    ],
}

FILLED_STEP = {"verb": "finds", "do_line": "Find three podcasts that take outside guests",
               "hand_over_line": "Three shows with the host name for each",
               "need_line": "", "declared_odds": 0.3, "proof": "text", "who": "agent"}


class FormBench:
    """THE PLAN DOOR AS IT ANSWERS TODAY (bench draft_routes + plan_form).

    The first empty PUT is the outline round: `next: "form"` with the blank
    form, every question in plain words, the stance line and one finished
    example. The form comes back, the code expands it, and what is still
    unanswered comes back as blanks; a CONTENT problem comes back as a
    question, and the third time the same (path, code) comes back the door
    closes with `plan_failed`.
    """

    fleet = None

    def __init__(self, *, steps=3, content=None, example=None, trims=None):
        self.blank_steps = steps
        self.content = list(content or [])
        self.example = example if example is not None else EXAMPLE_PLAN
        self.trims = list(trims or [])
        self.form: dict | None = None
        self.puts: list[dict] = []
        self.patches: list[list[dict]] = []
        self.tries: dict[tuple[str, str], int] = {}
        self.closed: str | None = None
        self.plan_filed = None
        self.reads = 0

    # -- the answers ----------------------------------------------------
    def _expanded(self):
        return {"steps": [
            {"title": (step.get("do_line") or "")[:60],
             "outcome_promise": step.get("hand_over_line") or "",
             "acts": [{"kind": "document"}]}
            for step in (self.form or {}).get("steps") or []
        ]}

    def _ask_for_the_form(self):
        form = {"steps": [_blank_step() for _ in range(self.blank_steps)],
                "span_days": None}
        return {
            "ok": True, "kind": "plan", "next": "form",
            "proposal_id": "p-9",
            "your_proposal": ["1 APPROVE Find the shows"],
            "steps_you_bid": ["1 APPROVE Find the shows"],
            "the_person_answered": [{"ordinal": 1, "question": "Known for?",
                                     "answer": "Bread baking"}],
            "stance": "Approach this like a master who is hyper-creative and moves fast.",
            "example_plan": self.example,
            "form": form,
            "optional_picks": list(OPTIONAL_PICKS),
            "send": SEND,
            "draft": {},
            "blanks": _form_questions(form),
            "next_fix": {"path": "form", "current": None, "code": "FORM",
                         "detail": "This plan has not been written yet.", "fix": SEND},
            "problems": [], "bench_fixed": [], "remaining": 1, "ready": False,
            "rounds": {"used": 1, "left": 99, "cap": 100}, "closed": None,
        }

    def _form_answer(self):
        questions = _form_questions(self.form or {})
        content = [row for row in self.content
                   if self.tries.get((row["path"], row["code"]), 0) < 3]
        first = content[0] if content else (questions[0] if questions else None)
        remaining = len(content) + len([q for q in questions if q.get("required")])
        return {
            "ok": self.closed is None, "kind": "plan", "next": "form",
            "form": self.form, "optional_picks": list(OPTIONAL_PICKS),
            "stance": "Approach this like a master who is hyper-creative and moves fast.",
            "example_plan": self.example,
            "draft": self._expanded(),
            "blanks": questions,
            "next_fix": None if self.closed else (
                {"path": first["path"], "code": first.get("code", "blank"),
                 "current": None,
                 "fix": first.get("question") or first.get("note")}
                if first else None),
            "problems": content,
            "bench_fixed": list(self.trims),
            "remaining": remaining,
            "ready": self.closed is None and remaining == 0,
            "rounds": {"used": len(self.patches) + 1, "left": 99, "cap": 100},
            "closed": self.closed,
        }

    # -- the three doors -------------------------------------------------
    def read_draft(self, target_id, *, kind="bid"):
        self.reads += 1
        if self.form is None and not self.puts:
            return {"ok": False, "error": "no_draft", "status": 404,
                    "message": "there is no draft on this target."}
        return self._form_answer()

    def put_draft(self, target_id, body, *, kind="bid"):
        self.puts.append(json.loads(json.dumps(body or {})))
        form = (body or {}).get("form")
        if not isinstance(form, dict):
            return self._ask_for_the_form()
        self.form = json.loads(json.dumps(form))
        self._count()
        return self._form_answer()

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.patches.append(list(patches))
        for entry in patches:
            path = str(entry.get("path") or "")
            bare = path[5:] if path.startswith("form.") else path
            node = self.form
            parts = bare.split(".")
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node.setdefault(part, {})
            if isinstance(node, list):
                node[int(parts[-1])] = entry.get("value")
            else:
                node[parts[-1]] = entry.get("value")
        self._count()
        return self._form_answer()

    def _count(self):
        """THREE TRIES ON THE SAME (path, code), the bench's own rule."""
        for row in self.content:
            key = (row["path"], row["code"])
            self.tries[key] = self.tries.get(key, 0) + 1
            if self.tries[key] >= 3:
                self.closed = "plan_failed"

    def file_plan_from_draft(self, target_id, proposal_id, idempotency_key=""):
        self.plan_filed = (target_id, proposal_id, idempotency_key)
        return {"ok": True, "plan_revised": True, "proposal_id": proposal_id}


def _filled(count, **over):
    return {"steps": [dict(FILLED_STEP, **over) for _ in range(count)],
            "span_days": 14}


def test_the_form_is_filled_in_one_reply_and_put_back_whole():
    bench = FormBench()
    model = _model(_filled(3))

    out = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Get me on three podcasts"},
        idempotency_key="k")

    assert out["ok"] is True and out["filed"] is True, out
    # One empty open, then the form, and the form is the ONE model call.
    assert bench.puts[0] == {}
    assert list(bench.puts[1]) == ["form"]
    assert len(bench.puts[1]["form"]["steps"]) == 3
    assert bench.puts[1]["form"]["span_days"] == 14
    assert len(model.invocations) == 1
    assert bench.patches == []
    assert bench.plan_filed == ("t-1", "p-9", "k")


def test_the_form_ask_carries_the_stance_the_example_and_the_questions_once():
    bench = FormBench(steps=3)
    model = _model(_filled(3))

    DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Get me on three podcasts"},
        idempotency_key="k")

    ask = model.invocations[0]["messages"][0].content[0]["text"]
    # The person's own sentence about how, first thing.
    assert "hyper-creative and moves fast" in ask
    # One finished plan for a want like this one, as the bench filled it.
    assert "TaskRabbit" in ask
    # The seven questions ONCE, in the bench's words, with their choices and
    # their caps -- not seven times three.
    assert ask.count("what do you do, in one line") == 1
    assert "Up to 140 characters" in ask
    assert '"choices":["finds"' in ask.replace(", ", ",")
    # The picks a step MAY carry, and the door's own instruction.
    assert "only_if" in ask and "repeats" in ask
    assert "the_person_answered" in ask and "your_proposal" in ask


def test_what_the_form_reply_carries_is_the_forms_own_twelve_fields():
    read = read_form({"steps": [dict(FILLED_STEP, title="no", acts=[{"kind": "email"}],
                                     har_blocks=[])], "span_days": 21})
    assert set(read["steps"][0]) <= set(FORM_STEP_FIELDS)
    assert read["span_days"] == 21
    # An empty `need_line` is an ANSWER ("nothing from you"), not a blank.
    assert read["steps"][0]["need_line"] == ""
    assert read_form({"form": {"steps": [FILLED_STEP]}})["steps"]
    assert read_form({"nothing": True}) == {}


def test_a_form_that_never_came_back_gives_up_rather_than_putting_nothing():
    bench = FormBench()
    out = DraftLoop(_model({"sorry": "no"}), bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False and out["error"] == "no_form"
    assert len(bench.puts) == 1  # the empty open, and nothing else


def test_what_the_form_left_open_is_answered_a_step_at_a_time():
    """A reply that leaves a blank is not a failed reply: what is still open
    comes back as that step's own question, at the FORM's own path."""
    bench = FormBench(steps=2)
    model = _model(
        _filled(2, do_line=""),
        {"patches": [{"path": "form.steps.0.do_line", "value": "Find the shows"}]},
        {"patches": [{"path": "form.steps.1.do_line", "value": "Send the pitch"}]},
    )

    out = DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                      brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is True, out
    assert [call[0]["path"] for call in bench.patches] == [
        "form.steps.0.do_line", "form.steps.1.do_line"
    ]


def test_a_standing_form_is_never_written_over():
    """A PUT replaces the document and zeroes the rounds. Once a form stands,
    the next cycle answers what is left -- it does not fill the form again."""
    bench = FormBench(steps=2)
    DraftLoop(_model(_filled(2)), bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "x"},
        idempotency_key="k")
    puts_after_one = len(bench.puts)

    # A model with nothing scripted: an ask here would raise, and a PUT would
    # show up in the count.
    out = DraftLoop(_model(), bench).run("t-1", kind="plan", proposal_id="p-9",
                                         brief={"want": "x"}, idempotency_key="k")

    assert len(bench.puts) == puts_after_one
    assert out["ok"] is True and out["filed"] is True, out
    assert bench.reads >= 1


def test_a_blanks_round_is_asked_about_the_forms_step_not_the_expanded_one():
    """The expander stamps connect steps of its own in front of the ones the
    agent wrote, so `steps.3` of the document and step 4 of the form are not
    the same step."""
    bench = FormBench(steps=2)
    model = _model(
        _filled(2, do_line=""),
        {"patches": [{"path": "form.steps.0.do_line", "value": "Find the shows"}]},
        {"patches": [{"path": "form.steps.1.do_line", "value": "Send the pitch"}]},
    )

    DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                brief={"want": "x"}, idempotency_key="k")

    ask = model.invocations[1]["messages"][0].content[0]["text"]
    assert '"step_number":1' in ask
    assert "hand_over_line" in ask       # the form's step
    assert "outcome_promise" not in ask  # not the expanded document's


def test_a_correction_is_logged_and_never_asked_again(caplog):
    bench = FormBench(trims=[{"path": "form.steps.0.do_line", "from_chars": 188,
                              "to_chars": 140}])
    model = _model(_filled(2))

    with caplog.at_level("INFO", logger="toll_harness.draft"):
        out = DraftLoop(model, bench).run(
            "t-1", kind="plan", proposal_id="p-9", brief={"want": "x"},
            idempotency_key="k")

    assert out["ok"] is True
    assert "corrected" in caplog.text
    # ONE model call: the trim cost no round and no second ask.
    assert len(model.invocations) == 1


def test_three_content_misses_close_the_plan_and_that_is_the_end_of_it():
    """RULE 245: the door gives up out loud. The bench scores the agent
    "selected, could not present a plan", the person is told in red and asked
    to pick another, and the runtime never comes back to that want."""
    from toll_harness import cli

    bench = FormBench(content=[{"path": "form.steps.0.do_line",
                                "code": "does_not_address",
                                "question": "This plan does not answer the want."}])
    model = _model(
        _filled(2),
        {"patches": [{"path": "form.steps.0.do_line", "value": "Something else"}]},
        {"patches": [{"path": "form.steps.0.do_line", "value": "Something else again"}]},
    )

    out = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "x"}, idempotency_key="k")

    assert out["ok"] is False
    assert out["error"] == "plan_failed"
    assert bench.plan_filed is None
    # And the runtime treats it as terminal, like a closed want.
    assert "plan_failed" in cli._TERMINAL_DOOR_ERRORS


def test_the_content_question_reaches_the_model_in_the_benchs_own_words():
    bench = FormBench(content=[{"path": "form.steps.0.do_line",
                                "code": "person_does_the_work",
                                "question": "Step 1 asks the person to hand over the work."}])
    model = _model(
        _filled(2),
        {"patches": [{"path": "form.steps.0.do_line", "value": "I do it myself"}]},
        {"patches": [{"path": "form.steps.0.do_line", "value": "I really do it myself"}]},
    )

    DraftLoop(model, bench).run("t-1", kind="plan", proposal_id="p-9",
                                brief={"want": "x"}, idempotency_key="k")

    fix = model.invocations[1]["messages"][0].content[0]["text"]
    assert "Step 1 asks the person to hand over the work." in fix
    assert "person_does_the_work" in fix


def test_the_stance_and_the_example_are_the_doors_when_it_sends_them():
    answer = {"stance": "  Approach this   like a master.  ", "example_plan": {"steps": []},
              "example": {"steps": [{"old": True}]}}
    assert stance_of(answer) == "Approach this like a master."
    assert example_plan(answer) == {"steps": []}
    assert example_plan({"example": {"steps": [1]}}) == {"steps": [1]}
    assert stance_of({}) == "" and example_plan({}) is None


def test_a_form_answer_that_carries_a_document_is_not_an_ask_for_the_form():
    assert the_form_is_blank({"next": "form", "draft": {}}) is True
    assert the_form_is_blank({"next": "form", "draft": {"steps": [{"title": "x"}]}}) is False


# ---------------------------------------------------------------------------
# THE PROMPT BUDGET — a seventeen-step example must not blow the ask
# ---------------------------------------------------------------------------
def test_the_form_ask_stays_inside_the_prompt_budget_with_a_big_example():
    """Steven's test 4 is a seventeen-step plan, so the example shelf can hand
    back one. The ask sheds the example before it sheds the question, and the
    question always survives."""
    bench = FormBench(steps=17, example=EXAMPLE_PLAN)
    model = _model(_filled(17))

    DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9",
        brief={"want": "One million views, and a car"}, idempotency_key="k")

    tail = model.invocations[0]["messages"][0].content[0]["text"]
    # The form ask has a budget of its own: it replaces thirty small rounds,
    # and the bench's finished example is the thing that makes a weak model
    # work, so it must not be the first thing shed on the biggest plans.
    assert PROMPT_CHAR_BUDGET < len(tail) <= FORM_CHAR_BUDGET
    # The example survived, whole.
    assert tail.count("TaskRabbit") >= 17
    # The question survived whatever was shed.
    assert "what do you do, in one line" in tail
    assert "span_days" in tail
