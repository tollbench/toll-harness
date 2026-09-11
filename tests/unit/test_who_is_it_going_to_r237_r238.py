"""RULE 238 CORRECTED (Steven Ochs, 2026-09-11) -- WHO IS IT GOING TO IS A STEP.

A person is contacted through their own contact book and never through a loose
address. THE BOOK CAME OUT OF THE QUESTIONS. Who this goes to is a STEP the
BENCH stamps into the plan -- ask PROVIDE, control `contact_picker`, title
"Who should this go to?", `count` a floor and never a ceiling -- in front of
the first step that reaches anybody, after the person has chosen this agent.
The agent never writes that step and never sees a picker on the proposal form.

WHAT FORCED THE CORRECTION, and this rewrite. One fleet unit filed a plan whose
step 2 was "finds two friends from the contact list provided by the person" --
a step whose whole work was to hand back the person's own pick. Every filing
of it was refused as a stand-in, and it looped every forty seconds at 60-76k
tokens a try. The ask was in the wrong place. So the bench stopped taking a
contact question on a proposal at all (REJ-15: "The contact book is not one of
your questions...") -- and on the first fleet cycle after that, EVERY bid that
reached a person was refused, because THIS PACKAGE was the thing adding the
picker: `merge_contact_picker` copied the brief's published block onto the bid.

These tests now hold the other law: the harness never adds, suggests or
validates a contact_picker on a PROPOSAL, and what it keeps is reading the
answer back -- `person.<id>` pointers and the seats a picked list fans out
over.

The third subject is the provider key. `composio:<toolkit slug>` and
`key:<service slug>` are lanes the PLATFORM resolves; the harness carries them
through untouched and never manufactures a refusal about one, because the
bench matches a row by FAMILY and that table lives on the server.
"""
import copy

from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench import blocks, draft
from toll_harness.toll_bench.book_of_houses import (
    BookOfHousesTollBenchProvider,
    finalist_question_problems,
)

# --------------------------------------------------------------------------
# The bench's own shapes, as a live brief hands them out.
# --------------------------------------------------------------------------

# The block the bench used to publish as question three, and now stamps onto
# a STEP of the plan instead. Kept here as the shape nothing may put on a bid.
PUBLISHED_PICKER = {
    "id": "who",
    "format": "contact_picker",
    "title": "",
    "required": True,
    "config": {"count": 1},
}

BID_TEMPLATE = {
    "model_declared": "",
    "pitch_title": "",
    "pitch_body": "",
    "finalist_questions": [
        [
            {"id": "q1", "format": "single_choice", "title": "", "required": True,
             "config": {"options": []}},
            {"id": "q2", "format": "yes_no", "title": "", "required": True,
             "config": {}},
            PUBLISHED_PICKER,
            {"id": "q4", "format": "short_answer", "title": "", "required": True,
             "config": {}},
        ]
    ],
}

# `_contact_notes`: the picker's two lines, and the words it offers for the
# title the agent has not written yet.
BID_TEMPLATE_NOTES = [
    {"path": "finalist_questions[0][2].title",
     "note": "WHO IS IT GOING TO. This one is the contact_picker...",
     "example": "Who should these go to?", "required": True},
    {"path": "finalist_questions[0][2].config.count",
     "note": "How many people this plan reaches, on the ONE picker...",
     "example": 1, "required": False},
]

GMAIL_ROW = {
    "id": "connect-google-gmail-1",
    "ask": "grant",
    "format": "connect_account",
    "required": True,
    "title": "Connect your email",
    "config": {
        "grant_request": {
            "kind": "oauth_connection",
            "what": "Your Gmail account",
            "why": "So the message goes out from your own address",
            "scope": "send only the messages you approve",
            "until": "target_end",
            "exposure": "agent_acts_through_connection",
            "connector": {
                "provider": "google-gmail",
                "actions": ["gmail.message.send"],
                "resources": {"mailbox_ids": ["primary"]},
            },
        },
        "fallback": "agent_account",
        "fallback_note": "The message goes out from the Book of Houses mailbox.",
    },
}

