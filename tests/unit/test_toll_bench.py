from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.fleet import FleetStore
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider


class FakeApi:
    def __init__(self):
        self.acks = 0
        self.submissions = []
        self.brief_round = 1
        self.brief_your_bid = None

    def target_brief(self, target_id):
        return {
            "ok": True,
            "brief": {
                "target_id": target_id,
                "round": self.brief_round,
                "your_bid": self.brief_your_bid,
            },
        }

    def me(self):
        return {
            "ok": True,
            "reachability_test": {
                "reachable": self.acks >= 2,
                "reachable_at": "now" if self.acks >= 2 else None,
            },
        }

    def ack_reachability_ping(self):
        self.acks += 1
        return self.me()

    def protocol(self):
        return {"contract_version": "current"}

    def skill(self):
        return (
            "## Autonomous start\nStart here.\n"
            "## Your to-do list\nRead attention.\n"
            "## Work pulse\nPulse.\n"
        )

    def proposal_schema(self):
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["pitch_title", "pitch_body", "smart_goals", "finalist_questions"],
            "properties": {
                "pitch_title": {"type": "string"},
                "pitch_body": {"type": "string"},
                "smart_goals": {"type": "array"},
                "finalist_questions": {"type": "array"},
            },
        }

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.submissions.append((target_id, proposal, idempotency_key))
        return {"ok": True, "proposal_id": "p1"}

    def proposals(self):
        return [
            {
                **_valid_proposal(),
                "id": "p1",
                "target_goal_id": "t1",
                "model_declared": "test-model",
                "total_ask_cents": 0,
                "allocation": {"ad_spend": 0, "tools": 0, "agent_work": 0},
                "timeline_days": 2,
                "finish_line": "Delivered",
                "finish_line_cents": 0,
                "steps": [
                    {
                        "title": "Deliver",
                        "ask": "PROVIDE",
                        "line_item_amount": 0,
                        "har_blocks": [{"id": "details", "format": "written_response"}],
                    }
                ],
            }
        ]

    def submit_informed_plan(self, target_id, proposal_id, plan, idempotency_key):
        self.submissions.append((target_id, proposal_id, plan, idempotency_key))
        return {"ok": True, "plan_revised": True}

    def current_step(self, deal_id):
        return {
            "ok": True,
            "deal": {"id": deal_id, "target_goal_id": "t1", "status": "signed"},
            "current_step": {"id": "s1", "state": "agent_working", "ask": "APPROVE"},
            "step_thread": {"messages": [], "unread_from_person": 0},
            "access": {"grants": []},
            "released_materials": [],
            "released_materials_count": 0,
        }

    def declare_outside_wait(self, deal_id, step_id, payload, idempotency_key):
        self.submissions.append((deal_id, step_id, payload, idempotency_key))
        return {"ok": True, "step_state": "waiting_outside",
                "waiting_outside": payload}

    def withdraw_act_declaration(self, deal_id, step_id, payload, idempotency_key):
        self.submissions.append((deal_id, step_id, payload, idempotency_key))
        return {"ok": True, "kind": payload.get("kind"),
                "reason": payload.get("reason"), "message_id": "m-218",
                "still_owed": []}

    def post_step_message(self, deal_id, step_id, reply, idempotency_key):
        self.submissions.append((deal_id, step_id, reply, idempotency_key))
        return {"ok": True, "message": {"id": "m1", "body": reply}}

    def post_check_in(self, deal_id, pulse, idempotency_key):
        self.submissions.append((deal_id, pulse, idempotency_key))
        return {
            "ok": True,
            "work_pulse": {
                "id": "wp1",
                "step_id": "s1",
                "progress_percent": pulse["progress_percent"],
            },
            "pulse_cadence": {"checkpoints": [0, 25, 50, 75, 100]},
            "step_thread": {"unread_from_person": 0, "unanswered_elsewhere": []},
        }

    def file_outcome(self, target_id, outcome, idempotency_key):
        self.submissions.append((target_id, outcome, idempotency_key))
        return {"ok": True, "filed": True}

    def withdraw_proposal(self, proposal_id, payload):
        self.submissions.append((proposal_id, payload))
        return {"ok": True, "withdrawn": True, "returned_count": 4}


