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

from tests.unit.plan_door import a_row
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
    step_of,
    tools_index,
)


def _says(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage.text("assistant", text), text=text, tool_calls=[]
    )


class CachingModel(ScriptedModelAdapter):
    """A provider whose adapter says a repeated prefix costs less. The default
    ScriptedModelAdapter says it does not, which is the honest default for an
    unknown provider -- and the two roads are different, so both are tested."""

    def caches_a_stable_prefix(self) -> bool:
        return True


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

    def __init__(self, *, fixes=None, cap=20, owned_steps=None, asks_outline=True):
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
        # THE PLAN DOOR ASKS FIRST. A plan draft opens EMPTY (rule 113) and
        # the door answers with a round of its own -- the form on this bench,
        # the outline on the older one -- before anything is expanded. This
        # fake plays the outline road; `asks_outline=False` is the door that
        # has nothing to ask and expands the steps already filed.
        self.asks_outline = asks_outline
        self.proposals: list[tuple[str, dict, str]] = []
        self.opens: list[tuple[str, dict, str]] = []

    # -- the three doors ------------------------------------------------
    def put_draft(self, target_id, outline, *, kind="bid"):
        if kind == "plan" and self.asks_outline and not (outline or {}).get("steps"):
            # The empty open. It costs a round and answers with the ask; the
            # outline that answers it is the PUT that expands (see `puts`).
            self.opens.append((target_id, dict(outline or {}), kind))
            self.kind = kind
            self.rounds = 1
            return {
                "ok": True,
                "kind": "plan",
                "next": "outline",
                "steps_you_bid": [dict(step) for step in self.owned_steps],
                "the_person_answered": [],
                "send": "PUT this door again with the outline.",
                "draft": {},
                "blanks": [],
                "next_fix": {"path": "outline"},
                "problems": [],
                "remaining": 1,
                "ready": False,
                "rounds": {"used": 1, "left": self.cap - 1, "cap": self.cap},
                "closed": None,
            }
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
        # STAGE ONE (rule 243): the seven fields, filed in one call.
        self.proposals.append((target_id, json.loads(json.dumps(proposal)),
                               idempotency_key))
        return {"ok": True, "proposal_id": "old-road-1"}

    # -- filing ----------------------------------------------------------
    def file_from_draft(self, target_id, idempotency_key=""):
        self.filed = (target_id, idempotency_key, json.loads(json.dumps(self.document)))
        return {"ok": True, "proposal_id": "proposal-1"}

    def file_plan_from_draft(self, target_id, proposal_id, idempotency_key=""):
        self.plan_filed = (target_id, proposal_id, idempotency_key)
        # The document that reached the door, exactly as `file_from_draft`
        # records it: what was filed is what the bench was holding.
        self.filed = (target_id, idempotency_key, json.loads(json.dumps(self.document)))
        return {"ok": True, "plan_revised": True, "proposal_id": proposal_id}

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
        # ONE LANGUAGE (contract 4.0): every problem is a row of the one
        # shape, `problems` carries them all and `next_fix` only says where
        # to start. A blank the form still owes is a row like any other.
        rows = [
            a_row(row["path"], step=step_of(row["path"]) and step_of(row["path"]) + 1,
                  slot=(row["path"].rsplit(".", 1)[-1] if "." in row["path"]
                        else row["path"]),
                  say=row["note"], codes=["blank"])
            for row in blank_rows
        ]
        rows += [dict(fix) for fix in self.pending_fixes]
        mine = [row for row in rows if row["who_fixes"] == "agent"]
        with_path = [row for row in mine if row.get("path")]
        remaining = len(rows)
        return {
            "ok": closed is None,
            "kind": self.kind,
            "draft": self.document,
            "blanks": blank_rows,
            "form_steps": [],
            "next_fix": None if closed else (with_path or mine or [None])[0],
            "problems": rows,
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


def test_the_plan_kind_reads_its_own_draft_before_opening_one():
    reads = []

    class Watching(FakeDraftBench):
        def read_draft(self, target_id, *, kind="bid"):
            reads.append(kind)
            return super().read_draft(target_id, kind=kind)

    bench = Watching(owned_steps=[{"title": "Deliver the list"}], asks_outline=False)
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
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert outcome["ok"] is False
    assert bench.puts == []


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
        "t-1", kind="plan", proposal_id="p-9", brief={"want": "Book a table"}, idempotency_key="k"
    )

    assert outcome["ok"] is False
    assert bench.filed is None
    assert len(bench.patch_calls) == 0


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


def test_the_tools_index_is_the_one_the_bench_publishes():
    """The brief carries `tools` (bench 965e61c5a) and that is the index. The
    outline needs the name, the service and one line -- never the argument
    list, which the draft door writes and hands back as blanks."""
    index = tools_index(
        {
            "tools": [
                {"family": "platform", "provider": "", "tool": "platform.research",
                 "required": ["brief"], "fields": ["depth"],
                 "one_line": "Find something out.", "shapes": {"brief": "text"}},
                {"family": "calendar", "provider": "google-calendar",
                 "tool": "calendar.events.create", "required": ["summary"],
                 "fields": [], "one_line": "Put an event on their calendar."},
            ]
        }
    )

    assert index == [
        {"tool": "platform.research", "on": "", "does": "Find something out."},
        {"tool": "calendar.events.create", "on": "google-calendar",
         "does": "Put an event on their calendar."},
    ]


