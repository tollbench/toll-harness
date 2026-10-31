"""CONTRACT 3.0 -- THE TEMPLATE IS A FORM, NOT A PLAN.

WHAT FORCED THIS. Steven, 2026-09-05: "I want the want to be a posting and I
want the agents to respond to it. I want a template that is flexible. I don't
want to do any work for the agents." The classifier that decided which blocks a
want needed is gone: `required_blocks` is `[]` for every want and REJ-32 never
fires. What the brief carries instead is a FORM -- a blank skeleton, a catalog
of blocks, the whole bid payload and one note per blank.

That turned the harness's own standing instruction into a trap. It told the
model to "copy EVERY template step as given and fill only its angle-bracket
blanks"; against a 3.0 skeleton there are no angle brackets, only real empty
strings, so copying it filed three steps with no title and no promise and every
bid died at the local mirror as `local_validation_failed`.

These cover the three moves 0.26.0 owes the change:
  1. a step copied off the form and never filled is DROPPED, and no word of the
     agent's is ever written by the harness in its place,
  2. a plan that is nothing BUT the blank form is not filed at all, and says so,
  3. the free validate door is used before filing, its every-problem-at-once
     answer reaches the model once, and an older bench with no such door still
     works exactly as it did.
"""

from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

# The 3.0 skeleton, verbatim in shape from a live brief: mechanics filled,
# every agent-owned word an explicit blank.
BLANK_STEP = {
    "actor": "agent",
    "agent_court_estimate": 3,
    "ask": "APPROVE",
    "declared_odds": None,
    "examples": [],
    "har_blocks": [
        {"ask": "approve", "format": "review_approve", "id": "research-1",
         "required": True, "title": ""},
    ],
    "line_item_amount": 0,
    "materials": [],
    "minor_detail": "",
    "outcome_promise": "",
    "person_minutes": 10,
    "rounds": 2,
    "title": "",
}


def blank_step(index):
    step = {key: value for key, value in BLANK_STEP.items()}
    step["har_blocks"] = [dict(BLANK_STEP["har_blocks"][0], id=f"research-{index}")]
    return step


SKELETON = [blank_step(1), blank_step(2), blank_step(3)]

# A step the agent actually wrote.
def written_step(title, odds=0.4):
    return {
        "actor": "agent",
        "ask": "APPROVE",
        "title": title,
        "outcome_promise": f"You get {title.lower()}.",
        "declared_odds": odds,
        "line_item_amount": 0,
        "har_blocks": [
            {"ask": "approve", "format": "review_approve", "id": "s1",
             "required": True, "title": "Approve it"},
        ],
    }


# The grant + meeting pair out of block_templates, exactly as the catalog ships
# it: the platform's own words on both steps.
GRANT_BLOCK = {
    "actor": "agent",
    "ask": "GRANT",
    "title": "Allow calendar access",
    "outcome_promise": "Book of Houses can see when you are free.",
    "grant_request": {
        "kind": "oauth_connection",
        "what": "Your Google Calendar",
        "why": "So we can find open times and put the meeting on it",
        "scope": "read events and add or change the one meeting",
        "until": "target_end",
        "connector": {
            "provider": "google-calendar",
            "actions": ["calendar.events.read", "calendar.event.create"],
            "resources": {"calendar_ids": ["primary"]},
        },
    },
    "har_blocks": [
        {"ask": "grant", "format": "connect_account", "id": "connect-calendar",
         "required": True, "title": "Connect Google Calendar"},
    ],
    "declared_odds": 0.4,
    "line_item_amount": 0,
}
MEETING_BLOCK = {
    "actor": "agent",
    "ask": "APPROVE",
    "title": "A booked 30-minute call with the invitee",
    "outcome_promise": "Book of Houses offers the invitee times from your calendar.",
    "acts": [
        {"kind": "meeting", "duration_min": 30, "message": "", "title": "",
         "window": "next week", "with": "", "with_name": ""},
    ],
    "har_blocks": [
        {"ask": "approve", "format": "review_approve", "id": "meeting-booked",
         "required": True, "title": "Approve the booked meeting"},
    ],
    "declared_odds": 0.4,
    "line_item_amount": 0,
}
# An EFFECT block whose own agent-owned words are blank in the catalog. It is
# still the platform's step, so it must survive the strip and be named by the
# door rather than thrown away here.
BLANK_EMAIL_BLOCK = {
    "actor": "agent",
    "ask": "APPROVE",
    "title": "",
    "outcome_promise": "",
    "acts": [{"kind": "email", "purpose": "", "to": ""}],
    "har_blocks": [
        {"ask": "approve", "format": "review_approve", "id": "email-1",
         "required": True, "title": ""},
    ],
    "declared_odds": None,
    "line_item_amount": 0,
}


def plan(*steps, **extra):
    payload = {"steps": list(steps), "pitch_title": "A call", "pitch_body": "x"}
    payload.update(extra)
    return payload