# The email block, on the PERSON's own lane: one step, the Gmail row and the
# act on the same card (rule 236), and the act names nobody (rule 238 -- the
# person's pick fills `contact_ref`).
EMAIL_STEP = {
    "ask": "APPROVE",
    "actor": "agent",
    "title": "Send the thank-you notes",
    "outcome_promise": "Each person gets your note from your own address.",
    "har_blocks": [
        GMAIL_ROW,
        {"id": "message-approved", "ask": "approve", "format": "review_approve",
         "required": True, "title": "Approve the message"},
    ],
    "acts": [
        {
            "kind": "email",
            "runs_on": "person",
            "purpose": "<one line>",
            "subject": "<subject>",
            "body": "<your words>",
        }
    ],
    "rounds": 1,
    "declared_odds": "<fill 0.05..0.99>",
    "declared_odds_reason": "<why that number>",
    "person_minutes": 3,
    "line_item_amount": 0,
    "agent_court_estimate": 1,
    "examples": [],
    "materials": [],
}

WORK_STEP = {
    "ask": "APPROVE",
    "actor": "agent",
    "title": "Draft the notes",
    "outcome_promise": "You get a draft of every note before anything goes out.",
    "declared_odds": 0.6,
    "line_item_amount": 0,
    "acts": [],
}

# The four the model writes for itself: the form's shapes, its own words, and
# no picker anywhere -- because the model does not know the form was holding
# one for it.
MODEL_QUESTIONS = [
    {"id": "q1", "format": "single_choice", "title": "Warm or formal?",
     "required": True,
     "config": {"options": [{"id": "warm", "label": "Warm"},
                            {"id": "formal", "label": "Formal"}]}},
    {"id": "q2", "format": "yes_no", "title": "Should I sign them from you?",
     "required": True, "config": {}},
    {"id": "q3", "format": "yes_no", "title": "Send them all on the same day?",
     "required": True, "config": {}},
    {"id": "q4", "format": "short_answer", "title": "Anything to mention?",
     "required": True, "config": {}},
]


def _plan(*steps, questions=None):
    return {
        "steps": list(steps),
        "pitch_title": "Thank-you notes, from your own address",
        "pitch_body": "x",
        "finalist_questions": [
            copy.deepcopy(MODEL_QUESTIONS if questions is None else questions)
        ],
    }


def _group(proposal):
    return proposal["finalist_questions"][0]


def _formats(proposal):
    return [q.get("format") for q in _group(proposal)]


class _Api:
    """A live bench's answers, contract 3.0: a blank `plan_template`, the
    blocks in `block_templates`, and the whole bid payload in `bid_template`
    with one line per blank in `bid_template_notes`."""

    def __init__(self, *, bid_template=BID_TEMPLATE, notes=BID_TEMPLATE_NOTES,
                 refuse=None, required=("email",)):
        self.bid_template = bid_template
        self.notes = notes
        self.refuse = refuse
        self.required = list(required)
        self.submissions = []
        self.plans = []

    def target_brief(self, target_id):
        return {
            "ok": True,
            "brief": {
                "target_id": target_id,
                "round": 1,
                "want": "I want thank-you notes sent to the people who helped",
                "required_blocks": self.required,
                "required_blocks_reason": None,
                "plan_template": [EMAIL_STEP],
                "block_templates": {"email": [EMAIL_STEP]},
                "bid_template": self.bid_template,
                "bid_template_notes": self.notes,
                "your_bid": None,
            },
        }

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.submissions.append((target_id, proposal, idempotency_key))
        if self.refuse is not None and len(self.submissions) == 1:
            raise self.refuse
        return {"ok": True, "proposal_id": "p-1"}

    def submit_informed_plan(self, target_id, proposal_id, plan, idempotency_key):
        self.plans.append((target_id, plan, idempotency_key))
        if self.refuse is not None and len(self.plans) == 1:
            raise self.refuse
        return {"ok": True}

    def act_kinds(self):
        return {"kinds": {"email": {"declaration": {}, "template": EMAIL_STEP,
                                    "requires_grants": ["google-gmail"]}}}

    def proposal_schema(self):
        return {"type": "object"}

    def me(self):
        return {"ok": True, "reachability_test": {"reachable": True}}

    def current_step(self, deal_id):
        return {}


def _provider(api):
    provider = BookOfHousesTollBenchProvider(api)
    # The JSON schema is production's business, not this file's subject.
    provider.validate_proposal = lambda proposal: {"ok": True, "problems": []}
    return provider


