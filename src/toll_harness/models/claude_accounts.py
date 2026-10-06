"""Claude account failover for the claude_code rail (0.61.0).

WHAT FORCED IT (Steven, 2026-10-06): the lab's Claude-brained agents all ride
one Claude subscription, and when that account hits its usage limit every one
of them stops mid-deal until the limit resets, while a second signed-in
account on the same machine sits idle. Steven's law for the harness is that it
never hobbles an agent and that a general gap is fixed in the harness, never
with a per-agent hack. So the rail carries an ordered list of Claude config
directories (one per signed-in account) and, when a call comes back at a usage
limit, it marks that account until its reset and runs the SAME prompt on the
next one.

Three pieces live here:

* ``resolve_config_dirs`` reads the ordered list: ``model.claude_config_dirs``
  in agent.yaml, else ``TOLL_HARNESS_CLAUDE_CONFIG_DIRS`` (colon separated),
  else nothing, which means "whatever the CLI does on its own", byte for byte
  the pre-0.61 call.
* ``detect_usage_limit`` reads one finished CLI call and says whether it ended
  at a usage limit, and when that limit resets.
* ``AccountTable`` is the rejected-until table every agent on the machine
  shares (one agent's hit tells the others), a small JSON file.

WHAT A USAGE LIMIT LOOKS LIKE (Claude Code 2.1.280, `claude -p --output-format
stream-json --verbose`, a subscription sign-in answered with the API's 429 and
`anthropic-ratelimit-unified-status: rejected`): the CLI exits 1 and does not
retry, and the stream carries three things:

    {"type": "rate_limit_event",
     "rate_limit_info": {"status": "rejected", "resetsAt": 1791326931,
                         "rateLimitType": "five_hour", ...}}
    {"type": "assistant", "error": "rate_limit", "is_api_error_message": true,
     "message": {"content": [{"type": "text",
       "text": "You've hit your session limit · resets 3:48pm (America/Los_Angeles)"}]}}
    {"type": "result", "subtype": "success", "is_error": true,
     "api_error_status": 429, "terminal_reason": "api_error",
     "result": "You've hit your session limit · resets 3:48pm (America/Los_Angeles)"}

Any ONE of these on a FAILED call counts (generous on purpose): the rejected
rate-limit event, the CLI's own `rate_limit` / `billing_error` class on the
API-error message, HTTP status 429 on the result, or the CLI's limit wording in
its error text ("You've hit your ... limit", "usage limit reached", "out of
usage credits", the older "Claude AI usage limit reached|<epoch>"). The reset
is the event's `resetsAt`, else the time the text names ("resets 3:48pm
(America/Los_Angeles)", "resets Sep 23, 5pm (...)", "|<epoch>"), else an hour.

WHAT IS NOT A LIMIT. A server-side throttle also ends in a 429 after the CLI's
own ten retries, and its stream looks almost the same: a `rate_limit_event`
with status "rejected" but no `resetsAt` and no `rateLimitType`, the
`rate_limit` class, `api_error_status: 429`, and the text "API Error: Server is
temporarily limiting requests (not your usage limit) · ...". The CLI says in
words that it is not the usage limit, so it is not one here: it keeps today's
error and marks no account. Nothing is read from a call that succeeded, and the
model's own words are never read: an agent drafting an email about someone's
plan limit is not a limit. A timeout, an overloaded API (529, the CLI's
`server_error`), a prompt-too-long (400, `invalid_request`), a tool error and a
native tool call are not limits either; those keep today's errors.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # POSIX: serialize the shared table's read-modify-write across agents
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl; atomic replace still holds
    fcntl = None  # type: ignore[assignment]

_LOGGER = logging.getLogger("toll_harness.models.claude_accounts")

CONFIG_DIRS_ENV = "TOLL_HARNESS_CLAUDE_CONFIG_DIRS"
ACCOUNTS_FILE_ENV = "TOLL_HARNESS_CLAUDE_ACCOUNTS_FILE"
DEFAULT_ACCOUNTS_FILE = "~/.toll-harness/claude-accounts.json"
# A limit whose reset the CLI did not name is held this long, then tried again.
DEFAULT_HOLD_SECONDS = 3600.0

_LIMIT_CLASSES = frozenset({"rate_limit", "billing_error"})

_LIMIT_TEXT = re.compile(
    r"you(?:'|’)?ve (?:hit|reached) your\b"
    r"|usage limit reached"
    r"|\bout of (?:extra )?usage\b"
    r"|out of usage credits"
    r"|\b(?:5-hour|session|weekly|opus|sonnet) limit reached",
    re.IGNORECASE,
)
# The CLI's own words for a server-side throttle, which is NOT a usage limit.
_THROTTLE_TEXT = re.compile(
    r"not your usage limit|temporarily limiting requests", re.IGNORECASE
)
_PIPE_EPOCH = re.compile(r"usage limit reached\|(\d{9,11})", re.IGNORECASE)
_RESETS_AT = re.compile(
    r"resets (?:at )?(?:(?P<month>[A-Z][a-z]{2})[a-z]* (?P<day>\d{1,2}),? (?:at )?)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)"
    r"(?:\s*\((?P<zone>[A-Za-z0-9_+\-/]+)\))?",
    re.IGNORECASE,
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


def label_for(config_dir: str | None) -> str:
    """How a log line names an account: the directory's basename."""
    if not config_dir:
        return "default"
    return os.path.basename(config_dir.rstrip("/\\")) or config_dir


