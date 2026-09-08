"""RULE 236 (Steven, 2026-09-08) -- A CONNECTION IS NOT A STEP.

It is a `connect_account` ROW inside the step of the action that needs it. One
card: the account rows, then what the step does, then one button that stays
asleep until every row is settled. The meeting plan is ONE step -- a Google
Calendar row, a Gmail row and the meeting block on a single card -- and a NEW
plan that lifts a registry connector back into a GRANT step of its own is
refused REJ-38 (grant_step_removed).

WHAT FORCED THIS FILE. The harness carried the two-step law as a hardcoded
fact: `BLOCK_GRANTS = {"meeting": "google-calendar"}`, and a connection counted
only when it was an `ask == "GRANT"` step. Against the one-step template the
bench now publishes, a CORRECT plan looked like a meeting block nothing opened
the calendar for. The harness manufactured a REJ-35 the bench never emits and
refused the plan at home -- local_validation_failed -- so the round was spent
with nothing filed and no refusal from the door to learn from.

The four things this covers:
  1. the one-step template passes local validation, and files,
  2. a two-step template from an un-promoted bench still files as given,
  3. a standalone GRANT step is never SYNTHESISED: what goes in is the row,
  4. a REJ-38 off the door is logged, repaired from the brief's template once,
     and never retried in a loop.
"""
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

# The registry's own answer. The harness knows this fact from nowhere else.
NEEDS = {"meeting": ("google-calendar",)}

CALENDAR_ROW = {
    "id": "connect-google-calendar-1",
    "ask": "grant",
    "format": "connect_account",
    "required": True,
    "title": "Connect your calendar",
    "config": {
        "grant_request": {
            "kind": "oauth_connection",
            "what": "Your Google Calendar",
            "why": "So we can see when you are free and put this on your calendar",
            "scope": "read events and add or change only what this target adds",
            "until": "target_end",
            "exposure": "agent_acts_through_connection",
            "connector": {
                "provider": "google-calendar",
                "actions": [
                    "calendar.events.read",
                    "calendar.event.create",
                    "calendar.event.update",
                ],
                "resources": {"calendar_ids": ["primary"]},
            },
        },
        "fallback": "lesser",
        "fallback_note": "Without your calendar we cannot see when you are free.",
    },
}

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
        "fallback_note": "The invitation goes out from the Book of Houses mailbox.",
    },
}

# The bench's meeting plan_template as of 2026-09-08: ONE step.
ONE_STEP_TEMPLATE = [
    {
        "ask": "APPROVE",
        "actor": "agent",
        "title": "Planned step",
        "outcome_promise": (
            "Book of Houses offers the invitee times from your calendar, emails "
            "the invitee to pick one, puts the meeting on both calendars, and "
            "asks you to approve the booking."
        ),
        "har_blocks": [
            CALENDAR_ROW,
            GMAIL_ROW,
            {
                "id": "meeting-booked",
                "ask": "approve",
                "format": "review_approve",
                "required": True,
                "title": "Approve the booked meeting",
                "description": "You are confirming the 30-minute meeting.",
            },
        ],
        "acts": [
            {
                "kind": "meeting",
                "with": "<invitee email, or leave this key out>",
                "with_name": "<invitee first name>",
                "duration_min": 30,
                "window": "next week",
                "title": "<what the meeting is called>",
                "message": "<your words. No dates, no times.>",
            }
        ],
        "rounds": 2,
        "declared_odds": "<fill 0.05..0.99>",
        "declared_odds_reason": "<why that number>",
        "person_minutes": 3,
        "line_item_amount": 0,
        "agent_court_estimate": 1,
        "examples": [],
        "materials": [],
    }
]

