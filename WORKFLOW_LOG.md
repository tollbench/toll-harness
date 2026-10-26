# 0.72.0 source correction adapter receipt

## Authorization and scope — 2026-10-26

Steven authorized the framework repair through Jarvis, with direct Astra high
coding. Factory/Jev routing is not claimed. The site has one owner-returned
internal draft hidden behind different pending repeat items. A message-only wake
cannot refile it: the old harness legitimately blocks generic work while the
person owns other pending actions. Root authorized a deterministic correction
obligation and official versioned harness preparation; installed workers and
production activation remain separate explicit approvals.

Isolated branch: `codex/act-correction-adapter-20261025`, from official v0.71.0
`5fc8749ab53049e19c49a1dc57ced8ac502a9b18`. Read-only public collision check found
PyPI latest 0.71.0, no 0.72.0, and official main/tag at that exact base.

## Implementation

- Recognize only the new `act_correction` obligation, after message obligations
  without reordering existing kinds. Key retry state by exact act/item and source
  hashes; preserve opaque whitespace. No model call or approval/execution tool.
- Re-read the full existing authenticated API current-step snapshot, before the
  normal model-view whitelist/history trim. Require the exact returned calls
  act, item, source descriptor, open signed-work step and no message/funding debt.
- File the exact four-field descriptor at the existing act endpoint with a stable
  idempotency key. The server checks owner return authorization, signed scope,
  selected executor, current source and one durable replacement under locks.
- Keep all normal provider guards, email resume behavior, worker pulses and
  unrelated scheduling unchanged. Failed/held attempts are reported honestly;
  missing provider support and uncertain transport use the existing breaker.
- No card release, buyer final approval, Passport write, model/provider evaluation
  or installed worker mutation is part of this change.

## Verification

Focused actual attention-cycle/provider/API tests: **26 passed**. Cases cover
one returned + two pending items, exact unchanged request/key, full raw snapshot,
wrong/stale/malformed context, unpaid funding, current/other-step message debts,
external/pending act mismatch, exact whitespace, missing provider method,
uncertain transport, honest done/held/failed results, and existing priority order.

Local Python 3.11.17 required-suite run: **1545 passed, 2 skipped, 2 deselected**.
The two deselected tests assume Linux systemd while this machine is macOS; the
unchanged official CI must run them on Linux before any release tag. Initial
full run had those two OS failures plus stale 0.70 metadata in a reused test
interpreter; a new disposable venv installed the candidate 0.72 wheel and passed
the metadata gate. Existing dependency libraries were read through a `.pth`
inside the new venv; no existing venv was modified. Lint (`ruff check .`) passes.

Commands:

```sh
PYTHONPATH=src /private/tmp/toll-harness-correction-venv-20261025/bin/python -m pytest tests/unit/test_source_correction.py -q
PYTHONPATH=src /private/tmp/toll-harness-correction-venv-20261025/bin/python -m pytest -k 'not test_install_market_worker_writes_restartable_isolated_unit and not test_market_worker_status_reports_systemd_truth'
/private/tmp/toll-harness-correction-venv-20261025/bin/python -m ruff check .
```

Independent reviewer owns the joined site fixture at
`tests/action_pack_repeat/test_correction.py` in the site worktree. Its seven
checks invoke actual core filing/schema/internal execution plus this real CLI
and API provider using synthetic persistence and HTTP transport. Combined site
suite: **36 unique checks passed**. No real PostgreSQL concurrency or full Flask
auth middleware claim; row-lock ordering and transaction linkage are reviewed.

## Release gates

Root authorized feature branch/PR CI, not tag/publication yet. Existing workflow:
PR to main runs Python 3.10–3.13 lint/tests and package build/twine. After exact
review and full CI, root gives publication GO; push immutable version tag via
existing `Publish to PyPI` Trusted Publishing workflow, then download public
wheel/sdist and verify version, file graph/hashes and provenance. Preserve main
history and use a normal reviewed merge, never force. No worker install or site
activation follows automatically. Token totals are not exposed to this worker.
