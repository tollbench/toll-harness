"""RULES 228 AND 229 (contract 2.44) -- the want names its blocks, and a
declared block files itself.

What forced it: one meeting want, three agents, no meeting booked. The third
dropped the act altogether and filed a text document called "Scheduling request
for approval" on a plain APPROVE step, so nothing was declared, nothing gated
it, and the person's Approve would have closed the step with nothing sent.

These cover the four moves the harness owes the rule:
  1. the blocks and the template reach the model,
  2. a plan missing one is repaired from the brief before it is filed,
  3. a REJ-32 refusal carries the form and is filed ONCE, never in a loop,
  4. a block the platform is running takes no act and no outcome from us.

RULE 230 (contract 2.46) added a fifth: the template is a GROUP, in order, and
the GRANT step that connects the person's Google Calendar comes before the
meeting block that reads it. Steven, 2026-09-05: "they are supposed to connect
my calendar IN the plan." A block with no grant before it is refused REJ-35.
"""
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

MEETING_TEMPLATE = {
    "ask": "APPROVE",
    "actor": "agent",
    "title": "Book a meeting with the invitee",
    "outcome_promise": (
        "Book of Houses offers open times from your calendar, emails the "
        "invitee to pick one, puts the meeting on both calendars, and hands "
        "you the receipt to approve."
    ),
    "har_blocks": [
        {
            "id": "meeting-booked",
            "ask": "approve",
            "format": "review_approve",
            "required": True,
            "title": "Approve the booked meeting",
            "description": "You are confirming the 30-minute meeting.",
        }
    ],
    "acts": [
        {
            "kind": "meeting",
            "with": "<invitee email, or leave this key out and the person is asked for it>",
            "with_name": "<invitee first name>",
            "duration_min": 30,
            "window": "next week",
            "title": "<what the meeting is called on the calendar>",
            "message": "<your words that open the invitation. No dates, no times.>",
        }
    ],
    "rounds": 2,
    "declared_odds": "<fill 0.05..0.99>",
    "declared_odds_reason": "<why that number>",
    "person_minutes": 3,
    "line_item_amount": 0,
    "agent_court_estimate": 1,
    "minor_detail": "Book of Houses runs the invitation; you approve it before it goes out.",
    "examples": [],
    "materials": [],
}

GRANT_TEMPLATE = {
    "ask": "GRANT",
    "actor": "agent",
    "title": "Allow calendar access",
    "outcome_promise": (
        "Book of Houses can see when you are free and put this one meeting on "
        "your calendar. Nothing else on it is read or changed."
    ),
    "grant_request": {
        "kind": "oauth_connection",
        "what": "Your Google Calendar",
        "why": "So we can find open times and put the meeting on it",
        "scope": "read events and add or change the one meeting",
        "until": "target_end",
        "exposure": "agent_acts_through_connection",
        "connector": {
            "provider": "google-calendar",
            "actions": [
                "calendar.events.read",
                "calendar.events.create",
                "calendar.events.update",
                "calendar.events.delete",
            ],
            "resources": {"calendar_ids": ["primary"]},
        },
    },
    "har_blocks": [
        {
            "id": "connect-calendar",
            "ask": "grant",
            "format": "connect_account",
            "required": True,
            "title": "Connect Google Calendar",
        }
    ],
    "rounds": 1,
    "declared_odds": "<fill 0.05..0.99>",
    "declared_odds_reason": "<why that number>",
    "person_minutes": 2,
    "line_item_amount": 0,
    "agent_court_estimate": 0,
    "examples": [],
    "materials": [],
}

TWO_STEP_TEMPLATE = [GRANT_TEMPLATE, MEETING_TEMPLATE]

WORK_STEP = {
    "ask": "APPROVE",
    "actor": "agent",
    "title": "Write the agenda",
    "outcome_promise": "You get a one page agenda for the call.",
    "declared_odds": 0.6,
    "line_item_amount": 0,
    "acts": [],
}


def _plan(*steps):
    return {"steps": list(steps), "pitch_title": "A call with Ruby", "pitch_body": "x"}


