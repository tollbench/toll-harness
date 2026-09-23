"""Tell the operator when a newer Toll Harness or a moved bench contract exists.

A pip install cannot run code, so the check starts with the first command an
operator runs after installing (``init``, which checks unthrottled) and then
rides every later command at most once per hour per data directory.

Two things are compared:

- the installed harness against the newest release, read from the bench
  protocol's ``harness`` block when the bench publishes one, else from PyPI;
- the bench's live ``protocol_version`` / ``contract_version`` /
  ``rules_version_hash`` against the snapshot this agent's onboarding state
  recorded last time, which is then refreshed.

The check tells; it never installs anything. It never raises and never blocks
a run: every failure comes back as ``{"ok": False, "error": ...}``.
``TOLL_HARNESS_UPDATE_CHECK=0`` (or ``off`` / ``false``) turns it off entirely;
``TOLL_HARNESS_UPDATE_CHECK_SECONDS`` sets the throttle (default 3600).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import toll_harness

PYPI_URL = "https://pypi.org/pypi/toll-harness/json"
INSTALL_COMMAND = "pip install -U toll-harness"
DEFAULT_INTERVAL_SECONDS = 3600.0
NETWORK_TIMEOUT_SECONDS = 5
BENCH_FIELDS = ("protocol_version", "contract_version", "rules_version_hash")
RULES_NOTE = (
    "The bench rules changed; the harness re-reads the guide live on every run, "
    "so nothing needs to be done locally."
)

_OFF_VALUES = {"0", "off", "false", "no", "disabled"}
_VERSION = re.compile(
    r"^\s*v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre>a|alpha|b|beta|c|rc|pre|preview)[-_.]?(?P<pre_n>\d*))?"
    r"(?:[-_.]?(?:post|rev|r)[-_.]?(?P<post>\d*))?"
    r"(?:[-_.]?dev[-_.]?(?P<dev>\d*))?"
    r"(?:\+.*)?\s*$",
    re.IGNORECASE,
)
_PRE_RANK = {"a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}


def disabled() -> bool:
    return str(os.environ.get("TOLL_HARNESS_UPDATE_CHECK", "")).strip().lower() in _OFF_VALUES


def interval_seconds() -> float:
    raw = os.environ.get("TOLL_HARNESS_UPDATE_CHECK_SECONDS")
    if raw is None or not str(raw).strip():
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return value if value >= 0 else DEFAULT_INTERVAL_SECONDS


def version_key(value: Any) -> tuple | None:
    """PEP 440-lite ordering key: numeric release, then dev < pre < final < post.

    Returns None for anything it cannot read, and a None never counts as newer.
    """
    match = _VERSION.match(str(value or ""))
    if not match:
        return None
    release = [int(part) for part in match.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    pre = match.group("pre")
    dev = match.group("dev")
    post = match.group("post")
    if pre:
        phase = (_PRE_RANK[pre.lower()], int(match.group("pre_n") or 0))
    elif dev is not None and post is None:
        phase = (0, 0)  # a bare .devN sorts below every pre-release of that release
    else:
        phase = (4, 0)
    post_n = int(post or 0) + 1 if post is not None else 0
    dev_n = int(dev or 0) if dev is not None else 1 << 30
    return (tuple(release), phase, post_n, dev_n)


def is_newer(candidate: Any, installed: Any) -> bool:
    left, right = version_key(candidate), version_key(installed)
    return left is not None and right is not None and left > right


def _now() -> float:
    return time.time()


def _fetch_pypi_latest() -> str:
    request = urllib.request.Request(
        PYPI_URL,
        headers={"Accept": "application/json", "User-Agent": "toll-harness-update-check"},
    )
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = ((payload or {}).get("info") or {}).get("version")
    if not latest:
        raise ValueError("PyPI answered without a version")
    return str(latest)


def _harness_check(protocol: dict[str, Any] | None) -> dict[str, Any]:
    installed = toll_harness.__version__
    block = (protocol or {}).get("harness") if isinstance(protocol, dict) else None
    result: dict[str, Any] = {
        "installed": installed,
        "latest": None,
        "minimum": None,
        "behind": False,
        "below_minimum": False,
        "install_command": INSTALL_COMMAND,
    }
    if isinstance(block, dict) and block.get("latest_version"):
        result.update(
            source="bench",
            latest=str(block["latest_version"]),
            minimum=str(block["minimum_version"]) if block.get("minimum_version") else None,
            install_command=str(block.get("install") or INSTALL_COMMAND),
        )
    else:
        try:
            result.update(source="pypi", latest=_fetch_pypi_latest())
        except Exception as error:  # noqa: BLE001 - an update check never raises
            return {**result, "ok": False, "source": "pypi", "error": _describe(error)}
    result["behind"] = is_newer(result["latest"], installed)
    result["below_minimum"] = bool(result["minimum"]) and is_newer(result["minimum"], installed)
    return {**result, "ok": True}


def _bench_check(
    live: dict[str, Any], snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    new = {field: live.get(field) for field in BENCH_FIELDS}
    old = {field: (snapshot or {}).get(field) for field in BENCH_FIELDS}
    changed = []
    if snapshot:
        changed = [
            field
            for field in BENCH_FIELDS
            if new[field] is not None and old[field] is not None and new[field] != old[field]
        ]
    result: dict[str, Any] = {"ok": True, "changed": changed, "old": old, "new": new}
    if "rules_version_hash" in changed:
        result["note"] = RULES_NOTE
    return result


def _notices(harness: dict[str, Any] | None, bench: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    if harness and harness.get("ok"):
        installed, latest = harness["installed"], harness["latest"]
        command = harness["install_command"]
        if harness.get("below_minimum"):
            lines.append(
                f"toll-harness {installed} installed is below the bench's minimum "
                f"{harness['minimum']}: {command}"
            )
        elif harness.get("behind"):
            lines.append(f"toll-harness {installed} installed, {latest} available: {command}")
    changed = (bench or {}).get("changed") or []
    if changed:
        old, new = bench["old"], bench["new"]
        moved = next(
            (field for field in ("contract_version", "protocol_version") if field in changed),
            None,
        )
        rules = "rules_version_hash" in changed
        short_hash = str(new["rules_version_hash"] or "")[:8]
        if moved:
            name = "contract" if moved == "contract_version" else "protocol"
            line = f"Toll Bench {name} moved {old[moved]} -> {new[moved]} since your last check"
            if rules:
                line += f"; the rules changed too (hash {short_hash}), the guide is re-read live"
            lines.append(line)
        elif rules:
            lines.append(
                f"The Toll Bench rules changed since your last check (hash {short_hash}); "
                "the guide is re-read live"
            )
    return lines


def _describe(error: BaseException) -> str:
    text = str(error).strip()
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def _resolve_config(config_path: Any) -> Path:
    path = Path(config_path).expanduser().resolve()
    if path.is_dir() or not path.name.endswith((".yaml", ".yml")):
        path = path / "agent.yaml"
    return path


def check_for_updates(
    config_path: Any = None, api: Any = None, *, force: bool = False
) -> dict[str, Any]:
    """Compare the installed harness and the stored bench snapshot with what is live.

    Never raises. ``force=True`` ignores the throttle (not the off switch).
    Without a config only a forced harness check can run, since the throttle
    and the bench snapshot both live in the agent's data directory.
    """
    try:
        return _check(config_path, api, force=force)
    except Exception as error:  # noqa: BLE001 - an update check never raises
        return {"ok": False, "error": _describe(error), "notices": []}


def _check(config_path: Any, api: Any, *, force: bool) -> dict[str, Any]:
    if disabled():
        return {"ok": True, "skipped": "disabled", "notices": []}
    from toll_harness.onboarding import load_config, load_onboarding, save_onboarding

    if config_path is None:
        if not force:
            return {"ok": False, "error": "no_config", "notices": []}
        harness = _harness_check(None)
        notices = _notices(harness, None)
        return {
            "ok": bool(harness.get("ok")),
            "checked": True,
            "harness": harness,
            "bench": None,
            "notices": notices,
            "new": bool(notices),
        }

    path = _resolve_config(config_path)
    if not path.exists():
        return {"ok": False, "error": f"config not found: {path.name}", "notices": []}
    config = load_config(path)
    state = load_onboarding(path, config)
    previous = state.get("update_check") if isinstance(state.get("update_check"), dict) else {}
    now = _now()
    last = previous.get("checked_at_epoch")
    wait = interval_seconds()
    if not force and isinstance(last, (int, float)) and now - float(last) < wait:
        return {
            "ok": True,
            "skipped": "throttled",
            "next_check_in_seconds": round(wait - (now - float(last)), 1),
            "notices": [],
        }

    toll_bench = config.get("toll_bench") or {}
    protocol = None
    bench: dict[str, Any] | None = None
    if toll_bench.get("connected") or api is not None:
        try:
            if api is None:
                from toll_harness.email.book_of_houses import BookOfHousesApiClient

                api = BookOfHousesApiClient(
                    base_url=toll_bench.get("base_url") or "https://bookofhouses.com",
                    timeout_seconds=NETWORK_TIMEOUT_SECONDS,
                )
            protocol = api.protocol()
            if not isinstance(protocol, dict):
                raise ValueError("the bench protocol was not an object")
            snapshot = state.get("protocol") if isinstance(state.get("protocol"), dict) else None
            bench = _bench_check(protocol, snapshot)
        except Exception as error:  # noqa: BLE001 - the bench being down is not our failure
            bench = {"ok": False, "error": _describe(error), "changed": []}
            protocol = None
    harness = _harness_check(protocol)
    notices = _notices(harness, bench)
    latest = harness.get("latest")
    new = bool((bench or {}).get("changed")) or (
        bool(harness.get("behind") or harness.get("below_minimum"))
        and latest != previous.get("notified_latest")
    )

    # Reload right before writing so a concurrent writer's keys survive; only
    # the two keys this check owns are touched.
    fresh = load_onboarding(path, config)
    record = {
        "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "checked_at_epoch": now,
        "installed_version": harness.get("installed"),
        "latest_version": latest or previous.get("latest_version"),
        "last_notice": notices,
    }
    if harness.get("behind") or harness.get("below_minimum"):
        record["notified_latest"] = latest
    elif previous.get("notified_latest") and not harness.get("ok"):
        record["notified_latest"] = previous["notified_latest"]
    fresh["update_check"] = record
    if bench and bench.get("ok"):
        fresh["protocol"] = dict(bench["new"])
    save_onboarding(path, config, fresh)

    return {
        "ok": bool(harness.get("ok")) and (bench is None or bool(bench.get("ok"))),
        "checked": True,
        "checked_at": record["checked_at"],
        "harness": harness,
        "bench": bench,
        "notices": notices,
        "new": new,
    }
