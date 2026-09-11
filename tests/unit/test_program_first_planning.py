"""STEVEN, 2026-09-09 -- FIND THE NEAREST PROGRAM AND CHANGE WHAT DIFFERS.

"The test is can the AI use strategy to build the correct plan that can
execute", and "use our kit of parts to code (it's just a JSON file)". So the
brief stopped handing out a blank form alone. It carries `plan_examples`:
twelve worked programs, each a COMPLETE proposal that already passes the
validate door, with `wants_like` saying which wants each is for. The move is
to find the nearest program, copy it whole, change only what this want makes
different, compile at the validate door and file.

WHAT FORCED THIS FILE. A model handed twelve programs and told to use them
reads them, absorbs the flavour and writes its own plan anyway -- and from the
outside that run is indistinguishable from one that copied. So the pick is
made HERE, deterministically, before the model sees the brief, and what the
model did with it is logged as a diff against that program. One line, and the
foreman knows whether the run copied or composed.

Three more subjects ride with it:

  * the `calls` kind, whose runs name a tool, the account row it runs on, and
    where every argument came from -- refused REJ-41 when an argument comes
    from anywhere but an answer, an earlier run or a draft;
  * `contact_research`, the person answering the contact question with "find
    them for me" instead of picking anybody, which binds the send to a
    research run and forbids both a picker and an invented address;
  * `each`, when the person picked more than one contact.
"""
import copy

from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench import blocks, programs
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

# ---------------------------------------------------------------------------
# The twelve worked programs, as a brief hands them out.
# ---------------------------------------------------------------------------


def _proposal(*steps, **fields):
    payload = {
        "pitch_title": "A worked program",
        "pitch_body": "What this program does.",
        "steps": list(steps),
        "finalist_questions": [
            [
                {"id": "who", "format": "contact_picker", "title": "Who to?",
                 "required": True, "config": {"count": 1}},
            ]
        ],
    }
    payload.update(fields)
    return payload


def _step(title, *acts, **fields):
    step = {
        "ask": "APPROVE",
        "actor": "agent",
        "title": title,
        "outcome_promise": f"You get {title.lower()}.",
        "declared_odds": 0.6,
        "line_item_amount": 0,
        "acts": list(acts),
    }
    step.update(fields)
    return step


PLAN_EXAMPLES = [
    {
        "key": "01",
        "title": "Book a meeting with one person",
        "wants_like": ["book a meeting", "get time on a calendar", "schedule a call"],
        "proposal": _proposal(_step("Book the meeting")),
    },
    {
        "key": "02",
        "title": "Introduce two people by email",
        "wants_like": ["introduce me to someone", "warm intro", "connect me with"],
        "proposal": _proposal(_step("Write the introduction"), _step("Send it")),
    },
    {
        "key": "03",
        "title": "Chase an unanswered thread",
        "wants_like": ["follow up", "chase a reply", "nudge somebody"],
        "proposal": _proposal(_step("Write the follow-up")),
    },
    {
        "key": "04",
        "title": "Cold outreach to a list of people",
        "wants_like": [
            "invite people to a party",
            "send invites to a list of people",
            "reach out to a list",
        ],
        "proposal": _proposal(_step("Write the invitation"), _step("Send the invitations")),
    },
    {
        "key": "05",
        "title": "Fill in a form on somebody else's site",
        "wants_like": ["submit an application", "fill in a form", "register me"],
        "proposal": _proposal(_step("Fill the form in")),
    },
    {
        "key": "06",
        "title": "Buy a thing",
        "wants_like": ["buy me", "order a thing", "purchase"],
        "proposal": _proposal(_step("Buy it")),
    },
    {
        "key": "07",
        "title": "Make a video",
        "wants_like": ["make a video", "animate something", "edit footage"],
        "proposal": _proposal(_step("Make the video")),
    },
    {
        "key": "08",
        "title": "Write a document",
        "wants_like": ["write me a document", "draft a letter", "put together a report"],
        "proposal": _proposal(_step("Write the document")),
    },
    {
        "key": "09",
        "title": "Move files into connected storage",
        "wants_like": ["put the files somewhere", "upload to my drive", "organise storage"],
        "proposal": _proposal(_step("Move the files")),
    },
    {
        "key": "10",
        "title": "Book a table or a room",
        "wants_like": ["book a table", "reserve a room", "make a booking"],
        "proposal": _proposal(_step("Make the booking")),
    },
    {
        "key": "11",
        "title": "Watch something and report back",
        "wants_like": ["keep an eye on", "watch for a change", "tell me when"],
        "proposal": _proposal(_step("Watch it")),
    },
    {
        "key": "12",
        "title": "Local coffee shops, researched and written up",
        "wants_like": [
            "find a coffee shop near me",
            "recommend places to work from",
            "where should I get coffee",
        ],
        "proposal": _proposal(_step("Research the coffee shops")),
    },
]