def _normalize(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path.strip()))


def resolve_config_dirs(
    configured: Any = None, environ: Mapping[str, str] | None = None
) -> list[str]:
    """The ordered Claude config directories this rail rotates through.

    Source order: ``configured`` (agent.yaml ``model.claude_config_dirs``, a
    list or one colon-separated string), else ``TOLL_HARNESS_CLAUDE_CONFIG_DIRS``
    (colon separated), else ``[]``. An empty list means the CLI's own choice
    (``CLAUDE_CONFIG_DIR`` from the environment, or Claude's default): the
    pre-0.61 call exactly. Duplicates collapse, first one wins.
    """
    environ = os.environ if environ is None else environ
    raw: list[str]
    if configured not in (None, "", []):
        if isinstance(configured, str):
            raw = configured.split(os.pathsep)
        elif isinstance(configured, (list, tuple)):
            if not all(isinstance(item, str) for item in configured):
                raise ValueError("model.claude_config_dirs must be a list of directory paths")
            raw = list(configured)
        else:
            raise ValueError("model.claude_config_dirs must be a list of directory paths")
    else:
        raw = str(environ.get(CONFIG_DIRS_ENV) or "").split(os.pathsep)
    dirs: list[str] = []
    for item in raw:
        if not item.strip():
            continue
        path = _normalize(item)
        if path not in dirs:
            dirs.append(path)
    return dirs


@dataclass(frozen=True)
class UsageLimit:
    """One call that ended at an account's usage limit."""

    resets_at: float | None
    limit: str | None
    message: str

    def reason(self) -> str:
        return f"usage_limit:{self.limit}" if self.limit else "usage_limit"


def _events(stdout: str) -> Iterator[dict[str, Any]]:
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _reset_from_text(text: str, now: float) -> float | None:
    match = _PIPE_EPOCH.search(text)
    if match:
        return float(match.group(1))
    match = _RESETS_AT.search(text)
    if not match:
        return None
    zone = None
    if match.group("zone"):
        try:
            from zoneinfo import ZoneInfo

            zone = ZoneInfo(match.group("zone"))
        except Exception:  # noqa: BLE001 - an unknown zone falls back to local time
            zone = None
    current = datetime.fromtimestamp(now, tz=zone) if zone else datetime.fromtimestamp(now)
    hour = int(match.group("hour")) % 12 + (12 if match.group("ampm").lower() == "pm" else 0)
    minute = int(match.group("minute") or 0)
    try:
        if match.group("month"):
            month = _MONTHS.get(match.group("month")[:3].lower())
            if month is None:
                return None
            moment = current.replace(
                month=month, day=int(match.group("day")), hour=hour, minute=minute,
                second=0, microsecond=0,
            )
            if moment.timestamp() < now - 86400:
                moment = moment.replace(year=moment.year + 1)
        else:
            moment = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if moment.timestamp() <= now:
                moment = moment + timedelta(days=1)
    except ValueError:
        return None
    return moment.timestamp()


