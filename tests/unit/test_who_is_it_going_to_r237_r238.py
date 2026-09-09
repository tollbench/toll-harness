"""RULES 237 + 238 (Steven, 2026-09-08/09) -- WHO IS IT GOING TO.

A person is contacted through their own private Contacts and never through a
loose address. The brief hands the question out already: when anything it
publishes can reach a person, `bid_template.finalist_questions[0]` ships a
`contact_picker` as the THIRD of the four -- {"id": "who", "format":
"contact_picker", "title": "", "config": {"count": 1}} -- in place of the
second of the two identical yes/no questions, because four is the whole cap.
An act on the person's own lane that names nobody, with no picker anywhere in
the bid, is refused REJ-40 (contact_route).

WHAT FORCED THIS FILE, in two halves.

  1. `HAR_FORMAT_SLUGS` did not carry `contact_picker`, so the local mirror
     answered "`contact_picker` is not a HAR format slug (REJ-15)" about the
     very question the bench hands out -- and with the validate door
     unreachable that is `local_validation_failed`, a legal plan buried at
     home and the round spent with nothing filed.
  2. Nothing in this package ever copied the brief's questions.
     `merge_required_blocks` inserted the email step out of `block_templates`
     and left `finalist_questions` alone, so the harness's OWN repair created
     the REJ-40 condition it was then refused for: a plan that reaches a
     person, filed beside four questions that ask nobody who.

The third subject is the provider key. `composio:<toolkit slug>` and
`key:<service slug>` are lanes the PLATFORM resolves; the harness carries them
through untouched and never manufactures a refusal about one, because the
bench matches a row by FAMILY and that table lives on the server.
"""
import copy

from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import (
    BookOfHousesTollBenchProvider,
    finalist_question_problems,
)

# --------------------------------------------------------------------------
# The bench's own shapes, as a live brief hands them out.
# --------------------------------------------------------------------------

# `_finalist_form(contacts=True)` in want_blocks: the picker is question three.
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
# 1. The mirror knows the slug the bench hands out
# ---------------------------------------------------------------------------
def test_contact_picker_is_a_har_format_slug():
    # The exact question off `bid_template`, with the agent's words in it.
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1], _picker(),
                   MODEL_QUESTIONS[3])
    )
    assert problems == []


def test_the_picker_is_not_a_text_box():
    # Two text boxes AND the picker: legal, because a picker is a tap and the
    # two-text cap counts boxes the person types into.
    problems = finalist_question_problems(
        _questions(
            MODEL_QUESTIONS[0],
            {"id": "q2", "format": "short_answer", "title": "Anything to add?"},
            _picker(),
            {"id": "q4", "format": "short_answer", "title": "Anything to avoid?"},
        )
    )
    assert problems == []


def test_a_second_picker_is_refused():
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], _picker(id="who"),
                   _picker(id="who2", title="And who else?"), MODEL_QUESTIONS[3])
    )
    assert "one contact_picker" in _messages(problems)


def test_the_picker_carries_only_a_count():
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
                   _picker(config={"count": 5, "options": []}), MODEL_QUESTIONS[3])
    )
    assert "only config.count" in _messages(problems)


def test_a_count_of_eighty_is_one_picker():
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
                   _picker(config={"count": 80}), MODEL_QUESTIONS[3])
    )
    assert problems == []


def test_a_count_outside_the_book_is_refused():
    for bad in (0, 501, True, "5", 2.5):
        problems = finalist_question_problems(
            _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
                       _picker(config={"count": bad}), MODEL_QUESTIONS[3])
        )
        assert "config.count must be a whole number" in _messages(problems), bad


def test_the_picker_may_not_carry_a_contact_of_its_own():
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
                   _picker(email="ruby@example.com"), MODEL_QUESTIONS[3])
    )
    assert "cannot carry a contact" in _messages(problems)


def test_the_picker_is_a_provide_question():
    problems = finalist_question_problems(
        _questions(MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
                   _picker(ask="APPROVE"), MODEL_QUESTIONS[3])
    )
    assert "PROVIDE question" in _messages(problems)