def _brief(**fields):
    brief = {
        "target_id": "t-1",
        "round": 1,
        "want": "I want a good coffee shop near me to work from",
        "required_blocks": [],
        "required_blocks_reason": None,
        "plan_template": [],
        "block_templates": {},
        "bid_template": None,
        "bid_template_notes": None,
        "plan_examples": copy.deepcopy(PLAN_EXAMPLES),
        "your_bid": None,
    }
    brief.update(fields)
    return brief


# ---------------------------------------------------------------------------
# 1. THE PICK. Deterministic, explainable, and made before the model reads it.
# ---------------------------------------------------------------------------
def test_a_coffee_want_picks_the_coffee_program():
    pick = programs.nearest_program(_brief())
    assert pick["key"] == "12"
    assert "coffee" in pick["why"]
    assert pick["proposal"]["steps"][0]["title"] == "Research the coffee shops"


def test_a_party_invites_want_picks_the_outreach_program():
    pick = programs.nearest_program(
        _brief(want="I want to send invites to my party to a list of people")
    )
    assert pick["key"] in {"02", "04"}
    assert pick["key"] == "04"


def test_a_want_nothing_is_written_for_picks_nothing():
    assert programs.nearest_program(_brief(want="zzzz qqqq")) is None


def test_a_brief_with_no_programs_picks_nothing():
    assert programs.nearest_program(_brief(plan_examples=[])) is None
    assert programs.nearest_program({}) is None


def test_wants_like_outweighs_a_word_in_the_title():
    # "document" is in 08's title AND its wants_like; "letter" only in its
    # wants_like. Both should reach 08 and nothing else.
    assert programs.nearest_program(_brief(want="draft a letter"))["key"] == "08"


def test_a_tie_goes_to_the_shorter_program():
    examples = [
        {"key": "long", "title": "Send the notes", "wants_like": ["send notes"],
         "proposal": _proposal(_step("a"), _step("b"), _step("c"))},
        {"key": "short", "title": "Send the notes", "wants_like": ["send notes"],
         "proposal": _proposal(_step("a"))},
    ]
    pick = programs.nearest_program({"want": "send notes", "plan_examples": examples})
    assert pick["key"] == "short"
    assert "shorter program" in pick["why"]


def test_the_pick_is_the_same_every_time():
    first = programs.nearest_program(_brief())
    second = programs.nearest_program(_brief())
    assert first["key"] == second["key"]
    assert first["score"] == second["score"]


def test_the_score_is_two_a_wants_like_word_and_one_a_title_word():
    example = {
        "key": "x",
        "title": "Coffee mornings",
        "wants_like": ["find a coffee shop"],
        "proposal": _proposal(),
    }
    # "coffee" is in both, so it counts once, at the wants_like weight;
    # "mornings" is title-only.
    assert programs.score_program("coffee mornings", example) == 3


# ---------------------------------------------------------------------------
# 2. DID IT COPY, OR DID IT COMPOSE?
# ---------------------------------------------------------------------------
def test_a_reworded_copy_reads_as_copied():
    example = PLAN_EXAMPLES[11]
    filed = copy.deepcopy(example["proposal"])
    filed["pitch_title"] = "Three places to work from, walkable from you"
    filed["pitch_body"] = "I will visit the reviews and write each one up."
    filed["steps"][0]["title"] = "Find and write up three coffee shops"
    filed["steps"][0]["outcome_promise"] = "You get three places with hours."
    diff = programs.diff_from_program(filed, example)
    assert diff["verdict"] == programs.DIFF_COPIED
    assert diff["structural"] == []
    assert len(diff["changed"]) == 4
    assert diff["key"] == "12"
    assert "copied" in diff["line"]


def test_a_plan_that_shares_no_shape_reads_as_composed():
    example = PLAN_EXAMPLES[11]
    filed = _proposal(
        _step("Ask around"),
        _step("Write it up"),
        _step("Send it over"),
    )
    filed["finalist_questions"] = [[{"id": "q1", "format": "yes_no", "title": "Ok?"}]]
    diff = programs.diff_from_program(filed, example)
    assert diff["verdict"] == programs.DIFF_COMPOSED
    assert diff["structural"]


