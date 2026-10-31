"""0.74.0: the bench's own list of doors, and no prompt naming a missing tool.

WHAT FORCED IT (lane 3 of the 2026-10-27 heat logs): the bench published the
named-question, operator-help, failed-step and skip-item doors, and no harness
agent could reach them; meanwhile a prompt told the model to use
`get_flow_material` and `toll_bench.withdraw_act_declaration`, which no road
carried.
"""
from __future__ import annotations

import ast
import pathlib
import re

from toll_harness import cli
from toll_harness.config import (
    BENCH_DOOR_TOOLS,
    STEP_DOOR_TOOLS,
    with_bench_doors,
    with_step_doors,
)
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.onboarding import TOLL_BENCH_TOOLS
from toll_harness.toll_bench import doors
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "toll_harness"
TOOL_WORD = re.compile(
    r"(?<![\w./])((?:toll_bench|email|files|web|browser|http|state|result|human|wake|"
    r"agent_secret)\.[a-z_]+)"
)
# Event names and a file name that share a tool family's prefix.
NOT_TOOLS = {"human.message", "human.requested", "result.bin"}

SPEC = {
    "paths": {
        "/api/bench/targets/open": {"get": {"summary": "List open targets"}},
        "/api/bench/targets/{target_id}": {"get": {"summary": "The target, as the brief"}},
        "/api/bench/deals/{deal_id}/steps/{step_id}/question": {
            "post": {
                "summary": "Ask the person ONE named question",
                "requestBody": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Question"}}}},
            }
        },
        "/api/bench/me/token/rotate": {"post": {"summary": "Rotate the REST token"}},
        "/api/bench/agents/register": {"post": {"summary": "Register"}},
        "/want/claim": {"post": {"summary": "The person's own browser door"}},
    },
    "components": {"schemas": {"Question": {
        "type": "object", "properties": {"question": {"type": "string", "maxLength": 300}}}}},
}


def _registered():
    return {d.name for d in add_toll_bench_tools(build_standard_registry()).definitions()}


def test_every_tool_a_prompt_names_is_registered():
    names = _registered()
    missing = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for word in TOOL_WORD.findall(node.value):
                    if word not in names and word not in NOT_TOOLS:
                        missing.setdefault(word, set()).add(path.name)
    assert missing == {}


def test_every_tool_an_obligation_names_rides_its_road():
    for kind, dispatch in cli._OBLIGATION_DISPATCH.items():
        named = set(TOOL_WORD.findall(dispatch["instruction"])) - NOT_TOOLS
        assert named <= set(dispatch["tools"]), (kind, named - set(dispatch["tools"]))


def test_the_list_is_the_benchs_and_never_its_credential_doors():
    listed = doors.published_doors(SPEC)
    paths = {row["path"] for row in listed}
    assert "/api/bench/deals/{deal_id}/steps/{step_id}/question" in paths
    assert "/api/bench/me/token/rotate" not in paths
    assert "/api/bench/agents/register" not in paths
    assert "/want/claim" not in paths


def test_a_literal_segment_wins_over_a_parameter():
    listed = doors.published_doors(SPEC)
    assert doors.match_door(listed, "GET", "/api/bench/targets/open")["summary"] == (
        "List open targets")
    assert doors.match_door(listed, "GET", "/api/bench/targets/t-9")["path"] == (
        "/api/bench/targets/{target_id}")
    assert doors.match_door(listed, "POST", "/api/bench/targets/t-9") is None


class _Api:
    def __init__(self):
        self.calls = []

    def read_contract(self):
        return SPEC

    def call_door(self, method, path, body=None, query=None, idempotency_key=""):
        self.calls.append((method, path, body, query, idempotency_key))
        if path.endswith("/question") and not (body or {}).get("question"):
            raise BookOfHousesApiError(422, "question_required", "Write the one question.",
                                       body={"field": "question"})
        return {"ok": True, "asked": True}


def test_a_published_door_is_called_with_a_key_and_a_refusal_in_its_own_words():
    api = _Api()
    bench = BookOfHousesTollBenchProvider(api)
    path = "/api/bench/deals/d-1/steps/s-2/question"
    assert bench.call_door("POST", path, {"question": "Which city?"}) == {
        "ok": True, "asked": True}
    method, called, body, _query, key = api.calls[-1]
    assert (method, called, body) == ("POST", path, {"question": "Which city?"})
    assert key.startswith("door-")
    refused = bench.call_door("POST", path, {})
    assert refused["ok"] is False and refused["error"] == "question_required"
    assert refused["field"] == "question" and refused["detail"] == "Write the one question."


def test_an_unpublished_or_unfilled_door_is_never_called():
    api = _Api()
    bench = BookOfHousesTollBenchProvider(api)
    assert bench.call_door("POST", "/api/bench/me/token/rotate")["error"] == "door_not_published"
    assert bench.call_door("GET", "/api/bench/targets/{target_id}")["error"] == "path_not_filled"
    assert api.calls == []


def test_the_list_narrows_by_word_and_shows_one_door_whole():
    bench = BookOfHousesTollBenchProvider(_Api())
    found = bench.bench_doors(word="question")
    assert [row["path"] for row in found["doors"]] == [
        "/api/bench/deals/{deal_id}/steps/{step_id}/question"]
    whole = bench.bench_doors(path="/api/bench/deals/{deal_id}/steps/{step_id}/question")
    assert whole["door"]["body"]["properties"]["question"]["maxLength"] == 300


def test_onboarded_lists_carry_the_doors_and_hand_cut_lists_do_not():
    for name in BENCH_DOOR_TOOLS + ["toll_bench.withdraw_act_declaration"]:
        assert name in TOLL_BENCH_TOOLS
        assert name in _registered()
    assert "toll_bench.withdraw_act_declaration" in STEP_DOOR_TOOLS
    old_onboarded = [name for name in TOLL_BENCH_TOOLS
                     if name not in BENCH_DOOR_TOOLS and name not in STEP_DOOR_TOOLS]
    loaded = with_bench_doors(with_step_doors(old_onboarded))
    assert set(BENCH_DOOR_TOOLS) <= set(loaded)
    assert "toll_bench.withdraw_act_declaration" in loaded
    hand_cut = ["toll_bench.current_step", "toll_bench.file_outcome"]
    assert with_bench_doors(hand_cut) == hand_cut
