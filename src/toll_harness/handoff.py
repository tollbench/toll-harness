"""The ONE hand-off: run the agent's command with the item as JSON on stdin.

The command is an argv list run without a shell. Item text is data: it goes only to stdin,
never onto a command line and never into an environment value (only the item's id and kind,
checked to be plain words, ride the environment, and for an agent woken by name the path of
the folder the harness made for the files the item lists).

What the command prints decides what happens:
  - JSON object on stdout  -> that is the answer; the harness posts it.
  - nothing on stdout      -> the agent answers later with `toll-harness answer <item-id>`.
  - anything else, a non-zero exit, or the time limit -> a failure (three park the item).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from typing import Any

from toll_harness.incoming import FILES_ENV

ANSWERED = "answered"
LATER = "later"
FAILED = "failed"
_PLAIN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_MAX_STDOUT = 8 * 1024 * 1024


@dataclass
class HandOff:
    status: str  # answered | later | failed
    answer: dict[str, Any] | None = None
    why: str = ""  # one plain line, never the item's or the answer's contents
    said: str = ""  # the command's last stderr line: shown on this computer only, never sent
    seconds: float = 0.0


def _plain(value: Any) -> str:
    text = str(value or "")
    return text if _PLAIN.fullmatch(text) else "unknown"


def _stop(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass


def hand_off(command: list[str], item: dict[str, Any], *, time_limit: float,
             config_path: str | None = None, cwd: str | None = None,
             files_dir: str | None = None) -> HandOff:
    import time

    environment = dict(os.environ)
    environment["TOLL_ITEM_ID"] = _plain(item.get("id"))
    environment["TOLL_ITEM_KIND"] = _plain(item.get("kind"))
    if config_path:
        environment["TOLL_AGENT_CONFIG"] = str(config_path)
    environment.pop("TOLL_HARNESS_AGENT_TOKEN", None)  # the token stays with the harness
    environment.pop(FILES_ENV, None)
    if files_dir:
        environment[FILES_ENV] = str(files_dir)  # a folder the watch made, never item text
    data = json.dumps(item, ensure_ascii=False).encode("utf-8")
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=environment, cwd=cwd, shell=False,
            start_new_session=(os.name == "posix"),
        )
    except OSError as error:
        return HandOff(FAILED, why=f"the command could not start ({type(error).__name__})")
    try:
        out, _err = process.communicate(data, timeout=time_limit)
    except subprocess.TimeoutExpired:
        _stop(process)
        process.communicate()
        return HandOff(FAILED, why=f"no answer within the time limit ({int(time_limit)} s)",
                       seconds=time.monotonic() - started)
    except BaseException:  # the harness is stopping: the agent's command stops with it
        _stop(process)
        raise
    seconds = time.monotonic() - started
    if process.returncode != 0:
        lines = (_err or b"").decode("utf-8", errors="replace").strip().splitlines()
        return HandOff(FAILED, why=f"the command exited with {process.returncode}",
                       said=lines[-1][:300] if lines else "", seconds=seconds)
    text = out[:_MAX_STDOUT].decode("utf-8", errors="replace").strip()
    if not text:
        return HandOff(LATER, why="nothing printed: the agent answers later", seconds=seconds)
    try:
        answer = json.loads(text)
    except ValueError:
        return HandOff(FAILED, why="the command printed something that is not JSON",
                       seconds=seconds)
    if not isinstance(answer, dict):
        return HandOff(FAILED, why="the command printed JSON that is not an object",
                       seconds=seconds)
    return HandOff(ANSWERED, answer=answer, seconds=seconds)
