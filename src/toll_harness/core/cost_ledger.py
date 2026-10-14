"""What the agent spent on each deal, kept until a hand-back carries it.

The model adapters count every billed call here (``ModelAdapter.count_spend``);
the Toll Bench provider reads the deal's tab when it files an outcome and sends
it as the optional ``agent_cost`` slot ({usd, tokens_in, tokens_out, model,
basis}), then clears what it sent. A tab that never counted anything yields no
slot at all: the field is absent, never zero.

A call is counted only while a deal is the current one (``deal(...)``), so the
bidding scan, the model test and anything else that is not work on a deal costs
the deal nothing. Persisted to a small JSON file when given a path, so a restart
between the spend and the hand-back does not lose it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from toll_harness.models.pricing import price_usd

_LOGGER = logging.getLogger("toll_harness.cost_ledger")

BASIS_METERED = "metered"
BASIS_ESTIMATE = "subscription_estimate"
MODEL_MAX = 80


def _empty_tab() -> dict[str, Any]:
    return {
        "tokens_in": 0,
        "tokens_out": 0,
        "usd": 0.0,
        "priced": False,
        "calls": 0,
        "basis": "",
        "models": {},
    }


class CostLedger:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._tabs: dict[str, dict[str, Any]] = {}
        self._deal: str | None = None
        self._target: str | None = None
        self._lock = threading.RLock()
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if self._path is None:
            return
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._tabs = {
                str(key): {**_empty_tab(), **value}
                for key, value in data.items()
                if isinstance(value, dict)
            }

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(self._path.name + ".tmp")
            temporary.write_text(json.dumps(self._tabs, sort_keys=True))
            os.replace(temporary, self._path)
        except OSError:
            # Counting never costs the person a filing.
            _LOGGER.warning("could not save the agent cost ledger", exc_info=True)

    # -- the current deal --------------------------------------------------
    @contextmanager
    def deal(self, deal_id: object, target_id: object = None) -> Iterator[None]:
        """Spend counted inside this block belongs to the deal. No deal id, no
        counting."""
        with self._lock:
            before = (self._deal, self._target)
            self._deal = str(deal_id or "") or None
            self._target = str(target_id or "") or None
        try:
            yield
        finally:
            with self._lock:
                self._deal, self._target = before

    # -- counting ----------------------------------------------------------
    def add(
        self,
        model: object,
        *,
        basis: str,
        fresh_in: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        out: int = 0,
    ) -> None:
        fresh_in, cache_read, cache_write, out = (
            max(0, int(value or 0)) for value in (fresh_in, cache_read, cache_write, out)
        )
        if not (fresh_in or cache_read or cache_write or out):
            return
        with self._lock:
            if self._deal is None:
                return
            tab = self._tabs.setdefault(self._deal, _empty_tab())
            total_in = fresh_in + cache_read + cache_write
            tab["tokens_in"] += total_in
            tab["tokens_out"] += out
            tab["calls"] += 1
            dollars = price_usd(
                model,
                fresh_in=fresh_in,
                cache_read=cache_read,
                cache_write=cache_write,
                out=out,
            )
            if dollars is not None:
                tab["usd"] += dollars
                tab["priced"] = True
            # Two kinds of basis in one tab: say the weaker claim.
            if tab["basis"] and tab["basis"] != basis:
                tab["basis"] = BASIS_ESTIMATE
            else:
                tab["basis"] = basis
            name = str(model or "").strip()
            by_model = tab["models"]
            by_model[name] = by_model.get(name, 0) + total_in + out
            self._save()

    # -- the slot ----------------------------------------------------------
    def slot(self, target_id: object = None) -> dict[str, Any] | None:
        """The deal's ``agent_cost`` for its next hand-back, or None when
        nothing has been counted since the last one.

        ``usd`` is the dollars of the calls the table could price. The bench's
        slot requires a number, so a tab with only unpriced models says 0.0
        beside its token counts and model name instead of sending null (which
        the bench refuses, and the refusal would hold the hand-back).
        """
        with self._lock:
            if self._deal is None:
                return None
            if target_id and self._target and str(target_id) != self._target:
                return None
            tab = self._tabs.get(self._deal)
            if not tab or not tab.get("calls"):
                return None
            models = tab.get("models") or {}
            model = max(models, key=lambda key: models[key]) if models else ""
            return {
                "usd": round(float(tab["usd"]), 6),
                "tokens_in": int(tab["tokens_in"]),
                "tokens_out": int(tab["tokens_out"]),
                "model": model[:MODEL_MAX],
                "basis": tab["basis"] or BASIS_ESTIMATE,
            }

    def paid(self) -> None:
        """The hand-back landed with the slot: the deal's tab starts over."""
        with self._lock:
            if self._deal is not None and self._tabs.pop(self._deal, None) is not None:
                self._save()
