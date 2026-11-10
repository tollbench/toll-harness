"""THE HARNESS SAYS AND DOES WHAT THE BENCH SAYS (0.76.2).

What forced it (Steven's order, 2026-11-10): the three doors (API, harness,
MCP) and every skill page must say and do the same thing. The bench's register
201 now carries `on_your_own`, thirteen sentences describing what the harness
does, and a fresh checker comparing the three doors found the harness itself
disagreeing in three places:

1. It took attention items in its own order (deal_step, the paid-pack kinds,
   pack_handoff, draft_sent_back and file_informed_plan all before an
   unanswered_message). The server sorts the list itself
   (agent_market._GLOBAL_ORDER) with unanswered_message first, and refuses an
   outcome filed over an unanswered message.
2. The plan's model road told the model to "file once with
   toll_bench.submit_informed_plan", which posted raw steps to
   POST .../proposals/{p}/plan; the bench answers that 409 use_the_plan_door.
   The plan is built and filed on PUT .../proposals/draft, filed with
   {"kind": "plan", "file": true, "accept_rules": true}.
3. The docs said the worker never proposes and that running it takes
   `market connect` plus `market watch`; the code scans the board every 300 s
   and files at most one proposal per scan, and init installs and starts the
   service itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.test_market_watch import _completed_run
from tests.unit.test_registered_means_0760 import _output_strings
from toll_harness import cli
from toll_harness.core.types import AutonomyMode
from toll_harness.storage.filesystem import FilesystemArtifactStore
from toll_harness.storage.local import SQLiteStore
from toll_harness.toll_bench.api import BookOfHousesApiClient, BookOfHousesApiError
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import (
    ToolContext,
    add_toll_bench_tools,
    build_standard_registry,
)

REPO = Path(__file__).resolve().parents[2]

# A copy of the bench's own sort key, app/services/agent_market.py
# _GLOBAL_ORDER (read 2026-11-10). The harness mirrors it in
# cli._OBLIGATION_PRIORITY; when the bench changes it, both change.
BENCH_GLOBAL_ORDER = {
    "unanswered_message": 0,
    "act_correction": 1,
    "deal_step": 2,
    "sign_deal": 3,
    "file_informed_plan": 4,
    "goal_check": 5,
    "reachability_ping": 6,
}

PLAN_FILING = {"kind": "plan", "file": True, "accept_rules": True}


def _item(kind, tag):
    return {"kind": kind, "tag": tag}


# ---------------------------------------------------------------------------
# 1. The server's order
# ---------------------------------------------------------------------------
def test_the_priority_is_the_benchs_own_sort_key():
    assert cli._OBLIGATION_PRIORITY == tuple(
        sorted(BENCH_GLOBAL_ORDER, key=BENCH_GLOBAL_ORDER.get)
    )
    assert cli._OBLIGATION_PRIORITY[0] == "unanswered_message"


@pytest.mark.parametrize(
    "ahead",
    [
        "deal_step",
        "paid_pack_review",
        "paid_pack_work",
        "pack_handoff",
        "draft_sent_back",
        "file_informed_plan",
        "act_correction",
        "goal_check",
        "funding_research",
    ],
)
def test_an_unanswered_message_is_taken_before_anything_else(ahead):
    message = _item("unanswered_message", "m")
    assert cli._select_obligation([_item(ahead, "a"), message]) is message
    assert cli._select_obligation([message, _item(ahead, "a")]) is message


def test_every_kind_the_old_order_put_first_now_waits_for_the_message():
    message = _item("unanswered_message", "m")
    listed = [
        _item(kind, kind)
        for kind in (
            "deal_step",
            "paid_pack_review",
            "paid_pack_work",
            "pack_handoff",
            "draft_sent_back",
            "file_informed_plan",
        )
    ] + [message]
    assert cli._select_obligation(listed) is message


def test_ranked_kinds_follow_the_bench_and_unranked_ones_stand_after_them():
    step = _item("deal_step", "s")
    plan = _item("file_informed_plan", "p")
    sent_back = _item("draft_sent_back", "d")
    handoff = _item("pack_handoff", "h")
    assert cli._select_obligation([plan, step]) is step
    assert cli._select_obligation([sent_back, plan]) is plan
    assert cli._select_obligation([handoff, step]) is step
    assert cli._select_obligation([_item("goal_check", "g"), plan])["kind"] == (
        "file_informed_plan"
    )


def test_items_of_equal_standing_keep_the_servers_order():
    # Two of one kind: the one the server listed first.
    first, second = _item("unanswered_message", 1), _item("unanswered_message", 2)
    assert cli._select_obligation([first, second]) is first
    # Kinds the bench does not rank stand level: the list's own order wins,
    # exactly as the bench's stable sort leaves them.
    work, sent_back = _item("paid_pack_work", "w"), _item("draft_sent_back", "d")
    assert cli._select_obligation([work, sent_back]) is work
    assert cli._select_obligation([sent_back, work]) is sent_back
    assert cli._select_obligation([]) is None


def test_the_cycle_hands_the_model_the_message_not_the_step():
    class MailClient:
        def configure_send_context(self, **_kwargs):
            pass

        def resume_pending_send(self):
            return None

    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {
            "attention": [
                {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s-1"},
                {"kind": "draft_sent_back", "deal_id": "d1", "step_id": "s-1"},
                {"kind": "unanswered_message", "deal_id": "d2", "step_id": "s-2"},
            ]
        },
    )
    observed = {}
    configured = [
        "state.load", "state.save", "result.complete", "result.fail",
        "toll_bench.current_step", "toll_bench.reply_step_message",
        "toll_bench.file_outcome", "toll_bench.propose_act", "web.search",
    ]
    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()), enabled_tools=list(configured)
    )

    def start(goal, mode):
        observed["goal"] = goal
        observed["tools"] = list(runtime.enabled_tools)
        return _completed_run(goal, mode)

    runtime.start = start
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)

    result = cli._process_market_attention(resources, wait=20)

    assert result["ok"] is True
    assert observed["goal"].startswith(cli._UNANSWERED_MESSAGE_INSTRUCTION)
    assert '"s-2"' in observed["goal"]
    assert '"s-1"' not in observed["goal"]
    assert set(observed["tools"]) <= cli._OBLIGATION_DISPATCH["unanswered_message"]["tools"]
    assert "toll_bench.file_outcome" not in observed["tools"]


# ---------------------------------------------------------------------------
# 2. The plan files on the plan door, and nothing posts raw steps
# ---------------------------------------------------------------------------
class _RecordingClient(BookOfHousesApiClient):
    """The real client with the wire cut: every request is written down."""

    def __init__(self, refusal=None):
        super().__init__(base_url="https://bench.example", token="token")
        self.sent = []
        self.refusal = refusal

    def _request(self, method, path, **kwargs):
        self.sent.append((method, path, kwargs.get("payload")))
        if self.refusal is not None:
            raise self.refusal
        return {"ok": True, "plan_revised": True}


def _carries_steps(value):
    if isinstance(value, dict):
        return "steps" in value or any(_carries_steps(item) for item in value.values())
    if isinstance(value, list):
        return any(_carries_steps(item) for item in value)
    return False


def _context(tmp_path, provider):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    return ToolContext(
        run.id, store, store, FilesystemArtifactStore(tmp_path / "artifacts"), 0,
        toll_bench_provider=provider,
    )


def test_the_provider_files_the_draft_on_the_plan_door():
    client = _RecordingClient()
    provider = BookOfHousesTollBenchProvider(client)

    result = provider.file_plan_from_draft("t-1", "p-1", "key-1")

    assert result["ok"] is True
    assert client.sent == [("PUT", "/api/bench/targets/t-1/proposals/draft", PLAN_FILING)]


def test_the_tool_files_the_draft_on_the_plan_door(tmp_path):
    client = _RecordingClient()
    provider = BookOfHousesTollBenchProvider(client)
    registry = add_toll_bench_tools(build_standard_registry())

    result = registry.execute(
        _context(tmp_path, provider),
        "call-1",
        "toll_bench.submit_informed_plan",
        {"target_id": "t-1", "proposal_id": "p-1", "idempotency_key": "key-1"},
    )

    assert result.is_error is False
    assert client.sent == [("PUT", "/api/bench/targets/t-1/proposals/draft", PLAN_FILING)]


def test_the_tool_refuses_a_plan_and_sends_nothing(tmp_path):
    client = _RecordingClient()
    provider = BookOfHousesTollBenchProvider(client)
    registry = add_toll_bench_tools(build_standard_registry())

    result = registry.execute(
        _context(tmp_path, provider),
        "call-1",
        "toll_bench.submit_informed_plan",
        {
            "target_id": "t-1",
            "proposal_id": "p-1",
            "idempotency_key": "key-1",
            "plan": {"steps": [{"title": "Deliver"}], "accept_rules": True},
        },
    )

    assert result.is_error is True
    assert client.sent == []


def test_a_refused_filing_comes_back_with_the_benchs_rows():
    refusal = BookOfHousesApiError(
        422,
        "draft_not_ready",
        "step 2 is not ready",
        body={
            "ok": False,
            "error": "draft_not_ready",
            "problems": [{"step": 2, "slot": "title", "who_fixes": "agent"}],
            "next_fix": {"step": 2, "slot": "title"},
        },
    )
    provider = BookOfHousesTollBenchProvider(_RecordingClient(refusal))

    result = provider.file_plan_from_draft("t-1", "p-1", "key-1")

    assert result["ok"] is False
    assert result["error"] == "draft_not_ready"
    assert result["problems"] == [{"step": 2, "slot": "title", "who_fixes": "agent"}]
    assert result["next_fix"] == {"step": 2, "slot": "title"}


def test_no_code_path_posts_raw_steps():
    # The raw door is gone from the client and the provider.
    assert not hasattr(BookOfHousesApiClient, "submit_informed_plan")
    assert not hasattr(BookOfHousesTollBenchProvider, "submit_informed_plan")
    # No path this package builds ends at .../plan (docstrings and comments
    # say what was retired; they are not requests).
    plan_route = re.compile(r"/plan(?![-_\w])")
    found = [
        f"{path.relative_to(REPO)}:{line}: {text!r}"
        for path in sorted((REPO / "src" / "toll_harness").rglob("*.py"))
        for line, text in _output_strings(path)
        if plan_route.search(text)
    ]
    assert found == []
    # Every road that files a plan sends the filing body and no steps.
    client = _RecordingClient()
    provider = BookOfHousesTollBenchProvider(client)
    provider.file_plan_from_draft("t-1", "p-1", "k")
    assert client.sent and all(
        method == "PUT" and path.endswith("/proposals/draft") and payload == PLAN_FILING
        and not _carries_steps(payload)
        for method, path, payload in client.sent
    )


def test_the_model_road_names_the_plan_door_the_bench_names():
    instruction = cli._FILE_INFORMED_PLAN_INSTRUCTION
    assert "PUT /api/bench/targets/{target_id}/proposals/draft" in instruction
    assert '{"kind": "plan", "file": true, "accept_rules": true}' in instruction
    assert "toll_bench.submit_informed_plan" in instruction
    assert "/plan " not in instruction and "proposals/{proposal_id}/plan" not in instruction
    tools = cli._OBLIGATION_DISPATCH["file_informed_plan"]["tools"]
    assert {
        "toll_bench.get_proposal_draft",
        "toll_bench.patch_proposal_draft",
        "toll_bench.submit_informed_plan",
        "toll_bench.call",
    } <= tools


def test_the_tools_page_names_the_put_door_first_and_the_post_as_the_alias():
    text = " ".join((REPO / "docs" / "tools.md").read_text().split())
    put = text.index(
        '`PUT .../proposals/draft {"kind": "plan", "file": true, "accept_rules": true}`'
    )
    alias = text.index("`POST .../proposals/{proposal_id}/plan")
    assert put < alias
    assert "is the bench's alias for the same filing" in text


# ---------------------------------------------------------------------------
# 3 and 4. The pages say what the worker does and how it runs
# ---------------------------------------------------------------------------
def test_the_onboarding_page_says_the_worker_proposes_on_its_cadence():
    text = " ".join((REPO / "docs" / "onboarding.md").read_text().split())
    assert "does not automatically propose" not in text
    assert "once every 300 seconds" in text
    assert "at most one proposal (or a decline) per scan" in text
    assert "an `unanswered_message` first" in text
    assert "`toll-harness service-status AGENT_CONFIG`" in text


def test_the_readme_says_two_commands_and_service_status_in_one_place():
    text = (REPO / "README.md").read_text()
    flat = " ".join(text.split())
    assert "market connect" not in text
    # `market watch` is what the service runs, never a step the agent types.
    assert ".venv/bin/toll-harness market watch" not in text
    assert "toll-harness init ./my-agent --registered" in text
    assert "That is the whole setup: two commands" in flat
    assert text.count("toll-harness service-status ./my-agent/agent.yaml") == 2
    assert "The service runs `toll-harness market watch ./my-agent/agent.yaml` for you" in flat
    assert "While idle" not in flat
    assert "files at most one proposal (or a decline) per scan" in flat


def test_no_page_still_describes_the_model_provider_picker():
    pages = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
    text = "\n".join(page.read_text() for page in pages)
    assert "model-provider picker" not in text
    assert "model picker" not in text
    assert "Which intelligence brand" not in text
    assert cli.INTELLIGENCE_QUESTION in " ".join(text.split())
