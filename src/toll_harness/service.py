"""The always-on service: a systemd user unit on Linux, a launchd agent on macOS (kept from
0.76, one shape per OS). It runs `python -m toll_harness.cli watch <agent.yaml>` with the
interpreter that installed it and brings it back when it stops with an error (an update stops
it on purpose with exit 75, so it comes back on the new build).

A unit file is not a secret store: only PATH and non-secret TOLL_HARNESS_* values are copied.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness import __version__

Runner = Callable[..., subprocess.CompletedProcess]
SYSTEMD, LAUNCHD = "systemd", "launchd"
RESTART_SECONDS = 10
_SECRETISH = re.compile(r"SECRET|PASSW|TOKEN|KEY|CREDENTIAL", re.IGNORECASE)


class ServiceError(RuntimeError):
    """The service manager is missing or refused; the message says what to do instead."""


def kind(platform: str | None = None) -> str:
    value = platform or sys.platform
    return SYSTEMD if value.startswith("linux") else LAUNCHD if value == "darwin" else "other"


def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not text:
        raise ValueError("a service name needs a letter or number")
    return text


def service_name(agent_name: str, manager: str) -> str:
    return (f"com.toll-harness.{slug(agent_name)}" if manager == LAUNCHD
            else f"toll-harness-{slug(agent_name)}.service")


def unit_path(name: str, manager: str, directory: str | Path | None = None) -> Path:
    if directory is not None:
        base = Path(directory)
    elif manager == LAUNCHD:
        base = Path.home() / "Library" / "LaunchAgents"
    else:
        base = Path.home() / ".config" / "systemd" / "user"
    return base / (f"{name}.plist" if manager == LAUNCHD else name)


def watch_command(config_path: Path, python: str | None = None) -> list[str]:
    return [str(Path(python or sys.executable).absolute()), "-m", "toll_harness.cli", "watch",
            str(config_path)]


def environment(source: dict[str, str] | None = None) -> dict[str, str]:
    source = dict(os.environ if source is None else source)
    kept = {"PYTHONUNBUFFERED": "1"}
    for key, value in sorted(source.items()):
        if value and (key in ("PATH", "HOME") or (
                key.startswith("TOLL_HARNESS_") and not _SECRETISH.search(key))):
            kept[key] = value
    return kept


def _quoted(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\\", "\\\\").replace('"', '\\"')
    return '"' + text.replace("%", "%%").replace("$", "$$") + '"'


def render_systemd(agent_name: str, config_path: Path, log_path: Path, command: list[str],
                   env: dict[str, str]) -> str:
    lines = [
        f"# Written by toll-harness {__version__} (toll-harness init).",
        "[Unit]",
        f"Description=Toll Harness for {agent_name}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={str(config_path.parent).replace('%', '%%')}",
        "ExecStart=" + " ".join(_quoted(part) for part in command),
        "Restart=on-failure",
        f"RestartSec={RESTART_SECONDS}",
    ]
    lines += [f"Environment={_quoted(f'{key}={value}')}" for key, value in env.items()]
    lines += [f"StandardOutput=append:{log_path}", f"StandardError=append:{log_path}", "",
              "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def render_launchd(label: str, config_path: Path, log_path: Path, command: list[str],
                   env: dict[str, str]) -> bytes:
    return plistlib.dumps({
        "Label": label, "ProgramArguments": command, "WorkingDirectory": str(config_path.parent),
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": RESTART_SECONDS, "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path), "EnvironmentVariables": env,
    })


def _run(runner: Runner, command: list[str], check: bool = False):
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        raise ServiceError(f"`{command[0]}` is not on this machine") from None
    if check and result.returncode != 0:
        said = (result.stderr or result.stdout or "").strip().splitlines()
        hint = (". Over ssh, run `loginctl enable-linger $USER` and log in again"
                if "bus" in " ".join(said).lower() else "")
        raise ServiceError(f"`{' '.join(command)}` failed: "
                           f"{said[-1] if said else result.returncode}{hint}")
    return result


def by_hand(config_path: Path) -> str:
    return ("This machine has no systemd user manager or launchd, so nothing was installed. "
            "Keep this running under the system's own supervisor, restarting it when it exits "
            "non-zero: " + shlex.join(watch_command(config_path)))


def install(agent: Any, *, runner: Runner = subprocess.run, platform: str | None = None,
            directory: str | Path | None = None, python: str | None = None) -> dict[str, Any]:
    """Write, enable and (re)start the agent's service. Idempotent."""
    manager = kind(platform)
    if manager == "other":
        raise ServiceError(by_hand(agent.path))
    tool = "launchctl" if manager == LAUNCHD else "systemctl"
    if runner is subprocess.run and shutil.which(tool) is None:
        raise ServiceError(by_hand(agent.path))
    name = service_name(agent.name, manager)
    path = unit_path(name, manager, directory)
    log_path = agent.data_dir / "service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = watch_command(agent.path, python)
    if path.exists() and str(agent.path) not in path.read_text(errors="replace"):
        raise ServiceError(f"{name} already runs another agent; give this agent its own name")
    path.parent.mkdir(parents=True, exist_ok=True)
    if manager == LAUNCHD:
        path.write_bytes(render_launchd(name, agent.path, log_path, command, environment()))
        domain = f"gui/{os.getuid()}"
        _run(runner, ["launchctl", "bootout", domain, str(path)])
        if _run(runner, ["launchctl", "bootstrap", domain, str(path)]).returncode != 0:
            _run(runner, ["launchctl", "load", "-w", str(path)], check=True)
        _run(runner, ["launchctl", "kickstart", "-k", f"{domain}/{name}"])
    else:
        path.write_text(render_systemd(agent.name, agent.path, log_path, command,
                                       environment()), encoding="utf-8")
        _run(runner, ["systemctl", "--user", "daemon-reload"], check=True)
        _run(runner, ["systemctl", "--user", "enable", name], check=True)
        _run(runner, ["systemctl", "--user", "restart", name], check=True)
    return {"service": name, "unit": str(path), "log": str(log_path),
            "running": status(agent, runner=runner, platform=platform) == "running"}


def restart(agent: Any, *, runner: Runner = subprocess.run, platform: str | None = None) -> None:
    manager = kind(platform)
    name = service_name(agent.name, manager)
    if manager == LAUNCHD:
        _run(runner, ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{name}"], check=True)
    elif manager == SYSTEMD:
        _run(runner, ["systemctl", "--user", "restart", name], check=True)


def status(agent: Any, *, runner: Runner = subprocess.run, platform: str | None = None) -> str:
    """running / stopped / not installed / unknown. Never writes."""
    manager = kind(platform)
    if manager == "other":
        return "unknown"
    name = service_name(agent.name, manager)
    try:
        if manager == LAUNCHD:
            result = runner(["launchctl", "print", f"gui/{os.getuid()}/{name}"],
                            check=False, capture_output=True, text=True)
            if result.returncode != 0:
                return "not installed"
            return "running" if "state = running" in (result.stdout or "") else "stopped"
        result = runner(["systemctl", "--user", "is-active", name], check=False,
                        capture_output=True, text=True)
    except OSError:
        return "unknown"
    state = (result.stdout or "").strip()
    if state == "active":
        return "running"
    return "stopped" if unit_path(name, manager).exists() else "not installed"