def test_the_diff_names_what_was_added_and_dropped():
    example = PLAN_EXAMPLES[11]
    filed = copy.deepcopy(example["proposal"])
    filed["steps"][0].pop("declared_odds")
    filed["steps"][0]["acts"] = [{"kind": "calls"}]
    diff = programs.diff_from_program(filed, example)
    assert "steps.0.declared_odds" in diff["removed"]
    assert "steps.0.acts.0.kind" in diff["added"]
    assert "steps.0.acts.0.kind" in diff["structural"]
    # A dropped odds number is one of the fields every plan rewrites.
    assert "steps.0.declared_odds" not in diff["structural"]


def test_no_program_is_an_honest_diff_and_not_a_crash():
    diff = programs.diff_from_program({"steps": []}, None)
    assert diff["verdict"] == programs.DIFF_COMPOSED
    assert diff["kept"] == 0


def test_the_diff_prints_as_one_json_line():
    diff = programs.diff_from_program(
        copy.deepcopy(PLAN_EXAMPLES[11]["proposal"]), PLAN_EXAMPLES[11]
    )
    line = programs.diff_json(diff)
    assert line.startswith("{") and "\n" not in line
    assert '"verdict":"copied"' in line


# ---------------------------------------------------------------------------
# 3. THE `calls` KIND: shape, order and provenance, as the door reads them.
#
# Checked against the bench's own `act_kinds/calls.py`: a run is EITHER a call
# or a wait, `row` is the ID of a connect_account block on the step (never a
# provider name), `each` BINDS a list, a draft must be declared in the act's
# own `drafts`, and `item` exists only inside a run that declares `each`.
# ---------------------------------------------------------------------------
QUESTIONS = [
    [
        {"id": "who", "format": "contact_picker", "title": "Who to?",
         "config": {"count": 1}},
        {"id": "tone", "format": "single_choice", "title": "Warm or formal?"},
    ]
]


def _calls_step(*runs, rows=("connect-google-gmail-1",), **act_fields):
    act = {
        "kind": "calls",
        "title": "Find them and write",
        "drafts": {"offer": "Would you like a coffee?"},
        "runs": list(runs),
    }
    act.update(act_fields)
    step = _step("Do the work", act)
    step["har_blocks"] = [
        {
            "id": row,
            "format": blocks.CONNECT_FORMAT,
            "config": {
                "grant_request": {
                    "connector": {"provider": "google-gmail", "actions": ["send"]}
                }
            },
        }
        for row in rows
    ]
    return step


GOOD_RUNS = (
    {"name": "look", "tool": "platform.research", "args": {"summary": "the roasters"}},
    {
        "name": "send",
        "tool": "composio:gmail/GMAIL_SEND_EMAIL",
        "row": "connect-google-gmail-1",
        "args": {"to": {"$from": "person.who"}, "body": {"$from": "draft.offer"}},
    },
    {"name": "answer", "wait": {"event": "email_reply", "of": "send",
                                "timeout_hours": 72}},
    {"name": "tell", "tool": "platform.notify",
     "args": {"to": "the person", "text": "They said {{answer.text}}"}},
)


def _problems(*runs, questions=QUESTIONS, **kwargs):
    return blocks.calls_problems([_calls_step(*runs, **kwargs)], questions)


def _said(problems):
    return " ".join(problem["message"] for problem in problems)


def test_a_good_program_passes_the_mirror():
    assert _problems(*GOOD_RUNS) == []


def test_an_argument_from_nowhere_is_refused():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["body"] = {"$from": "brief.want"}
    assert "names no run in this act" in _said(_problems(*runs))


def test_a_run_may_not_read_a_run_below_it():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[0]["args"] = {"summary": {"$from": "send.subject"}}
    assert "further down the card" in _said(_problems(*runs))


def test_a_run_may_not_read_itself():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[0]["args"] = {"summary": {"$from": "look.summary"}}
    assert "further down the card" in _said(_problems(*runs))


def test_a_question_nobody_was_asked_is_refused():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["to"] = {"$from": "person.nobody_asked"}
    assert "no question with id" in _said(_problems(*runs))


def test_with_no_questions_to_read_the_id_is_not_second_guessed():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["to"] = {"$from": "person.nobody_asked"}
    assert _problems(*runs, questions=None) == []