class _Api:
    """The bench, as far as these tests need it."""

    def __init__(self, *, required=("meeting",), refuse=None, template=None):
        self.required = list(required)
        self.template = TWO_STEP_TEMPLATE if template is None else list(template)
        self.refuse = refuse  # a BookOfHousesApiError to raise on the first file
        self.submissions = []
        self.acks = 0

    def target_brief(self, target_id):
        return {
            "ok": True,
            "brief": {
                "target_id": target_id,
                "round": 1,
                "want": "I want to set up a call with Ruby to plan the launch",
                "required_blocks": self.required,
                "required_blocks_reason": {"meeting": "It only exists once two calendars agree."},
                "plan_template": self.template if self.required else [],
                "your_bid": None,
            },
        }

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.submissions.append((target_id, proposal, idempotency_key))
        if self.refuse is not None and len(self.submissions) == 1:
            raise self.refuse
        return {"ok": True, "proposal_id": "p-1"}

    def me(self):
        return {
            "ok": True,
            "reachability_test": {"reachable": self.acks >= 1, "reachable_at": "now"},
        }

    def ack_reachability_ping(self):
        self.acks += 1
        return self.me()

    def act_kinds(self):
        return {"kinds": {"meeting": {"declaration": {}, "template": MEETING_TEMPLATE}}}

    def proposal_schema(self):
        return {"type": "object"}

    def current_step(self, deal_id):
        return {}

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        return {"ok": True, "act_id": "ap-1"}

    def file_outcome(self, target_id, payload, idempotency_key):
        return {"ok": True}


def _provider(api):
    provider = BookOfHousesTollBenchProvider(api)
    # The local schema check is production's, not this test's subject.
    provider.validate_proposal = lambda proposal: {"ok": True, "problems": []}
    return provider


# ---------------------------------------------------------------------------
# 1. Reading the blocks off the brief
# ---------------------------------------------------------------------------
def test_the_missing_block_is_named():
    assert blocks.missing_blocks([WORK_STEP], ["meeting"]) == ["meeting"]
    assert blocks.missing_blocks([WORK_STEP, MEETING_TEMPLATE], ["meeting"]) == []
    assert blocks.missing_blocks([WORK_STEP], []) == []


def test_a_template_blank_is_filled_or_left_out():
    """`with` is OPTIONAL since rule 229: leaving it out asks the person for
    the address on their card, which is better than inventing one."""
    filled = blocks.fill_template_step(
        MEETING_TEMPLATE, proposal=_plan(WORK_STEP), want="a call with Ruby"
    )
    act = filled["acts"][0]
    assert "with" not in act
    assert "with_name" not in act and "title" not in act
    assert act["duration_min"] == 30 and act["window"] == "next week"
    assert act["message"] and "<" not in act["message"]
    assert filled["declared_odds"] == 0.6  # the plan's own line, which may not fall
    assert "<" not in filled["declared_odds_reason"]
    # The platform's own words for the step survive untouched.
    assert filled["title"] == MEETING_TEMPLATE["title"]
    assert filled["har_blocks"] == MEETING_TEMPLATE["har_blocks"]


def test_the_invitee_comes_from_the_models_own_plan():
    plan = _plan({**WORK_STEP, "outcome_promise": "I will write to ruby@studio.example"})
    filled = blocks.fill_template_step(MEETING_TEMPLATE, proposal=plan, want=None)
    assert filled["acts"][0]["with"] == "ruby@studio.example"


def test_the_filled_message_carries_no_when():
    """Rule 223: the platform inserts the person's real open times, so a day
    or a clock time in the agent's words can only contradict them."""
    filled = blocks.fill_template_step(
        MEETING_TEMPLATE, proposal=_plan(WORK_STEP), want="a call on Tuesday at 3pm"
    )
    assert blocks.meeting_problems(filled["acts"][0]) == []


# ---------------------------------------------------------------------------
# 2. The deterministic guard before filing
# ---------------------------------------------------------------------------
def test_a_plan_without_the_block_is_repaired_before_it_is_filed():
    """RULE 230: the whole form goes in, in the template's order, in FRONT of
    the model's own work. The calendar is connected before anything reads it."""
    api = _Api()
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert [step.get("ask") for step in filed] == ["GRANT", "APPROVE", "APPROVE"]
    assert blocks.grant_provider(filed[0]) == "google-calendar"
    assert [a["kind"] for s in filed for a in (s.get("acts") or [])] == ["meeting"]
    assert filed[-1] == WORK_STEP  # the model's own work step is untouched
    # Rule 121: an inserted step may not make the line fall.
    assert [step["declared_odds"] for step in filed] == [0.6, 0.6, 0.6]
    assert blocks.grant_problems(filed) == []