def detect_usage_limit(
    *,
    stdout: str,
    stderr: str,
    returncode: int,
    payload: Mapping[str, Any] | None,
    now: float | None = None,
) -> UsageLimit | None:
    """Did this finished `claude -p` call end at the account's usage limit?

    Only a FAILED call is read (non-zero exit or ``is_error``), and only the
    CLI's own error text, never a model reply. See the module docstring for
    the exact shapes.
    """
    failed = returncode != 0 or bool(payload and payload.get("is_error"))
    if not failed:
        return None
    now = time.time() if now is None else now
    rejected: Mapping[str, Any] | None = None
    limit_class = False
    texts: list[str] = []
    for event in _events(stdout):
        kind = event.get("type")
        if kind == "rate_limit_event":
            info = event.get("rate_limit_info")
            if isinstance(info, Mapping) and info.get("status") == "rejected":
                rejected = info
        elif kind == "assistant" and (
            event.get("error") or event.get("is_api_error_message")
        ):
            if event.get("error") in _LIMIT_CLASSES:
                limit_class = True
            content = (event.get("message") or {}).get("content") or []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, Mapping) and block.get("type") == "text":
                    texts.append(str(block.get("text") or ""))
    status = payload.get("api_error_status") if payload else None
    if payload and payload.get("is_error") and isinstance(payload.get("result"), str):
        texts.append(payload["result"])
    if payload is None and stdout:
        texts.append(stdout[-2000:])
    if stderr:
        texts.append(stderr[-2000:])
    if any(_THROTTLE_TEXT.search(text) for text in texts):
        return None
    worded = any(_LIMIT_TEXT.search(text) for text in texts)
    if rejected is None and not limit_class and status != 429 and not worded:
        return None

    resets_at: float | None = None
    limit_type: str | None = None
    if rejected is not None:
        limit_type = str(rejected.get("rateLimitType") or "") or None
        for key in ("resetsAt", "overageResetsAt"):
            value = rejected.get(key)
            if isinstance(value, (int, float)) and value > 0:
                resets_at = float(value)
                break
    message = next((text for text in texts if _LIMIT_TEXT.search(text)), texts[0] if texts else "")
    if resets_at is None:
        for text in texts:
            resets_at = _reset_from_text(text, now)
            if resets_at is not None:
                break
    if resets_at is not None and resets_at <= now:
        resets_at = None
    return UsageLimit(resets_at=resets_at, limit=limit_type, message=message.strip()[:300])


def iso_utc(moment: float | None) -> str:
    if moment is None:
        return "unknown"
    return datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AccountTable:
    """The shared rejected-until table: one small JSON file for the machine.

    ``{"version": 1, "accounts": {"<config dir>": {"rejected_until": <epoch>,
    ...}}}``. Every agent on the box reads and writes the same file, so one
    agent's limit tells the others. A missing or corrupt file reads as empty,
    an expired entry reads as absent, a write is a temp file plus an atomic
    rename under an advisory lock, and a write that fails is logged and kept in
    this process's memory, so this agent still fails over.
    """

    def __init__(self, path: str | Path | None = None):
        chosen = path or os.environ.get(ACCOUNTS_FILE_ENV) or DEFAULT_ACCOUNTS_FILE
        self.path = Path(chosen).expanduser()
        self._memory: dict[str, dict[str, Any]] = {}

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        accounts = value.get("accounts") if isinstance(value, dict) else None
        if not isinstance(accounts, dict):
            return {}
        return {
            str(key): dict(entry)
            for key, entry in accounts.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("rejected_until"), (int, float))
        }

    def entry(self, config_dir: str, now: float | None = None) -> dict[str, Any] | None:
        """The live rejection for this account, or None when it may be used."""
        now = time.time() if now is None else now
        candidates = [self._read().get(config_dir), self._memory.get(config_dir)]
        live = [item for item in candidates if item and float(item["rejected_until"]) > now]
        if not live:
            return None
        return max(live, key=lambda item: float(item["rejected_until"]))

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        if fcntl is None:
            yield
            return
        lock_path = self.path.with_name(self.path.name + ".lock")
        with open(lock_path, "a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def reject(
        self,
        config_dir: str,
        until: float,
        *,
        limit: UsageLimit | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        entry = {
            "label": label_for(config_dir),
            "rejected_until": float(until),
            "rejected_until_utc": iso_utc(until),
            "reason": limit.reason() if limit else "usage_limit",
            "message": limit.message if limit else "",
            "marked_at_utc": iso_utc(now),
        }
        self._memory[config_dir] = entry
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._locked():
                accounts = {
                    key: value
                    for key, value in self._read().items()
                    if float(value["rejected_until"]) > now
                }
                accounts[config_dir] = entry
                self._write({"version": 1, "accounts": accounts})
        except OSError as error:
            _LOGGER.warning(
                "claude_account_table_write_failed path=%s error=%s; kept in memory",
                self.path,
                error,
            )
        return entry

    def _write(self, value: dict[str, Any]) -> None:
        handle, temp = tempfile.mkstemp(prefix=".claude-accounts-", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w") as stream:
                json.dump(value, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise


def describe_rejections(rows: Sequence[tuple[str, dict[str, Any]]]) -> str:
    """'.claude (/x/.claude) until 2026-10-06T22:48:51Z [usage_limit:five_hour], ...'"""
    parts = []
    for config_dir, entry in rows:
        parts.append(
            f"{label_for(config_dir)} ({config_dir}) until "
            f"{iso_utc(float(entry['rejected_until']))} [{entry.get('reason') or 'usage_limit'}]"
        )
    return ", ".join(parts)