def _refusal(rej, detail):
    return BookOfHousesApiError(422, rej, detail, body={"ok": False, "rej": rej,
                                                        "detail": detail})


def _picker(**overrides):
    block = copy.deepcopy(PUBLISHED_PICKER)
    block["title"] = "Who should these go to?"
    block.update(overrides)
    return block


def _questions(*blocks_):
    return [list(blocks_)]


def _messages(problems):
    return " ".join(problem["message"] for problem in problems)


# ---------------------------------------------------------------------------
# 1. THE MIRROR REFUSES A CONTACT QUESTION ON A PROPOSAL
# ---------------------------------------------------------------------------
def test_a_contact_picker_on_a_proposal_is_refused():
    # The bench's REJ-15, mirrored at home so the bid is never spent on it.
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1], _picker(),
                   MODEL_QUESTIONS[3])
    )
    assert "the contact book is not one of your questions" in _messages(problems)
    assert "on a step of the plan" in _messages(problems)


def test_a_picker_is_refused_however_it_is_dressed():
    # A count, a contact of its own, an ask of its own: none of it matters any
    # more. The shape is refused, not its fields.
    for dressed in (
        _picker(config={"count": 80}),
        _picker(config={"count": 5, "options": []}),
        _picker(email="ruby@example.com"),
        _picker(ask="APPROVE"),
    ):
        problems = finalist_question_problems(
            _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1], dressed,
                       MODEL_QUESTIONS[3])
        )
        assert "the contact book is not one of your questions" in _messages(problems)


def test_the_three_shapes_that_are_still_questions_pass():
    problems = finalist_question_problems(
        _questions(
            MODEL_QUESTIONS[0],
            {"id": "q2", "format": "short_answer", "title": "Anything to add?"},
            MODEL_QUESTIONS[2],
            {"id": "q4", "format": "short_answer", "title": "Anything to avoid?"},
        )
    )
    assert problems == []


def test_a_picker_is_refused_wherever_it_sits():
    # Not a seat rule. The brief used to keep the third seat for it; there is
    # no seat now, so every position is the same refusal.
    for seat in range(4):
        group = [
            MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
            MODEL_QUESTIONS[2], MODEL_QUESTIONS[3],
        ]
        group[seat] = _picker()
        problems = finalist_question_problems(_questions(*group))
        assert "the contact book is not one of your questions" in _messages(problems), seat


def test_two_pickers_are_two_refusals_not_a_count_rule():
    # "Use one contact_picker" was the old sentence. One is no longer a legal
    # number, so each of them is refused where it stands.
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], _picker(id="who"),
                   _picker(id="who2", title="And who else?"), MODEL_QUESTIONS[3])
    )
    said = _messages(problems)
    assert said.count("the contact book is not one of your questions") == 2
    assert "use one contact_picker" not in said


def test_the_refusal_names_the_question_that_carries_it():
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1], _picker(),
                   MODEL_QUESTIONS[3])
    )
    assert [problem["path"] for problem in problems] == ["finalist_questions[1][3]"]


def test_the_two_text_box_cap_still_stands():
    # The correction took one shape off the form; it changed no other rule.
    problems = finalist_question_problems(
        _questions(
            {"id": "q1", "format": "short_answer", "title": "One?"},
            {"id": "q2", "format": "short_answer", "title": "Two?"},
            {"id": "q3", "format": "short_answer", "title": "Three?"},
            MODEL_QUESTIONS[1],
        )
    )
    assert problems != []


def test_a_bid_shaped_wrong_is_still_the_door_s_to_say_so():
    # Not one group of questions. The repair touches the steps and leaves the
    # shape alone, so the door's own sentence is what the model reads.
    plan = {"steps": [EMAIL_STEP], "finalist_questions": MODEL_QUESTIONS}
    merged, inserted = blocks.merge_required_blocks(plan, [], [])
    assert inserted == []
    assert merged is plan


def test_this_package_writes_no_who_step_of_its_own():
    # The bench stamps it. A harness that wrote one would be writing the step
    # whose whole work is handing back the person's own pick -- the thing that
    # forced the correction.
    plan = _plan(WORK_STEP)
    merged, _inserted = blocks.merge_required_blocks(
        plan, ["email"], [EMAIL_STEP], needs={"email": ("google-gmail",)},
        block_templates={"email": [EMAIL_STEP]},
    )
    for step in merged["steps"]:
        formats = [b.get("format") for b in (step.get("har_blocks") or [])]
        assert "contact_picker" not in formats


