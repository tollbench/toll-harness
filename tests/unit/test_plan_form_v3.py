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
import pytest

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

# The 2.4x shape a PRODUCTION bench still sends today.
LEGACY_MEETING_TEMPLATE = {
    "ask": "APPROVE",
    "actor": "agent",
    "title": "Book a meeting with the invitee",
    "outcome_promise": "Book of Houses offers open times and books the meeting.",
    "acts": [
        {"kind": "meeting", "with": "<invitee email, or leave this key out>",
         "duration_min": 30, "window": "next week",
         "message": "<your words that open the invitation. No dates, no times.>"},
    ],
    "har_blocks": [
        {"id": "meeting-booked", "ask": "approve", "format": "review_approve",
         "required": True, "title": "Approve the booked meeting"},
    ],
    "declared_odds": "<fill 0.05..0.99>",
    "line_item_amount": 0,
}
LEGACY_GRANT_TEMPLATE = dict(GRANT_BLOCK, declared_odds="<fill 0.05..0.99>")


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


# ---------------------------------------------------------------------------
# 1. Reading a blank form apart from a written plan
# ---------------------------------------------------------------------------
def test_a_copied_blank_step_is_recognised_and_a_written_one_is_not():
    assert blocks.unfilled_step(blank_step(1)) is True
    assert blocks.unfilled_step(written_step("Find three venues")) is False
    # Half written is WRITTEN: the model's words stay and the door names the
    # field it left, because throwing the step away throws the words away.
    half = dict(written_step("Find three venues"), outcome_promise="")
    assert blocks.unfilled_step(half) is False


def test_a_platform_written_step_is_never_stripped():
    assert blocks.platform_written(GRANT_BLOCK) is True
    assert blocks.platform_written(MEETING_BLOCK) is True
    assert blocks.platform_written(BLANK_EMAIL_BLOCK) is True
    assert blocks.platform_written(blank_step(1)) is False
    assert blocks.platform_written(written_step("Find three venues")) is False
    # The blank email block is unfilled AND the platform's: it survives.
    assert blocks.unfilled_step(BLANK_EMAIL_BLOCK) is True
    assert blocks.blank_form_steps([BLANK_EMAIL_BLOCK]) == []


def test_the_blank_ones_are_named_by_index():
    steps = [written_step("One"), blank_step(2), MEETING_BLOCK, blank_step(3)]
    assert blocks.blank_form_steps(steps) == [1, 3]


def test_the_floor_is_the_briefs_own_skeleton_length():
    assert blocks.band_floor(SKELETON) == 3
    assert blocks.band_floor([]) is None
    assert blocks.band_floor(None) is None


def test_dropping_writes_no_words_of_the_agents():
    proposal = plan(written_step("One"), blank_step(2))
    trimmed, dropped, below = blocks.drop_blank_form_steps(proposal, floor=1)
    assert dropped == ["step 2 (blank form step)"]
    assert below is False
    assert trimmed["steps"] == [written_step("One")]
    # The original is untouched, and nothing gained a title it did not have.
    assert len(proposal["steps"]) == 2
    assert all(step.get("title") for step in trimmed["steps"])


def test_an_old_angle_bracket_blank_counts_as_blank_too():
    legacy = dict(blank_step(1), title="<the step title>", outcome_promise="<what they get>")
    assert blocks.unfilled_step(legacy) is True


# ---------------------------------------------------------------------------
# 2. The filing door: the form alone is never filed
# ---------------------------------------------------------------------------
def test_a_plan_that_is_only_the_blank_form_files_nothing():
    api = _Api()
    provider = _provider(api)
    result = _file(provider, plan(*SKELETON))
    assert result["ok"] is False
    assert result["error"] == "plan_is_still_the_blank_form"
    assert result["terminal"] is False
    assert len(result["dropped"]) == 3
    assert api.submissions == []
    # And the harness did NOT invent the words the model owed.
    assert "title" in result["message"]


def test_one_copied_blank_step_is_dropped_and_the_rest_is_filed():
    api = _Api()
    provider = _provider(api)
    result = _file(
        provider,
        plan(written_step("Find three venues"), blank_step(2),
             written_step("Book the room", odds=0.5),
             written_step("Send the confirmation", odds=0.6)),
    )
    assert result["ok"] is True
    filed = api.submissions[0][1]
    assert [step["title"] for step in filed["steps"]] == [
        "Find three venues", "Book the room", "Send the confirmation",
    ]


def test_dropping_below_the_band_floor_stops_the_filing():
    """The floor is the bench's, not ours: the skeleton IS the band minimum
    (REJ-12), so two written steps plus one copied blank on a three-step band
    is a plan the door would refuse anyway. Better refused at home, where the
    round is not spent and the model is told what it owes."""
    api = _Api()
    provider = _provider(api)
    result = _file(
        provider,
        plan(written_step("Find three venues"), blank_step(2),
             written_step("Book the room", odds=0.5)),
    )
    assert result["ok"] is False
    assert result["error"] == "plan_is_still_the_blank_form"
    assert "band allows" in result["message"]
    assert api.submissions == []


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


