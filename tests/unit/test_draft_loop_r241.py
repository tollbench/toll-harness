"""RULE 241 — the draft loop, against a fake bench that behaves like the door.

WHAT FORCED THESE TESTS. The harness used to ask one model call for a whole
proposal and hand back every problem at once; models answered by rewriting the
whole document and breaking something new each pass. The loop below is the
replacement: the outline in, one step's blanks back, then one `next_fix` at a
time, and the document the bench holds is what gets filed. Each test names the
part of that sentence it is holding to.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from toll_harness import cli
from toll_harness.core.types import (
    AutonomyMode,
    ModelMessage,
    ModelResponse,
)
from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.draft import (
    DraftLoop,
    group_blanks,
    read_json_object,
    read_patches,
    tools_index,
)


def _says(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[]
    )


def _model(*answers: object) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(
        [_says(json.dumps(answer) if not isinstance(answer, str) else answer)
         for answer in answers]
    )


class FakeDraftBench:
    """The draft door, small enough to read: it expands an outline, names the
    blanks it owns nothing of, spends a round per patch, and hands back ONE
    next_fix at a time."""

    fleet = None
    fleet_proposal_limit = 4

    def __init__(self, *, fixes=None, cap=20, owned_steps=None):
        self.puts: list[tuple[str, dict, str]] = []
        self.patch_calls: list[list[dict]] = []
        self.filed: tuple | None = None
        self.plan_filed: tuple | None = None
        self.document: dict = {}
        self.kind = "bid"
        self.rounds = 0
        self.cap = cap
        self.pending_fixes = list(fixes or [])
        self.reads = 0
        self.owned_steps = owned_steps or [{"title": "The step already filed"}]
        self.briefs = 0

    # -- the three doors ------------------------------------------------
    def put_draft(self, target_id, outline, *, kind="bid"):
        self.puts.append((target_id, dict(outline or {}), kind))
        self.kind = kind
        self.rounds = 0
        specs = (outline or {}).get("steps") or (
            self.owned_steps if kind == "plan" else []
        )
        self.document = {
            "pitch_title": "",
            "steps": [
                {
                    "ask": str(spec.get("ask") or "APPROVE"),
                    "title": str(spec.get("title") or ""),
                    "outcome_promise": "",
                    "statement": {"action": "finds", "thing": "x", "benefit": "y"},
                }
                for spec in specs
            ],
        }
        return self.answer()

    def patch_draft(self, target_id, patches, *, kind="bid"):
        self.patch_calls.append(list(patches))
        self.rounds += 1
        for entry in patches:
            self._set(str(entry.get("path")), entry.get("value"))
            self.pending_fixes = [
                fix for fix in self.pending_fixes if fix["path"] != entry.get("path")
            ]
        if self.rounds >= self.cap:
            return self.answer(closed="this draft used its rounds. Send a fresh outline.")
        return self.answer()

    def read_draft(self, target_id, *, kind="bid"):
        self.reads += 1
        if not self.document:
            return {"ok": False, "error": "no_draft", "status": 404,
                    "message": "there is no draft on this target."}
        if self.rounds >= self.cap:
            return self.answer(closed="this draft used its rounds.")
        return self.answer()

    def submit_proposal(self, target_id, proposal, idempotency_key):
        # The single-shot road, for a bench with no draft door.
        return {"ok": True, "proposal_id": "old-road-1"}

    # -- filing ----------------------------------------------------------
    def file_from_draft(self, target_id, idempotency_key=""):
        self.filed = (target_id, idempotency_key, json.loads(json.dumps(self.document)))
        return {"ok": True, "proposal_id": "proposal-1"}

    def file_plan_from_draft(self, target_id, proposal_id, idempotency_key=""):
        self.plan_filed = (target_id, proposal_id, idempotency_key)
        return {"ok": True, "plan_revised": True}

    # -- the bench's own bookkeeping -------------------------------------
    def read_brief(self, target_id):
        self.briefs += 1
        return {"brief": {"target_id": target_id, "want": "Book a table for four"}}

    def list_act_kinds(self):
        return {"act_kinds": {"meeting": {}, "email": {}}}

    def list_targets(self):
        return {
            "targets": [
                {
                    "target_id": "t-1",
                    "want": "Book a table for four",
                    "posted_at": "2026-09-09T00:00:00Z",
                    "your_bid": None,
                }
            ]
        }

    # -- the document ----------------------------------------------------
    def _set(self, path, value):
        parts = path.split(".")
        node = self.document
        for part in parts[:-1]:
            if isinstance(node, list):
                node = node[int(part)]
            else:
                node = node.setdefault(part, {})
        if isinstance(node, list):
            node[int(parts[-1])] = value
        else:
            node[parts[-1]] = value

    def blanks(self):
        rows = []
        for index, step in enumerate(self.document.get("steps") or []):
            if not step.get("outcome_promise"):
                rows.append(
                    {
                        "path": f"steps.{index}.outcome_promise",
                        "note": "What this step hands back, in your own words.",
                        "example": "",
                        "required": True,
                    }
                )
        if not self.document.get("pitch_title"):
            rows.append(
                {
                    "path": "pitch_title",
                    "note": "The name of your plan.",
                    "example": "",
                    "required": True,
                }
            )
        return rows

    def answer(self, closed=None):
        blank_rows = self.blanks()
        fix = None
        if closed is None:
            if blank_rows:
                fix = {
                    "path": blank_rows[0]["path"],
                    "current": "",
                    "code": "blank",
                    "fix": blank_rows[0]["note"],
                    "detail": None,
                }
            elif self.pending_fixes:
                fix = dict(self.pending_fixes[0])
        remaining = len(blank_rows) + len(self.pending_fixes)
        return {
            "ok": closed is None,
            "kind": self.kind,
            "draft": self.document,
            "blanks": blank_rows,
            "next_fix": None if closed else fix,
            "problems": [],
            "remaining": remaining,
            "ready": closed is None and remaining == 0,
            "rounds": {"used": self.rounds, "left": self.cap - self.rounds, "cap": self.cap},
            "closed": closed,
        }


_OUTLINE = {
    "steps": [
        {"ask": "APPROVE", "title": "Find three places that take a booking",
         "block": "research"},
        {"ask": "APPROVE", "title": "Offer the times and book it",
         "tool": "gmail.message.send", "on": "google-gmail"},
    ]
}


def _happy_path_model():
    return _model(
        _OUTLINE,
        {"patches": [{"path": "steps.0.outcome_promise", "value": "Three places, with sources."}]},
        {"patches": [{"path": "steps.1.outcome_promise", "value": "A booked table."}]},
        {"patches": [{"path": "pitch_title", "value": "A table for four"}]},
        {"patches": [{"path": "steps.1.title", "value": "Offer the times and book the table"}]},
    )


def test_the_outline_goes_in_the_blanks_come_back_and_the_bench_files_what_it_held():
    bench = FakeDraftBench(
        fixes=[
            {
                "path": "steps.1.title",
                "current": "Offer the times and book it",
                "code": "REJ-34",
                "fix": "Say how the booking is made.",
                "detail": "step 2: the promise has no act",
            }
        ]
    )
    model = _happy_path_model()

    outcome = DraftLoop(model, bench).run(
        "t-1", brief={"want": "Book a table for four"}, idempotency_key="key-1"
    )

    assert outcome["ok"] is True
    assert outcome["filed"] is True
    assert outcome["proposal_id"] == "proposal-1"
    # One outline in, then a round per piece: two steps, the bid's own fields,
    # and the one problem the bench named.
    assert len(bench.puts) == 1
    assert bench.puts[0][2] == "bid"
    assert len(bench.patch_calls) == 4
    assert bench.filed[0] == "t-1"
    assert bench.filed[2]["pitch_title"] == "A table for four"
    # NOTHING WAS FILED BY HAND: the document that reached the door is the one
    # the bench had been holding.
    assert bench.filed[2]["steps"][1]["outcome_promise"] == "A booked table."


def test_the_outline_call_asks_for_an_outline_and_nothing_else():
    bench = FakeDraftBench()
    model = _happy_path_model()

    DraftLoop(model, bench).run(
        "t-1",
        brief={
            "want": "Book a table for four",
            "want_in_own_words": "somewhere quiet",
            # A worked program on the brief must NOT ride the outline prompt:
            # copying one is the habit this loop replaces.
            "nearest_program": {"key": "book-a-table", "proposal": {"steps": [{"x": 1}]}},
        },
        idempotency_key="key-1",
    )

    first = model.invocations[0]["messages"][0].content[0]["text"]
    assert "OUTLINE" in first
    assert "Book a table for four" in first
    assert "somewhere quiet" in first
    assert "nearest_program" not in first
    assert model.invocations[0]["tools"] == []


def test_a_blanks_call_shows_one_step_and_that_step_alone():
    bench = FakeDraftBench()
    model = _happy_path_model()

    DraftLoop(model, bench).run("t-1", brief={"want": "Book a table"}, idempotency_key="k")

    step_one = model.invocations[1]["messages"][0].content[0]["text"]
    assert '"step_number":1' in step_one
    assert "steps.0.outcome_promise" in step_one
    assert "steps.1.outcome_promise" not in step_one


def test_a_fix_call_carries_one_problem_and_the_step_around_it():
    bench = FakeDraftBench(
        fixes=[
            {
                "path": "steps.1.title",
                "current": "Offer the times and book it",
                "code": "REJ-34",
                "fix": "Say how the booking is made.",
                "detail": "step 2: the promise has no act",
            }
        ]
    )
    model = _happy_path_model()

    DraftLoop(model, bench).run("t-1", brief={"want": "Book a table"}, idempotency_key="k")

    fix_call = model.invocations[-1]["messages"][0].content[0]["text"]
    assert "REJ-34" in fix_call
    assert "Say how the booking is made." in fix_call
    assert '"step_number":2' in fix_call
    assert "steps.0" not in fix_call


def test_a_closed_draft_ends_the_run_and_never_puts_twice():
    # A PUT replaces the draft and zeroes the rounds, so a run never sends a
    # second one. The next cycle reads the closed draft and opens one fresh
    # outline -- at most one PUT per want per cycle.
    bench = FakeDraftBench(cap=1)
    model = _model(_OUTLINE, {"patches": [{"path": "pitch_title", "value": "A"}]})

    outcome = DraftLoop(model, bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert outcome["ok"] is False
    assert outcome["error"] == "draft_closed"
    assert outcome["filed"] is False
    assert len(bench.puts) == 1
    assert bench.filed is None


def test_a_standing_draft_is_resumed_not_replaced():
    """WHAT FORCED IT: the watch loop comes back to the same want every scan
    and the plan obligation stands until it files, so a loop that opened with a
    PUT opened a NEW draft every cycle -- dozens on one want in two minutes,
    every answer thrown away. A run reads first."""
    bench = FakeDraftBench()
    # Cycle one: the outline goes in and one piece comes back.
    DraftLoop(
        _model(_OUTLINE,
               {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
               {"patches": [{"path": "steps.1.outcome_promise", "value": "B."}]},
               {"patches": [{"path": "pitch_title", "value": "C"}]}),
        bench,
    ).run("t-1", brief={"want": "Book a table"}, idempotency_key="k")
    puts_after_one = len(bench.puts)

    # Cycle two, same want: nothing is re-outlined, and no round is spent
    # re-asking for what is already answered.
    second = DraftLoop(_model(), bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert puts_after_one == 1
    assert len(bench.puts) == 1
    assert bench.reads >= 1
    assert second["ok"] is True
    assert second["filed"] is True


def test_a_closed_standing_draft_is_never_put_over():
    """Since the bench counts a repeated PUT as a round and holds a used-up
    draft closed until it expires, a fresh outline over a closed draft burns
    the cap and costs the want a day. The next cycle reads it and leaves."""
    bench = FakeDraftBench(cap=1)
    DraftLoop(
        _model(_OUTLINE, {"patches": [{"path": "pitch_title", "value": "A"}]}),
        bench,
    ).run("t-1", brief={"want": "Book a table"}, idempotency_key="k")
    assert len(bench.puts) == 1

    # A model with nothing scripted: if the loop asked for an outline here it
    # would raise, and if it PUT one the count would move.
    second = DraftLoop(_model(), bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert len(bench.puts) == 1
    assert second["ok"] is False
    assert second["error"] == "draft_closed"
    assert bench.filed is None


def test_the_plan_kind_reads_its_own_draft_before_opening_one():
    reads = []

    class Watching(FakeDraftBench):
        def read_draft(self, target_id, *, kind="bid"):
            reads.append(kind)
            return super().read_draft(target_id, kind=kind)

    bench = Watching(owned_steps=[{"title": "Deliver the list"}])
    model = _model(
        {"patches": [{"path": "steps.0.outcome_promise", "value": "The list."}]},
        {"patches": [{"path": "pitch_title", "value": "The list"}]},
    )

    DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", idempotency_key="k"
    )

    # The read carries the kind, so a plan draft is never mistaken for a bid.
    assert reads == ["plan"]
    assert len(bench.puts) == 1


def test_a_draft_that_will_not_read_is_never_put_over():
    class Unreadable(FakeDraftBench):
        def read_draft(self, target_id, *, kind="bid"):
            raise RuntimeError("the door did not answer")

    bench = Unreadable()

    outcome = DraftLoop(_model(), bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert outcome["ok"] is False
    assert bench.puts == []


def test_the_plan_kind_opens_empty_and_files_at_the_plan_door():
    # RULE 113 through 241: a `plan` draft starts from the steps already filed,
    # so the loop sends NO outline -- an outline here would replace the plan
    # the person picked.
    bench = FakeDraftBench(owned_steps=[{"title": "Deliver the list"}])
    model = _model(
        {"patches": [{"path": "steps.0.outcome_promise", "value": "The list, delivered."}]},
        {"patches": [{"path": "pitch_title", "value": "The list"}]},
    )

    outcome = DraftLoop(model, bench).run(
        "t-1", kind="plan", proposal_id="p-9", idempotency_key="plan-key"
    )

    assert outcome["ok"] is True
    assert bench.puts[0][1] == {}
    assert bench.puts[0][2] == "plan"
    assert bench.plan_filed == ("t-1", "p-9", "plan-key")
    assert bench.filed is None


def test_there_is_no_strike_count_only_the_bench_s_own_bound():
    """Steven, 2026-09-09: "3 strikes on a 30 step job is too little", "I don't
    think we should do any levers". The bench keeps naming the same fix, the
    model keeps missing it, and the loop keeps going until the BENCH closes the
    draft -- well past three tries."""
    bench = FakeDraftBench(cap=9)
    bench.pending_fixes = [
        {"path": "steps.0.title", "current": "", "code": "REJ-34",
         "fix": "Say how.", "detail": None}
    ]
    model = _model(
        _OUTLINE,
        {"patches": [{"path": "steps.0.outcome_promise", "value": "A."}]},
        {"patches": [{"path": "steps.1.outcome_promise", "value": "B."}]},
        {"patches": [{"path": "pitch_title", "value": "C"}]},
        # Six answers that never touch the path the bench named. The old three
        # strike rule stopped here; now the bench's rounds do.
        *[{"patches": [{"path": "steps.1.outcome_promise", "value": "B."}]}
          for _ in range(20)],
    )

    outcome = DraftLoop(model, bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert outcome["ok"] is False
    assert outcome["error"] == "draft_closed"
    # The bench's own cap, not a harness number: nine rounds, and more than
    # three of them spent on the one fix the model keeps missing.
    assert len(bench.patch_calls) > 3 + 3
    assert len(bench.puts) == 1
    assert bench.filed is None


def test_a_thirty_step_plan_gets_thirty_steps_worth_of_rounds():
    bench = FakeDraftBench(cap=200)
    outline = {"steps": [{"ask": "APPROVE", "title": f"Step {n}"} for n in range(30)]}
    model = _model(
        outline,
        *[
            {"patches": [{"path": f"steps.{n}.outcome_promise", "value": f"Piece {n}."}]}
            for n in range(30)
        ],
        {"patches": [{"path": "pitch_title", "value": "Thirty pieces"}]},
    )

    outcome = DraftLoop(model, bench).run(
        "t-1", brief={"want": "A thirty step job"}, idempotency_key="k"
    )

    assert outcome["ok"] is True
    assert outcome["filed"] is True
    # One round per piece, thirty steps and the bid's own fields, and nothing
    # in the harness stopped it partway.
    assert len(bench.patch_calls) == 31
    assert bench.filed[2]["steps"][29]["outcome_promise"] == "Piece 29."


def test_the_loop_stops_when_the_bench_says_it_has_no_rounds_left():
    """The one safety net, and it is the bench's own arithmetic: a bench that
    publishes `rounds.left` 0 and keeps answering 200 still stops the loop."""

    class NeverCloses(FakeDraftBench):
        def answer(self, closed=None):
            out = super().answer(closed=closed)
            out["rounds"] = {"used": 99, "left": 0, "cap": 99}
            return out

    bench = NeverCloses()
    model = _model(
        _OUTLINE,
        *[{"patches": [{"path": "pitch_title", "value": "A"}]} for _ in range(4)],
    )

    outcome = DraftLoop(model, bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert outcome["ok"] is False
    assert bench.filed is None
    assert len(bench.patch_calls) == 0


def test_a_dry_run_walks_the_whole_loop_and_files_nothing():
    bench = FakeDraftBench()
    model = _happy_path_model()

    outcome = DraftLoop(model, bench).run(
        "t-1", brief={"want": "Book a table"}, idempotency_key="k", file=False
    )

    assert outcome["ok"] is True
    assert outcome["filed"] is False
    assert outcome["draft"]["pitch_title"] == "A table for four"
    assert bench.filed is None


# ---------------------------------------------------------------------------
# READING THE MODEL
# ---------------------------------------------------------------------------
def test_the_patch_shapes_a_raw_model_actually_writes_all_read():
    assert read_patches({"patches": [{"path": "a.b", "value": 1}]}) == [
        {"path": "a.b", "value": 1}
    ]
    assert read_patches({"path": "a.b", "value": 2}) == [{"path": "a.b", "value": 2}]
    assert read_patches({"steps.0.title": "T"}) == [{"path": "steps.0.title", "value": "T"}]
    assert read_patches({"nothing": "here"}) == []


def test_json_reads_through_a_fence_and_a_sentence():
    assert read_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert read_json_object('Here you go: {"a": 2}') == {"a": 2}
    assert read_json_object("no json at all") == {}


def test_the_blanks_are_grouped_a_step_at_a_time_in_document_order():
    groups = group_blanks(
        [
            {"path": "steps.0.title"},
            {"path": "steps.1.title"},
            {"path": "steps.0.outcome_promise"},
            {"path": "pitch_title"},
        ]
    )

    assert [key for key, _rows in groups] == [0, 1, None]
    assert len(groups[0][1]) == 2


def test_the_tools_index_names_only_tools_a_published_place_names():
    index = tools_index(
        {
            "nearest_program": {
                "proposal": {
                    "steps": [
                        {"acts": [{"runs": [{"tool": "calendar.events.create",
                                             "on": "google-calendar"}]}]}
                    ]
                }
            }
        }
    )

    names = [row["tool"] for row in index]
    assert "calendar.events.create" in names
    assert "platform.research" in names
    assert all(row["tool"] for row in index)


# ---------------------------------------------------------------------------
# THE WIRING — the market scan and the informed plan both walk the loop
# ---------------------------------------------------------------------------
def _resources(bench, model):
    return SimpleNamespace(
        toll_bench=bench,
        agent_identity=SimpleNamespace(
            id="00000002-0000-0000-0000-000000000000",
            autonomy_mode=AutonomyMode.AUTONOMOUS,
        ),
        runtime=SimpleNamespace(model=model, enabled_tools=[]),
    )


def test_the_market_scan_bids_through_the_draft_loop():
    bench = FakeDraftBench()
    resources = _resources(bench, _happy_path_model())

    result = cli._process_market_opportunities(resources, {"ok": True})

    assert result["ok"] is True
    assert result["proposal_filed"] is True
    assert result["dispatch"]["kind"] == "market_scan_draft_loop"
    assert bench.filed is not None
    # The single-shot road was not taken: no runtime run happened at all.
    assert result["run"] is None


def test_a_bench_with_no_draft_door_still_bids_the_old_way():
    class NoDoor(FakeDraftBench):
        def put_draft(self, target_id, outline, *, kind="bid"):
            return {"ok": False, "error": "http_error", "status": 404,
                    "message": "no such route"}

    started = {}

    def start(goal, mode):
        started["goal"] = goal
        raise AssertionError("stop here: the old road was taken")

    bench = NoDoor()
    resources = _resources(bench, _model(_OUTLINE))
    resources.runtime.start = start

    try:
        cli._process_market_opportunities(resources, {"ok": True})
    except AssertionError as error:
        assert "the old road was taken" in str(error)
    assert "making and submitting one concrete" in started["goal"]


# ---------------------------------------------------------------------------
# THE THREE CALLS THEMSELVES
# ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self, answer=None, error=None):
        self.calls = []
        self.answer = answer or {"ok": True}
        self.error = error

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.error is not None:
            raise self.error
        return self.answer


def test_the_three_draft_calls_are_the_three_http_calls():
    client = BookOfHousesApiClient(base_url="https://bench.test", token="t", maker_id="m")
    recorder = _Recorder()
    client._request = recorder

    client.put_proposal_draft("t 1", {"steps": [{"ask": "APPROVE"}]})
    client.patch_proposal_draft("t 1", [{"path": "pitch_title", "value": "A"}], kind="plan")
    client.get_proposal_draft("t 1", kind="plan")

    methods = [call[0] for call in recorder.calls]
    paths = [call[1] for call in recorder.calls]
    assert methods == ["PUT", "PATCH", "GET"]
    assert paths == ["/api/bench/targets/t%201/proposals/draft"] * 3
    assert recorder.calls[0][2]["payload"]["kind"] == "bid"
    assert recorder.calls[0][2]["payload"]["steps"] == [{"ask": "APPROVE"}]
    assert recorder.calls[1][2]["payload"] == {
        "kind": "plan",
        "patches": [{"path": "pitch_title", "value": "A"}],
    }
    assert recorder.calls[2][2]["query"] == {"kind": "plan"}
    assert all(call[2].get("authenticated") for call in recorder.calls)


def test_a_closed_draft_comes_back_as_a_body_not_an_exception():
    # The loop reads `closed` to decide whether to start a fresh outline, so a
    # 409 must arrive as the door's own answer and not as a raised error.
    api = SimpleNamespace(
        put_proposal_draft=lambda *a, **k: (_ for _ in ()).throw(
            BookOfHousesApiError(
                409,
                "draft_closed",
                "this draft used its rounds",
                body={"ok": False, "closed": "this draft used its rounds.",
                      "error": "draft_closed", "rounds": {"used": 9, "left": 0}},
            )
        )
    )
    provider = BookOfHousesTollBenchProvider(api)

    answer = provider.put_draft("t-1", {"steps": []})

    assert answer["ok"] is False
    assert answer["error"] == "draft_closed"
    assert answer["status"] == 409
    assert "used its rounds" in answer["closed"]


def test_filing_from_the_draft_sends_only_the_flag():
    submissions = []

    class Api:
        def submit_proposal(self, target_id, payload, idempotency_key):
            submissions.append((target_id, payload, idempotency_key))
            return {"ok": True, "proposal_id": "p-2"}

        def me(self):
            return {"reachability_test": {"reachable": True, "reachable_at": "now"}}

    provider = BookOfHousesTollBenchProvider(Api())

    result = provider.file_from_draft("t-1", "key-9")

    assert result["ok"] is True
    assert submissions == [("t-1", {"from_draft": True}, "key-9")]


def test_filing_the_plan_from_the_draft_carries_the_signature():
    submissions = []

    class Api:
        def submit_informed_plan(self, target_id, proposal_id, payload, idempotency_key):
            submissions.append((target_id, proposal_id, payload, idempotency_key))
            return {"ok": True, "plan_revised": True}

    provider = BookOfHousesTollBenchProvider(Api())

    result = provider.file_plan_from_draft("t-1", "p-1", "key-9")

    assert result["ok"] is True
    assert submissions[0][2] == {"from_draft": True, "accept_rules": True}


def test_the_informed_plan_obligation_walks_the_same_loop():
    bench = FakeDraftBench(owned_steps=[{"title": "Deliver the list"}])
    bench.attention = lambda wait=0: {"attention": []}
    resources = _resources(
        bench,
        _model(
            {"patches": [{"path": "steps.0.outcome_promise", "value": "The list."}]},
            {"patches": [{"path": "pitch_title", "value": "The list"}]},
        ),
    )
    obligation = {
        "kind": "file_informed_plan",
        "target_id": "t-1",
        "proposal_id": "p-9",
    }

    result = cli._file_the_informed_plan_from_draft(
        resources, obligation, {"ok": True}, 1, 3
    )

    assert result["ok"] is True
    assert result["plan_filing_verified"] is True
    assert result["dispatch"]["kind"] == "file_informed_plan_draft_loop"
    assert bench.plan_filed == ("t-1", "p-9", "draft-plan-p-9")
    # No outline was sent: the plan starts from the steps already filed.
    assert bench.puts[0][1] == {}


def test_an_informed_plan_that_never_files_trips_the_breaker():
    bench = FakeDraftBench(owned_steps=[{"title": "Deliver the list"}])
    resources = _resources(
        bench, _model(*[{"nothing": "patchable"} for _ in range(6)])
    )
    obligation = {
        "kind": "file_informed_plan",
        "target_id": "t-2",
        "proposal_id": "p-8",
    }

    result = cli._file_the_informed_plan_from_draft(
        resources, obligation, {"ok": True}, 1, 3
    )

    assert result["ok"] is False
    assert result["breaker"]["consecutive_failures"] >= 1
    assert bench.plan_filed is None


def test_the_put_door_is_not_a_tool_the_model_can_call():
    """A PUT replaces the draft and zeroes the rounds. The loop owns it; a
    model that can call it answers a hard plan by starting over."""
    from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

    names = {
        definition.name
        for definition in add_toll_bench_tools(build_standard_registry()).definitions()
    }

    assert "toll_bench.put_proposal_draft" not in names
    assert "toll_bench.patch_proposal_draft" in names
    assert "toll_bench.get_proposal_draft" in names
