"""The bridge that wakes an agent known by name with one item.

    python -m toll_harness.wake --agent claude [--model M] [--program P] [--config-dir D] [-- extra]
    python -m toll_harness.wake --agent prompt -- /the/0.x/command and its args

The harness hands it the item as JSON on stdin, as it hands any agent's command. It writes the
item as a prompt (what to do, only the context the item carries, the exact shape of the
answer), runs the agent's program ONCE with that prompt on stdin (never on a command line,
never through a shell), and prints the one JSON object in the reply: that is the answer. When
there is none it says why in one plain line on stderr and exits 3; the harness reports that as
a failure and the bench counts it. The harness's time limit stops the program with it.

It has no model and no memory: the agent's own program does the thinking. The program runs in
a scratch folder made for this one item, never in the agent's folder (where its token is), as
0.x ran it in its own scratch folder. Claude Code with several signed-in accounts
(`--config-dir` given more than once, from 0.x's `claude_config_dirs`) tries them in order:
when one fails (a usage limit, a signed-out account), the next one is woken.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from toll_harness.known import PROMPT, by_name

FAILED_EXIT = 3


class NoAnswer(Exception):
    """The agent's reply held no answer; the message is one plain line."""


def prompt_for(item: dict[str, Any]) -> str:
    answer = item.get("answer") if isinstance(item.get("answer"), dict) else {}
    parts = [
        "You are an agent on Toll Bench. toll-harness woke you for ONE item. The item stands "
        "alone: everything you need is below, and you keep no memory between items.",
        "", "# What to do", str(item.get("do") or "(the item says nothing to do)"),
        "", "# The item",
        json.dumps({"id": item.get("id"), "kind": item.get("kind"), "about": item.get("about"),
                    "due_at": item.get("due_at")}, ensure_ascii=False),
    ]
    if item.get("context"):
        parts += ["", "# Context", json.dumps(item["context"], ensure_ascii=False, indent=1)]
    if item.get("refs"):
        parts += ["", "# Earlier results you can ask for",
                  'To read one, reply {"need": ["<name>"]} instead of an answer; the item comes '
                  "back with it.", json.dumps(item["refs"], ensure_ascii=False)]
    parts += ["", "# How to answer",
              "Reply with EXACTLY one JSON object and nothing else: no prose before or after, "
              "no markdown fence. Use your own tools as you need to first."]
    if answer.get("needed") is False:
        parts.append('No answer is needed for this item: read it, then reply {"ok": true}.')
    if answer.get("shape"):
        parts.append("Its shape: " + json.dumps(answer["shape"], ensure_ascii=False))
    if answer.get("example"):
        parts.append("For example: " + json.dumps(answer["example"], ensure_ascii=False))
    parts.append('If you cannot do it, reply {"failed": "<short reason>"}.')
    return "\n".join(parts) + "\n"


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _first_object(text: str) -> Any:
    """The first complete JSON object in prose (a stray brace before it is skipped)."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            value, _end = decoder.raw_decode(text, start)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
        start = text.find("{", start + 1)
    raise NoAnswer("the agent's reply held no JSON object")


def answer_from(reply: str) -> dict[str, Any]:
    """The one JSON object in the agent's reply (a 0.x envelope is unwrapped)."""
    text = _strip_fences(reply or "")
    if not text:
        raise NoAnswer("the agent replied with nothing")
    try:
        value = json.loads(text)
    except ValueError:
        value = _first_object(text)
    if not isinstance(value, dict):
        raise NoAnswer("the agent's reply was JSON but not an object")
    if set(value) and set(value) <= {"text", "tool_calls"}:  # the 0.x envelope
        for call in value.get("tool_calls") or []:
            if not isinstance(call, dict) or not isinstance(call.get("arguments"), dict):
                continue
            arguments = call["arguments"]
            if call.get("name") == "result.complete":
                inner = arguments.get("result", arguments)
                if isinstance(inner, dict):
                    return inner
            if call.get("name") == "result.fail":
                return {"failed": str(arguments.get("reason") or arguments.get("error")
                                      or "the agent gave up")[:300]}
        inner = value.get("text")
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str) and inner.strip():
            return answer_from(inner)
        raise NoAnswer("the agent's reply was an empty 0.x envelope")
    return value


def argv_for(agent: str, *, model: str | None, program: str | None,
             extra: list[str], last_message: str | None = None,
             scratch: str | None = None) -> list[str]:
    known = by_name(agent)
    if agent == PROMPT:
        if not extra:
            raise NoAnswer("no 0.x command was given")
        return list(extra)
    if known is None:
        raise NoAnswer(f"the harness does not know an agent named {agent!r}")
    binary = program or known.program
    with_model = ["--model", model] if model else []
    if known.name == "claude":
        return [binary, "-p", "--output-format", "text", *with_model, *extra]
    if known.name == "codex":
        where = ["--cd", scratch] if scratch else []
        return [binary, "exec", "--skip-git-repo-check", *where, "--output-last-message",
                str(last_message), *with_model, *extra, "-"]
    return [binary, *with_model, *extra]  # gemini: the prompt on stdin, the reply on stdout


def run(agent: str, prompt: str, *, model: str | None = None, program: str | None = None,
        config_dirs: list[str] | None = None, extra: list[str] | None = None) -> str:
    """Run the agent's program once with the prompt on stdin; its reply. With several Claude
    accounts, a failed run wakes the next account."""
    extra = list(extra or [])
    accounts = list(config_dirs or []) or [None]
    with tempfile.TemporaryDirectory(prefix="toll-wake-") as scratch:
        last = str(Path(scratch) / ".last-message.txt")
        work = Path(scratch) / "work"
        work.mkdir()
        argv = argv_for(agent, model=model, program=program, extra=extra, last_message=last,
                        scratch=str(work))
        name = Path(argv[0]).name
        problem = ""
        for number, account in enumerate(accounts, 1):
            env = dict(os.environ)
            if account:
                env["CLAUDE_CONFIG_DIR"] = account
            try:
                done = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                                      env=env, cwd=str(work), check=False)
            except FileNotFoundError:
                raise NoAnswer(f"`{argv[0]}` is not on this computer") from None
            except PermissionError:
                raise NoAnswer(f"`{argv[0]}` cannot be run (permission denied)") from None
            if done.returncode == 0:
                if by_name(agent) is not None and by_name(agent).name == "codex":
                    try:
                        return Path(last).read_text(encoding="utf-8")
                    except OSError:
                        return done.stdout
                return done.stdout
            problem = f"{name} exited with {done.returncode}"
            if len(accounts) > 1:
                problem += f" on account {number} of {len(accounts)}"
        raise NoAnswer(problem)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m toll_harness.wake")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--model")
    parser.add_argument("--program")
    parser.add_argument("--config-dir", action="append", default=[])
    parser.add_argument("extra", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    extra = arguments.extra[1:] if arguments.extra[:1] == ["--"] else arguments.extra
    try:
        item = json.loads(sys.stdin.read() or "{}")
        if not isinstance(item, dict):
            raise ValueError
    except ValueError:
        print("the item on stdin was not a JSON object", file=sys.stderr)
        return FAILED_EXIT
    try:
        reply = run(arguments.agent, prompt_for(item), model=arguments.model,
                    program=arguments.program, config_dirs=arguments.config_dir,
                    extra=extra)
        answer = answer_from(reply)
    except NoAnswer as error:
        print(str(error), file=sys.stderr)
        return FAILED_EXIT
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
