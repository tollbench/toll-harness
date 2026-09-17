"""W29 -- AN APPROVED OUTSIDE ACT IS A MOVE, AND SILENCE IS NEVER THE ANSWER.

WHAT FORCED THESE TESTS (prod deal 4f061f54, step 3, 17 September 2026). Kai's
plan put "design the invite" on Canva. Nobody here has a tool for Canva, so the
bench declared the step an OUTSIDE act (rule 232): the person tapped Allow and
the step became the agent's to go and do itself. For seven minutes the server
answered every poll with "Agent working: You said OK; Kai is doing this outside
the site. Do it, then file what you brought back" -- and the harness filed
nothing, posted nothing on the step thread and never reported itself parked.
Its check-in was already overdue. Steven failed the step by hand and the want
reposted.

Two changes, one per end of the deal:

  (a) THE WALK. An approved outside act with an evidence form on it is the
      agent's MOVE, asked as one. The model brings the work back, asks for its
      tools, or says it cannot -- and either way something lands in that same
      poll: a parked report AND one plain sentence on the step thread.
  (b) THE PLAN. A step that names a service this want offers no tool for is not
      the agent's work at all. It becomes the PERSON's own step, and the tool
      comes off, so no outside act is ever stamped on hands nobody has.
"""
from __future__ import annotations

from tests.unit.test_step_ask import (
    ACTIONS,
    BRIEF,
    OBLIGATION,
    FakeBench,
    _form,
    _model,
    _obj,
    _payload,
)
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.step import (
    NEED_TOOLS,
    OUTSIDE_PARKED_SENTENCE,
    ROAD_AGENTIC,
    ROAD_STEP_ASK,
    StepAsk,
    step_tools,
    the_move,
)

TEXT = {"type": "string", "minLength": 1}
EVIDENCE_FORM = _form(
    "file_outside_evidence",
    _obj(
        {
            "summary": dict(TEXT, maxLength=2000),
            "links": {"type": "array", "items": {"type": "string"}},
            "receipt_ids": {"type": "array", "items": {"type": "string"}},
        },
        ("summary",),
    ),
    "/api/bench/deals/d1/steps/s-1/acts/evidence",
)
WORKER_STATUS = _form(
    "report_worker_status",
    _obj({"state": {"enum": ["running", "parked"]}, "round": {"const": 0}},
         ("state", "round")),
    "/api/bench/deals/d1/steps/s-1/worker-status",
)
APPROVED_OUTSIDE = {"kind": "outside", "state": "approved", "id": "a-1",
                    "title": "Design the invite"}


def _outside(*, evidence=True, worker=True):
    """The payload as the bench sent it on that step: Allow tapped, the act
    approved, and (since the server's f5e7085f2) an evidence form on it."""
    submission = {"completion_recorded_on_outcome": True,
                  "actions": list(ACTIONS) + ([EVIDENCE_FORM] if evidence else []),
                  "recommended_action": "file_outside_evidence"}
    if worker:
        submission["worker_status"] = WORKER_STATUS
    return _payload(acts=[APPROVED_OUTSIDE], submission=submission)


class OutsideBench(FakeBench):
    """The two doors W29 is about, beside the ones the step ask already knows."""

    def __init__(self, *, evidence=None, **kw):
        super().__init__(**kw)
        self.evidence, self.statuses = [], []
        self._evidence_answer = evidence or {"ok": True, "act_id": "a-1"}

    def file_evidence(self, deal_id, step_id, *, summary, links=None, receipt_ids=None):
        self.evidence.append((deal_id, step_id, summary, list(links or []),
                              list(receipt_ids or [])))
        return self._evidence_answer

    def report_worker_status(self, deal_id, step_id, state, round_number, reason=None):
        self.statuses.append((deal_id, step_id, state, round_number, reason))
        return {"ok": True, "worker_status": {"state": state}}


# ---------------------------------------------------------------------------
# (a) THE WALK
# ---------------------------------------------------------------------------
def test_an_approved_outside_act_is_the_agents_move_not_the_old_road():
    # The line that forced it: this returned {"move": None, "road": agentic}
    # and the model was handed the whole instruction sheet and did nothing.
    move = the_move(_outside(), OBLIGATION)

    assert move["move"] == "outside_work"
    assert move["calls"] == ["file_outside_evidence", "reply_step_message", "report_parked"]


def test_a_bench_with_no_evidence_form_still_takes_the_old_road():
    # There is nothing to ask for: an older bench publishes no door for the
    # work to come back through.
    move = the_move(_outside(evidence=False), OBLIGATION)

    assert move["move"] is None and move["road"] == ROAD_AGENTIC


def test_the_move_carries_the_evidence_door_the_thread_and_the_parked_report():
    payload = _outside()
    tools = step_tools(the_move(payload, OBLIGATION), payload)

    assert set(tools) == {"file_outside_evidence", "reply_step_message",
                          "report_parked", NEED_TOOLS}
    # The parked report is narrowed to the one thing the model says here.
    parked = tools["report_parked"]["schema"]
    assert parked["properties"]["state"] == {"const": "parked"}
    assert parked["required"] == ["state", "round", "reason"]
    assert parked["properties"]["reason"]["maxLength"] == 280