def test_the_door_is_called_before_filing_and_its_problems_come_back_once():
    api = _Api(door=DOOR_PROBLEMS)
    provider = _provider(api)
    result = _file(provider, plan(written_step("One"), written_step("Two")))
    assert result["ok"] is False
    assert result["error"] == "plan_has_problems"
    assert result["terminal"] is False
    assert result["problem_count"] == 2
    assert [p["code"] for p in result["problems"]] == ["REJ-16", "REJ-21"]
    assert all("fix" in problem for problem in result["problems"])
    assert api.submissions == []          # nothing was filed
    assert len(api.door_calls) == 1       # and nothing was counted

    # ONE repair pass is the ceiling: the next submit files, so the bench's own
    # refusal is the record rather than a harness that never bids at all.
    again = _file(provider, plan(written_step("One"), written_step("Two")))
    assert again["ok"] is True
    assert len(api.submissions) == 1


def test_a_mechanically_corrected_plan_is_filed_as_corrected():
    corrected = plan(written_step("One", odds=0.35), written_step("Two", odds=0.5))
    api = _Api(door={**DOOR_PROBLEMS, "corrected_plan": corrected,
                     "corrected_ok": True, "corrections": ["odds 35 -> 0.35"]})
    provider = _provider(api)
    result = _file(provider, plan(written_step("One", odds=35), written_step("Two")))
    assert result["ok"] is True
    assert api.submissions[0][1] == corrected


def test_a_clean_door_files_straight_through():
    api = _Api()
    provider = _provider(api)
    assert _file(provider, plan(written_step("One"), written_step("Two")))["ok"] is True
    assert len(api.door_calls) == 1
    assert len(api.submissions) == 1


def test_the_local_mirror_never_buries_a_plan_the_door_takes():
    """The mirror is a pre-check, not the law. Production is authoritative."""
    api = _Api()
    provider = _provider(api, local_ok=False,
                         local_problems=[{"path": "steps", "message": "stale rule"}])
    assert _file(provider, plan(written_step("One")))["ok"] is True
    assert len(api.submissions) == 1


def test_a_local_problem_rides_along_when_the_door_also_refuses():
    api = _Api(door=DOOR_PROBLEMS)
    provider = _provider(api, local_ok=False,
                         local_problems=[{"path": "steps.0", "message": "also this"}])
    result = _file(provider, plan(written_step("One")))
    codes = [problem["code"] for problem in result["problems"]]
    assert codes == ["REJ-16", "REJ-21", "LOCAL"]


# ---------------------------------------------------------------------------
# 4. Compatibility: a bench with no door, and a bench that still names blocks
# ---------------------------------------------------------------------------
def test_an_older_bench_is_never_asked_for_a_door_it_does_not_have():
    api = _Api(contract="2.46", template=[LEGACY_GRANT_TEMPLATE, LEGACY_MEETING_TEMPLATE],
               required=("meeting",))
    provider = _provider(api)
    result = _file(provider, plan(written_step("Write the agenda")))
    assert result["ok"] is True
    assert api.door_calls == []
    # And rule 228 still repairs the plan from the template it was sent.
    filed = api.submissions[0][1]
    assert "meeting" in blocks.declared_kinds(filed["steps"])
    assert blocks.grant_problems(filed["steps"]) == []


def test_the_local_mirror_still_refuses_when_there_is_no_door():
    api = _Api(contract="2.46", template=[])
    provider = _provider(api, local_ok=False,
                         local_problems=[{"path": "pitch_title", "message": "blank"}])
    result = _file(provider, plan(written_step("One")))
    assert result["ok"] is False
    assert result["error"] == "local_validation_failed"
    assert api.submissions == []


def test_a_missing_route_downgrades_to_the_mirror_for_the_rest_of_the_run():
    """A 3.x bench whose deployment has no such path answers a bare 404."""
    api = _Api(door_error=BookOfHousesApiError(404, "http_error", "Not Found", {}))
    provider = _provider(api)
    assert _file(provider, plan(written_step("One")))["ok"] is True
    assert len(api.door_calls) == 1
    # Asked once, remembered: the second bid does not pay for the probe again.
    assert _file(provider, plan(written_step("Two")), key="k-2")["ok"] is True
    assert len(api.door_calls) == 1


def test_a_closed_target_is_the_callers_to_handle_not_the_doors():
    error = BookOfHousesApiError(404, "target not found or not open", "closed", {})
    api = _Api(door_error=error)
    provider = _provider(api)
    with pytest.raises(BookOfHousesApiError):
        _file(provider, plan(written_step("One")))


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


def test_validate_proposal_without_a_target_id_is_the_offline_mirror():
    api = _Api()
    provider = BookOfHousesTollBenchProvider(api)
    provider._local_validation = lambda proposal: {"ok": True, "problems": ["mirror"]}
    answer = provider.validate_proposal(plan(written_step("One")))
    assert answer == {"ok": True, "problems": ["mirror"]}
    assert api.door_calls == []