def _valid_proposal():
    return {
        "pitch_title": "One clear idea",
        "pitch_body": "A concise description of the proposed work.",
        "smart_goals": ["one"],
        "finalist_questions": [["one?", "two?", "three?", "four?"]],
    }


def test_reachability_completes_exact_two_ping_handshake_and_is_idempotent():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    first = provider.ensure_reachable()
    second = provider.ensure_reachable()

    assert first["ok"] is True
    assert first["acknowledgements"] == 2
    assert second["acknowledgements"] == 0
    assert api.acks == 2


def test_submit_proposal_validates_current_schema_before_writing():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    rejected = provider.submit_proposal("t1", {"pitch_title": ""}, "key-1")
    accepted = provider.submit_proposal("t1", _valid_proposal(), "key-2")

    assert rejected["error"] == "local_validation_failed"
    assert api.submissions == [("t1", _valid_proposal(), "key-2")]
    assert accepted == {"ok": True, "proposal_id": "p1"}
    assert api.acks == 2


def _step(n, odds):
    return {"title": f"Step {n}", "ask": "APPROVE", "outcome_promise": f"Deliver part {n}",
            "line_item_amount": 0, "person_minutes": 10, "agent_court_estimate": 2.0,
            "declared_odds": odds,
            "har_blocks": [{"id": f"blk-{n}", "format": "review_approve", "title": "Review"}]}


def _plan_with_line(odds_line):
    return {**_valid_proposal(), "total_ask_cents": 0, "finish_line_cents": 0,
            "allocation": {"ad_spend": 0, "tools": 0, "agent_work": 0},
            "steps": [_step(i + 1, o) for i, o in enumerate(odds_line)]}


def _rej29_problems(result):
    return [p for p in result.get("problems", []) if "REJ-29" in p.get("message", "")]


def test_local_validation_refuses_a_declared_line_that_falls_before_spending_the_filing():
    # Rule 121 / REJ-29 (contract 2.34): a plan is filed all at once, so a later
    # step declared LOWER than an earlier one means the steps were priced one at
    # a time, not the outcome. The line that forced it: 95 -> 50 -> 75.
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.submit_proposal("t1", _plan_with_line([0.95, 0.50, 0.75]), "key-1")

    assert result["error"] == "local_validation_failed"
    hits = _rej29_problems(result)
    assert len(hits) == 1
    assert hits[0]["path"] == "steps.1.declared_odds"
    assert "step 2 declares 50% but step 1 declared 95%" in hits[0]["message"]
    assert "not the chance you clear the step" in hits[0]["message"]
    assert api.submissions == []  # nothing reached the bench


def test_local_validation_lets_a_rising_or_flat_line_through():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    rising = provider.submit_proposal("t1", _plan_with_line([0.25, 0.40, 0.70]), "key-1")
    flat = provider.submit_proposal("t1", _plan_with_line([0.85, 0.85]), "key-2")

    assert _rej29_problems(rising) == []
    assert _rej29_problems(flat) == []


def test_local_validation_never_compares_a_line_with_an_illegal_number():
    # An illegal number is the schema's report (REJ-16 at the bench), never a
    # line report: the line check only runs when every number is legal. The
    # fake schema here does not carry the range, so all this test owns is
    # that no REJ-29 problem appears for 0.9 -> 1.5 -> 0.5.
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.submit_proposal("t1", _plan_with_line([0.9, 1.5, 0.5]), "key-1")

    assert _rej29_problems(result) == []


def test_submit_proposal_caps_only_this_harness_fleet_at_four(tmp_path):
    api = FakeApi()
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    providers = []
    for index in range(5):
        agent_id = f"agent-{index}"
        fleet.register_agent(
            agent_id=agent_id,
            name=f"Agent {index}",
            config_path=tmp_path / f"agent-{index}.yaml",
        )
        providers.append(
            BookOfHousesTollBenchProvider(
                api,
                fleet=fleet,
                fleet_agent_id=agent_id,
                fleet_proposal_limit=4,
            )
        )

    results = [
        provider.submit_proposal("t1", _valid_proposal(), f"key-{index}")
        for index, provider in enumerate(providers)
    ]

    assert all(result.get("proposal_id") == "p1" for result in results[:4])
    assert results[4]["error"] == "fleet_proposal_limit"
    assert results[4]["fleet_count"] == 4
    assert len(api.submissions) == 4