def test_person_alone_names_no_answer():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["to"] = {"$from": "person"}
    assert "names no answer" in _said(_problems(*runs))


def test_a_draft_nothing_declared_is_refused():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["body"] = {"$from": "draft.never_written"}
    assert "is not a draft this act carries" in _said(_problems(*runs))


def test_item_exists_only_inside_a_run_that_runs_each():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["body"] = {"$from": "item.name"}
    assert "only exists inside a run that declares `each`" in _said(_problems(*runs))
    runs[1]["each"] = {"$from": "person.who"}
    assert _problems(*runs) == []


def test_each_must_bind_a_list():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["each"] = "person.who"
    assert "must bind a list" in _said(_problems(*runs))
    runs[1]["each"] = True
    assert "must bind a list" in _said(_problems(*runs))
    runs[1]["each"] = {"$from": "person.who"}
    assert _problems(*runs) == []


def test_each_is_held_to_the_same_provenance():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["each"] = {"$from": "everybody"}
    assert "names no run in this act" in _said(_problems(*runs))


# --- the row is a block id on this step ------------------------------------
def test_a_tool_on_a_connector_names_its_row():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1].pop("row")
    assert "must name the `row`" in _said(_problems(*runs))


def test_a_registry_verb_needs_a_row_too():
    step_runs = ({"name": "send", "tool": "email.send", "args": {}},)
    assert "must name the `row`" in _said(_problems(*step_runs))


def test_a_platform_tool_carries_no_row():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[0]["row"] = "connect-google-gmail-1"
    assert "runs on no account" in _said(_problems(*runs))


def test_a_row_this_step_does_not_carry_is_refused():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["row"] = "connect-somewhere-else-9"
    said = _said(_problems(*runs))
    assert "no `connect_account` block with that id" in said
    assert "connect-google-gmail-1" in said


def test_which_service_carries_a_tool_is_never_guessed_here():
    # The row is on the step and the ID matches. Whether google-gmail can
    # carry a composio:gmail tool is a FAMILY table that lives on the server.
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["tool"] = "composio:outlook/OUTLOOK_SEND_EMAIL"
    assert _problems(*runs) == []


# --- tools are never judged ------------------------------------------------
def test_no_tool_is_ever_judged_here():
    for tool in (
        "composio:notion/NOTION_ADD_PAGE",
        "key:twilio/send_sms",
        "mcp:weather/forecast",
        "email.send",
    ):
        runs = ({"name": "one", "tool": tool, "row": "connect-google-gmail-1",
                 "args": {}},)
        assert _problems(*runs) == [], tool
    for tool in blocks.PLATFORM_TOOLS:
        assert _problems({"name": "one", "tool": tool, "args": {}}) == [], tool


def test_a_lane_key_with_no_action_is_a_shape_problem():
    runs = ({"name": "one", "tool": "composio:gmail",
             "row": "connect-google-gmail-1"},)
    assert "must read composio:<service>/<action>" in _said(_problems(*runs))


def test_a_platform_verb_that_is_not_one_of_the_four_is_refused():
    assert "is not a platform tool" in _said(
        _problems({"name": "one", "tool": "platform.telepathy"})
    )


def test_a_tool_key_with_punctuation_and_no_lane_is_refused():
    runs = ({"name": "one", "tool": "gmail/send", "row": "connect-google-gmail-1"},)
    assert "not a tool this platform knows" in _said(_problems(*runs))


# --- a run is a call or a wait --------------------------------------------
def test_a_run_is_a_call_or_a_wait_and_never_both():
    runs = ({"name": "one", "tool": "platform.notify", "args": {},
             "wait": {"event": "email_reply", "of": "one"}},)
    assert "both a call and a wait" in _said(_problems(*runs))


def test_a_run_that_is_neither_is_refused():
    assert "needs a `tool` (or a `wait`)" in _said(_problems({"name": "one"}))


def test_a_wait_names_a_known_event_and_a_run_above_it():
    runs = (
        {"name": "one", "tool": "platform.notify", "args": {}},
        {"name": "two", "wait": {"event": "telepathy", "of": "three",
                                 "timeout_hours": 0}},
    )
    said = _said(_problems(*runs))
    assert "wait.event must be one of" in said
    assert "not a run ABOVE this one" in said
    assert "between 1 and" in said


def test_a_person_answer_wait_may_name_no_run():
    runs = ({"name": "one", "wait": {"event": "person_answer"}},)
    assert _problems(*runs) == []


