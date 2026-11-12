"""Activity lines: always a small local log; to POST /me/log when the bench says logging is on.

A line says what happened (arrived, handed off, answered, refused, parked, update...). It never
carries an item's contents, an answer's contents, or a secret: callers pass ids, kinds, counts
and the harness's own words only.
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_NAME = "harness.log"
_BATCH = 50  # lines per POST /me/log
_HELD = 500  # lines kept while the bench cannot take them


class ActivityLog:
    def __init__(self, directory: Path, *, echo: bool = True):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / LOG_NAME
        self.logger = logging.getLogger(f"toll_harness.activity.{directory}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                self.path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(handler)
            if echo:
                stream = logging.StreamHandler()
                stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
                self.logger.addHandler(stream)
        self.to_bench = False
        self._waiting: list[dict[str, Any]] = []

    def line(self, text: str, level: str = "info") -> None:
        self.logger.log(logging.WARNING if level == "warn" else logging.INFO, "%s", text)
        if self.to_bench:
            self._waiting.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "level": level, "line": text[:300]})
            del self._waiting[:-_HELD]

    def warn(self, text: str) -> None:
        self.line(text, "warn")

    def flush(self, bench: Any) -> None:
        """Send the waiting lines; keep them for next time when the bench does not take them."""
        while self._waiting and self.to_bench:
            batch = self._waiting[:_BATCH]
            try:
                bench.log(batch)
            except Exception:  # noqa: BLE001 - a log line never stops the harness
                return
            del self._waiting[:len(batch)]


def tail(directory: Path, lines: int = 40) -> list[str]:
    path = directory / LOG_NAME
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