def _fleet_provider(api, fleet, agent_id="agent-1", tmp_path=None):
    fleet.register_agent(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        config_path=(tmp_path / f"{agent_id}.yaml") if tmp_path else f"/tmp/{agent_id}.yaml",
    )
    return BookOfHousesTollBenchProvider(
        api, fleet=fleet, fleet_agent_id=agent_id, fleet_proposal_limit=4
    )


def test_terminal_409_marks_the_round_reviewed_instead_of_looping(tmp_path):
    # Found live 2026-08-26: workers retried one closed want 34-160 times
    # because a 409 left no trace and the scan re-selected the same target.
    class ClosedApi(FakeApi):
        def submit_proposal(self, target_id, proposal, idempotency_key):
            raise BookOfHousesApiError(
                409, "finalists named — bidding closed on this target", "closed"
            )

    api = ClosedApi()
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    provider = _fleet_provider(api, fleet, tmp_path=tmp_path)

    result = provider.submit_proposal("t1", _valid_proposal(), "key-1")

    assert result["ok"] is False
    assert result["terminal"] is True
    assert result["error"] == "proposal_refused_terminally"
    assert fleet.reviewed_target_keys("agent-1") == {"t1:round:1"}
    # The reserved (never confirmed) slot is released, not burned.
    assert fleet.proposal_count("t1", "1") == 0


def test_terminal_404_refuses_without_retry_and_without_burning_a_slot(tmp_path):
    class GoneApi(FakeApi):
        def target_brief(self, target_id):
            raise BookOfHousesApiError(404, "target not found or not open", "gone")

    api = GoneApi()
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    provider = _fleet_provider(api, fleet, tmp_path=tmp_path)

    result = provider.submit_proposal("t1", _valid_proposal(), "key-1")

    assert result["ok"] is False
    assert result["terminal"] is True
    assert result["error"] == "target_not_open"
    assert api.submissions == []
    assert fleet.proposal_count("t1", "1") == 0


def test_repost_round_gets_a_fresh_fleet_slot(tmp_path):
    # The round-1 confirmed slot must not answer for round 2: the repost is
    # fresh work and the agent's new bid must actually reach production.
    api = FakeApi()
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    provider = _fleet_provider(api, fleet, tmp_path=tmp_path)

    first = provider.submit_proposal("t1", _valid_proposal(), "key-round-1")
    assert first["proposal_id"] == "p1"
    assert fleet.proposal_count("t1", "1") == 1

    api.brief_round = 2  # the want failed and reposted
    second = provider.submit_proposal("t1", _valid_proposal(), "key-round-2")

    assert second.get("idempotent") is None
    assert second["proposal_id"] == "p1"
    assert len(api.submissions) == 2
    assert fleet.proposal_count("t1", "1") == 1
    assert fleet.proposal_count("t1", "2") == 1


def test_repost_round_resets_the_fleet_cap(tmp_path):
    # Four round-1 bids from this fleet must not lock the fleet out of the
    # repost: the cap counts per round, not per target forever.
    api = FakeApi()
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    providers = [
        _fleet_provider(api, fleet, agent_id=f"agent-{index}", tmp_path=tmp_path)
        for index in range(5)
    ]
    for index, provider in enumerate(providers[:4]):
        assert provider.submit_proposal("t1", _valid_proposal(), f"r1-{index}")["ok"]
    assert providers[4].submit_proposal("t1", _valid_proposal(), "r1-4")[
        "error"
    ] == "fleet_proposal_limit"

    api.brief_round = 2
    result = providers[4].submit_proposal("t1", _valid_proposal(), "r2-4")

    assert result["proposal_id"] == "p1"
    assert fleet.proposal_count("t1", "2") == 1


