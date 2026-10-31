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
from toll_harness.toll_bench import programs
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


def _said(problems):
    return " ".join(problem["message"] for problem in problems)


# ---------------------------------------------------------------------------
# 4. RULE 240 -- "have you find them", the person handing the question back.
# ---------------------------------------------------------------------------
RESEARCH = {"question_id": "who", "brief": "the owner of the roastery on Fifth"}


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


def test_the_pick_is_always_present_even_when_there_is_none():
    read = _provider(_Api(brief=_brief(plan_examples=[]))).read_brief("t-1")
    assert read["brief"]["nearest_program"] is None
    assert read["brief"]["program_to_copy"]