def test_the_whole_index_goes_with_the_wildcard_last():
    # The last row is the door to every other service. Since 0.74.0 no budget
    # cuts rows off the index: every row goes, the wildcard last.
    published = [
        {"provider": f"svc-{n}", "tool": f"svc.tool.{n}",
         "one_line": "x" * 120}
        for n in range(80)
    ]
    published.append(
        {"provider": "composio:<service>", "tool": "composio:<service>/<TOOL>",
         "one_line": "Any of ~1,500 other services."}
    )

    index = tools_index({"tools": published})

    assert len(index) == len(published)
    assert index[-1]["tool"] == "composio:<service>/<TOOL>"


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
        runtime=SimpleNamespace(
            model=model,
            enabled_tools=["toll_bench.submit_proposal", "toll_bench.submit_informed_plan"],
        ),
    )


_PROPOSAL = {
    "pitch_title": "A table for four, booked this week",
    "headline": "Four seats. This week.",
    "pitch_body": "I call the three places that take a booking for four, offer "
                  "your times, and confirm the one that takes it.",
    "odds": 0.6,
    "total_ask_cents": 4_000,
    "research_links": [{"url": "https://example.test/places", "note": "The three."}],
    "finalist_questions": ["Which night?"],
    "tools_needed": ["gmail.message.send"],
}


def test_the_market_scan_answers_a_want_with_one_proposal():
    """RULE 243 (2026-09-11): a proposal is the agent's short answer to a want
    and it is ONE call. It opens no draft: the draft door is the PLAN's now,
    and the plan is owed by the agent the person picked."""
    bench = FakeDraftBench()
    model = _model(_PROPOSAL)
    resources = _resources(bench, model)

    result = cli._process_market_opportunities(resources, {"ok": True})

    assert result["ok"] is True
    assert result["proposal_filed"] is True
    assert result["dispatch"]["kind"] == "market_scan_draft_loop"
    assert result["dispatch"]["model_calls"] == 1
    assert result["dispatch"]["rounds"] == 0
    # One model call, no draft, and the eight fields went to the filing door.
    assert len(model.invocations) == 1
    assert bench.puts == [] and bench.opens == []
    assert bench.proposals and bench.proposals[0][1]["pitch_title"] == _PROPOSAL["pitch_title"]
    assert "steps" not in bench.proposals[0][1]
    # The single-shot road was not taken: no runtime run happened at all.
    assert result["run"] is None


def test_a_bench_that_cannot_file_a_proposal_still_bids_the_old_way():
    class NoDoor(FakeDraftBench):
        submit_proposal = None

    started = {}

    def start(goal, mode):
        started["goal"] = goal
        raise AssertionError("stop here: the old road was taken")

    bench = NoDoor()
    resources = _resources(bench, _model(_PROPOSAL))
    resources.runtime.start = start

    try:
        cli._process_market_opportunities(resources, {"ok": True})
    except AssertionError as error:
        assert "the old road was taken" in str(error)
    assert "ONE PROPOSAL" in started["goal"]


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


def test_an_informed_plan_that_never_files_trips_the_breaker():
    bench = FakeDraftBench(owned_steps=[{"title": "Deliver the list"}], asks_outline=False)
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


def test_the_cached_share_is_read_from_whatever_the_provider_called_it():
    from types import SimpleNamespace

    from toll_harness.toll_bench.draft import cached_input_tokens

    assert cached_input_tokens(
        SimpleNamespace(raw={"cache_read_input_tokens": 4_100})
    ) == 4_100
    assert cached_input_tokens(SimpleNamespace(raw={"cacheReadInputTokens": 9})) == 9
    assert cached_input_tokens(SimpleNamespace(raw={"cached_tokens": 12})) == 12
    assert cached_input_tokens(
        SimpleNamespace(raw={"prompt_tokens_details": {"cached_tokens": 7}})
    ) == 7
    # Not reported is not zero, and must never read as zero.
    assert cached_input_tokens(SimpleNamespace(raw={"input_tokens": 5})) is None
    assert cached_input_tokens(None) is None


# ---------------------------------------------------------------------------
# LAW A (Steven, 2026-09-09): the blanks and fix tails carry what the person said
# ---------------------------------------------------------------------------
THE_PERSON_SAID = [
    {"question": "What do you want?", "answer": "Book a table for four"},
    {"question": "Who is coming?", "answer": "Jane Real, John Actual",
     "people": [{"name": "Jane Real", "contact_ref": "c-1"},
                {"name": "John Actual", "contact_ref": "c-2"}],
     "finding": []},
]




