"""Deterministic refile of one server-authorized internal source correction."""

from __future__ import annotations

import hashlib
import json
from typing import Any

FIELDS = frozenset({"act_id", "repeat_item", "approval_hash", "source_hash"})


def matches_current(payload: Any, deal_id: str, step_id: str, correction: Any) -> bool:
    if (
        not isinstance(payload, dict)
        or not isinstance(correction, dict)
        or set(correction) != FIELDS
        or any(not isinstance(v, str) or not v for v in correction.values())
    ):
        return False
    step = payload.get("current_step") or {}
    deal = payload.get("deal") or {}
    thread = payload.get("step_thread") or {}
    if not all(isinstance(v, dict) for v in (step, deal, thread)):
        return False
    if (
        str(step.get("id") or "") != step_id
        or step.get("state") != "agent_working"
        or str(deal.get("id") or "") != deal_id
        or payload.get("awaiting_funding")
        or payload.get("unread_from_person")
        or thread.get("unread_from_person")
        or thread.get("unanswered_messages")
        or thread.get("unanswered_elsewhere")
        or payload.get("owed_replies")
    ):
        return False
    corrections, rows = payload.get("source_corrections"), payload.get("acts")
    if not isinstance(corrections, list) or not isinstance(rows, list):
        return False
    matches = [v for v in corrections if v == correction]
    if len(matches) != 1:
        return False
    acts = [a for a in rows if isinstance(a, dict) and a.get("act_id") == correction["act_id"]]
    return (
        len(acts) == 1
        and acts[0].get("kind") == "calls"
        and acts[0].get("state") == "sent_back"
        and acts[0].get("repeat_item") == correction["repeat_item"]
    )


def dispatch(resources: Any, obligation: dict[str, Any]) -> dict[str, Any]:
    correction = obligation.get("correction")
    deal_id, step_id = (str(obligation.get(k) or "") for k in ("deal_id", "step_id"))
    if (
        not deal_id
        or not step_id
        or not isinstance(correction, dict)
        or obligation.get("act_id") != correction.get("act_id")
        or obligation.get("repeat_item") != correction.get("repeat_item")
    ):
        return {"ok": False, "error": "invalid_source_correction"}
    key = (
        "source-correction-"
        + hashlib.sha256(
            json.dumps(
                [deal_id, step_id, correction], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    # This method rereads the live selected-agent step, then uses only the
    # existing act endpoint. No generic model/tool dispatch or approval.
    refile = getattr(resources.toll_bench, "refile_source_correction", None)
    if not callable(refile):
        return {"ok": False, "error": "source_correction_unavailable"}
    try:
        result = refile(deal_id, step_id, correction, key)
    except Exception:  # transport may have committed: retry the same immutable request/key
        return {"ok": False, "error": "source_correction_transport_unavailable"}
    if not isinstance(result, dict):
        return {"ok": False, "error": "invalid_source_correction_response"}
    if result.get("ok") is not True:
        return result
    status = result.get("status")
    if status in ("done", "executed"):
        return dict(result, repaired=True, terminal=True)
    if status == "held":
        return dict(result, repaired=False, terminal=True, awaiting_person=True)
    return dict(
        result,
        ok=False,
        repaired=False,
        terminal=True,
        error="source_correction_failed" if status == "failed" else "source_correction_incomplete",
    )
