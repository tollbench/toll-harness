"""Run an agent's market watch in the background: one command per OS.

``toll-harness install-service <agent.yaml>`` writes a per-user service that
runs ``python -m toll_harness.cli market watch <agent.yaml>`` with the
interpreter that ran the command, starts it, and brings it back when it dies:
a systemd user unit on Linux, a launchd agent on macOS. ``market worker
install`` (and so ``init``) writes through the same renderer, so each OS has
one unit shape and one name per agent: ``toll-harness-<name>.service`` and
``com.toll-harness.<name>``.

Windows gets the equivalent Task Scheduler / NSSM line and exit 2; nothing is
faked. A unit file is not a secret store: secrets set in the installing shell
are named and left out, never copied.

WHAT FORCED IT: on 2026-09-24 Tilly's watch, run by hand on a machine that
restarts, died three times in one day with nothing to bring it back.
"""

from __future__ import annotations

import getpass
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import toll_harness
from toll_harness.onboarding import READY, data_directory, load_config

Runner = Callable[..., subprocess.CompletedProcess[str]]

SYSTEMD = "systemd"
LAUNCHD = "launchd"
SYSTEMD_PREFIX = "toll-harness-"
LAUNCHD_PREFIX = "com.toll-harness."
RESTART_SECONDS = 10
RESTART_POLICY = "on-failure"

# Not secrets, and a service does not inherit the shell they were set in:
# PATH finds a CLI rail (claude, codex) the way this shell found it.
PASSTHROUGH_NAMES = ("PATH", "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION")
PASSTHROUGH_PREFIX = "TOLL_HARNESS_"
PROVIDER_SECRETS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
_SECRET_NAME = re.compile(r"SECRET|PASSW|(?:^|_)(?:TOKEN|KEY|CREDENTIALS?)$")
_VALUE_FLAGS = {"--wait", "--interval", "--scan-interval"}
_NO_BUS = ("Failed to connect to bus", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")

HAND_RUN_NOTE = (
    "If a watch for this agent is still running in a terminal, stop it (Ctrl-C): "
    "the service runs it now, and two watches on one agent bid twice."
)


class ServiceError(ValueError):
    """The service manager refused or is not there; the message says what to do."""


class ServiceUnsupported(ServiceError):
    """No service manager this command knows; the message is the equivalent by hand."""


def service_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not slug:
        raise ValueError("A service name must contain a letter or number")
    return slug


def service_kind(platform: str | None = None) -> str:
    value = platform or sys.platform
    if value.startswith("linux"):
        return SYSTEMD
    if value == "darwin":
        return LAUNCHD
    if value in {"win32", "cygwin", "msys"}:
        return "windows"
    return "unsupported"


def resolve_config_path(config_path: str | Path) -> Path:
    """The agent.yaml itself; a directory means the agent.yaml inside it."""
    path = Path(config_path).expanduser()
    if path.is_dir():
        path = path / "agent.yaml"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"no agent.yaml at {path}")
    return path


def _slug_for(config: dict[str, Any], name: str | None) -> str:
    if name:
        return service_slug(name)
    agent_name = (config.get("agent") or {}).get("name")
    if not agent_name:
        raise ValueError("agent.yaml names no agent (agent.name); pass --name")
    return service_slug(agent_name)


def service_name(config: dict[str, Any], *, name: str | None = None, kind: str = SYSTEMD) -> str:
    slug = _slug_for(config, name)
    return f"{LAUNCHD_PREFIX}{slug}" if kind == LAUNCHD else f"{SYSTEMD_PREFIX}{slug}.service"


def unit_directory_for(kind: str, unit_directory: str | Path | None = None) -> Path:
    if unit_directory is not None:
        return Path(unit_directory).expanduser().resolve()
    if kind == LAUNCHD:
        return Path.home() / "Library" / "LaunchAgents"
    return Path.home() / ".config" / "systemd" / "user"


def unit_path_for(kind: str, service: str, unit_directory: str | Path | None = None) -> Path:
    suffix = ".plist" if kind == LAUNCHD else ""
    return unit_directory_for(kind, unit_directory) / f"{service}{suffix}"


def watch_command(config_path: Path, python: str | Path | None = None) -> list[str]:
    interpreter = Path(python) if python else Path(sys.executable)
    return [
        str(interpreter.absolute()),
        "-m",
        "toll_harness.cli",
        "market",
        "watch",
        str(config_path),
    ]


def _is_secret(key: str) -> bool:
    return bool(_SECRET_NAME.search(key.upper()))


