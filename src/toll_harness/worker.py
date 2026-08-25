from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness.onboarding import READY, data_directory, load_config


def _service_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError("Agent name must contain a letter or number")
    return slug


def market_worker_service_name(config: dict[str, Any]) -> str:
    return f"toll-harness-{_service_slug(str(config['agent']['name']))}.service"


def _systemd_quote(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _systemd_path(value: str | Path) -> str:
    encoded = []
    safe = b"/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    for byte in os.fsencode(value):
        if byte in safe:
            encoded.append(chr(byte))
        else:
            encoded.append(f"\\x{byte:02x}")
    return "".join(encoded)


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def install_market_worker(
    config_path: str | Path,
    *,
    unit_directory: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    path = Path(config_path).resolve()
    config = load_config(path)
    toll_bench = config.get("toll_bench") or {}
    if not toll_bench.get("connected") or toll_bench.get("status") != READY:
        raise ValueError("Market workers require a connected READY agent")

    service = market_worker_service_name(config)
    units = (
        Path(unit_directory).expanduser().resolve()
        if unit_directory is not None
        else (Path.home() / ".config/systemd/user").resolve()
    )
    unit_path = units / service
    log_path = data_directory(path, config) / "market.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    description_name = str(config["agent"]["name"]).replace("\n", " ").replace("\r", " ")
    command = " ".join(
        _systemd_quote(value)
        for value in (
            Path(sys.executable).absolute(),
            "-m",
            "toll_harness.cli",
            "market",
            "watch",
            path,
        )
    )
    unit = f"""[Unit]
Description=Toll Harness market worker for {description_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={_systemd_path(path.parent)}
ExecStart={command}
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:{_systemd_path(log_path)}
StandardError=append:{_systemd_path(log_path)}

[Install]
WantedBy=default.target
"""
    _write_atomic(unit_path, unit)
    runner(
        ["systemctl", "--user", "daemon-reload"],
        check=True,
        capture_output=True,
        text=True,
    )
    runner(
        ["systemctl", "--user", "enable", "--now", service],
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
        "log": str(log_path),
        "active": active.returncode == 0 and active.stdout.strip() == "active",
        "restart_policy": "always",
    }


def market_worker_status(
    config_path: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    config = load_config(Path(config_path).resolve())
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
