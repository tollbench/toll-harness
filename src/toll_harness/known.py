"""The agents the harness knows by name, and how each one is woken. ONE table.

Steven, Nov 11: "we want to be able to wake the agent" and "why doesnt it run the command when
its installed." So nobody types a command line for an agent the harness knows:

- `init` looks for these programs on this computer. One found: it says which and goes on.
  Several: the owner picks one by name. A command line is asked only for "something else".
- The move from 0.x reads the old agent.yaml's `model.adapter` and wakes the same agent the
  same way, with no question: claude_code -> claude, codex -> codex, external -> the same
  command, given the item as a prompt as 0.x gave it.

An agent woken by name gets each item through `python -m toll_harness.wake` (the bridge: the
item as a prompt on stdin, the one JSON object in the reply is the answer).
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PROMPT = "prompt"  # a 0.x `external` command: prompt on stdin, reply on stdout


@dataclass(frozen=True)
class Known:
    name: str  # what agent.yaml and `--agent` say
    label: str  # what a person reads
    program: str  # the program looked for on PATH
    model: str  # the model's name sent at registration when nobody gives one
    old_adapter: str | None = None  # the 0.x `model.adapter` that meant this agent


KNOWN: tuple[Known, ...] = (
    Known("claude", "Claude Code", "claude", "Claude", "claude_code"),
    Known("codex", "Codex", "codex", "GPT", "codex"),
    Known("gemini", "Gemini CLI", "gemini", "Gemini"),
)
# 0.x adapters that reach a model with an API key: there is no program to wake, so the move
# cannot pick one by itself. `init` asks the owner which agent answers now.
API_ADAPTERS = ("anthropic", "openai", "bedrock")


def by_name(name: str) -> Known | None:
    wanted = str(name or "").strip().lower()
    return next((k for k in KNOWN if wanted in (k.name, k.program, k.label.lower())), None)


def names() -> str:
    return ", ".join(k.name for k in KNOWN)


def found(which: Callable[[str], str | None] = shutil.which) -> list[Known]:
    """The known agents whose program is on this computer, in table order."""
    return [k for k in KNOWN if which(k.program)]


def describe(wake: dict[str, Any]) -> str:
    known = by_name(wake.get("agent"))
    if known:
        return f"{known.label} ({known.program}" + (
            f", model {wake['model']})" if wake.get("model") else ")")
    if wake.get("agent") == PROMPT:
        return "the command it ran under toll-harness 0.x (" + " ".join(wake["command"]) + ")"
    return str(wake.get("agent"))


def checked(wake: Any) -> dict[str, Any]:
    from toll_harness.agent import ConfigError

    if not isinstance(wake, dict) or not wake.get("agent"):
        raise ConfigError("agent.wake must name an agent: " + names())
    name = str(wake["agent"])
    if name == PROMPT:
        command = wake.get("command")
        if not isinstance(command, list) or not command or not all(
                isinstance(part, str) and part and "\0" not in part for part in command):
            raise ConfigError("agent.wake.command must be the 0.x command as a list")
    elif by_name(name) is None:
        raise ConfigError(f"agent.wake.agent is {name!r}; the harness knows: {names()}")
    extra = wake.get("extra_args") or []
    if not isinstance(extra, list) or not all(isinstance(part, str) for part in extra):
        raise ConfigError("agent.wake.extra_args must be a list of strings")
    return wake


def config_dirs(wake: dict[str, Any]) -> list[str]:
    """Claude Code's signed-in accounts, in the order they are tried (0.x claude_config_dirs)."""
    many = wake.get("config_dirs")
    if isinstance(many, list):
        return [str(folder) for folder in many if folder]
    return [str(wake["config_dir"])] if wake.get("config_dir") else []


def wake_argv(wake: dict[str, Any], python: str | None = None) -> list[str]:
    """The command the harness runs for an agent woken by name (never through a shell)."""
    wake = checked(wake)
    argv = [python or sys.executable, "-m", "toll_harness.wake", "--agent", str(wake["agent"])]
    for key, flag in (("model", "--model"), ("program", "--program")):
        if wake.get(key):
            argv += [flag, str(wake[key])]
    for folder in config_dirs(wake):
        argv += ["--config-dir", folder]
    tail = list(wake.get("command") or []) + list(wake.get("extra_args") or [])
    return argv + (["--", *tail] if tail else [])


def wake_from_old(config: dict[str, Any]) -> dict[str, Any] | None:
    """How a 0.x agent.yaml's agent is woken under 1.0; None when it names no program."""
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    adapter = str(model.get("adapter") or "bedrock")  # 0.x: no adapter meant Bedrock
    extra = [str(part) for part in (model.get("extra_args") or [])]
    if adapter == "external":
        command = [str(part) for part in (model.get("command") or [])]
        if not command:
            return None
        return {"agent": PROMPT, "command": command, **({"extra_args": extra} if extra else {})}
    known = next((k for k in KNOWN if k.old_adapter == adapter), None)
    if known is None:
        return None
    wake: dict[str, Any] = {"agent": known.name}
    if model.get("model_id"):
        wake["model"] = str(model["model_id"])
    if model.get("binary"):
        wake["program"] = str(model["binary"])
    dirs = model.get("claude_config_dirs")
    if known.name == "claude" and isinstance(dirs, list) and dirs:
        wake["config_dirs"] = [str(folder) for folder in dirs]  # tried in order, as in 0.x
    if extra:
        wake["extra_args"] = extra
    return wake