def test_only_the_missing_grant_is_inserted_before_a_declared_block():
    """The model wrote the meeting block itself and no grant. Only the GRANT
    step goes in, immediately before the block that needs it (REJ-35)."""
    api = _Api()
    declared = {**MEETING_TEMPLATE, "acts": [{"kind": "meeting", "with_name": "Ruby"}]}
    plan = _plan(WORK_STEP, declared)
    out = _provider(api).submit_proposal("t-1", plan, "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert [step.get("ask") for step in filed] == ["APPROVE", "GRANT", "APPROVE"]
    assert filed[0] == WORK_STEP
    assert filed[2] == declared
    assert blocks.grant_problems(filed) == []


def test_the_grant_the_model_wrote_itself_is_never_doubled():
    """A grant the model wrote its own way is REWRITTEN from the template, not
    doubled: the door counts a grant that names the account but not
    calendar.events.read as no grant at all, and the person does not need two
    connect cards."""
    api = _Api()
    own_grant = {
        "ask": "GRANT",
        "actor": "agent",
        "title": "Connect your calendar",
        "grant_request": {"connector": {"provider": "google-calendar"}},
        "declared_odds": 0.4,
    }
    out = _provider(api).submit_proposal("t-1", _plan(own_grant, WORK_STEP), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    grants = [step for step in filed if blocks.grant_provider(step)]
    assert len(grants) == 1
    assert grants[0]["grant_request"] == GRANT_TEMPLATE["grant_request"]
    # The block still went in, and it went in after the grant.
    assert [step.get("ask") for step in filed] == ["GRANT", "APPROVE", "APPROVE"]
    assert [a["kind"] for s in filed for a in (s.get("acts") or [])] == ["meeting"]


def test_a_grant_the_door_already_counts_is_left_alone():
    api = _Api()
    declared = {**MEETING_TEMPLATE, "acts": [{"kind": "meeting", "with_name": "Ruby"}]}
    own_grant = {
        "ask": "GRANT",
        "actor": "agent",
        "title": "My own words about connecting the calendar",
        "grant_request": {
            "connector": {
                "provider": "google-calendar",
                "actions": ["calendar.events.read", "calendar.events.create"],
            }
        },
        "declared_odds": 0.4,
    }
    plan = _plan(own_grant, declared)
    out = _provider(api).submit_proposal("t-1", plan, "k-1")
    assert out["ok"] is True
    assert api.submissions[0][1]["steps"] == plan["steps"]


def test_the_door_does_not_count_a_grant_without_the_access():
    """_GRANT_MIN_ACTIONS, mirrored: read is the floor."""
    named_only = {"ask": "GRANT", "grant_request": {"connector": {"provider": "google-calendar"}}}
    assert blocks.grant_provider(named_only) is None
    assert blocks.intended_grant_provider(named_only) == "google-calendar"
    assert blocks.grant_problems([named_only, {"acts": [{"kind": "meeting"}]}])


def test_a_plan_that_carries_the_grant_and_the_block_is_filed_as_written():
    api = _Api()
    declared = {**MEETING_TEMPLATE, "acts": [{"kind": "meeting", "with_name": "Ruby"}]}
    plan = _plan(GRANT_TEMPLATE, WORK_STEP, declared)
    out = _provider(api).submit_proposal("t-1", plan, "k-1")
    assert out["ok"] is True
    assert api.submissions[0][1]["steps"] == plan["steps"]


def test_a_one_step_template_still_works():
    """A 2.45 server whose plan_template is still one step: the harness may
    not wait for the server to catch up before it can file."""
    api = _Api(template=[MEETING_TEMPLATE])
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert [a["kind"] for s in filed for a in (s.get("acts") or [])] == ["meeting"]
    assert filed[-1] == WORK_STEP
    # No grant to insert, so the plan files and the door speaks for itself.
    assert [step for step in filed if blocks.grant_provider(step)] == []


def test_a_want_that_needs_no_block_is_left_alone():
    api = _Api(required=())
    plan = _plan(WORK_STEP)
    _provider(api).submit_proposal("t-1", plan, "k-1")
    assert api.submissions[0][1]["steps"] == plan["steps"]


def test_a_bad_window_is_caught_at_home():
    problems = blocks.meeting_problems({"kind": "meeting", "window": "sometime soonish"})
    assert problems and "window must be" in problems[0]
    assert blocks.meeting_problems({"kind": "meeting", "window": "next 10 days"}) == []
    assert blocks.meeting_problems({"kind": "meeting", "duration_min": 600})
    assert blocks.meeting_problems({"kind": "meeting", "message": "how about 3pm"})
    assert blocks.meeting_problems({"kind": "meeting", "with": "not-an-address"})


def test_the_local_validator_reports_a_block_no_grant_opens():
    """The REJ-35 mirror: a meeting block with no calendar grant before it."""
    problems = blocks.grant_problems([{"acts": [{"kind": "meeting"}]}])
    assert len(problems) == 1
    assert problems[0]["rej"] == "REJ-35"
    assert problems[0]["provider"] == "google-calendar"
    assert "Google Calendar" in problems[0]["message"]
    assert blocks.grant_problems([GRANT_TEMPLATE, {"acts": [{"kind": "meeting"}]}]) == []
    # The grant has to come BEFORE the block, not after it.
    assert blocks.grant_problems([{"acts": [{"kind": "meeting"}]}, GRANT_TEMPLATE])


def test_a_grant_gap_never_buries_the_plan_the_person_waits_on():
    """With no template to insert there is nothing to repair, so the filing
    goes and the door refuses it in its own words."""
    provider = _provider(_Api())
    failed = {"ok": False, "problems": [{"rej": "REJ-35", "message": "no grant"}]}
    assert provider._grant_gap_never_blocks_the_filing(failed, [])["ok"] is True
    assert provider._grant_gap_never_blocks_the_filing(failed, [MEETING_TEMPLATE])["ok"] is True
    held = provider._grant_gap_never_blocks_the_filing(failed, TWO_STEP_TEMPLATE)
    assert held["ok"] is False


def test_the_local_validator_reports_the_declared_block():
    api = _Api()
    provider = BookOfHousesTollBenchProvider(api)
    out = provider.validate_proposal(
        {
            "steps": [{"acts": [{"kind": "meeting", "window": "whenever"}]}],
            "smart_goals": ["one"],
            "pitch_title": "t",
            "pitch_body": "b",
            "strategy": "s",
            "capabilities": ["a"],
            "wins": [],
            "research_links": [{"url": "https://x.example", "note": "n"}],
            "skill_research": "r",
            "finalist_questions": None,
        }
    )
    paths = [problem["path"] for problem in out["problems"]]
    assert "steps.0.acts.0" in paths


# ---------------------------------------------------------------------------
# 3. The refusals: the form rides REJ-32, and one retry is the ceiling
# ---------------------------------------------------------------------------
def _refusal(rej, detail="no", template=None):
    body = {"ok": False, "rej": rej, "detail": detail}
    if template is not None:
        body["plan_template"] = template
    return BookOfHousesApiError(422, rej, detail, body=body)


def test_the_error_carries_the_body_the_server_sent():
    error = _refusal("REJ-32", "this want needs a meeting", [MEETING_TEMPLATE])
    assert error.rej == "REJ-32"
    assert error.plan_template == [MEETING_TEMPLATE]


def test_rej_32_is_repaired_from_the_refusal_and_filed_once():
    """The refusal carries the form. The agent that never read the brief is
    exactly the agent this refuses, so it fills it in and files once."""
    api = _Api(required=(), refuse=_refusal("REJ-32", "needs meeting", [MEETING_TEMPLATE]))
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "k-1")
    assert out["ok"] is True
    assert len(api.submissions) == 2
    second = api.submissions[1]
    assert second[2] == "k-1-rej32"
    assert [a["kind"] for s in second[1]["steps"] for a in (s.get("acts") or [])] == ["meeting"]


def test_rej_35_is_repaired_from_the_refusal_and_filed_once():
    """RULE 230: REJ-35 carries the form exactly as REJ-32 does. The plan
    already declares the meeting; the grant that opens the calendar is what
    goes in, and it goes in before the block."""
    api = _Api(
        required=(),
        refuse=_refusal("REJ-35", "the meeting block needs a calendar grant", TWO_STEP_TEMPLATE),
    )
    declared = {**MEETING_TEMPLATE, "acts": [{"kind": "meeting", "with_name": "Ruby"}]}
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP, declared), "k-1")
    assert out["ok"] is True
    assert len(api.submissions) == 2
    second = api.submissions[1]
    assert second[2] == "k-1-rej35"
    filed = second[1]["steps"]
    assert [step.get("ask") for step in filed] == ["APPROVE", "GRANT", "APPROVE"]
    assert [a["kind"] for s in filed for a in (s.get("acts") or [])] == ["meeting"]