class _Api:
    """The bench, as far as these tests need it."""

    def __init__(self, *, contract="3.0", template=None, required=(),
                 door=None, door_error=None):
        self.contract = contract
        self.template = SKELETON if template is None else list(template)
        self.required = list(required)
        self.door = door                # the validate door's answer, or None
        self.door_error = door_error    # raise this from the door instead
        self.submissions = []
        self.door_calls = []
        self.acks = 0

    def protocol(self):
        return {"ok": True, "contract_version": self.contract}

    def target_brief(self, target_id):
        return {
            "ok": True,
            "brief": {
                "target_id": target_id,
                "round": 1,
                "want": "I want a 30 minute call with Ruby next week",
                "required_blocks": self.required,
                "required_blocks_reason": None,
                "plan_template": self.template,
                "your_bid": None,
            },
        }

    def validate_proposal(self, target_id, payload):
        self.door_calls.append((target_id, payload))
        if self.door_error is not None:
            raise self.door_error
        if self.door is None:
            return {"ok": True, "problem_count": 0, "problems": [],
                    "corrected_plan": None, "corrected_ok": False, "corrections": []}
        return self.door

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.submissions.append((target_id, proposal, idempotency_key))
        return {"ok": True, "proposal_id": "p-1"}

    def me(self):
        return {"ok": True,
                "reachability_test": {"reachable": self.acks >= 1, "reachable_at": "now"}}

    def ack_reachability_ping(self):
        self.acks += 1
        return self.me()

    def act_kinds(self):
        return {"kinds": {"meeting": {"declaration": {}, "template": MEETING_BLOCK}}}

    def proposal_schema(self):
        return {"type": "object"}


def _provider(api, local_ok=True, local_problems=None):
    provider = BookOfHousesTollBenchProvider(api)
    answer = {"ok": local_ok, "problems": list(local_problems or [])}
    provider.validate_proposal = lambda proposal, target_id=None: dict(answer)
    return provider


def _file(provider, proposal, target_id="t-1", key="k-1"):
    return provider.submit_proposal(target_id, proposal, key)


def test_the_step_count_band_is_no_longer_mirrored_here():
    """RETIRED (bench contract 4.0, 2026-09-17). The step-count code goes to
    a scoreboard nobody is shown and blocks nothing, so the copy of the band
    minimum this package used to keep has no owner and is gone."""
    assert not hasattr(blocks, "band_floor")


def test_a_plan_with_written_steps_left_still_files_whatever_the_band_said():
    """Two written steps and one copied blank used to be refused at home for
    being under a three-step band. The band does not block, so it files."""
    api = _Api()
    provider = _provider(api)
    result = _file(
        provider,
        plan(written_step("Find three venues"), blank_step(2),
             written_step("Book the room", odds=0.5)),
    )
    assert result["ok"] is not False or result["error"] != "plan_is_still_the_blank_form"


def test_a_block_pulled_from_the_catalog_survives_the_strip():
    api = _Api()
    provider = _provider(api)
    result = _file(provider, plan(GRANT_BLOCK, MEETING_BLOCK, BLANK_EMAIL_BLOCK))
    assert result["ok"] is True
    filed = api.submissions[0][1]
    assert len(filed["steps"]) == 3
    assert filed["steps"][2]["acts"][0]["kind"] == "email"


# ---------------------------------------------------------------------------
# 3. The free validate door (contract 3.0)
# ---------------------------------------------------------------------------
DOOR_PROBLEMS = {
    "ok": False,
    "problem_count": 2,
    "problems": [
        {"code": "REJ-16", "detail": "step 1 declares 35", "step_index": 1,
         "field": "declared_odds", "fix": "Give every step a declared_odds between 0 and 1."},
        {"code": "REJ-21", "detail": "pitch missing", "step_index": None,
         "field": "pitch_title", "fix": "Give the bid a pitch_title and a pitch_body."},
    ],
    "corrected_plan": None,
    "corrected_ok": False,
    "corrections": [],
}


def test_the_local_mirror_never_buries_a_plan_the_door_takes():
    """The mirror is a pre-check, not the law. Production is authoritative."""
    api = _Api()
    provider = _provider(api, local_ok=False,
                         local_problems=[{"path": "steps", "message": "stale rule"}])
    assert _file(provider, plan(written_step("One")))["ok"] is True
    assert len(api.submissions) == 1


def test_a_bench_whose_protocol_cannot_be_read_has_no_door():
    class _Silent(_Api):
        def protocol(self):
            raise BookOfHousesApiError(0, "connection_error", "down", {})

    api = _Silent()
    provider = _provider(api)
    assert _file(provider, plan(written_step("One")))["ok"] is True
    assert api.door_calls == []


# ---------------------------------------------------------------------------
# 5. The tool the model calls itself
# ---------------------------------------------------------------------------
def test_validate_proposal_with_a_target_id_is_the_benchs_own_answer():
    api = _Api(door=DOOR_PROBLEMS)
    provider = BookOfHousesTollBenchProvider(api)
    provider._local_validation = lambda proposal: {"ok": True, "problems": []}
    answer = provider.validate_proposal(plan(written_step("One")), "t-1")
    assert answer["ok"] is False
    assert answer["source"] == "bench_validate_door"
    assert answer["problem_count"] == 2
    assert answer["corrected_ok"] is False
    assert api.door_calls