# ---------------------------------------------------------------------------
# 2. The swap: the brief's own picker, onto a plan that reaches a person
# ---------------------------------------------------------------------------
def test_an_act_on_the_persons_lane_reaches_a_person():
    assert blocks.steps_reach_a_person([EMAIL_STEP]) is True
    assert blocks.steps_reach_a_person([WORK_STEP]) is False
    # The meeting shape: no lane field, but it names its invitee.
    assert blocks.act_reaches_a_person({"kind": "meeting", "with": ""}) is True


def test_the_inserted_block_brings_the_brief_s_question_with_it():
    plan = _plan(WORK_STEP)
    merged, inserted = blocks.merge_required_blocks(
        plan, ["email"], [EMAIL_STEP], needs={"email": ("google-gmail",)},
        block_templates={"email": [EMAIL_STEP]},
        bid_template=BID_TEMPLATE, bid_template_notes=BID_TEMPLATE_NOTES,
    )
    assert any("contact_picker:who" in line for line in inserted)
    # The picker replaces the SECOND yes/no -- the one shape the form was
    # offering twice -- and the group is still exactly four.
    assert _formats(merged) == ["single_choice", "yes_no", "contact_picker",
                                "short_answer"]
    assert len(_group(merged)) == 4
    picker = _group(merged)[2]
    assert picker["id"] == "who"
    assert picker["config"] == {"count": 1}
    # The brief published the title blank; `bid_template_notes` published the
    # words beside it.
    assert picker["title"] == "Who should these go to?"
    # And what came out passes the gate that would have refused it.
    assert finalist_question_problems(merged["finalist_questions"]) == []


def test_the_model_s_own_picker_is_never_touched():
    mine = _picker(id="who", title="Which colleagues get one?",
                   config={"count": 12})
    plan = _plan(WORK_STEP, questions=[MODEL_QUESTIONS[0], MODEL_QUESTIONS[1],
                                       mine, MODEL_QUESTIONS[3]])
    merged, inserted = blocks.merge_required_blocks(
        plan, ["email"], [EMAIL_STEP], needs={"email": ("google-gmail",)},
        block_templates={"email": [EMAIL_STEP]},
        bid_template=BID_TEMPLATE, bid_template_notes=BID_TEMPLATE_NOTES,
    )
    assert not any("contact_picker" in line for line in inserted)
    assert _group(merged)[2]["title"] == "Which colleagues get one?"
    assert _group(merged)[2]["config"] == {"count": 12}


def test_a_plan_that_reaches_nobody_is_asked_nothing():
    plan = _plan(WORK_STEP)
    merged, inserted = blocks.merge_required_blocks(
        plan, [], [], bid_template=BID_TEMPLATE,
        bid_template_notes=BID_TEMPLATE_NOTES,
    )
    assert inserted == []
    assert merged is plan


def test_a_brief_with_no_picker_adds_none():
    # An older bench, or a want nothing on it can reach a person for.
    no_picker = copy.deepcopy(BID_TEMPLATE)
    no_picker["finalist_questions"][0][2] = {
        "id": "q3", "format": "yes_no", "title": "", "required": True, "config": {}
    }
    plan = _plan(WORK_STEP)
    merged, inserted = blocks.merge_required_blocks(
        plan, ["email"], [EMAIL_STEP], needs={"email": ("google-gmail",)},
        block_templates={"email": [EMAIL_STEP]},
        bid_template=no_picker, bid_template_notes=[],
    )
    assert not any("contact_picker" in line for line in inserted)
    assert _formats(merged) == ["single_choice", "yes_no", "yes_no",
                                "short_answer"]


