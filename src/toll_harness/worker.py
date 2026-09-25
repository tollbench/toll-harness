from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness.onboarding import data_directory, load_config
from toll_harness.service import (
    LAUNCHD,
    LAUNCHD_PREFIX,
    SYSTEMD_PREFIX,
    install_service,
    service_kind,
    service_slug,
)


def market_worker_service_name(config: dict[str, Any]) -> str:
    return f"{SYSTEMD_PREFIX}{service_slug(str(config['agent']['name']))}.service"


def market_worker_launchd_label(config: dict[str, Any]) -> str:
    return LAUNCHD_PREFIX + service_slug(str(config["agent"]["name"]))


def install_market_worker(
    config_path: str | Path,
    *,
    unit_directory: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    platform: str | None = None,
) -> dict[str, Any]:
    """The worker `init` installs: the same unit `toll-harness install-service` writes.

    A systemd user unit on Linux, a launchd agent on macOS (unit_directory
    doubles as the LaunchAgents directory override). Idempotent: the unit is
    rewritten and restarted.
    """
    result = install_service(
        config_path, unit_directory=unit_directory, runner=runner, platform=platform
    )
    return {
        "service": result["service"],
        "unit": result["unit"],
        "log": result["log"],
        "active": result["active"],
        "restart_policy": result["restart_policy"],
        "service_manager": result["service_manager"],
    }


def market_worker_status(
    config_path: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    platform: str | None = None,
) -> dict[str, Any]:
    config = load_config(Path(config_path).resolve())
    if service_kind(platform) == LAUNCHD:
        label = market_worker_launchd_label(config)
        result = runner(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            check=False, capture_output=True, text=True,
        )
        loaded = result.returncode == 0
        return {
            "service": label,
            "active": loaded and "state = running" in (result.stdout or ""),
            "enabled": loaded,
            "service_manager": "launchd",
        }
    service = market_worker_service_name(config)
    active = runner(
        ["systemctl", "--user", "is-active", service],
        check=False,
        capture_output=True,
        text=True,
    )
    enabled = runner(
        ["systemctl", "--user", "is-enabled", service],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "service": service,
        "active": active.returncode == 0 and active.stdout.strip() == "active",
        "enabled": enabled.returncode == 0 and enabled.stdout.strip() == "enabled",
    }


def market_worker_log_path(config_path: str | Path) -> Path:
    """Where the market worker for this config writes its stdout and stderr."""
    path = Path(config_path).resolve()
    return data_directory(path, load_config(path)) / "market.log"


# The standing direction: a short text file in the agent's data directory
# (the directory that holds harness.sqlite3). An operator tool writes it; the
# open-want scan reads it fresh every cycle. Absent or blank means no change.
FOCUS_FILE = "focus.md"
FOCUS_LIMIT = 4000


def focus_path(config_path: str | Path) -> Path:
    """Where this config's standing direction lives: <data_directory>/focus.md."""
    path = Path(config_path).resolve()
    return data_directory(path, load_config(path)) / FOCUS_FILE


def read_focus(data_dir: str | Path) -> str:
    """The standing direction in this data directory, stripped and capped, or ""."""
    try:
        text = (Path(data_dir) / FOCUS_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    return text.strip()[:FOCUS_LIMIT]


def stop_market_worker(
    config_path: str | Path,
    *,
    unit_directory: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    platform: str | None = None,
) -> dict[str, Any]:
    """Stop, disable and remove this agent's market worker (idempotent)."""
    path = Path(config_path).resolve()
    config = load_config(path)
    if service_kind(platform) == LAUNCHD:
        label = market_worker_launchd_label(config)
        agents_dir = (
            Path(unit_directory).expanduser().resolve()
            if unit_directory is not None
            else (Path.home() / "Library/LaunchAgents").resolve()
        )
        plist_path = agents_dir / (label + ".plist")
        runner(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)],
            check=False, capture_output=True, text=True,
        )
        removed = plist_path.exists()
        if removed:
            plist_path.unlink()
        return {
            "service": label,
            "unit": str(plist_path),
            "active": False,
            "enabled": False,
            "removed": removed,
            "service_manager": "launchd",
        }
    service = market_worker_service_name(config)
    units = (
        Path(unit_directory).expanduser().resolve()
        if unit_directory is not None
        else (Path.home() / ".config/systemd/user").resolve()
    )
    unit_path = units / service
    disabled = runner(
        ["systemctl", "--user", "disable", "--now", service],
        check=False,
        capture_output=True,
        text=True,
    )
    if disabled.returncode != 0 and unit_path.exists():
        detail = (disabled.stderr or disabled.stdout or "").strip().splitlines()
        raise ValueError(
            f"systemctl could not disable {service}: "
            + (detail[-1] if detail else f"exit {disabled.returncode}")
        )
    removed = unit_path.exists()
    if removed:
        unit_path.unlink()
    runner(
        ["systemctl", "--user", "daemon-reload"],
        check=True,
        capture_output=True,
        text=True,
    )
    active = runner(
        ["systemctl", "--user", "is-active", service],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "service": service,
        "unit": str(unit_path),
        "active": active.returncode == 0 and (active.stdout or "").strip() == "active",
        "enabled": False,
        "removed": removed,
    }
