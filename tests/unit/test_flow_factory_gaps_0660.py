"""THE FLOW FACTORY GAPS DR. MOOSE'S PICTURE BOOK FORCED (0.66.0).

Staging, 2026-10-21: Dr. Moose (a planning agent on this harness) built a
seven-step picture-book flow and walked it to painting round 1. Three things
in this package stopped him, each one a key the bench already published that
this harness refused or dropped:

1. `last_round` (bench contract 4.1.42) was not on `toll_bench.file_outcome`'s
   schema and not in the provider's allowed set, so he could never say "this
   was the last round"; told all pages were in, he tried three times and his
   loop guard parked the step.
2. `repeats` -- the bench's own count of a step done in rounds, whose `say`
   names the round and says when to send last_round -- was dropped by the
   `current_step` whitelist and never reached the model.
3. `flow_materials` (bench contract 4.1.43) -- the recipe his operator put in
   the materials box -- rode the brief and every plan-door answer and no plan
   prompt carried it, so his first plan asked the person for the recipe.

Each test holds one of those lines.
"""
from __future__ import annotations

import json
import urllib.request

from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import MOVE_INSTRUCTIONS, step_tail, the_move
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _transport(monkeypatch, body):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8")) if request.data else None
        return _Response(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _provider():
    return BookOfHousesTollBenchProvider(
        BookOfHousesApiClient(base_url="https://bench.example", token="t", maker_id="m")
    )


ROUNDS = {
    "of_step": None, "cadence": "rounds", "pace": "gated", "total": 8, "per_round": 4,
    "current": {"item": "2", "label": "Round 2"},
    "counts": {"of": 2, "done": 1, "open": 1, "left": 1}, "approved": 1,
    "set_aside": [], "paused": None, "ended": False, "until": None,
    "items": [{"item": "1", "label": "Round 1", "status": "done", "last": False},
              {"item": "2", "label": "Round 2", "status": "open", "last": False}],
    "say": 'Round 2 is yours: file it at the outcome door (4 a round by your plan). '
           'Send "last_round": true on the round that finishes the step '
           '(round 8 is the last by your plan).',
}


# ---------------------------------------------------------------------------
# 1. last_round rides file_outcome and deliver_hosted_file
# ---------------------------------------------------------------------------
def test_the_outcome_schema_takes_last_round():
    tools = {tool.name: tool
             for tool in add_toll_bench_tools(build_standard_registry()).definitions()}
    tool = tools["toll_bench.file_outcome"]
    outcome = tool.input_schema["properties"]["outcome"]
    assert outcome["properties"]["last_round"] == {
        "type": "boolean",
        "description": outcome["properties"]["last_round"]["description"],
    }
    assert "last round" in outcome["properties"]["last_round"]["description"].lower()
    assert outcome["additionalProperties"] is False  # the key is named, not a loophole
    assert "last_round" in tool.description
    hosted = tools["toll_bench.deliver_hosted_file"]
    assert hosted.input_schema["properties"]["last_round"]["type"] == "boolean"


def test_the_provider_posts_last_round_on_the_outcome(monkeypatch):
    captured = _transport(monkeypatch, {"ok": True, "receipt_id": "r-2",
                                        "round": {"round": 2, "last": True,
                                                  "total": 8, "approved": 1}})
    result = _provider().file_outcome(
        "want-1",
        {"note": "Pages 29 to 32, the last round.", "text": "Four pages.",
         "step_ref": "step-6", "last_round": True},
        "key-1",
    )
    assert result["ok"] is True and result["round"]["last"] is True
    assert captured["body"]["last_round"] is True
    assert captured["url"].endswith("/api/bench/targets/want-1/outcomes")


def test_a_hosted_file_carries_last_round_too(monkeypatch):
    captured = _transport(monkeypatch, {"ok": True, "receipt_id": "r-3"})
    result = _provider().deliver_hosted_file(
        "want-1",
        {"note": "The last four pages.", "file_url": "https://files.example/last.zip",
         "last_round": True},
        "key-2",
    )
    assert result["ok"] is True
    assert captured["body"]["last_round"] is True
    assert captured["body"]["file_url"] == "https://files.example/last.zip"
    # false is a value, not an absence: it rides too
    captured = _transport(monkeypatch, {"ok": True, "receipt_id": "r-4"})
    _provider().deliver_hosted_file(
        "want-1", {"note": "Round 3.", "file_url": "https://files.example/3.zip",
                   "last_round": False}, "key-3")
    assert captured["body"]["last_round"] is False


# ---------------------------------------------------------------------------
# 2. repeats reaches the model
# ---------------------------------------------------------------------------
def test_current_step_keeps_the_rounds(monkeypatch):
    _transport(monkeypatch, {
        "ok": True,
        "deal": {"id": "deal-1", "target_goal_id": "want-1", "status": "signed"},
        "current_step": {"id": "step-6", "number": 6, "title": "Paint the pages",
                         "state": "agent_working", "ask": "APPROVE",
                         "deliverable": {"channel": "file", "family": "image"},
                         "file_receipts": [], "rounds_used": 0, "repeats": ROUNDS},
        "step_thread": {"messages": [], "unread_from_person": 0},
        "acts": [], "declared_acts": [],
    })
    payload = _provider().current_step("deal-1")
    assert payload["current_step"]["repeats"] == ROUNDS
    assert payload["current_step"]["rounds_used"] == 0


def test_the_step_tail_carries_the_rounds_only_when_there_are_any():
    obligation = {"deal_id": "deal-1", "step_id": "step-6", "target_id": "want-1"}
    with_rounds = {
        "current_step": {"id": "step-6", "number": 6, "title": "Paint the pages",
                         "state": "agent_working", "repeats": ROUNDS},
        "deal": {"id": "deal-1", "target_goal_id": "want-1"},
        "step_thread": {}, "acts": [], "declared_acts": [],
    }
    move = the_move(with_rounds, obligation)
    tail = step_tail(with_rounds, obligation, move, [])
    assert tail["step"]["repeats"] == ROUNDS
    assert "last_round" in tail["step"]["repeats"]["say"]
    plain = dict(with_rounds, current_step={"id": "step-6", "number": 6,
                                            "title": "Write the story",
                                            "state": "agent_working", "repeats": None})
    tail = step_tail(plain, obligation, the_move(plain, obligation), [])
    assert "repeats" not in tail["step"]


def test_the_hand_back_instruction_says_what_a_round_is():
    words = MOVE_INSTRUCTIONS["hand_back"]
    assert "`step.repeats`" in words and "last_round: true" in words
    assert "per_round" in words


# ---------------------------------------------------------------------------
# 3. what the operator gave you rides the plan asks
# ---------------------------------------------------------------------------
MATERIALS = [
    {"id": "m-1", "kind": "note", "title": "Dr. Moose recipe", "size": "2 KB",
     "added_at": "2026-10-21T20:00:00Z", "source": "flow", "content_type": None,
     "url": None, "text": "32-page paperback. Ask photo permission first.",
     "text_trimmed": False, "text_chars": 46, "use_in_flow": False,
     "public_url": None, "fetch_url": "/api/bench/me/flow-materials/m-1",
     "file_url": None},
    {"id": "m-2", "kind": "image", "title": "Watercolor", "size": "1.2 MB",
     "added_at": "2026-10-21T20:01:00Z", "source": "flow", "content_type": "image/png",
     "url": None, "text": None, "text_trimmed": False, "text_chars": None,
     "use_in_flow": True, "public_url": "https://app.example/styles/watercolor.png",
     "fetch_url": "/api/bench/me/flow-materials/m-2",
     "file_url": "/api/bench/me/flow-materials/m-2/file"},
]


def test_operator_materials_are_compact_and_budgeted():
    rows = draft.operator_materials({"flow_materials": MATERIALS})
    assert rows == [
        {"id": "m-1", "kind": "note", "title": "Dr. Moose recipe",
         "use_in_flow": False, "text": "32-page paperback. Ask photo permission first."},
        {"id": "m-2", "kind": "image", "title": "Watercolor", "use_in_flow": True,
         "public_url": "https://app.example/styles/watercolor.png"},
    ]
    assert draft.operator_materials({"flow_materials": []}) is None
    assert draft.operator_materials({}) is None
    assert draft.operator_materials(None) is None
    # the door's answer is the newer copy and wins over the brief
    rows = draft.operator_materials({"flow_materials": MATERIALS},
                                    {"flow_materials": [MATERIALS[0]]})
    assert [r["id"] for r in rows] == ["m-1"]
    # a recipe past the budget is cut and says so
    long = dict(MATERIALS[0], text="x" * (draft.MATERIALS_CHAR_BUDGET + 10))
    rows = draft.operator_materials({"flow_materials": [long, MATERIALS[0]]})
    assert len(rows[0]["text"]) == draft.MATERIALS_CHAR_BUDGET and rows[0]["text_trimmed"]
    assert "text" not in rows[1] and rows[1]["text_trimmed"]


def test_the_outline_and_form_asks_carry_the_materials():
    loop = draft.DraftLoop(model=None, provider=None)
    brief = {"want": "A storybook about you", "flow_materials": MATERIALS}
    outline = loop._outline_payload("A storybook about you", None, brief, None)
    assert outline[draft.MATERIALS_KEY][0]["title"] == "Dr. Moose recipe"
    form = loop._the_form_ask(
        {"blanks": [], "form": {"steps": []}, "flow_materials": MATERIALS},
        "A storybook about you", None, {"want": "A storybook about you"})
    assert form[draft.MATERIALS_KEY][1]["public_url"].endswith("watercolor.png")
    bare = loop._outline_payload("A want", None, {"want": "A want"}, None)
    assert draft.MATERIALS_KEY not in bare


def test_every_plan_instruction_names_the_materials():
    for words in (draft.PLAN_OUTLINE_INSTRUCTION, draft.SEEDED_OUTLINE_INSTRUCTION,
                  draft.FORM_INSTRUCTION, draft.form_instruction(None)):
        assert draft.MATERIALS_KEY in words
        assert "get_flow_material" in words