def test_another_wait_must_name_the_run_it_waits_on():
    runs = ({"name": "one", "wait": {"event": "email_reply"}},)
    assert "must name the run whose answer" in _said(_problems(*runs))


# --- the rest of the grammar ----------------------------------------------
def test_a_recipient_is_never_a_typed_address():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["to"] = "sam@example.com"
    assert "carries a raw address" in _said(_problems(*runs))


def test_an_address_in_a_body_is_not_a_recipient():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["body"] = "reply to sam@example.com if you like"
    assert _problems(*runs) == []


def test_a_platform_tool_is_not_an_outward_send():
    # `_outward` is the registry's own resource_kind and a platform verb is
    # never one, so platform.notify's own `to` is left alone.
    runs = ({"name": "one", "tool": "platform.notify",
             "args": {"to": "sam@example.com", "text": "hi"}},)
    assert _problems(*runs) == []


def test_an_argument_the_platform_fills_may_not_be_named():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["mailbox_id"] = "inbox-1"
    assert "The platform fills that" in _said(_problems(*runs))


def test_a_handlebars_binding_is_read_the_same_way():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[3]["args"]["text"] = "They said {{nobody.text}}"
    assert "names no run in this act" in _said(_problems(*runs))


def test_a_source_is_the_whole_argument_or_none_of_it():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["body"] = {"$from": "draft.offer", "or": "hello"}
    assert "the ONLY key" in _said(_problems(*runs))


def test_a_from_that_is_not_a_path_is_refused():
    runs = copy.deepcopy(list(GOOD_RUNS))
    runs[1]["args"]["body"] = {"$from": "Draft Offer!"}
    assert "must be a path" in _said(_problems(*runs))


def test_two_runs_may_not_share_a_name():
    runs = (
        {"name": "one", "tool": "platform.notify", "args": {}},
        {"name": "one", "tool": "platform.notify", "args": {}},
    )
    assert "both called" in _said(_problems(*runs))


def test_a_run_name_is_lower_case_letters_digits_and_underscores():
    assert "lower case letters" in _said(
        _problems({"name": "Send It", "tool": "platform.notify", "args": {}})
    )


def test_an_act_that_runs_nothing_does_nothing():
    step = _step("Do the work", {"kind": "calls", "title": "Nothing", "runs": []})
    assert "an ORDERED LIST of calls" in _said(blocks.calls_problems([step], QUESTIONS))


def test_an_act_with_no_title_is_named():
    step = _calls_step(*GOOD_RUNS)
    step["acts"][0]["title"] = ""
    assert "carries a `title`" in _said(blocks.calls_problems([step], QUESTIONS))


def test_a_step_with_no_calls_act_draws_nothing_new():
    assert blocks.calls_problems([_step("Write it up")], QUESTIONS) == []


# ---------------------------------------------------------------------------
# 4. RULE 240 -- "have you find them", the person handing the question back.
# ---------------------------------------------------------------------------
RESEARCH = {"question_id": "who", "brief": "the owner of the roastery on Fifth"}


def test_the_research_answer_binds_a_calls_send_to_the_research_run():
    step = _calls_step(*copy.deepcopy(list(GOOD_RUNS)))
    step["acts"][0]["runs_on"] = "person"
    step["acts"][0]["runs"][1]["args"]["to"] = "sam@example.com"
    plan = _proposal(step)
    bound_plan, bound = blocks.bind_contact_research(plan, RESEARCH)
    assert bound
    send = bound_plan["steps"][0]["acts"][0]["runs"][1]
    assert send["args"]["to"] == {"$from": "look.contact"}


def test_a_calls_act_with_no_research_run_is_not_given_one():
    # A platform.research run's own arguments ARE the research, and the
    # research has not been done. The harness writes no arguments it made up.
    act = {
        "kind": "calls",
        "title": "Write and send",
        "runs_on": "person",
        "runs": [
            {"name": "send", "tool": "composio:gmail/GMAIL_SEND_EMAIL",
             "row": "connect-google-gmail-1",
             "args": {"to": "sam@example.com"}}
        ],
    }
    plan = _proposal(_step("Reach out", act))
    bound_plan, bound = blocks.bind_contact_research(plan, RESEARCH)
    assert bound == []
    assert bound_plan is plan