def test_the_work_comes_back_through_the_evidence_door():
    bench = OutsideBench()
    model = _model({"call": "file_outside_evidence",
                    "summary": "Made the invite in a drawing app and exported a PNG.",
                    "links": ["https://example.org/invite.png"]})

    out = StepAsk(model, bench).run(OBLIGATION, _outside(), brief=BRIEF)

    assert out["ok"] is True and out["road"] == ROAD_STEP_ASK
    assert out["move"] == "outside_work" and out["call"] == "file_outside_evidence"
    assert bench.evidence[0][:3] == (
        "d1", "s-1", "Made the invite in a drawing app and exported a PNG.")
    assert bench.evidence[0][3] == ["https://example.org/invite.png"]


def test_saying_it_cannot_be_done_parks_the_step_and_tells_the_person():
    # The heart of W29: a report the person never sees is still silence, so
    # the same sentence goes on the thread in the same round.
    bench = OutsideBench()
    reason = "I have no tool for that design site and no browser of my own."
    model = _model({"call": "report_parked", "state": "parked", "round": 0,
                    "reason": reason})

    out = StepAsk(model, bench).run(OBLIGATION, _outside(), brief=BRIEF)

    assert out["ok"] is True and out["call"] == "report_parked"
    assert bench.statuses == [("d1", "s-1", "parked", 0, reason)]
    assert bench.replies[0][:3] == ("d1", "s-1", reason)


def test_the_model_saying_nothing_still_ends_in_a_sentence_and_a_parked_report():
    # Exactly what happened on prod: no call at all, seven minutes of nothing.
    # The harness now says it itself rather than letting the step sit.
    bench = OutsideBench()
    model = _model("I will go and work on this now.", "Still working on it.")

    out = StepAsk(model, bench).run(OBLIGATION, _outside(), brief=BRIEF)

    assert out["ok"] is False and out["result"]["parked"] is True
    assert [row[2] for row in bench.statuses] == ["parked"]
    assert bench.replies[0][2] == OUTSIDE_PARKED_SENTENCE


def test_a_refused_status_report_never_swallows_the_sentence():
    # Visibility is best-effort; the person's sentence is not.
    class NoStatus(OutsideBench):
        def report_worker_status(self, *a, **kw):
            raise RuntimeError("worker-status door is down")

    bench = NoStatus()
    model = _model({"call": "report_parked", "state": "parked", "round": 0,
                    "reason": "No tool for it."})

    out = StepAsk(model, bench).run(OBLIGATION, _outside(), brief=BRIEF)

    assert out["ok"] is True
    assert bench.replies[0][2] == "No tool for it."


def test_the_model_may_still_ask_for_its_tools():
    bench = OutsideBench()
    model = _model({"call": NEED_TOOLS, "why": "I have a design tool for this"})

    out = StepAsk(model, bench).run(OBLIGATION, _outside(), brief=BRIEF)

    assert out["ok"] is False and out["road"] == ROAD_AGENTIC
    assert "design tool" in out["why"]
    # Asking for tools is not parking: nobody has said the work cannot be done.
    assert bench.statuses == [] and bench.replies == []


# ---------------------------------------------------------------------------
# (b) THE PLAN
# ---------------------------------------------------------------------------
def _form_with(*steps):
    return {"overview": "x", "odds": 0.4, "span_days": 14, "steps": list(steps)}


def test_a_step_on_a_service_with_no_tool_becomes_the_persons_own():
    plan, moved = draft.person_does_what_the_agent_cannot(
        _form_with(
            {"verb": "makes", "do_line": "Design the invite", "who": "agent",
             "tool": "canva.design.create", "declared_odds": 0.5},
            {"verb": "sends", "do_line": "Send it", "who": "agent",
             "tool": "gmail.message.send", "declared_odds": 0.6},
        ),
        BRIEF,
    )

    assert moved == ["step 1 (canva.design.create)"]
    assert plan["steps"][0]["who"] == "person" and "tool" not in plan["steps"][0]
    # The model's own words are untouched, and a tool the want DOES offer stays.
    assert plan["steps"][0]["do_line"] == "Design the invite"
    assert plan["steps"][1] == {"verb": "sends", "do_line": "Send it", "who": "agent",
                                "tool": "gmail.message.send", "declared_odds": 0.6}


def test_a_service_named_on_who_is_the_same_rule():
    plan, moved = draft.person_does_what_the_agent_cannot(
        _form_with({"verb": "books", "do_line": "Book the hall", "who": "service:Canva"}),
        BRIEF,
    )

    assert moved == ["step 1 (Canva)"]
    assert plan["steps"][0]["who"] == "person"


def test_a_want_that_publishes_no_tool_list_is_left_alone():
    # The same law pick_tools keeps: with no list, this package has no opinion.
    plan, moved = draft.person_does_what_the_agent_cannot(
        _form_with({"verb": "makes", "do_line": "Design it", "who": "agent",
                    "tool": "canva.design.create"}),
        {"want": "x"},
    )

    assert moved == [] and plan["steps"][0]["who"] == "agent"


def test_a_step_already_the_persons_is_not_touched():
    step = {"verb": "buys", "do_line": "Buy the template", "who": "person",
            "do_ask": {"link": "https://example.org/buy", "cost_cents": 1200,
                       "cost_note": "about $12"}}
    plan, moved = draft.person_does_what_the_agent_cannot(_form_with(dict(step)), BRIEF)

    assert moved == [] and plan["steps"][0] == step


def test_the_form_ask_says_the_rule_in_the_models_own_words():
    # The agents never learn our internal timing; they do read this sheet.
    assert "NEVER GIVE YOURSELF WORK ON A SERVICE YOU HAVE NO TOOL FOR" in (
        draft.FORM_INSTRUCTION)
    assert '"person"' in draft.FORM_INSTRUCTION and "do_ask" in draft.FORM_INSTRUCTION
