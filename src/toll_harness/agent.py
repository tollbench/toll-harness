"""agent.yaml and the agent's own folder: the token, the cursor, the log, the outbox."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import yaml

from toll_harness.secrets import FileSecretStore

CONFIG_VERSION = 2
DEFAULT_BENCH_URL = "https://bookofhouses.com"
BASE_URL_ENV = "TOLL_HARNESS_BASE_URL"  # which bench init talks to (kept from 0.76.1)
TOKEN_ENV = "TOLL_HARNESS_AGENT_TOKEN"  # init --registered reads the agent's token here
CONFIG_ENV = "TOLL_AGENT_CONFIG"  # set for the agent's command, so `answer` finds agent.yaml
TOKEN_SECRET = "book_of_houses_agent_token"  # same name as 0.76, so a moved agent keeps it
DEFAULT_TIME_LIMIT_MINUTES = 15
# A STEP GETS LONGER (staging, Nov 16: Sam's research steps for Steven ran 10 to 11 minutes
# against the 15-minute limit; the foreman, on Steven's "whey do we have a time limeit?", asked
# for 30 minutes on steps and quick jobs as they are). The limit is only there to stop a
# program that hangs: the harness cannot tell hung from still working. agent.yaml may set any
# kind's own limit under time_limit_minutes_by_kind; a kind it does not name gets these, and
# any other kind time_limit_minutes.
DEFAULT_KIND_LIMIT_MINUTES = {"step": 30}


class ConfigError(ValueError):
    """agent.yaml is missing or wrong; the message says what to do."""


def bench_url_from_env() -> str:
    return (os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/") or DEFAULT_BENCH_URL


def checked_command(command: Any) -> list[str]:
    """The agent's command as an argv list. It is never run through a shell."""
    if not isinstance(command, list) or not command:
        raise ConfigError("agent.command must be a list: the program and its arguments")
    if not all(isinstance(part, str) and part and "\0" not in part for part in command):
        raise ConfigError("every part of agent.command must be a non-empty string")
    return list(command)


def write_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class Agent:
    """One agent: its agent.yaml and the folder beside it."""

    def __init__(self, path: Path, config: dict[str, Any]):
        self.path = path
        self.config = config

    # -- what agent.yaml says ------------------------------------------------------
    @property
    def section(self) -> dict[str, Any]:
        return self.config.setdefault("agent", {})

    @property
    def name(self) -> str:
        return str(self.section.get("name") or self.path.parent.name or "agent")

    @property
    def wake(self) -> dict[str, Any] | None:
        """The agent known by name that the harness wakes (agent.wake), else None."""
        value = self.section.get("wake")
        return value if isinstance(value, dict) else None

    @property
    def command(self) -> list[str]:
        """What the harness runs for each item: the bridge for an agent known by name
        (agent.wake), else the owner's own command (agent.command)."""
        if self.section.get("wake") is not None:
            from toll_harness.known import wake_argv

            return wake_argv(self.section["wake"])
        return checked_command(self.section.get("command"))

    @property
    def time_limit_seconds(self) -> float:
        minutes = self.section.get("time_limit_minutes", DEFAULT_TIME_LIMIT_MINUTES)
        return max(1.0, float(minutes) * 60)

    def time_limit_for(self, kind: str) -> float:
        """Seconds one wake for an item of this kind may run: agent.yaml's
        time_limit_minutes_by_kind for the kind when it names it; else
        DEFAULT_KIND_LIMIT_MINUTES for the kind when that is longer than time_limit_minutes;
        else time_limit_minutes."""
        own = self.section.get("time_limit_minutes_by_kind")
        if isinstance(own, dict) and kind in own:
            try:
                return max(1.0, float(own[kind]) * 60)
            except (TypeError, ValueError):
                pass
        default = DEFAULT_KIND_LIMIT_MINUTES.get(kind)
        if default is not None:
            return max(self.time_limit_seconds, float(default) * 60)
        return self.time_limit_seconds

    @property
    def bench_url(self) -> str:
        return str((self.config.get("bench") or {}).get("url") or DEFAULT_BENCH_URL).rstrip("/")

    @property
    def auto_update(self) -> bool:
        return bool((self.config.get("update") or {}).get("auto", True))

    @property
    def update_source(self) -> str | None:
        """A local wheel or folder of wheels to update from instead of the package index."""
        value = (self.config.get("update") or {}).get("source")
        return str(value) if value else None

    # -- the folder beside it ------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return (self.path.parent / str(self.config.get("storage") or ".toll-harness")).resolve()

    @property
    def secrets(self) -> FileSecretStore:
        return FileSecretStore(self.data_dir / "secrets")

    def token(self) -> str | None:
        return self.secrets.get(TOKEN_SECRET)

    def bench(self, timeout: float = 30):
        from toll_harness.bench import Bench

        return Bench(self.bench_url, token=self.token(), timeout=timeout)

    def read_json(self, name: str, default: Any) -> Any:
        try:
            return json.loads((self.data_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def write_json(self, name: str, value: Any) -> None:
        write_atomic(self.data_dir / name, json.dumps(value, indent=1, sort_keys=True) + "\n")

    def save(self) -> None:
        write_atomic(self.path, yaml.safe_dump(self.config, sort_keys=False))


def config_path(value: str | Path | None = None) -> Path:
    """agent.yaml from the argument, else TOLL_AGENT_CONFIG, else ./agent.yaml."""
    raw = value or os.environ.get(CONFIG_ENV) or "agent.yaml"
    path = Path(raw).expanduser()
    if path.is_dir():
        path = path / "agent.yaml"
    return path.resolve()


def read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConfigError(f"{path} is not a YAML object")
    return value


def is_old_config(config: dict[str, Any]) -> bool:
    """An agent.yaml written by toll-harness 0.x (it has a toll_bench block)."""
    return config.get("version") != CONFIG_VERSION and "toll_bench" in config


def load(value: str | Path | None = None) -> Agent:
    path = config_path(value)
    if not path.is_file():
        raise ConfigError(f"no agent.yaml at {path}; run `toll-harness init` first")
    config = read_yaml(path)
    if is_old_config(config):
        raise ConfigError(
            f"{path} was written by toll-harness 0.x; `toll-harness watch` moves it by itself "
            f"(or run `toll-harness init {path.parent}`): the agent keeps its identity and token"
        )
    agent = Agent(path, config)
    agent.command  # noqa: B018 - refuse a bad command at load, not at the first item
    return agent