def test_a_legacy_act_says_where_the_contact_comes_from_and_drops_the_address():
    act = {"kind": "email", "runs_on": "person", "with": "sam@example.com",
           "subject": "Hello", "body": "Hi"}
    plan = _proposal(_step("Send it", act))
    bound_plan, bound = blocks.bind_contact_research(plan, RESEARCH)
    sent = bound_plan["steps"][0]["acts"][0]
    assert sent[blocks.CONTACT_FROM_FIELD] == "research"
    assert "with" not in sent
    assert "contact_ref" not in sent
    assert bound


def test_an_act_already_bound_is_left_alone():
    act = {"kind": "email", "runs_on": "person", "contact_from": "research"}
    plan = _proposal(_step("Send it", act))
    bound_plan, bound = blocks.bind_contact_research(plan, RESEARCH)
    assert bound == []
    assert bound_plan is plan


def test_no_research_answer_binds_nothing():
    plan = _proposal(_step("Send it", {"kind": "email", "runs_on": "person"}))
    assert blocks.bind_contact_research(plan, None) == (plan, [])
    assert blocks.contact_research_of({"contact_research": {"question_id": "who"}}) is None
    assert blocks.contact_research_of({"contact_research": None}) is None
    assert blocks.contact_research_of({}) is None


def test_no_bid_ever_gets_a_picker_research_answer_or_not():
    # Rule 238 corrected (2026-09-11): the contact book is not a question at
    # all, so there is nothing here for a research answer to turn off. The
    # step repair leaves the questions exactly as the model wrote them.
    plan = _proposal(_step("Send it", {"kind": "email", "runs_on": "person"}))
    plan["finalist_questions"] = [
        [{"id": "q1", "format": "yes_no", "title": "Ok?", "config": {}}]
    ]
    for _ in range(2):
        merged, inserted = blocks.merge_required_blocks(plan, [], [])
        assert inserted == []
        assert blocks.picker_position(merged["finalist_questions"]) is None


# ---------------------------------------------------------------------------
# 5. `each`: N people, one outreach.
# ---------------------------------------------------------------------------
CONTACTS = [
    {"contact_ref": "c-1", "name": "Sam"},
    {"contact_ref": "c-2", "name": "Ada"},
    {"contact_ref": "c-3", "name": "Lee"},
]


def test_a_calls_outreach_takes_the_each_form():
    act = {
        "kind": "calls",
        "title": "Send the invitations",
        "runs_on": "person",
        "runs": [
            {"name": "send", "tool": "composio:gmail/GMAIL_SEND_EMAIL",
             "row": "connect-google-gmail-1",
             "args": {"to": {"$from": "person.who"}}}
        ],
    }
    plan = _proposal(_step("Send", act), finalist_questions=QUESTIONS)
    spread_plan, spread = blocks.spread_over_contacts(plan, CONTACTS)
    assert spread
    assert spread_plan["steps"][0]["acts"][0]["runs"][0]["each"] == {
        "$from": "person.who"
    }


def test_the_each_form_the_harness_writes_passes_its_own_mirror():
    act = {
        "kind": "calls",
        "title": "Send the invitations",
        "runs_on": "person",
        "runs": [
            {"name": "send", "tool": "composio:gmail/GMAIL_SEND_EMAIL",
             "row": "connect-google-gmail-1",
             "args": {"to": {"$from": "person.who"}}}
        ],
    }
    step = _step("Send", act)
    step["har_blocks"] = [
        {"id": "connect-google-gmail-1", "format": blocks.CONNECT_FORMAT, "config": {}}
    ]
    plan = _proposal(step, finalist_questions=QUESTIONS)
    spread_plan, _ = blocks.spread_over_contacts(plan, CONTACTS)
    assert blocks.calls_problems(spread_plan["steps"], QUESTIONS) == []


def test_with_no_picker_to_bind_the_each_is_not_invented():
    act = {
        "kind": "calls",
        "title": "Send",
        "runs_on": "person",
        "runs": [{"name": "send", "tool": "platform.notify", "args": {}}],
    }
    plan = _proposal(_step("Send", act))
    plan["finalist_questions"] = [[{"id": "q1", "format": "yes_no", "title": "Ok?"}]]
    assert blocks.spread_over_contacts(plan, CONTACTS) == (plan, [])


def test_a_legacy_outreach_is_filed_once_per_contact():
    act = {"kind": "email", "runs_on": "person", "subject": "Party", "body": "Come"}
    plan = _proposal(_step("Send", act))
    spread_plan, spread = blocks.spread_over_contacts(plan, CONTACTS)
    acts = spread_plan["steps"][0]["acts"]
    assert [act["contact_ref"] for act in acts] == ["c-1", "c-2", "c-3"]
    assert [act["with_name"] for act in acts] == ["Sam", "Ada", "Lee"]
    assert "once per contact" in spread[0]