def service_environment(
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """What the service is given, and the secrets set here that it is not given."""
    source = os.environ if environ is None else environ
    environment = {"PYTHONUNBUFFERED": "1"}
    withheld: list[str] = []
    for key in sorted(source):
        value = source[key]
        if not value:
            continue
        if key.startswith(PASSTHROUGH_PREFIX):
            if _is_secret(key):
                withheld.append(key)
            else:
                environment[key] = value
        elif key in PASSTHROUGH_NAMES:
            environment[key] = value
        elif key in PROVIDER_SECRETS:
            withheld.append(key)
    return environment, withheld


# -- rendering ------------------------------------------------------------------


def _one_line(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def _unit_literal(value: Any) -> str:
    """A path setting: systemd takes the rest of the line literally, % excepted."""
    return _one_line(value).replace("%", "%%")


def _unit_quoted(value: Any, *, command: bool = False) -> str:
    text = _one_line(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        text = text.replace("$", "$$")
    return f'"{text}"'


def render_systemd_unit(
    *,
    description_name: str,
    config_path: Path,
    log_path: Path,
    command: list[str],
    environment: Mapping[str, str],
) -> str:
    lines = [
        f"# Written by toll-harness {toll_harness.__version__}. "
        "Run `toll-harness install-service` again to change it.",
        "[Unit]",
        f"Description=Toll Harness market worker for {_unit_literal(description_name)}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={_unit_literal(config_path.parent)}",
        "ExecStart=" + " ".join(_unit_quoted(part, command=True) for part in command),
        f"Restart={RESTART_POLICY}",
        f"RestartSec={RESTART_SECONDS}",
    ]
    lines += [f"Environment={_unit_quoted(f'{key}={value}')}" for key, value in environment.items()]
    lines += [
        f"StandardOutput=append:{_unit_literal(log_path)}",
        f"StandardError=append:{_unit_literal(log_path)}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def render_launchd_plist(
    *,
    label: str,
    config_path: Path,
    log_path: Path,
    command: list[str],
    environment: Mapping[str, str],
) -> bytes:
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": list(command),
            "WorkingDirectory": str(config_path.parent),
            "RunAtLoad": True,
            # Restart only after a failed exit, like systemd's Restart=on-failure.
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": RESTART_SECONDS,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
            "EnvironmentVariables": dict(environment),
        }
    )


# -- which agent a unit watches ---------------------------------------------------


def _config_after_watch(arguments: list[str], working_directory: str | None) -> Path | None:
    for index in range(len(arguments) - 1):
        if arguments[index] == "market" and arguments[index + 1] == "watch":
            rest = arguments[index + 2 :]
            skip = False
            for argument in rest:
                if skip:
                    skip = False
                    continue
                if argument in _VALUE_FLAGS:
                    skip = True
                    continue
                if argument.startswith("-"):
                    continue
                path = Path(argument).expanduser()
                if not path.is_absolute() and working_directory:
                    path = Path(working_directory) / path
                return path.resolve()
            return None
    return None


def _unit_field(text: str, key: str) -> str | None:
    prefix = f"{key}="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def watched_config(unit_path: Path, kind: str) -> Path | None:
    """The agent.yaml a unit or plist runs `market watch` on, or None."""
    try:
        if kind == LAUNCHD:
            data = plistlib.loads(unit_path.read_bytes())
            arguments = [str(part) for part in data.get("ProgramArguments") or []]
            return _config_after_watch(arguments, data.get("WorkingDirectory"))
        text = unit_path.read_text(encoding="utf-8")
        command = _unit_field(text, "ExecStart")
        if not command:
            return None
        arguments = [
            part.replace("$$", "$").replace("%%", "%")
            for part in shlex.split(command.lstrip("-@:+!"))
        ]
        directory = _unit_field(text, "WorkingDirectory")
        return _config_after_watch(arguments, directory.replace("%%", "%") if directory else None)
    except Exception:  # noqa: BLE001 - a unit we cannot read watches nothing we know of
        return None


def _runs_another_agent(unit: Path, kind: str, config_path: Path) -> bool:
    """A unit of this name exists and watches a different agent.yaml: not ours to touch."""
    if not unit.exists():
        return False
    watched = watched_config(unit, kind)
    return watched is not None and watched != config_path


def installed_units(
    config_path: Path, kind: str, unit_directory: str | Path | None = None
) -> list[dict[str, Any]]:
    """This harness's units (toll-harness-* / com.toll-harness.*) that watch this agent."""
    directory = unit_directory_for(kind, unit_directory)
    pattern = f"{LAUNCHD_PREFIX}*.plist" if kind == LAUNCHD else f"{SYSTEMD_PREFIX}*.service"
    found = []
    for path in sorted(directory.glob(pattern)) if directory.is_dir() else []:
        if watched_config(path, kind) == config_path:
            service = path.name[: -len(".plist")] if kind == LAUNCHD else path.name
            found.append({"service": service, "unit": path})
    return found


# -- running the service manager ----------------------------------------------------


def _run(
    runner: Runner, command: list[str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise ServiceError(
            f"`{command[0]}` was not found, so this machine has no "
            f"{'launchd' if command[0] == 'launchctl' else 'systemd user manager'} "
            "to keep the watch running"
        ) from error
    if check and result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        detail = output.splitlines()
        no_bus = any(mark in output for mark in _NO_BUS)
        raise ServiceError(
            f"`{' '.join(command)}` failed: "
            + (detail[-1] if detail else f"exit {result.returncode}")
            + (
                ". systemctl --user needs your own login session (not sudo); over ssh, run "
                "`loginctl enable-linger $USER` and log in again"
                if no_bus
                else ""
            )
        )
    return result


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.environ.get("USER") or os.environ.get("LOGNAME") or "$USER"


LINGER_DIRECTORY = Path("/var/lib/systemd/linger")


def linger_enabled(runner: Runner | None = subprocess.run, user: str | None = None) -> bool | None:
    """Whether this user's services run without a login (and so start at boot).

    Asks loginctl, else reads systemd's linger directory; None when neither
    can tell. With no runner only the directory is read (a dry run runs nothing).
    """
    user = user or _user()
    if runner is not None:
        try:
            result = runner(
                ["loginctl", "show-user", user, "--property=Linger", "--value"],
                check=False,
                capture_output=True,
                text=True,
            )
            value = (result.stdout or "").strip().lower()
            if result.returncode == 0 and value in {"yes", "no"}:
                return value == "yes"
        except OSError:
            pass
    if LINGER_DIRECTORY.is_dir():
        return (LINGER_DIRECTORY / user).exists()
    return None


def check_commands(kind: str, service: str, unit: Path, log_path: Path) -> list[str]:
    if kind == LAUNCHD:
        return [
            f"launchctl print gui/{os.getuid()}/{service}",
            f"tail -f {shlex.quote(str(log_path))}",
        ]
    return [
        f"systemctl --user status {service}",
        f"journalctl --user -u {service} -f",
        f"tail -f {shlex.quote(str(log_path))}",
    ]


def windows_hint(config_path: Path, slug: str, python: str | Path | None = None) -> str:
    command = watch_command(config_path, python)
    quoted = " ".join(f'"{part}"' if " " in part else part for part in command)
    task = quoted.replace('"', '\\"')
    return "\n".join(
        [
            "install-service writes systemd (Linux) and launchd (macOS) services; on Windows "
            "it installs nothing. To keep the watch running, use one of these:",
            f'  schtasks /Create /SC ONLOGON /RL LIMITED /TN "toll-harness-{slug}" /TR "{task}"',
            f"  nssm install toll-harness-{slug} {quoted}",
            "  (NSSM, https://nssm.cc, restarts it when it dies; Task Scheduler starts it at "
            "logon, set 'restart on failure' in the task's Settings tab.)",
        ]
    )


def _require_supported(kind: str, config_path: Path, slug: str, python: Any = None) -> None:
    if kind == "windows":
        raise ServiceUnsupported(windows_hint(config_path, slug, python))
    if kind not in {SYSTEMD, LAUNCHD}:
        raise ServiceUnsupported(
            f"install-service knows systemd (Linux) and launchd (macOS), not {sys.platform}. "
            "Run `" + " ".join(watch_command(config_path, python)) + "` under this "
            "system's own supervisor, restarting it when it exits non-zero."
        )


# -- the three verbs ------------------------------------------------------------------


def install_service(
    config_path: str | Path,
    *,
    name: str | None = None,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    platform: str | None = None,
    unit_directory: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    python: str | Path | None = None,
) -> dict[str, Any]:
    """Write, (re)start and report this agent's background market watch.

    Idempotent: the same unit is rewritten and restarted. A unit of this
    harness that watches the same agent under another name is removed, so one
    agent never has two watches. A unit of this name that watches a different
    agent is left alone and refused (pass another --name).
    """
    path = resolve_config_path(config_path)
    config = load_config(path)
    kind = service_kind(platform)
    slug = _slug_for(config, name)
    _require_supported(kind, path, slug, python)

    notes: list[str] = []
    toll_bench = config.get("toll_bench") or {}
    if not toll_bench.get("connected") or toll_bench.get("status") != READY:
        message = (
            f"{path} is not a connected, READY Toll Bench agent yet "
            f"(toll_bench.status: {toll_bench.get('status') or 'none'}), so a background watch "
            f"would have nothing to do: finish `toll-harness init {path.parent} --resume` first"
        )
        if not dry_run:
            raise ServiceError(message)
        notes.append(message)

    service = service_name(config, name=name, kind=kind)
    unit = unit_path_for(kind, service, unit_directory)
    log_path = data_directory(path, config) / "market.log"
    command = watch_command(path, python)
    environment, withheld = service_environment(environ)
    if withheld:
        notes.append(
            "Not copied into the service (a unit file is no place for a secret): "
            + ", ".join(withheld)
            + ". The bench token already lives in the agent's secret store; give a model "
            "key the same way with model.api_key_secret."
        )

    if _runs_another_agent(unit, kind, path):
        raise ServiceError(
            f"{service} already runs another agent ({watched_config(unit, kind)}); pass "
            "--name to give this one its own service"
        )
    replaced = [
        entry
        for entry in installed_units(path, kind, unit_directory)
        if entry["service"] != service
    ]

    agent_name = str((config.get("agent") or {}).get("name") or slug)
    if kind == LAUNCHD:
        content = render_launchd_plist(
            label=service, config_path=path, log_path=log_path,
            command=command, environment=environment,
        )
        domain = f"gui/{os.getuid()}"
        steps = [["launchctl", "bootout", domain, str(entry["unit"])] for entry in replaced]
        steps += [
            ["launchctl", "bootout", domain, str(unit)],
            ["launchctl", "bootstrap", domain, str(unit)],
            ["launchctl", "enable", f"{domain}/{service}"],
            ["launchctl", "kickstart", f"{domain}/{service}"],
        ]
    else:
        content = render_systemd_unit(
            description_name=agent_name, config_path=path, log_path=log_path,
            command=command, environment=environment,
        ).encode("utf-8")
        steps = [
            ["systemctl", "--user", "disable", "--now", entry["service"]] for entry in replaced
        ]
        steps += [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", service],
            ["systemctl", "--user", "restart", service],
        ]

    report: dict[str, Any] = {
        "ok": True,
        "action": "dry-run" if dry_run else "install",
        "service_manager": kind,
        "service": service,
        "unit": str(unit),
        "config": str(path),
        "log": str(log_path),
        "command": command,
        "restart_policy": RESTART_POLICY,
        "restart_seconds": RESTART_SECONDS,
        "environment": sorted(environment),
        "withheld": withheld,
        "replaced": [str(entry["unit"]) for entry in replaced],
        "commands": [" ".join(shlex.quote(part) for part in step) for step in steps],
        "check": check_commands(kind, service, unit, log_path),
        "notes": notes,
    }
    if dry_run:
        report["unit_text"] = content.decode("utf-8")
        report["active"] = False
        if kind == SYSTEMD:
            report["linger"] = linger_enabled(None)
        return report

    tool = "launchctl" if kind == LAUNCHD else "systemctl"
    if runner is subprocess.run and shutil.which(tool) is None:
        raise ServiceError(
            f"`{tool}` was not found, so this machine has no "
            f"{'launchd' if kind == LAUNCHD else 'systemd user manager'} to keep the watch "
            "running; nothing was written. Run `"
            + " ".join(command)
            + "` under this system's own supervisor instead"
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(unit, content)
    if kind == LAUNCHD:
        for entry in replaced:
            _run(runner, ["launchctl", "bootout", domain, str(entry["unit"])])
            entry["unit"].unlink(missing_ok=True)
        _run(runner, ["launchctl", "bootout", domain, str(unit)])
        booted = _run(runner, ["launchctl", "bootstrap", domain, str(unit)])
        if booted.returncode != 0:
            # macOS before 10.10 has no bootstrap; load -w is the old spelling.
            _run(runner, ["launchctl", "load", "-w", str(unit)], check=True)
            report["commands"].append(f"launchctl load -w {shlex.quote(str(unit))}")
        _run(runner, ["launchctl", "enable", f"{domain}/{service}"])
        _run(runner, ["launchctl", "kickstart", f"{domain}/{service}"])
        printed = _run(runner, ["launchctl", "print", f"{domain}/{service}"])
        report["active"] = printed.returncode == 0 and "state = running" in (printed.stdout or "")
    else:
        for entry in replaced:
            _run(runner, ["systemctl", "--user", "disable", "--now", entry["service"]])
            entry["unit"].unlink(missing_ok=True)
        _run(runner, ["systemctl", "--user", "daemon-reload"], check=True)
        _run(runner, ["systemctl", "--user", "enable", service], check=True)
        _run(runner, ["systemctl", "--user", "restart", service], check=True)
        active = _run(runner, ["systemctl", "--user", "is-active", service])
        report["active"] = active.returncode == 0 and (active.stdout or "").strip() == "active"
        report["linger"] = linger_enabled(runner)
    return report


def uninstall_service(
    config_path: str | Path,
    *,
    name: str | None = None,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    platform: str | None = None,
    unit_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Stop, disable and remove this agent's service (idempotent)."""
    path = resolve_config_path(config_path)
    config = load_config(path)
    kind = service_kind(platform)
    slug = _slug_for(config, name)
    _require_supported(kind, path, slug)

    service = service_name(config, name=name, kind=kind)
    named = unit_path_for(kind, service, unit_directory)
    # A unit of this name that runs a different agent is left alone.
    targets = {} if _runs_another_agent(named, kind, path) else {service: named}
    for entry in installed_units(path, kind, unit_directory):
        targets.setdefault(entry["service"], entry["unit"])
    removed = [str(unit) for unit in targets.values() if unit.exists()]
    if kind == LAUNCHD:
        domain = f"gui/{os.getuid()}"
        steps = [["launchctl", "bootout", domain, str(unit)] for unit in targets.values()]
    else:
        steps = [["systemctl", "--user", "disable", "--now", unit] for unit in targets]
    reload = ["systemctl", "--user", "daemon-reload"] if kind == SYSTEMD else None
    report: dict[str, Any] = {
        "ok": True,
        "action": "dry-run uninstall" if dry_run else "uninstall",
        "service_manager": kind,
        "service": service,
        "config": str(path),
        "removed": removed,
        "commands": [
            " ".join(shlex.quote(part) for part in step) for step in steps + [reload] if step
        ],
        "active": False,
    }
    if dry_run:
        return report
    for step in steps:
        _run(runner, step)
    for unit in targets.values():
        unit.unlink(missing_ok=True)
    if reload:
        _run(runner, reload, check=True)
    return report


def _manager_state(runner: Runner, kind: str, service: str) -> tuple[bool, bool]:
    """(running, starts on its own), as the service manager tells it; never raises."""
    try:
        if kind == LAUNCHD:
            printed = runner(
                ["launchctl", "print", f"gui/{os.getuid()}/{service}"],
                check=False, capture_output=True, text=True,
            )
            loaded = printed.returncode == 0
            return loaded and "state = running" in (printed.stdout or ""), loaded
        state = runner(
            ["systemctl", "--user", "is-active", service],
            check=False, capture_output=True, text=True,
        )
        boot = runner(
            ["systemctl", "--user", "is-enabled", service],
            check=False, capture_output=True, text=True,
        )
    except OSError:
        return False, False
    return (
        state.returncode == 0 and (state.stdout or "").strip() == "active",
        boot.returncode == 0 and (boot.stdout or "").strip() == "enabled",
    )


def service_status(
    config_path: str | Path,
    *,
    name: str | None = None,
    runner: Runner = subprocess.run,
    platform: str | None = None,
    unit_directory: str | Path | None = None,
) -> dict[str, Any]:
    """running / installed (not running) / not installed; never runs anything that writes."""
    path = resolve_config_path(config_path)
    config = load_config(path)
    kind = service_kind(platform)
    log_path = data_directory(path, config) / "market.log"
    if kind not in {SYSTEMD, LAUNCHD}:
        return {"state": "unsupported", "service_manager": kind, "config": str(path)}
    found = installed_units(path, kind, unit_directory)
    if name or not found:
        service = service_name(config, name=name, kind=kind)
        unit = unit_path_for(kind, service, unit_directory)
    else:
        service, unit = found[0]["service"], found[0]["unit"]
    # A unit of this name that runs a different agent is not this agent's service.
    other = _runs_another_agent(unit, kind, path)
    active, enabled = (False, False) if other else _manager_state(runner, kind, service)
    installed = not other and (unit.exists() or enabled)
    report = {
        "state": "running" if active else ("installed" if installed else "not installed"),
        "service_manager": kind,
        "service": service,
        "unit": str(unit),
        "installed": installed,
        "active": active,
        "enabled": enabled,
        "config": str(path),
        "log": str(log_path),
        "check": check_commands(kind, service, unit, log_path),
    }
    if kind == SYSTEMD:
        report["linger"] = linger_enabled(runner)
    return report