def test_live_bid_on_current_round_short_circuits_and_marks_reviewed(tmp_path):
    api = FakeApi()
    api.brief_your_bid = {"proposal_id": "p-live", "status": "filed"}
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    provider = _fleet_provider(api, fleet, tmp_path=tmp_path)

    result = provider.submit_proposal("t1", _valid_proposal(), "key-1")

    assert result["ok"] is True
    assert result["idempotent"] is True
    assert result["proposal_id"] == "p-live"
    assert api.submissions == []
    assert fleet.reviewed_target_keys("agent-1") == {"t1:round:1"}


def test_informed_plan_rejects_line_items_that_change_the_sealed_total():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.submit_informed_plan(
        "t1",
        "p1",
        {
            "steps": [{"title": "Deliver", "line_item_amount": 1000}],
            "finish_line_cents": 0,
            "accept_rules": True,
        },
        "plan-key",
    )

    assert result["error"] == "informed_plan_validation_failed"
    assert any(problem["path"] == "steps" for problem in result["problems"])
    assert api.submissions == []


def test_guide_returns_only_the_requested_live_section():
    provider = BookOfHousesTollBenchProvider(FakeApi())

    result = provider.guide("start")

    assert result["contract_version"] == "current"
    assert result["instructions"] == "## Autonomous start\nStart here."


def test_informed_plan_cannot_change_sealed_terms():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.submit_informed_plan(
        "t1",
        "p1",
        {"steps": [], "accept_rules": True, "total_ask_cents": 1000},
        "plan-key",
    )

    assert result["error"] == "informed_plan_changes_sealed_terms"
    assert result["unexpected_fields"] == ["total_ask_cents"]
    assert api.submissions == []


def test_informed_plan_inherits_unchanged_fields_from_sealed_steps():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.submit_informed_plan(
        "t1",
        "p1",
        {
            "steps": [{"ask": "APPROVE", "declared_odds": 0.8}],
            "accept_rules": True,
        },
        "plan-key",
    )

    submitted_plan = api.submissions[-1][2]
    assert result == {"ok": True, "plan_revised": True}
    assert submitted_plan["steps"] == [
        {
            "title": "Deliver",
            "ask": "PROVIDE",
            "line_item_amount": 0,
            "declared_odds": 0.8,
            "har_blocks": [{"id": "details", "format": "written_response"}],
        }
    ]


def test_deal_step_tools_enforce_pulse_and_outcome_boundaries():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    invalid_pulse = provider.post_check_in(
        "d1",
        {"changed": "drafted", "now": "reviewing", "next": "file", "progress_percent": 60},
        "pulse-1",
    )
    invalid_outcome = provider.file_outcome(
        "t1",
        {
            "note": "Review this draft and approve it.",
            "text": "draft",
            "document": {"blocks": [{"type": "paragraph", "text": "draft"}]},
        },
        "outcome-1",
    )
    pulse = provider.post_check_in(
        "d1",
        {"changed": "drafted", "now": "done", "next": "file", "progress_percent": 100},
        "pulse-2",
    )
    outcome = provider.file_outcome(
        "t1",
        {
            "note": "Review this draft and approve it.",
            "document": {"blocks": [{"type": "paragraph", "text": "draft"}]},
        },
        "outcome-2",
    )

    assert invalid_pulse["error"] == "invalid_work_pulse_progress"
    assert invalid_outcome["error"] == "exactly_one_outcome_content_required"
    assert pulse["ok"] is True
    assert pulse["work_pulse"]["progress_percent"] == 100
    assert outcome == {"ok": True, "filed": True}
    assert len(api.submissions) == 2


def test_deal_step_reply_posts_to_the_exact_step_thread():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.reply_step_message("d1", "s1", "Thanks, I have it.", "reply-1")

    assert result == {
        "ok": True,
        "message": {"id": "m1", "body": "Thanks, I have it."},
    }
    assert api.submissions == [("d1", "s1", "Thanks, I have it.", "reply-1")]


def test_current_step_compacts_live_payload_without_dropping_action_fields():
    provider = BookOfHousesTollBenchProvider(FakeApi())

    result = provider.current_step("d1")

    assert result["deal"]["id"] == "d1"
    assert result["current_step"] == {
        "id": "s1",
        "number": None,
        "title": None,
        "state": "agent_working",
        "ask": "APPROVE",
        "outcome_promise": None,
        "outcome_filed_at": None,
        "declared_odds_at_bid": None,
        "declared_odds_restated": None,
        "declared_odds_drift": None,
        "har_blocks": None,
        "har_responses": None,
    }
    assert result["step_thread"]["messages"] == []
    assert result["access"]["grants"] == []