def test_rej_33_is_one_correction_then_the_round_is_over():
    """Never loop: a harness filed and withdrew about a hundred times in 90
    minutes on 2026-09-04."""
    provider = _provider(_Api(required=()))
    error = _refusal("REJ-33", "window must be 'next week'")
    first = provider._block_refusal("t-1", error)
    assert first["terminal"] is False and first["rej"] == "REJ-33"
    second = provider._block_refusal("t-1", error)
    assert second["terminal"] is True


def test_rej_34_comes_back_as_a_refusal_the_model_can_read():
    api = _Api(required=(), refuse=_refusal("REJ-34", "this step describes an invitation"))
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "k-1")
    assert out["ok"] is False and out["rej"] == "REJ-34"
    assert out["terminal"] is False
    assert "this step describes an invitation" in out["detail"]
    assert len(api.submissions) == 1


# ---------------------------------------------------------------------------
# 4. Hands off a block the platform is running (rule 229)
# ---------------------------------------------------------------------------
class _StepApi(_Api):
    def __init__(self, acts, declared):
        super().__init__()
        self.step = {
            "ok": True,
            "deal": {"id": "d-1"},
            "current_step": {"id": "s-1", "number": 2, "state": "agent_working"},
            "acts": acts,
            "declared_acts": declared,
        }
        self.acts_filed = []
        self.outcomes = []

    def current_step(self, deal_id):
        return self.step

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        self.acts_filed.append(payload)
        return {"ok": True, "act_id": "ap-2"}

    def file_outcome(self, target_id, payload, idempotency_key):
        self.outcomes.append(payload)
        return {"ok": True}


