"""0.66.1: the box-only 0.64.3.post2 folded in, and the old-bench safety.

WHAT FORCED THIS FILE. The eight staging runners ran a hand-patched
0.64.3.post2 (plan revisions over `change_request`, the operator's own
dialogue) that 0.65.x and 0.66.0 never carried, so moving them to the
`read_results` harness would have taken those two abilities away. And the
production bench is still contract 4.1.39: it sends no `read_results`, no
`flow_materials`, no `repeats`, and its outcome door never heard of
`last_round`. Against that bench 0.66.1 must behave like 0.64.3.post2.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from toll_harness import cli
from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import _act_row, step_tail, the_move
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


# ---------------------------------------------------------------------------
# 1. The post2 abilities are here
# ---------------------------------------------------------------------------
def test_a_revision_files_under_its_own_key():
    plain = {"kind": "file_informed_plan", "proposal_id": "p-1"}
    assert cli._plan_filing_key(plain) == "draft-plan-p-1"
    revised = dict(plain, change_request={"text": "Drop step 3, add a call."})
    key = cli._plan_filing_key(revised)
    assert key.startswith("draft-plan-p-1-revision-") and key != "draft-plan-p-1"
    # the same request keeps the same key; a different one gets another
    assert cli._plan_filing_key(revised) == key
    other = dict(plain, change_request={"text": "Keep step 3."})
    assert cli._plan_filing_key(other) != key


def test_the_operator_reply_rides_its_own_door(monkeypatch):
    captured = _transport(monkeypatch, {"ok": True})
    result = _provider().operator_reply("want-1", "msg-9", "I can do that.", False)
    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/bench/targets/want-1/operator-reply")
    assert captured["body"] == {"message_id": "msg-9", "reply": "I can do that.",
                               "revise_plan": False}


def test_no_pending_dialogue_means_no_reply():
    loop = draft.DraftLoop(model=None, provider=None)
    assert cli._operator_dialogue_reply(None, loop, "want-1", {"want": "x"}) is None
    assert cli._operator_dialogue_reply(
        None, loop, "want-1", {"operator_dialogue": {"pending": {}}}) is None


def test_the_form_ask_carries_the_revision_and_the_conversation():
    loop = draft.DraftLoop(model=None, provider=None)
    answer = {"blanks": [], "form": {"steps": []},
              "change_request": {"text": "Add a phone call before the email."}}
    brief = {"want": "A want",
             "operator_dialogue": {"messages": [{"role": "operator", "text": "Call first?"}]}}
    ask = loop._the_form_ask(answer, "A want", None, brief)
    assert ask["change_request"] == answer["change_request"]
    assert ask["operator_conversation"] == brief["operator_dialogue"]["messages"]
    bare = loop._the_form_ask({"blanks": [], "form": {"steps": []}}, "A want", None,
                              {"want": "A want"})
    assert "change_request" not in bare and "operator_conversation" not in bare


def test_a_revision_needs_an_editable_form():
    class _OutlineDoor:
        def read_draft(self, target_id, kind="plan"):
            return {"ok": True, "next": "outline", "rounds": {"used": 0}}

    loop = draft.DraftLoop(model=None, provider=_OutlineDoor())
    outcome = loop.run("want-1", kind="plan", brief={"want": "A want"},
                       revision={"text": "Change it."})
    assert outcome.get("ok") is False
    assert "revision_form_unavailable" in json.dumps(outcome)


def test_artifact_listing_never_escapes_the_run(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    run = tmp_path / "run1"
    run.mkdir()
    (run / "inside.txt").write_text("ok")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (run / "link.txt").symlink_to(outside)
    listed = {row["path"] for row in store.list("run1")}
    assert listed == {"inside.txt"}
    with pytest.raises(ValueError):
        store._path("..", "x")


# ---------------------------------------------------------------------------
# 2. Against a 4.1.39 bench, 0.66.1 reads like 0.64.3.post2
# ---------------------------------------------------------------------------
OLD_STEP = {
    "current_step": {"id": "step-2", "number": 2, "title": "Read the calendar",
                     "state": "agent_working"},
    "deal": {"id": "deal-1", "target_goal_id": "want-1"},
    "step_thread": {}, "declared_acts": [],
    "acts": [{"id": "act-1", "kind": "calls", "tool": "calendar.events.read",
              "status": "ran", "actor": "platform"}],
}


def test_an_old_bench_step_carries_nothing_new():
    obligation = {"deal_id": "deal-1", "step_id": "step-2", "target_id": "want-1"}
    move = the_move(OLD_STEP, obligation)
    tail = step_tail(OLD_STEP, obligation, move, [])
    assert "repeats" not in tail["step"]
    row = _act_row(OLD_STEP["acts"][0])
    assert "read_results" not in row
    # an older bench that never sent the key is tellable from one that sent []
    assert _act_row(dict(OLD_STEP["acts"][0], read_results=[]))["read_results"] == []


def test_an_old_bench_plan_ask_carries_no_materials():
    loop = draft.DraftLoop(model=None, provider=None)
    brief = {"want": "A want"}
    assert draft.operator_materials(brief, {"blanks": []}) in (None, [])
    outline = loop._outline_payload("A want", None, brief, None)
    assert draft.MATERIALS_KEY not in outline
    form = loop._the_form_ask({"blanks": [], "form": {"steps": []}}, "A want", None, brief)
    assert draft.MATERIALS_KEY not in form


def test_last_round_is_only_offered_for_a_rounds_step():
    tools = {tool.name: tool
             for tool in add_toll_bench_tools(build_standard_registry()).definitions()}
    outcome = tools["toll_bench.file_outcome"].input_schema["properties"]["outcome"]
    # the key is optional: a plain step never has to send it
    assert "last_round" not in outcome.get("required", [])
    words = outcome["properties"]["last_round"]["description"].lower()
    assert "step.repeats" in words and "ignored on any other step" in words


def test_an_outcome_without_last_round_posts_exactly_the_post2_keys(monkeypatch):
    captured = _transport(monkeypatch, {"ok": True, "receipt_id": "r-1"})
    _provider().file_outcome(
        "want-1", {"note": "Here are the times.", "text": "Three slots.",
                   "step_ref": "step-2"}, "key-1")
    assert set(captured["body"]) == {"note", "text", "step_ref"}
    captured = _transport(monkeypatch, {"ok": True, "receipt_id": "r-2"})
    _provider().deliver_hosted_file(
        "want-1", {"note": "The file.", "file_url": "https://files.example/a.pdf"}, "key-2")
    assert set(captured["body"]) == {"note", "file_url"}