def test_current_step_passes_open_ask_keys_through():
    # The server added person_sees_control/open_ask_move on 2026-08-28 as the
    # belt riding the call every agent already makes; the whitelist stripped
    # both until v0.15.0, so railed models never saw the hint.
    class OpenAskApi(FakeApi):
        def current_step(self, deal_id):
            payload = FakeApi.current_step(self, deal_id)
            payload["person_sees_control"] = False
            payload["open_ask_move"] = "File your outcome to OPEN this ask."
            return payload

    result = BookOfHousesTollBenchProvider(OpenAskApi()).current_step("d1")

    assert result["person_sees_control"] is False
    assert result["open_ask_move"] == "File your outcome to OPEN this ask."


def test_withdraw_act_declaration_sends_the_kind_and_the_reason():
    # r218: a step that declared an act does not close without it. The way out
    # for an agent that changed its mind is words, not silence -- and the
    # reason is what the person reads beside the plan that promised the act.
    api = FakeApi()

    result = BookOfHousesTollBenchProvider(api).withdraw_act_declaration(
        "d1", "s1",
        {"kind": "email",
         "reason": "Steven and Ruby already met, so no introduction is needed."},
        "wd-1")

    assert result["message_id"] == "m-218"
    assert api.submissions == [(
        "d1", "s1",
        {"kind": "email",
         "reason": "Steven and Ruby already met, so no introduction is needed."},
        "wd-1")]


def test_withdraw_act_declaration_refuses_a_reasonless_or_unknown_withdrawal():
    provider = BookOfHousesTollBenchProvider(FakeApi())

    assert provider.withdraw_act_declaration(
        "d1", "s1", {"kind": "email", "reason": "  "},
        "w")["error"] == "missing_withdrawal_field"
    assert provider.withdraw_act_declaration(
        "d1", "s1", {"kind": "carrier_pigeon", "reason": "no"},
        "w")["error"] == "unknown_act_kind"
    assert provider.withdraw_act_declaration(
        "d1", "s1", {"kind": "email", "reason": "no", "what": "extra"},
        "w")["error"] == "invalid_withdrawal_fields"


def test_the_deal_step_instruction_names_the_r218_refusal():
    from toll_harness.cli import _DEAL_STEP_INSTRUCTION

    assert "acts_not_filed" in _DEAL_STEP_INSTRUCTION
    assert "toll_bench.withdraw_act_declaration" in _DEAL_STEP_INSTRUCTION


def test_wait_outside_declares_the_wait_on_the_step(monkeypatch=None):
    # r216: waiting on the outside world is a state, not silence. Before this
    # the only way to say "I emailed Ruby and cannot go on" was to say nothing,
    # which reads exactly like a stalled agent.
    api = FakeApi()

    result = BookOfHousesTollBenchProvider(api).wait_outside(
        "d1", "s1",
        {"on": "email_reply", "who": "Ruby",
         "what": "Ruby picks one of the three times I sent her",
         "until": "2026-09-06"},
        "wait-1")

    assert result["step_state"] == "waiting_outside"
    assert api.submissions == [(
        "d1", "s1",
        {"on": "email_reply", "who": "Ruby",
         "what": "Ruby picks one of the three times I sent her",
         "until": "2026-09-06"},
        "wait-1")]


def test_wait_outside_refuses_an_unknown_kind_or_a_missing_sentence():
    provider = BookOfHousesTollBenchProvider(FakeApi())

    assert provider.wait_outside(
        "d1", "s1", {"on": "vibes", "who": "Ruby", "what": "x"},
        "w")["error"] == "unknown_wait_kind"
    assert provider.wait_outside(
        "d1", "s1", {"on": "email_reply", "who": "Ruby", "what": "  "},
        "w")["error"] == "missing_wait_field"
    assert provider.wait_outside(
        "d1", "s1", {"on": "email_reply", "who": "Ruby", "what": "x",
                     "reason": "extra"},
        "w")["error"] == "invalid_wait_fields"


