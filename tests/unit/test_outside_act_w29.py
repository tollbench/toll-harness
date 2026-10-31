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
from toll_harness.toll_bench.step import (
    NEED_TOOLS,
    OUTSIDE_PARKED_SENTENCE,
    ROAD_AGENTIC,
    ROAD_STEP_ASK,
    StepAsk,
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


def test_the_work_comes_back_through_the_evidence_door():
    bench = OutsideBench()
    model = _model({"call": "file_outside_evidence",
                    "summary": "Made the invite in a drawing app and exported a PNG.",
                    "links": ["https://example.org/invite.png"]})

    out = StepAsk(model, bench).run(OBLIGATION, _outside(), brief=BRIEF)

    assert out["ok"] is True and out["road"] == ROAD_STEP_ASK
    assert out["move"] == "file_outside_evidence" and out["call"] == "file_outside_evidence"
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