def test_with_no_second_yes_no_the_picker_takes_the_seat_the_brief_keeps():
    plan = _plan(EMAIL_STEP, questions=[
        MODEL_QUESTIONS[0],
        {"id": "q2", "format": "number", "title": "How many?",
         "config": {"unit": "people"}},
        {"id": "q3", "format": "rank", "title": "Order them",
         "config": {"options": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}},
        MODEL_QUESTIONS[3],
    ])
    merged, note = blocks.merge_contact_picker(
        plan, plan["steps"], BID_TEMPLATE, BID_TEMPLATE_NOTES
    )
    assert note and "replaced question 3" in note
    assert _formats(merged) == ["single_choice", "number", "contact_picker",
                                "short_answer"]


def test_the_title_falls_back_to_plain_words_with_no_notes():
    picker = blocks.template_contact_picker(BID_TEMPLATE, None)
    assert picker["title"] == blocks.CONTACT_PICKER_TITLE


def test_a_bid_shaped_wrong_is_left_for_the_door_to_say_so():
    # Not one group of questions: the door refuses that in its own sentence,
    # and filling a question in would only hide it.
    plan = {"steps": [EMAIL_STEP], "finalist_questions": MODEL_QUESTIONS}
    merged, note = blocks.merge_contact_picker(
        plan, plan["steps"], BID_TEMPLATE, BID_TEMPLATE_NOTES
    )
    assert note is None and merged is plan


# ---------------------------------------------------------------------------
# 3. End to end: the filed bid, and the door's REJ-40
# ---------------------------------------------------------------------------
def test_the_filed_bid_asks_who():
    api = _Api()
    result = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "idem-1")
    assert result["ok"] is True
    _target, filed, _key = api.submissions[0]
    assert [q.get("format") for q in filed["finalist_questions"][0]] == [
        "single_choice", "yes_no", "contact_picker", "short_answer"
    ]
    assert filed["finalist_questions"][0][2]["title"] == "Who should these go to?"


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


def test_a_rej40_off_the_door_is_repaired_once():
    api = _Api(required=[], refuse=_refusal(
        "REJ-40",
        "step 1, acts[0] (sms) sends from the person's own account and names "
        "nobody to send it to: no `contact_ref`, no `found_contact`, and no "
        "contact_picker question anywhere in this bid.",
    ))
    plan = _plan(FUTURE_STEP)
    # The harness could not see it: nothing was added before filing.
    assert blocks.steps_reach_a_person(plan["steps"]) is False
    result = _provider(api).submit_proposal("t-1", plan, "idem-2")
    assert result["ok"] is True
    # Filed twice: the refusal, then the same bid carrying the question.
    assert len(api.submissions) == 2
    assert api.submissions[0][1]["finalist_questions"][0][2]["format"] == "yes_no"
    assert api.submissions[1][2] == "idem-2-rej40"
    refiled = api.submissions[1][1]
    assert [q.get("format") for q in refiled["finalist_questions"][0]] == [
        "single_choice", "yes_no", "contact_picker", "short_answer"
    ]
    assert refiled["finalist_questions"][0][2]["title"] == "Who should these go to?"


def test_a_rej40_with_no_question_to_add_is_the_door_s_own_answer():
    no_picker = copy.deepcopy(BID_TEMPLATE)
    no_picker["finalist_questions"][0][2] = {
        "id": "q3", "format": "yes_no", "title": "", "required": True, "config": {}
    }
    api = _Api(required=[], bid_template=no_picker, notes=[],
               refuse=_refusal("REJ-40", "names nobody to send it to"))
    result = _provider(api).submit_proposal("t-1", _plan(FUTURE_STEP), "idem-3")
    assert result["ok"] is False
    assert result["error"] == "contact_route"
    assert result["terminal"] is False
    assert result["detail"] == "names nobody to send it to"
    assert "contact_picker" in result["fix"]
    # Filed ONCE. A harness that kept bouncing the same plan spends the run.
    assert len(api.submissions) == 1


def test_a_raw_address_is_the_door_s_to_refuse_and_not_re_filed():
    """REJ-40's other half: an address typed into the plan. The picker is
    already on the bid, so there is nothing to add and nothing to re-file --
    the door's own sentence is the whole answer."""
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
