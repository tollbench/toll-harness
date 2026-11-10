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

    def file_plan_from_draft(self, target_id, idempotency_key=""):
        self.plans.append((target_id, idempotency_key))
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


def test_contact_picker_is_still_a_har_slug_because_the_who_step_carries_it():
    # The who STEP carries it. A mirror that called the slug unknown would
    # refuse a legal plan at home.
    from toll_harness.toll_bench.book_of_houses import HAR_FORMAT_SLUGS

    assert "contact_picker" in HAR_FORMAT_SLUGS


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
    result = _provider(api).submit_proposal("t-1", plan, "idem-2")
    assert result["ok"] is False
    assert result["rej"] == "REJ-40"
    assert result["terminal"] is False
    assert len(api.submissions) == 1
    assert "contact_picker" not in api.submissions[0][1]["finalist_questions"][0][2]["format"]


def test_the_rej40_refusal_hands_back_the_who_step_in_the_door_s_words():
    api = _Api(required=[], refuse=_refusal("REJ-40", "names nobody to send it to"))
    result = _provider(api).submit_proposal("t-1", _plan(FUTURE_STEP), "idem-3")
    assert result["ok"] is False
    # 0.74.0: the door's own words and nothing of the harness's.
    assert result["detail"] == "names nobody to send it to"
    assert "fix" not in result
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
    assert result["rej"] == "REJ-40"
    assert len(api.submissions) == 1


def test_a_lane_key_reads_as_the_service_it_is():
    assert blocks.connector_words("key:twilio") == "Twilio"
    assert blocks.connector_words("composio:gmail") == "Gmail"
    assert blocks.connected_sentence(["key:twilio", "composio:outlook"]) == (
        "The person already connected: Twilio, Outlook."
    )