def test_the_runtime_sheet_says_the_bench_asks_who():
    from toll_harness.core.runtime import TOLL_BENCH_SYSTEM_INSTRUCTION

    assert "THE BENCH DOES" in TOLL_BENCH_SYSTEM_INSTRUCTION
    assert "never plan a step to find or list the" in TOLL_BENCH_SYSTEM_INSTRUCTION
    assert "Never add a picker they already declined" not in TOLL_BENCH_SYSTEM_INSTRUCTION


def test_contact_picker_is_still_a_har_slug_because_the_who_step_carries_it():
    # The bench stamps it onto a step. A mirror that called the slug unknown
    # would refuse the bench's own plan at home.
    from toll_harness.toll_bench.book_of_houses import HAR_FORMAT_SLUGS

    assert "contact_picker" in HAR_FORMAT_SLUGS


# ---------------------------------------------------------------------------
# 2. Nothing puts a picker on a bid
# ---------------------------------------------------------------------------
def test_an_act_on_the_persons_lane_reaches_a_person():
    assert blocks.steps_reach_a_person([EMAIL_STEP]) is True
    assert blocks.steps_reach_a_person([WORK_STEP]) is False
    # The meeting shape: no lane field, but it names its invitee.
    assert blocks.act_reaches_a_person({"kind": "meeting", "with": ""}) is True


def test_the_inserted_block_brings_no_question_with_it():
    # The step repair still runs. The questions are not touched -- this is the
    # line that spent every bid on the first fleet cycle after the bench
    # stopped taking a contact question.
    plan = _plan(WORK_STEP)
    merged, inserted = blocks.merge_required_blocks(
        plan, ["email"], [EMAIL_STEP], needs={"email": ("google-gmail",)},
        block_templates={"email": [EMAIL_STEP]},
    )
    assert inserted and not any("contact_picker" in line for line in inserted)
    assert _formats(merged) == ["single_choice", "yes_no", "yes_no", "short_answer"]
    assert finalist_question_problems(merged["finalist_questions"]) == []


def test_merge_required_blocks_takes_no_bid_template_any_more():
    # The door that used to let a picker in is gone, not merely unused.
    import inspect

    taken = inspect.signature(blocks.merge_required_blocks).parameters
    assert "bid_template" not in taken
    assert "bid_template_notes" not in taken
    assert not hasattr(blocks, "merge_contact_picker")
    assert not hasattr(blocks, "template_contact_picker")


def test_a_plan_that_reaches_nobody_is_asked_nothing():
    plan = _plan(WORK_STEP)
    merged, inserted = blocks.merge_required_blocks(plan, [], [])
    assert inserted == []
    assert merged is plan


# ---------------------------------------------------------------------------
# 3. The proposal ask never mentions a picker
# ---------------------------------------------------------------------------
def test_the_proposal_ask_does_not_ask_the_model_for_a_picker():
    assert "contact_picker" not in draft.PROPOSAL_INSTRUCTION
    assert "DO NOT ASK WHO THIS GOES TO" in draft.PROPOSAL_INSTRUCTION
    assert "on a step of the plan" in draft.PROPOSAL_INSTRUCTION


def test_a_picker_the_model_writes_anyway_is_dropped_not_filed():
    # One refused question costs the whole bid on a one-bid-per-want board, so
    # the other two are filed rather than nothing.
    read = draft.read_questions(
        [
            {"format": "yes_no", "fill": "sign them from you"},
            {"format": "contact_picker", "config": {"count": 2}},
            {"format": "short_answer", "fill": "anything to mention"},
        ]
    )
    assert [q["format"] for q in read] == ["yes_no", "short_answer"]
    assert all("contact" not in q["format"] for q in read)
    # And an id apiece, so the answers still come back matched.
    assert [q["id"] for q in read] == ["q1", "q3"]


# ---------------------------------------------------------------------------
# 4. End to end: the filed bid, and the door's REJ-40
# ---------------------------------------------------------------------------
def test_the_filed_bid_asks_nobody_who():
    api = _Api()
    result = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "idem-1")
    assert result["ok"] is True
    _target, filed, _key = api.submissions[0]
    assert [q.get("format") for q in filed["finalist_questions"][0]] == [
        "single_choice", "yes_no", "yes_no", "short_answer"
    ]


