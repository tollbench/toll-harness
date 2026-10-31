"""THE BENCH'S OWN LIST OF DOORS (0.74.0, audit D12).

The bench publishes every door an agent may call in its contract,
`/agent-api.openapi.json` (the same file the skill page and the MCP list are
kept in step with). The harness reads that list at run time and hands the
model two tools over it: `toll_bench.doors` to look a door up, and
`toll_bench.call` to call one. A door the bench adds tomorrow is callable the
day it ships, and no door the bench does not publish can be called. The
hand-written `toll_bench.*` tools stay as they were; these two reach the doors
they never covered (the named question, operator help, a failed step, a
skipped repeat item, a flow material, a connector's tools, ...).

WHAT FORCED IT (lane 3 of the 2026-10-27 heat logs): the bench published 75
MCP tools and the harness carried 41, so the question door, the help door and
the failed-step door did not exist for any harness agent; one operator shipped
a private fork to reach the question door.

Doors that hold the agent's own credentials (token, recovery, registration,
the MCP token exchange) are never callable here: the harness keeps the token,
and a rotated token it never saw would lock the agent out.
"""

from __future__ import annotations

import re
from typing import Any

SPEC_PATH = "/agent-api.openapi.json"
METHODS = ("get", "post", "put", "patch", "delete")
BENCH_PREFIX = "/api/bench/"
# The harness's own credential doors.
CREDENTIAL_DOORS = (
    "/api/bench/agents/register",
    "/api/bench/agents/recovery",
    "/api/bench/oauth/",
    "/api/bench/me/token",
    "/api/bench/me/recovery-key",
    "/api/bench/payout-account/webhook",
)
SUMMARY_CHARS = 160
_REF_DEPTH = 6


def _resolve(node: Any, spec: dict[str, Any], depth: int = 0) -> Any:
    """`$ref`s into `components` filled in, a few levels deep."""
    if depth > _REF_DEPTH:
        return node
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = spec
            for part in ref[2:].split("/"):
                target = target.get(part) if isinstance(target, dict) else None
            if isinstance(target, dict):
                rest = {k: v for k, v in node.items() if k != "$ref"}
                return _resolve({**target, **rest}, spec, depth + 1)
        return {key: _resolve(value, spec, depth + 1) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve(item, spec, depth + 1) for item in node]
    return node


def published_doors(spec: Any) -> list[dict[str, Any]]:
    """Every `/api/bench/` door in the contract, minus the credential doors."""
    paths = spec.get("paths") if isinstance(spec, dict) else None
    if not isinstance(paths, dict):
        return []
    doors = []
    for path, operations in paths.items():
        if not str(path).startswith(BENCH_PREFIX) or not isinstance(operations, dict):
            continue
        if any(str(path).startswith(prefix) for prefix in CREDENTIAL_DOORS):
            continue
        for method in METHODS:
            operation = operations.get(method)
            if not isinstance(operation, dict):
                continue
            body = (((operation.get("requestBody") or {}).get("content") or {})
                    .get("application/json") or {}).get("schema")
            doors.append({
                "method": method.upper(),
                "path": str(path),
                "summary": str(operation.get("summary") or "")[:SUMMARY_CHARS],
                "operation": operation,
                "body": body,
            })
    return doors


def door_row(door: dict[str, Any]) -> dict[str, Any]:
    return {"method": door["method"], "path": door["path"], "summary": door["summary"]}


def door_whole(door: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """One door as the contract writes it: words, parameters and body schema."""
    operation = door.get("operation") or {}
    whole = door_row(door)
    if operation.get("description"):
        whole["description"] = operation["description"]
    parameters = _resolve(operation.get("parameters") or [], spec)
    if parameters:
        whole["parameters"] = parameters
    if door.get("body") is not None:
        whole["body"] = _resolve(door["body"], spec)
    return whole


def _pattern(path: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}/]+\})", path)
    return re.compile(
        "^" + "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts) + "$"
    )


def match_door(doors: list[dict[str, Any]], method: str, path: str) -> dict[str, Any] | None:
    """The published door this call is, or None. Literal segments win over
    `{param}` ones, so `/targets/open` is never read as `/targets/{target_id}`."""
    bare = str(path or "").split("?", 1)[0]
    method = str(method or "").upper()
    found = [door for door in doors
             if door["method"] == method and _pattern(door["path"]).match(bare)]
    if not found:
        return None
    return min(found, key=lambda door: door["path"].count("{"))


def find_doors(doors: list[dict[str, Any]], word: str = "") -> list[dict[str, Any]]:
    words = [w for w in str(word or "").lower().split() if w]
    if not words:
        return doors
    return [door for door in doors
            if all(w in (door["path"] + " " + door["summary"]).lower() for w in words)]
