"""`toll-harness market payout-sync` (bench 2026-10-12).

Re-reads Stripe, prints the bench's plain-words payout status, exits 0 when
paid work is possible and 3 when not; an older bench without the status door
gets the sync body, as before. Nothing reaches a network: the API is faked.
"""
from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import Any

from toll_harness import cli
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider


class _Api:
    def __init__(self, status: dict[str, Any] | None, sync: dict[str, Any] | None = None):
        self._status = status
        self._sync = sync or {"ok": True, "payout": {"readiness": "restricted",
                                                      "requirements": ["external_account"]}}
        self.calls: list[str] = []

    def payout_sync(self, idempotency_key: str) -> dict[str, Any]:
        self.calls.append("sync")
        assert idempotency_key.startswith("toll-harness-payout-sync-")
        return self._sync

    def payout_status(self) -> dict[str, Any]:
        self.calls.append("status")
        if self._status is None:
            raise BookOfHousesApiError(404, "not_found", "no such route")
        return self._status


def _run(monkeypatch, provider, capsys):
    resources = SimpleNamespace(toll_bench=provider, close=lambda: None)
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    code = cli.command_market_payout_sync(argparse.Namespace(config="agent.yaml"))
    return code, capsys.readouterr().out


def test_payout_sync_prints_status_and_exits_3_when_not_ready(monkeypatch, capsys):
    api = _Api({"ok": True, "ready": False, "operator_verified": False,
                "still_needed": ["register as this agent's operator", "a photo of your ID"],
                "operator_registration_url": "https://tollbench.com/operators/register?agent=t"})
    code, out = _run(monkeypatch, BookOfHousesTollBenchProvider(api), capsys)
    assert code == 3
    assert api.calls == ["sync", "status"]
    assert "a photo of your ID" in out and "operators/register" in out


def test_payout_sync_exits_0_when_ready(monkeypatch, capsys):
    api = _Api({"ok": True, "ready": True, "still_needed": []})
    code, _out = _run(monkeypatch, BookOfHousesTollBenchProvider(api), capsys)
    assert code == 0


def test_older_bench_without_the_status_door(monkeypatch, capsys):
    api = _Api(None)
    code, out = _run(monkeypatch, BookOfHousesTollBenchProvider(api), capsys)
    assert code == 3
    assert "external_account" in out


def test_the_subcommand_is_wired():
    parser = cli.build_parser()
    args = parser.parse_args(["market", "payout-sync", "agent.yaml"])
    assert args.handler is cli.command_market_payout_sync
