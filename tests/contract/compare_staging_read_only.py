"""Compare oldest and candidate staging read-only contract receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("oldest", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    old = json.loads(args.oldest.read_text())
    new = json.loads(args.candidate.read_text())
    if old["harness_version"] == new["harness_version"]:
        raise AssertionError("oldest and candidate harness versions must differ")
    for name, value in (("oldest", old), ("candidate", new)):
        if value.get("pass") is not True:
            raise AssertionError(f"{name} did not pass its staging contract")
    equal_keys = (
        "protocol",
        "proposal_schema",
        "openapi_paths",
        "skill_read",
        "capability_count",
        "public_board_read",
        "missing_token_local_refusals",
        "invalid_token_server_refusals",
    )
    different = [key for key in equal_keys if old.get(key) != new.get(key)]
    if different:
        raise AssertionError(f"staging contract differed for {', '.join(different)}")
    result = {
        "pass": True,
        "scope": "public/read-only staging compatibility and synthetic invalid-token refusals",
        "oldest_version": old["harness_version"],
        "candidate_version": new["harness_version"],
        "equal_checks": list(equal_keys),
        "live_authenticated_agent_lifecycle_tested": False,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        f"PASS {result['oldest_version']} and {result['candidate_version']} "
        "staging read-only matrix"
    )


if __name__ == "__main__":
    main()
