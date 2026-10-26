"""One authorized source repair costs no model call and cannot approve siblings."""

from copy import deepcopy
from types import SimpleNamespace as S
from unittest.mock import Mock

import pytest

from toll_harness import cli
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.corrections import dispatch, matches_current

CORRECTION = {
    "act_id": "returned-1",
    "repeat_item": "opaque item ",
    "approval_hash": "original-hash",
    "source_hash": "approved-source-hash",
}
ITEM = {
    "kind": "act_correction",
    "deal_id": "d1",
    "step_id": "s5",
    "act_id": "returned-1",
    "repeat_item": "opaque item ",
    "correction": CORRECTION,
}


def current():
    return {
        "deal": {"id": "d1"},
        "current_step": {"id": "s5", "state": "agent_working"},
        "step_thread": {},
        "source_corrections": [deepcopy(CORRECTION)],
        "acts": [
            {
                "act_id": "returned-1",
                "kind": "calls",
                "state": "sent_back",
                "repeat_item": "opaque item ",
            },
            {"act_id": "pending-2", "kind": "calls", "state": "pending", "repeat_item": "second"},
            {"act_id": "pending-3", "kind": "calls", "state": "pending", "repeat_item": "third"},
        ],
    }


def resources(response=None):
    provider = BookOfHousesTollBenchProvider.__new__(BookOfHousesTollBenchProvider)
    provider.api = S(
        propose_act=Mock(return_value=response or {"ok": True, "status": "done", "act_id": "new-1"})
    )
    provider.api.current_step = Mock(return_value=current())
    provider.current_step = Mock(side_effect=AssertionError("use full API snapshot"))
    provider.ensure_reachable = lambda: {"ok": True}
    provider.attention = lambda wait: {"attention": [deepcopy(ITEM)]}
    provider.list_proposals = lambda: {"proposals": []}
    return S(
        toll_bench=provider,
        runtime=S(
            email_provider=None,
            enabled_tools=None,
            model=None,
            start=Mock(side_effect=AssertionError("no model call")),
        ),
        agent_identity=None,
        store=None,
    )


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(cli, "_OBLIGATION_FAILURES", {})
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})


def test_real_attention_cycle_provider_exact_door_no_model():
    r = resources()
    out = cli._process_market_attention(r, wait=0)
    assert out["ok"] and out["source_correction"]["repaired"]
    assert out["dispatch"] == {"kind": "act_correction", "model_calls": 0}
    r.runtime.start.assert_not_called()
    args = r.toll_bench.api.propose_act.call_args.args
    assert args[:3] == ("d1", "s5", {"kind": "calls", "source_correction": CORRECTION})
    assert args[3].startswith("source-correction-")
    cli._process_market_attention(r, wait=0)
    assert r.toll_bench.api.propose_act.call_args.args == args  # lost-response key stable


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_step", []),
        ("deal", "bad"),
        ("step_thread", 12),
        ("source_corrections", 3),
        ("source_corrections", {}),
        ("acts", "bad"),
        ("acts", None),
        ("owed_replies", ["reply"]),
        ("awaiting_funding", True),
    ],
)
def test_malformed_or_held_current_never_posts(field, value):
    r = resources()
    payload = current()
    payload[field] = value
    r.toll_bench.api.current_step.return_value = payload
    assert dispatch(r, ITEM)["ok"] is False
    r.toll_bench.api.propose_act.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["step_thread"].update(unanswered_messages=["note"]),
        lambda p: p["step_thread"].update(unanswered_elsewhere=["note"]),
        lambda p: p["current_step"].update(id="other"),
        lambda p: p["current_step"].update(state="waiting_on_you"),
        lambda p: p["deal"].update(id="other"),
        lambda p: p["source_corrections"][0].update(source_hash="new"),
        lambda p: p["acts"][0].update(state="pending"),
        lambda p: p["acts"][0].update(kind="email"),
        lambda p: p["acts"][0].update(repeat_item="opaque item"),
        lambda p: p["source_corrections"].append(deepcopy(CORRECTION)),
    ],
)
def test_exact_live_context_required(mutation):
    payload = current()
    mutation(payload)
    assert not matches_current(payload, "d1", "s5", CORRECTION)


@pytest.mark.parametrize(
    "status,ok,repaired",
    [
        ("done", True, True),
        ("held", True, False),
        ("failed", False, False),
        ("unknown", False, False),
    ],
)
def test_terminal_attempt_is_reported_honestly_without_breaker_retry(status, ok, repaired):
    r = resources({"ok": True, "status": status, "act_id": "new-1"})
    out = cli._process_market_attention(r, wait=0)
    assert out["ok"] is ok
    assert out["source_correction"]["repaired"] is repaired
    assert out["source_correction"]["terminal"] is True
    assert "breaker" not in out
    if status == "held":
        assert out["source_correction"]["awaiting_person"]


def test_missing_provider_and_transport_use_bounded_failure_path():
    r = resources()
    r.toll_bench.refile_source_correction = None
    out = cli._process_market_attention(r, wait=0)
    assert out["source_correction"]["error"] == "source_correction_unavailable"
    assert "breaker" in out and out["retry_after_seconds"] > 0
    r = resources()
    r.toll_bench.api.propose_act.side_effect = TimeoutError("uncertain response")
    out = cli._process_market_attention(r, wait=0)
    assert out["source_correction"]["error"] == "source_correction_transport_unavailable"
    assert "breaker" in out
    r.runtime.start.assert_not_called()


def test_per_act_exact_item_keys_and_existing_priority_unchanged():
    a = cli._obligation_key(ITEM)
    assert a != cli._obligation_key(dict(ITEM, act_id="returned-2"))
    assert a != cli._obligation_key(dict(ITEM, repeat_item="opaque item"))
    assert tuple(k for k in cli._OBLIGATION_PRIORITY if k != "act_correction") == (
        "deal_step",
        "paid_pack_review",
        "paid_pack_work",
        "pack_handoff",
        "draft_sent_back",
        "file_informed_plan",
        "unanswered_message",
        "goal_check",
    )
    assert "act_correction" not in cli._STEP_ASK_KINDS