# A kind whose person lane is declared some way this package's mirror does not
# read. That is not a hypothetical: the lane table is the SERVER's, `runs_on`
# grew there after this package shipped, and the next one will too. The door
# knows; the harness asks it rather than guessing.
FUTURE_STEP = {
    "ask": "APPROVE",
    "title": "Text the reminders",
    "outcome_promise": "Everyone gets one reminder the day before.",
    "declared_odds": 0.6,
    "line_item_amount": 0,
    "acts": [{"kind": "sms", "on_behalf_of": "person", "purpose": "remind them"}],
}


def test_a_rej40_is_the_door_s_own_answer_and_never_re_filed():
    # There is nothing left to add. The who step is the BENCH'S, and a contact
    # question of ours is refused REJ-15 -- so a second filing would only
    # spend the round. It used to re-file with the picker on it.
    api = _Api(required=[], refuse=_refusal(
        "REJ-40",
        "step 1, acts[0] (sms) sends from the person's own account and names "
        "nobody to send it to: no `contact_ref` and no `found_contact`.",
    ))
    plan = _plan(FUTURE_STEP)
    # The harness could not see it: nothing was added before filing either.
    assert blocks.steps_reach_a_person(plan["steps"]) is False
    result = _provider(api).submit_proposal("t-1", plan, "idem-2")
    assert result["ok"] is False
    assert result["error"] == "contact_route"
    assert result["terminal"] is False
    assert len(api.submissions) == 1
    assert "contact_picker" not in api.submissions[0][1]["finalist_questions"][0][2]["format"]


def test_the_rej40_refusal_hands_back_the_who_step_in_the_door_s_words():
    api = _Api(required=[], refuse=_refusal("REJ-40", "names nobody to send it to"))
    result = _provider(api).submit_proposal("t-1", _plan(FUTURE_STEP), "idem-3")
    assert result["ok"] is False
    assert result["detail"] == "names nobody to send it to"
    assert "THE BENCH DOES" in result["fix"]
    assert "do not plan a step to find or list the people" in result["fix"]
    assert "contact_picker" not in result["fix"]
    # Filed ONCE. A harness that kept bouncing the same plan spends the run.
    assert len(api.submissions) == 1


def test_a_raw_address_is_the_door_s_to_refuse_and_not_re_filed():
    """REJ-40's other half: an address typed into the plan. Nothing here is
    the harness's to repair -- the door's own sentence is the whole answer."""
    api = _Api(refuse=_refusal(
        "REJ-40",
        "step 1, acts[0] (email) carries an email address in `to`.",
    ))
    result = _provider(api).submit_proposal("t-1", _plan(EMAIL_STEP), "idem-4")
    assert result["ok"] is False
    assert result["error"] == "contact_route"
    assert len(api.submissions) == 1


# ---------------------------------------------------------------------------
# 4. A provider key on a lane: carried through, never judged at home
# ---------------------------------------------------------------------------
TWILIO_ROW = {
    "id": "connect-twilio-1",
    "ask": "grant",
    "format": "connect_account",
    "required": True,
    "title": "Add your Twilio key",
    "config": {
        "grant_request": {
            "kind": "api_key",
            "what": "Your Twilio account",
            "why": "So the reminders go out from your own number",
            "scope": "send messages from the number you name",
            "until": "target_end",
            "exposure": "agent_acts_through_connection",
            "connector": {
                "provider": "key:twilio",
                "actions": ["TWILIO_SEND_MESSAGE"],
                "resources": {"from_numbers": ["<your number>"]},
                "operation_limit": 40,
            },
        },
        "fallback": "lesser",
        "fallback_note": "Without it nothing can go out from your number.",
    },
}

OUTLOOK_ROW = {
    "id": "connect-outlook-1",
    "ask": "grant",
    "format": "connect_account",
    "required": True,
    "title": "Connect your Outlook",
    "config": {
        "grant_request": {
            "kind": "oauth_connection",
            "what": "Your Outlook mailbox",
            "why": "So the message goes out from your own address",
            "scope": "send only the messages you approve",
            "until": "target_end",
            "exposure": "agent_acts_through_connection",
            "connector": {
                "provider": "composio:outlook",
                "actions": ["OUTLOOK_SEND_EMAIL"],
                "resources": {"mailbox_ids": ["primary"]},
            },
        },
        "fallback": "agent_account",
        "fallback_note": "The message goes out from the Book of Houses mailbox.",
    },
}