def test_the_bench_s_own_reference_shape_is_what_gets_addressed():
    # `private_contacts.reference()` on the bench: names only, never an
    # address. {"contact_ref": id, "label": name}.
    picks = [{"contact_ref": "c-1", "label": "Sam"}, {"contact_ref": "c-2", "label": "Ada"}]
    act = {"kind": "email", "runs_on": "person"}
    plan = _proposal(_step("Send", act))
    spread_plan, spread = blocks.spread_over_contacts(plan, picks)
    acts = spread_plan["steps"][0]["acts"]
    assert [act["with_name"] for act in acts] == ["Sam", "Ada"]
    assert spread


def test_one_contact_is_not_a_spread():
    act = {"kind": "email", "runs_on": "person"}
    plan = _proposal(_step("Send", act))
    assert blocks.spread_over_contacts(plan, CONTACTS[:1]) == (plan, [])
    assert blocks.spread_over_contacts(plan, None) == (plan, [])


def test_a_contact_with_nothing_to_address_it_by_is_not_invented():
    act = {"kind": "email", "runs_on": "person"}
    plan = _proposal(_step("Send", act))
    assert blocks.spread_over_contacts(plan, [{"name": "Sam"}, {"name": "Ada"}]) == (plan, [])


def test_an_act_that_already_names_its_contact_is_left_alone():
    act = {"kind": "email", "runs_on": "person", "contact_ref": "c-9"}
    plan = _proposal(_step("Send", act))
    assert blocks.spread_over_contacts(plan, CONTACTS) == (plan, [])


# ---------------------------------------------------------------------------
# 6. The provider: the pick rides the brief, and the door's own refusals.
# ---------------------------------------------------------------------------
class _Api:
    def __init__(self, *, brief=None, refuse=None):
        self.brief = brief if brief is not None else _brief()
        self.refuse = refuse
        self.submissions = []

    def target_brief(self, target_id):
        return {"ok": True, "brief": copy.deepcopy(self.brief)}

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.submissions.append((target_id, proposal, idempotency_key))
        if self.refuse is not None and len(self.submissions) == 1:
            raise self.refuse
        return {"ok": True, "proposal_id": "p-1"}

    def act_kinds(self):
        return {"kinds": {}}

    def proposal_schema(self):
        return {"type": "object"}

    def me(self):
        return {"ok": True, "reachability_test": {"reachable": True}}


def _provider(api):
    provider = BookOfHousesTollBenchProvider(api)
    provider.validate_proposal = lambda proposal: {"ok": True, "problems": []}
    return provider


def _refusal(rej, detail):
    return BookOfHousesApiError(
        422, rej, detail, body={"ok": False, "rej": rej, "detail": detail}
    )


def test_the_brief_hands_over_the_program_to_copy():
    read = _provider(_Api()).read_brief("t-1")
    brief = read["brief"]
    assert brief["nearest_program"]["key"] == "12"
    assert "Copy its proposal WHOLE" in brief["program_to_copy"]
    assert brief["contact_research_note"] == ""


def test_the_pick_is_always_present_even_when_there_is_none():
    read = _provider(_Api(brief=_brief(plan_examples=[]))).read_brief("t-1")
    assert read["brief"]["nearest_program"] is None
    assert read["brief"]["program_to_copy"]


def test_the_research_note_rides_the_brief_when_the_person_asked():
    read = _provider(_Api(brief=_brief(contact_research=RESEARCH))).read_brief("t-1")
    assert "hand the question back" in read["brief"]["contact_research_note"].lower()


def test_the_filed_bid_binds_the_research_and_spreads_the_contacts():
    api = _Api(
        brief=_brief(
            contact_research=RESEARCH,
            selected_contacts=CONTACTS,
            want="I want invitations sent to the people on my list",
        )
    )
    plan = _proposal(
        _step("Send it", {"kind": "email", "runs_on": "person", "with": "sam@example.com"})
    )
    result = _provider(api).submit_proposal("t-1", plan, "idem-1")
    assert result["ok"] is True
    filed = api.submissions[0][1]
    acts = filed["steps"][0]["acts"]
    # Bound to the research the person asked for, and no address survived.
    assert acts[0][blocks.CONTACT_FROM_FIELD] == "research"
    assert all("with" not in act for act in acts)
    # ...and one act per picked contact.
    assert [act["contact_ref"] for act in acts] == ["c-1", "c-2", "c-3"]