def _held_meeting_step():
    return _StepApi(
        [{"act_id": "ap-1", "kind": "meeting", "state": "held", "note": None}],
        [{"kind": "meeting", "filed": 1, "held": 1, "executed": 0}],
    )


def test_the_declared_acts_payload_reaches_the_model():
    api = _held_meeting_step()
    payload = BookOfHousesTollBenchProvider(api).current_step("d-1")
    assert payload["declared_acts"] == api.step["declared_acts"]


def test_no_second_act_on_a_block_the_platform_filed():
    api = _held_meeting_step()
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d-1")
    out = provider.propose_act(
        "d-1", "s-1", {"kind": "meeting", "with": "ruby@studio.example"}, "k-9"
    )
    assert out["ok"] is False and out["error"] == "platform_owned_block"
    assert api.acts_filed == []


def test_no_outcome_on_a_block_the_platform_will_close():
    api = _held_meeting_step()
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d-1")
    out = provider.file_outcome(
        "t-1", {"note": "booked", "text": "done", "step_ref": "s-1"}, "k-9"
    )
    assert out["ok"] is False and out["error"] == "platform_owned_block"
    assert api.outcomes == []


def test_an_owed_reply_still_goes_through_on_a_block_step():
    """Rule 220: the answer a person is owed is never the duplicate act."""
    api = _held_meeting_step()
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d-1")
    out = provider.propose_act(
        "d-1", "s-1", {"kind": "email", "in_reply_to": "m-1", "body_text": "yes"}, "k-9"
    )
    assert out["ok"] is True


def test_a_failed_block_hands_the_step_back():
    """Rule 225: an act that fails is the work coming back, so the harness
    files the replacement rather than sitting on a dead act."""
    api = _StepApi(
        [{"act_id": "ap-1", "kind": "meeting", "state": "failed", "error": "lapsed"}],
        [{"kind": "meeting", "filed": 1, "held": 0, "executed": 0}],
    )
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d-1")
    out = provider.propose_act(
        "d-1", "s-1", {"kind": "meeting", "with": "ruby@studio.example"}, "k-9"
    )
    assert out["ok"] is True
    assert api.acts_filed and api.acts_filed[0]["kind"] == "meeting"


def test_a_brief_that_has_closed_never_blocks_the_plan_the_person_waits_on():
    """A want whose agent is already selected can stop answering the open
    -target brief. The informed plan is the filing the person is waiting on,
    so a brief we cannot read costs the local repair and nothing else: the
    REJ-32 refusal still carries the form."""

    class _ClosedBrief(_Api):
        def target_brief(self, target_id):
            raise BookOfHousesApiError(404, "not_found", "Target not found or not open.")

        def submit_informed_plan(self, target_id, proposal_id, plan, idempotency_key):
            self.submissions.append((target_id, plan, idempotency_key))
            return {"ok": True}

    api = _ClosedBrief()
    provider = BookOfHousesTollBenchProvider(api)
    provider.validate_proposal = lambda proposal: {"ok": True, "problems": []}
    provider.list_proposals = lambda: {
        "ok": True,
        "proposals": [{"id": "p-1", "target_goal_id": "t-1", "steps": [WORK_STEP]}],
    }
    out = provider.submit_informed_plan(
        "t-1", "p-1", {"steps": [WORK_STEP], "accept_rules": True}, "k-1"
    )
    assert out["ok"] is True
    assert len(api.submissions) == 1