def _step_on(row, kind="email"):
    return {
        "ask": "APPROVE",
        "title": "Send it",
        "outcome_promise": "It goes out from your own account.",
        "har_blocks": [row],
        "acts": [{"kind": kind, "runs_on": "person"}],
    }


def test_a_lane_key_is_read_off_the_row_unchanged():
    assert blocks.connect_row_providers(_step_on(TWILIO_ROW, "sms")) == {"key:twilio"}
    assert blocks.connect_row_providers(_step_on(OUTLOOK_ROW)) == {"composio:outlook"}
    assert blocks.split_provider("key:twilio") == ("key", "twilio")
    assert blocks.split_provider("composio:outlook") == ("composio", "outlook")
    # A colon in front of something that is not a lane is not a lane.
    assert blocks.split_provider("google-gmail") == ("", "google-gmail")
    assert blocks.split_provider("weird:thing") == ("", "weird:thing")


def test_a_lane_key_is_held_to_no_floor_of_ours():
    # The row names Twilio's own tool slugs. There is no `gmail.message.send`
    # there to be missing, and judging it against our verbs would refuse a row
    # for a word it never used.
    assert blocks.grant_floor("key:twilio") == ()
    assert blocks.grant_floor("composio:outlook") == ()
    assert blocks.grant_floor("google-gmail") == ("gmail.message.send",)


def test_a_grant_step_on_a_lane_key_is_counted_as_written():
    step = {
        "ask": "GRANT",
        "title": "Add your Twilio key",
        "grant_request": {"connector": {"provider": "key:twilio",
                                        "actions": ["TWILIO_SEND_MESSAGE"]}},
    }
    assert blocks.grant_provider(step) == "key:twilio"
    assert blocks.intended_grant_provider(step) == "key:twilio"


def test_the_registry_s_own_lane_key_matches_exactly():
    steps = [_step_on(TWILIO_ROW, "sms")]
    assert blocks.grant_problems(steps, {"sms": "key:twilio"}) == []


def test_a_lane_row_is_never_refused_at_home_for_the_wrong_name():
    # The bench accepts `composio:outlook` where `google-gmail` was asked for,
    # because the person may re-point the row at any same-family service. The
    # family table is the server's, so the mirror says nothing and the free
    # validate door decides.
    steps = [_step_on(OUTLOOK_ROW)]
    assert blocks.grant_problems(steps, {"email": "google-gmail"}) == []


def test_the_silence_does_not_swallow_a_step_that_opens_nothing():
    bare = dict(_step_on(OUTLOOK_ROW))
    bare["har_blocks"] = []
    problems = blocks.grant_problems([bare], {"email": "google-gmail"})
    assert len(problems) == 1
    assert problems[0]["rej"] == "REJ-35"
    assert problems[0]["provider"] == "google-gmail"


def test_no_row_is_manufactured_for_a_lane_the_harness_cannot_judge():
    plan = {"steps": [_step_on(OUTLOOK_ROW)], "finalist_questions": [MODEL_QUESTIONS]}
    merged, inserted = blocks.merge_required_blocks(
        plan, ["email"], [], needs={"email": ("google-gmail",)},
        block_templates={"email": [EMAIL_STEP]},
    )
    # Nothing about the connection: no second connect card for an account the
    # person may already have settled on the row they were shown.
    assert not any("connect row" in line or "grant:" in line for line in inserted)
    assert merged["steps"][0]["har_blocks"] == [OUTLOOK_ROW]


def test_a_lane_key_reads_as_the_service_it_is():
    assert blocks.provider_words("key:twilio") == "Twilio"
    assert blocks.provider_words("composio:outlook") == "Outlook"
    assert blocks.provider_words("google-calendar") == "Google Calendar"
    assert blocks.connector_words("composio:gmail") == "Gmail"
    assert blocks.connected_sentence(["key:twilio", "composio:outlook"]) == (
        "The person already connected: Twilio, Outlook."
    )
