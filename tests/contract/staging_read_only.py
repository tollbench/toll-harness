"""Run one installed harness against public staging reads and auth refusals.

Run this file twice, once in the oldest supported release's environment and
once in the candidate release's environment. No real token is read or used.
Only GET requests reach staging; missing-token checks stop in the client.
The resulting JSON files can be compared with compare_staging_read_only.py.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from toll_harness.email.book_of_houses import BookOfHousesApiClient, BookOfHousesApiError

INVALID_TOKEN = "invalid-contract-probe-no-agent"
PROTECTED_READS = ("me", "attention", "events", "act_kinds", "open_targets")
OPENAPI_PATHS = (
    "/api/bench/me",
    "/api/bench/me/attention",
    "/api/bench/targets/open",
    "/api/bench/acts/kinds",
)


def _refusal(call, *, expected_status: int) -> dict[str, object]:
    try:
        call()
    except BookOfHousesApiError as exc:
        if exc.status != expected_status:
            raise AssertionError(
                f"expected HTTP {expected_status}, got {exc.status}: {exc.code}"
            ) from exc
        return {"status": exc.status, "code": exc.code}
    raise AssertionError(f"expected HTTP {expected_status} refusal")


def run(base_url: str) -> dict[str, object]:
    client = BookOfHousesApiClient(base_url=base_url, timeout_seconds=10)
    protocol = client.protocol()
    assert protocol.get("ok") is True
    assert isinstance(protocol.get("contract_version"), str)
    assert protocol.get("rest_base") == "/api/bench"
    for key in ("registration", "capabilities", "version_signals", "artifact_hashes"):
        assert isinstance(protocol.get(key), dict), key

    schema = client.proposal_schema()
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict)
    assert isinstance(schema.get("required"), list)
    assert isinstance(schema.get("$id"), str)

    openapi_path = protocol.get("openapi")
    assert isinstance(openapi_path, str) and openapi_path.startswith("/")
    openapi = client._request("GET", openapi_path)
    assert isinstance(openapi.get("paths"), dict)
    for path in OPENAPI_PATHS:
        assert path in openapi["paths"], path

    skill = client.skill()
    assert "GET /api/bench/targets/open" in skill

    # This client method sends bearer auth, but the taxonomy route is public;
    # a synthetic invalid token verifies that it remains readable either way.
    invalid = client.authenticated(INVALID_TOKEN)
    capabilities = invalid.capability_taxonomy()
    assert capabilities.get("ok") is True
    assert isinstance(capabilities.get("capability_keys"), list)
    assert capabilities.get("capability_count") == len(capabilities["capability_keys"])

    board = client._request("GET", "/api/bench/board")
    assert board.get("ok") is True
    assert isinstance(board.get("count"), int)
    assert isinstance(board.get("wants"), list)
    assert board["count"] == len(board["wants"])

    local_refusals = {
        name: _refusal(getattr(client, name), expected_status=401)
        for name in PROTECTED_READS
    }
    server_refusals = {
        name: _refusal(getattr(invalid, name), expected_status=401)
        for name in PROTECTED_READS
    }
    return {
        "harness_version": importlib.metadata.version("toll-harness"),
        "scope": "staging public GETs and synthetic invalid-token GET refusals only",
        "protocol": {
            "contract_version": protocol["contract_version"],
            "protocol_version": protocol.get("protocol_version"),
            "required_fields_present": True,
        },
        "proposal_schema": {
            "id": schema["$id"],
            "required": sorted(schema["required"]),
        },
        "openapi_paths": list(OPENAPI_PATHS),
        "skill_read": True,
        "capability_count": capabilities["capability_count"],
        "public_board_read": True,
        "missing_token_local_refusals": local_refusals,
        "invalid_token_server_refusals": server_refusals,
        "pass": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.base_url)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"PASS toll-harness {result['harness_version']}: public staging reads and auth refusals")


if __name__ == "__main__":
    main()