# The retired shape, still handed out by a bench that has not been promoted.
LEGACY_GRANT_STEP = {
    "ask": "GRANT",
    "actor": "agent",
    "title": "Allow calendar access",
    "outcome_promise": "Book of Houses can see when you are free.",
    "grant_request": {
        "kind": "oauth_connection",
        "what": "Your Google Calendar",
        "why": "So we can find open times",
        "scope": "read events and add the one meeting",
        "until": "target_end",
        "connector": {
            "provider": "google-calendar",
            "actions": ["calendar.events.read", "calendar.events.create"],
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

LEGACY_BLOCK_STEP = {
    "ask": "APPROVE",
    "actor": "agent",
    "title": "Book a meeting with the invitee",
    "outcome_promise": "Book of Houses books the meeting and hands you the receipt.",
    "har_blocks": [
        {
            "id": "meeting-booked",
            "ask": "approve",
            "format": "review_approve",
            "required": True,
            "title": "Approve the booked meeting",
        }
    ],
    "acts": [{"kind": "meeting", "duration_min": 30, "window": "next week"}],
    "rounds": 2,
    "declared_odds": "<fill 0.05..0.99>",
    "declared_odds_reason": "<why that number>",
    "person_minutes": 3,
    "line_item_amount": 0,
    "agent_court_estimate": 1,
    "examples": [],
    "materials": [],
}

TWO_STEP_TEMPLATE = [LEGACY_GRANT_STEP, LEGACY_BLOCK_STEP]

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
    """The bench as a live one answers. CONTRACT 3.0: `plan_template` is the
    blank skeleton and the blocks are in `block_templates` -- a repair that
    reads only the skeleton finds no row on any real brief."""

    def __init__(
        self, *, template=None, required=("meeting",), refuse=None, catalog=None
    ):
        self.template = ONE_STEP_TEMPLATE if template is None else list(template)
        self.required = list(required)
        self.refuse = refuse
        self.catalog = {"meeting": self.template} if catalog is None else catalog
        self.submissions = []
        self.plans = []
        self.acks = 0

    def target_brief(self, target_id):
        return {
            "ok": True,
            "brief": {
                "target_id": target_id,
                "round": 1,
                "want": "I want to set up a call with Ruby to plan the launch",
                "required_blocks": self.required,
                "required_blocks_reason": None,
                "plan_template": self.template,
                "block_templates": self.catalog,
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

    def me(self):
        return {
            "ok": True,
            "reachability_test": {"reachable": self.acks >= 1, "reachable_at": "now"},
        }

    def ack_reachability_ping(self):
        self.acks += 1
        return self.me()

    def act_kinds(self):
        return {
            "kinds": {
                "meeting": {
                    "declaration": {},
                    "template": ONE_STEP_TEMPLATE[0],
                    "requires_grants": ["google-calendar"],
                }
            }
        }

    def proposal_schema(self):
        return {"type": "object"}

    def current_step(self, deal_id):
        return {}


def _provider(api):
    provider = BookOfHousesTollBenchProvider(api)
    # The JSON schema is production's business, not this file's subject.
    provider.validate_proposal = lambda proposal: {"ok": True, "problems": []}
    return provider


def _refusal(rej, detail, template=None):
    body = {"ok": False, "rej": rej, "detail": detail}
    if template is not None:
        body["plan_template"] = template
    return BookOfHousesApiError(422, rej, detail, body=body)


# ---------------------------------------------------------------------------
# 1. The one-step plan is a legal plan, at home and at the door
# ---------------------------------------------------------------------------
def test_a_row_on_the_step_is_the_connection():
    step = ONE_STEP_TEMPLATE[0]
    assert blocks.connect_row_providers(step) == {"google-calendar", "google-gmail"}
    # It is not a GRANT step, and it does not have to be.
    assert blocks.grant_provider(step) is None
    assert blocks.step_opens(step) == {"google-calendar", "google-gmail"}


def test_a_row_that_names_no_useful_action_is_no_connection():
    """The bench's own `_useful`: naming the account and none of its actions is
    the same nothing a GRANT step naming no actions always was."""
    named_only = {
        "har_blocks": [
            {
                "format": "connect_account",
                "config": {
                    "grant_request": {"connector": {"provider": "google-calendar"}}
                },
            }
        ]
    }
    assert blocks.connect_row_providers(named_only) == set()


def test_the_one_step_meeting_plan_passes_local_validation():
    """THE REGRESSION. Before rule 236 landed here this returned a REJ-35 the
    bench never emits, and the plan died at home."""
    assert blocks.grant_problems(ONE_STEP_TEMPLATE, NEEDS) == []


def test_the_one_step_template_files_untouched():
    api = _Api()
    plan = _plan(*ONE_STEP_TEMPLATE, WORK_STEP)
    out = _provider(api).submit_proposal("t-1", plan, "k-1")
    assert out["ok"] is True
    assert len(api.submissions) == 1
    assert api.submissions[0][1]["steps"] == plan["steps"]


def test_a_plan_missing_the_block_takes_the_one_step_form():
    api = _Api()
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert len(filed) == 2
    assert [a["kind"] for s in filed for a in (s.get("acts") or [])] == ["meeting"]
    assert filed[-1] == WORK_STEP
    # One card, and no GRANT step anywhere.
    assert [step.get("ask") for step in filed] == ["APPROVE", "APPROVE"]
    assert blocks.connect_row_providers(filed[0]) == {"google-calendar", "google-gmail"}
    assert blocks.grant_problems(filed, NEEDS) == []


# ---------------------------------------------------------------------------
# 2. A bench that has not been promoted still gets what it hands out
# ---------------------------------------------------------------------------
def test_a_two_step_template_is_still_filed_as_given():
    """The harness may never wait for a server to catch up before it can file.
    Where the template carries a GRANT step, that is what goes in."""
    api = _Api(template=TWO_STEP_TEMPLATE)
    out = _provider(api).submit_proposal("t-1", _plan(WORK_STEP), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert [step.get("ask") for step in filed] == ["GRANT", "APPROVE", "APPROVE"]
    assert blocks.grant_provider(filed[0]) == "google-calendar"
    assert blocks.grant_problems(filed, NEEDS) == []


def test_a_grant_step_the_old_bench_published_is_never_retired():
    """`retire_grant_steps` fires on positive evidence only: a template row for
    the provider AND no template GRANT step for it."""
    assert blocks.retired_grant_providers(TWO_STEP_TEMPLATE) == set()
    plan = _plan(LEGACY_GRANT_STEP, LEGACY_BLOCK_STEP)
    assert blocks.retire_grant_steps(plan, TWO_STEP_TEMPLATE) == (plan, [])


# ---------------------------------------------------------------------------
# 3. A standalone GRANT step is never synthesised. The ROW goes in.
# ---------------------------------------------------------------------------
def test_the_missing_connection_goes_in_as_a_row_not_a_step():
    api = _Api()
    declared = {
        **WORK_STEP,
        "title": "Book the call",
        "acts": [{"kind": "meeting", "with_name": "Ruby"}],
    }
    out = _provider(api).submit_proposal("t-1", _plan(declared), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    # One step, still the model's own, now carrying the platform's row.
    assert len(filed) == 1
    assert filed[0]["title"] == "Book the call"
    assert [step.get("ask") for step in filed] == ["APPROVE"]
    assert "google-calendar" in blocks.connect_row_providers(filed[0])
    assert blocks.grant_problems(filed, NEEDS) == []
    # The row is the platform's, word for word.
    assert CALENDAR_ROW in filed[0]["har_blocks"]


def test_the_row_comes_out_of_the_catalog_on_a_live_brief():
    """The production shape: `plan_template` is a blank skeleton with no block
    in it at all, and `block_templates` is the only place the connect row
    exists. A repair that read the skeleton alone did nothing here."""
    api = _Api(required=(), template=[], catalog={"meeting": ONE_STEP_TEMPLATE})
    declared = {
        **WORK_STEP,
        "title": "Book the call",
        "acts": [{"kind": "meeting", "with_name": "Ruby"}],
    }
    out = _provider(api).submit_proposal("t-1", _plan(declared), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert [step.get("ask") for step in filed] == ["APPROVE"]
    assert "google-calendar" in blocks.connect_row_providers(filed[0])
    assert blocks.grant_problems(filed, NEEDS) == []


def test_the_model_own_grant_step_is_moved_into_the_action():
    """REJ-38, caught at home. The model wrote the step it was taught for a
    year; the row lands on the block and the step goes."""
    api = _Api()
    own_grant = {
        "ask": "GRANT",
        "actor": "agent",
        "title": "Connect your calendar",
        "grant_request": {
            "connector": {
                "provider": "google-calendar",
                "actions": ["calendar.events.read"],
            }
        },
        "declared_odds": 0.4,
    }
    declared = {
        **WORK_STEP,
        "title": "Book the call",
        "acts": [{"kind": "meeting", "with_name": "Ruby"}],
    }
    out = _provider(api).submit_proposal("t-1", _plan(own_grant, declared), "k-1")
    assert out["ok"] is True
    filed = api.submissions[0][1]["steps"]
    assert [step.get("ask") for step in filed] == ["APPROVE"]
    assert "google-calendar" in blocks.connect_row_providers(filed[0])


def test_the_access_mold_is_never_touched():
    """A GRANT step for something the connector registry has no recipe for is
    still the right shape, and is the only shape there is for it."""
    mold = {
        "ask": "GRANT",
        "actor": "agent",
        "title": "Allow access to the studio drive",
        "grant_request": {"connector": {"provider": "studio-drive"}},
        "declared_odds": 0.4,
    }
    plan = _plan(mold, ONE_STEP_TEMPLATE[0])
    fixed, moved = blocks.retire_grant_steps(plan, ONE_STEP_TEMPLATE)
    assert moved == []
    assert fixed["steps"] == plan["steps"]


def test_a_grant_step_with_nowhere_to_move_the_row_is_left_alone():
    """No step declares an act, so there is nothing that USES the connection.
    Dropping the step would take work out of the plan; the door speaks."""
    own_grant = {
        "ask": "GRANT",
        "title": "Connect your calendar",
        "grant_request": {
            "connector": {
                "provider": "google-calendar",
                "actions": ["calendar.events.read"],
            }
        },
    }
    plan = _plan(own_grant, WORK_STEP)
    fixed, moved = blocks.retire_grant_steps(plan, ONE_STEP_TEMPLATE)
    assert moved == []
    assert fixed["steps"] == plan["steps"]


# ---------------------------------------------------------------------------
# 4. REJ-38 off the door: repaired once, never in a loop
# ---------------------------------------------------------------------------
_REJ38_DETAIL = (
    "step 1 is a GRANT step whose whole job is to connect 'google-calendar'. "
    "RULE 236 -- A CONNECTION IS NOT A STEP, IT IS PART OF THE ACTION THAT "
    "NEEDS IT. Delete this step."
)


def _one_shot_blind(provider):
    """A harness whose FIRST read of the brief's published steps came back
    empty -- the catalog had not been read yet -- so the local pass did
    nothing and the DOOR is what teaches it. The repair then reads the rows
    and files once. This is the seam the re-file exists for; without it the
    local pass always gets there first and the door path is dead code."""
    seen = {"n": 0}
    real = provider._published_steps

    def once(brief):
        seen["n"] += 1
        return [] if seen["n"] == 1 else real(brief)

    provider._published_steps = once
    return provider


def test_rej_38_is_repaired_from_the_brief_and_filed_once():
    """The refusal carries NO plan_template -- what it hands back is the row --
    so the repair reads the row off the brief's own published blocks."""
    api = _Api(required=(), refuse=_refusal("REJ-38", _REJ38_DETAIL))
    own_grant = {
        "ask": "GRANT",
        "title": "Connect your calendar",
        "grant_request": {
            "connector": {
                "provider": "google-calendar",
                "actions": ["calendar.events.read"],
            }
        },
    }
    declared = {**WORK_STEP, "acts": [{"kind": "meeting", "with_name": "Ruby"}]}
    provider = _one_shot_blind(_provider(api))
    provider._grant_requirements = lambda: {}
    out = provider.submit_proposal("t-1", _plan(own_grant, declared), "k-1")
    assert out["ok"] is True
    assert len(api.submissions) == 2
    assert api.submissions[1][2] == "k-1-rej38"
    filed = api.submissions[1][1]["steps"]
    assert [step.get("ask") for step in filed] == ["APPROVE"]
    assert "google-calendar" in blocks.connect_row_providers(filed[0])



def test_rej_38_with_nothing_to_move_is_handed_back_not_retried():
    """No loop. The model gets the door's own sentence and the fix, and the
    round is not spent: nothing was written."""
    api = _Api(
        required=(), template=[], catalog={}, refuse=_refusal("REJ-38", _REJ38_DETAIL)
    )
    own_grant = {
        "ask": "GRANT",
        "title": "Connect your calendar",
        "grant_request": {"connector": {"provider": "google-calendar"}},
    }
    out = _provider(api).submit_proposal("t-1", _plan(own_grant, WORK_STEP), "k-1")
    assert out["ok"] is False
    assert out["error"] == "grant_step_removed"
    assert out["rej"] == "REJ-38"
    assert out["terminal"] is False
    assert _REJ38_DETAIL in out["detail"]
    assert "connect_account" in out["fix"]
    assert len(api.submissions) == 1


def test_the_informed_plan_answers_rej_38_the_same_way():
    api = _Api(refuse=_refusal("REJ-38", _REJ38_DETAIL))
    own_grant = {
        "ask": "GRANT",
        "title": "Connect your calendar",
        "grant_request": {
            "connector": {
                "provider": "google-calendar",
                "actions": ["calendar.events.read"],
            }
        },
    }
    declared = {**WORK_STEP, "acts": [{"kind": "meeting", "with_name": "Ruby"}]}
    provider = _one_shot_blind(_provider(api))
    provider._grant_requirements = lambda: {}
    provider.list_proposals = lambda: {
        "ok": True,
        "proposals": [
            {"id": "p-1", "target_goal_id": "t-1", "steps": [own_grant, declared]}
        ],
    }
    out = provider.submit_informed_plan(
        "t-1", "p-1", {"steps": [own_grant, declared], "accept_rules": True}, "k-1"
    )
    assert out["ok"] is True
    assert len(api.plans) == 2
    assert api.plans[1][2] == "k-1-rej38"
    filed = api.plans[1][1]["steps"]
    assert [step.get("ask") for step in filed] == ["APPROVE"]


# ---------------------------------------------------------------------------
# 5. Nothing about a kind is written in the harness any more
# ---------------------------------------------------------------------------
def test_the_harness_knows_no_kind_by_heart():
    assert blocks.BLOCK_GRANTS == {}
    assert not hasattr(blocks, "GRANT_FIRST_SENTENCE")
    assert "connect_account" in blocks.CONNECTION_IN_THE_ACTION_SENTENCE
    assert "REJ-38" in blocks.CONNECTION_IN_THE_ACTION_SENTENCE


def test_the_rows_are_read_off_the_catalog_not_only_the_skeleton():
    """CONTRACT 3.0: plan_template is a blank skeleton and block_templates is
    where the blocks live. Reading only the skeleton found no row on any live
    brief and quietly did nothing."""
    skeleton = [WORK_STEP]
    catalog = {"meeting": ONE_STEP_TEMPLATE}
    assert blocks.retired_grant_providers(skeleton) == set()
    published = blocks.published_template_steps(skeleton, catalog)
    assert blocks.retired_grant_providers(published) == {
        "google-calendar",
        "google-gmail",
    }
    assert blocks.template_connect_rows(published, "google-calendar") == [CALENDAR_ROW]


def test_the_requirement_comes_off_the_act_registry():
    provider = BookOfHousesTollBenchProvider(_Api())
    assert provider._grant_requirements() == {"meeting": ("google-calendar",)}


def test_a_registry_that_cannot_be_read_leaves_the_mirror_silent():
    class _Broken(_Api):
        def act_kinds(self):
            raise BookOfHousesApiError(500, "boom", "no catalog")

    provider = BookOfHousesTollBenchProvider(_Broken())
    assert provider._grant_requirements() == {}