def test_wait_outside_can_end_the_wait_without_the_other_fields():
    api = FakeApi()

    BookOfHousesTollBenchProvider(api).wait_outside(
        "d1", "s1", {"end": True}, "wait-end")

    assert api.submissions == [("d1", "s1", {"end": True}, "wait-end")]


def test_the_wait_and_the_awaited_reply_survive_the_payload_whitelist():
    # The whitelist is why person_sees_control never reached railed models
    # until v0.15.0: a key the server adds and the harness drops does not
    # exist. waiting_outside and inbound_replies must not repeat that.
    class WaitingApi(FakeApi):
        def current_step(self, deal_id):
            payload = FakeApi.current_step(self, deal_id)
            payload["waiting_outside"] = {
                "on": "email_reply", "who": "Ruby", "what": "she answers",
                "since": "2026-09-03T12:00:00Z", "until": "2026-09-06T12:00:00Z"}
            payload["inbound_replies"] = [{"message_id": "m9"}]
            return payload

        def post_check_in(self, deal_id, pulse, idempotency_key):
            payload = FakeApi.post_check_in(self, deal_id, pulse, idempotency_key)
            payload["waiting_outside"] = None
            payload["inbound_replies"] = [{"message_id": "m9"}]
            return payload

    provider = BookOfHousesTollBenchProvider(WaitingApi())

    step = provider.current_step("d1")
    assert step["waiting_outside"]["who"] == "Ruby"
    assert step["inbound_replies"] == [{"message_id": "m9"}]

    pulse = provider.post_check_in(
        "d1", {"changed": "a", "now": "b", "next": "c",
               "progress_percent": 25}, "p1")
    assert pulse["waiting_outside"] is None
    assert pulse["inbound_replies"] == [{"message_id": "m9"}]


def test_the_deal_step_instruction_tells_the_agent_to_declare_the_wait():
    from toll_harness.cli import _DEAL_STEP_INSTRUCTION

    assert "toll_bench.wait_outside" in _DEAL_STEP_INSTRUCTION
    assert "Never sit silent at agent_working" in _DEAL_STEP_INSTRUCTION


def test_check_in_returns_ask_not_open_as_structured_move():
    # The walk refuses a flat-progress no-blocker pulse on an unopened
    # person-held ask (422 ask_not_open, 2026-09-01). The wrapper returns the
    # refusal as a move, not an exception.
    class RefusingApi(FakeApi):
        def post_check_in(self, deal_id, pulse, idempotency_key):
            raise BookOfHousesApiError(
                422, "ask_not_open", "File your handover outcome to OPEN the ask."
            )

    result = BookOfHousesTollBenchProvider(RefusingApi()).post_check_in(
        "d1", {"changed": "x", "now": "y", "next": "z", "progress_percent": 50}, "ik"
    )

    assert result == {
        "ok": False,
        "error": "ask_not_open",
        "move": "File your handover outcome to OPEN the ask.",
    }


def test_withdrawal_states_the_cause_and_the_agent_own_reason():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.withdraw_proposal(
        "p1", reason="  model could not produce a valid plan  ", cause="cannot_deliver"
    )

    assert result == {"ok": True, "withdrawn": True, "returned_count": 4}
    assert api.submissions == [
        (
            "p1",
            {"reason": "model could not produce a valid plan", "cause": "cannot_deliver"},
        )
    ]


def test_withdrawal_defaults_to_cause_other_and_caps_the_reason():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    provider.withdraw_proposal("p1", reason="x" * 1500)

    proposal_id, payload = api.submissions[0]
    assert proposal_id == "p1"
    assert payload["cause"] == "other"
    assert len(payload["reason"]) == 1000


def test_withdrawal_without_words_or_with_an_unknown_cause_is_refused():
    api = FakeApi()
    provider = BookOfHousesTollBenchProvider(api)

    silent = provider.withdraw_proposal("p1", reason="   ")
    invented = provider.withdraw_proposal("p1", reason="done here", cause="bored")

    assert silent["error"] == "withdraw_reason_required"
    assert invented["error"] == "invalid_withdraw_cause"
    assert invented["allowed"] == ["cannot_deliver", "other"]
    assert api.submissions == []
