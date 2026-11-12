"""Stay updated: read the harness version door on start and daily; newer -> install and restart;
below the minimum -> at once.

THE INSTALL SEAM is `install()`: one pip call with the interpreter that runs the harness.
Only the version NUMBER from the bench is used (checked to be a version); the bench's
`install` sentence is never run. With `update.source` in agent.yaml (a wheel file or a folder
of wheels) pip is told --no-index and never reaches a package index: that is how the tests and
a staging run exercise it.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from toll_harness import __version__

DAY_SECONDS = 24 * 3600
RESTART_EXIT = 75  # the watch exits with this after an update; the service brings it back
_VERSION_TEXT = re.compile(r"^\d+(?:\.\d+){0,3}(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?$")
_VERSION = re.compile(
    r"^\s*v?(?P<release>\d+(?:\.\d+)*)(?:[-_.]?(?P<pre>a|b|rc)[-_.]?(?P<pre_n>\d*))?"
    r"(?:[-_.]?post[-_.]?(?P<post>\d*))?(?:[-_.]?dev[-_.]?(?P<dev>\d*))?(?:\+.*)?\s*$",
    re.IGNORECASE,
)


def version_key(value: Any) -> tuple | None:
    """PEP 440-lite order (kept from 0.76): dev < pre < final < post; None when unreadable."""
    match = _VERSION.match(str(value or ""))
    if not match:
        return None
    release = [int(part) for part in match.group("release").split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    pre, dev, post = match.group("pre"), match.group("dev"), match.group("post")
    if pre:
        phase = ({"a": 1, "b": 2, "rc": 3}[pre.lower()], int(match.group("pre_n") or 0))
    elif dev is not None and post is None:
        phase = (0, 0)
    else:
        phase = (4, 0)
    return (tuple(release), phase, int(post or 0) + 1 if post is not None else 0,
            int(dev or 0) if dev is not None else 1 << 30)


def is_newer(candidate: Any, installed: Any) -> bool:
    left, right = version_key(candidate), version_key(installed)
    return left is not None and right is not None and left > right


def decide(answer: dict[str, Any], installed: str = __version__) -> dict[str, Any]:
    """{'action': 'none' | 'update' | 'now', 'version': what to install}."""
    current = str(answer.get("current") or "")
    minimum = str(answer.get("minimum") or "")
    result = {"installed": installed, "current": current or None, "minimum": minimum or None,
              "action": "none", "version": None}
    if not _VERSION_TEXT.fullmatch(current):
        return result
    if minimum and is_newer(minimum, installed):
        result.update(action="now", version=current)
    elif is_newer(current, installed):
        result.update(action="update", version=current)
    return result


def pip_command(version: str, source: str | None = None,
                python: str | None = None) -> list[str]:
    if not _VERSION_TEXT.fullmatch(version):
        raise ValueError("not a version number")
    command = [python or sys.executable, "-m", "pip", "install", "--upgrade",
               "--disable-pip-version-check", "--quiet"]
    if source:
        path = Path(source).expanduser()
        if path.is_file():
            return command + ["--no-index", "--no-deps", str(path)]
        return command + ["--no-index", "--find-links", str(path), f"toll-harness=={version}"]
    return command + [f"toll-harness=={version}"]


def installed_version(python: str | None = None, runner=subprocess.run) -> str | None:
    result = runner([python or sys.executable, "-c",
                     "import importlib.metadata as m; print(m.version('toll-harness'))"],
                    capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def install(version: str, *, source: str | None = None, python: str | None = None,
            runner=subprocess.run) -> tuple[bool, str]:
    """THE SEAM. Install toll-harness==version; (worked, one plain line)."""
    try:
        command = pip_command(version, source, python)
    except ValueError as error:
        return False, str(error)
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, f"pip could not install toll-harness {version} (exit {result.returncode})"
    now = installed_version(python, runner)
    if now != version:
        return False, f"pip finished but toll-harness {now or 'unknown'} is installed"
    return True, f"installed toll-harness {version}"


def check_and_install(agent: Any, bench: Any, log: Any, *, force: bool = False,
                      runner=subprocess.run, python: str | None = None) -> dict[str, Any]:
    """Read the version door; install when newer (or below the minimum). Never raises."""
    agent.write_json("update.json", {"checked_at": time.time()})
    try:
        verdict = decide(bench.version())
    except Exception as error:  # noqa: BLE001 - an update check never stops the harness
        log.line(f"update check: the version door did not answer ({type(error).__name__})")
        return {"action": "none", "error": True}
    if verdict["action"] == "none":
        return verdict
    if not agent.auto_update and not force and verdict["action"] != "now":
        log.line(f"update: {verdict['version']} is out; automatic updates are off")
        return {**verdict, "action": "none", "off": True}
    worked, line = install(verdict["version"], source=agent.update_source, python=python,
                           runner=runner)
    log.line(f"update: {line}" if worked else f"update failed: {line}",
             "info" if worked else "warn")
    return {**verdict, "installed_now": worked, "line": line}


def due(agent: Any, now: float | None = None) -> bool:
    last = agent.read_json("update.json", {}).get("checked_at")
    return not isinstance(last, (int, float)) or (now or time.time()) - last >= DAY_SECONDS