def test_a_rej41_with_research_to_bind_is_repaired_once():
    # The bid path binds what it can before filing, so this repair is the
    # BACKSTOP: a plan that reached the door still unbound -- the door's own
    # corrected_plan replaced it, an earlier repair rewrote its steps -- gets
    # the binding put back on and is re-filed once.
    provider = _provider(_Api())
    error = _refusal("REJ-41", "run send: `to` names no source")
    plan = _proposal(
        _step("Send it", {"kind": "email", "runs_on": "person", "with": "sam@example.com"})
    )
    fixed, bound = provider._bind_the_research_after(
        "t-1", error, plan, _brief(contact_research=RESEARCH)
    )
    assert bound
    sent = fixed["steps"][0]["acts"][0]
    assert sent[blocks.CONTACT_FROM_FIELD] == "research"
    assert "with" not in sent


def test_a_rej41_on_a_want_with_no_research_repairs_nothing():
    provider = _provider(_Api())
    error = _refusal("REJ-41", "run send quotes a run that has not happened")
    plan = _proposal(_step("Send it", {"kind": "email", "runs_on": "person"}))
    fixed, bound = provider._bind_the_research_after("t-1", error, plan, _brief())
    assert bound == []
    assert fixed is plan


def test_a_rej40_on_a_research_want_binds_to_the_research_run():
    # The ONE repair left on REJ-40 since rule 238 was corrected: this person
    # said "find them for me", so the send reads its recipient off a research
    # run of the agent's own. No question is added, ever.
    provider = _provider(_Api())
    error = _refusal("REJ-40", "an act on the person's own account names nobody")
    plan = _proposal(_step("Send it", {"kind": "email", "runs_on": "person"}))
    plan["finalist_questions"] = [
        [{"id": "q1", "format": "yes_no", "title": "Ok?", "config": {}}]
    ]
    fixed, bound = provider._bind_who_after(
        "t-1", error, plan, _brief(contact_research=RESEARCH)
    )
    assert bound
    assert fixed["steps"][0]["acts"][0][blocks.CONTACT_FROM_FIELD] == "research"
    assert blocks.picker_position(fixed["finalist_questions"]) is None


def test_a_rej40_with_no_research_answer_adds_nothing_and_is_not_re_filed():
    # It used to put the brief's own picker on and file again. That line is
    # what the bench refused on every person-reaching want on 2026-09-11.
    provider = _provider(_Api())
    error = _refusal("REJ-40", "an act on the person's own account names nobody")
    plan = _proposal(_step("Send it", {"kind": "email", "runs_on": "person"}))
    fixed, bound = provider._bind_who_after("t-1", error, plan, _brief())
    assert bound is None
    assert fixed is plan


def test_a_rej40_with_research_and_nothing_to_bind_is_not_re_filed():
    provider = _provider(_Api())
    error = _refusal("REJ-40", "an act on the person's own account names nobody")
    plan = _proposal(_step("Send it", {"kind": "email", "runs_on": "person",
                                       "contact_from": "research"}))
    fixed, bound = provider._bind_who_after(
        "t-1", error, plan, _brief(contact_research=RESEARCH)
    )
    assert bound is None
    assert fixed is plan


def test_a_rej41_with_nothing_to_bind_is_the_door_s_own_answer():
    api = _Api(refuse=_refusal("REJ-41", "run send quotes a run that has not happened"))
    plan = _proposal(_step("Do it", {"kind": "calls", "title": "Work", "runs": []}))
    result = _provider(api).submit_proposal("t-1", plan, "idem-3")
    assert result["ok"] is False
    assert result["error"] == "argument_provenance"
    assert result["terminal"] is False
    assert "has not happened" in result["detail"]
    assert result["fix"] == blocks.ARGUMENT_PROVENANCE_SENTENCE
    assert len(api.submissions) == 1


def test_the_filing_logs_the_diff_against_the_program(caplog):
    api = _Api()
    plan = copy.deepcopy(PLAN_EXAMPLES[11]["proposal"])
    plan["pitch_title"] = "Three places, walkable"
    with caplog.at_level("INFO", logger="toll_harness.toll_bench"):
        _provider(api).submit_proposal("t-1", plan, "idem-5")
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "filed against 12" in logged
    assert "copied" in logged
