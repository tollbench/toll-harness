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

Files go both ways by their path: a file the item lists comes in, fetched by the watch and
moved here into the scratch folder (incoming.py), and a file the agent makes goes back
(attach_files).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from toll_harness import incoming
from toll_harness.known import PROMPT, by_name

FAILED_EXIT = 3


class NoAnswer(Exception):
    """The agent's reply held no answer; the message is one plain line."""


def prompt_for(item: dict[str, Any], brought: dict[str, Any] | None = None) -> str:
    """The item as a prompt. `brought` is what the watch fetched for it (incoming.py): with
    files, a few lines name each one by its path; without, the prompt is as it always was."""
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
    files_note = incoming.note_for(brought)
    if files_note:
        parts += ["", "# Files you were given", files_note]
    parts += ["", "# How to answer",
              "Reply with EXACTLY one JSON object and nothing else: no code fence, no "
              "Sources line, no words before or after it. Use your own tools as you need to "
              "first."]
    if answer.get("needed") is False:
        parts.append('No answer is needed for this item: read it, then reply {"ok": true}.')
    if answer.get("shape"):
        parts.append("Its shape: " + json.dumps(answer["shape"], ensure_ascii=False))
        if isinstance(answer["shape"], dict) and "files" in answer["shape"]:
            parts.append(FILES_NOTE)
    if answer.get("example"):
        parts.append("For example: " + json.dumps(answer["example"], ensure_ascii=False))
    parts.append('If you cannot do it, reply {"failed": "<short reason>"}.')
    return "\n".join(parts) + "\n"


_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?[ \t]*```", re.S)
NOT_ONE = "the agent's reply was not one JSON object (or exactly one ```json block)"


def _holds_json(text: str) -> bool:
    """Whether any complete JSON object or list starts somewhere in this text."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                decoder.raw_decode(text, index)
                return True
            except ValueError:
                continue
    return False


def answer_from(reply: str) -> dict[str, Any]:
    """The answer in the agent's reply: the whole reply as one JSON object, or EXACTLY one
    fenced code block holding one (`claude -p` fences its JSON and may add a "Sources:" line
    after it). JSON is never pulled out of prose: "I would send this if you say yes" with an
    example inside is not an answer (another worker's walk on staging, Nov 11). A 0.x envelope
    is unwrapped."""
    text = (reply or "").strip()
    if not text:
        raise NoAnswer("the agent replied with nothing")
    try:
        value = json.loads(text)
    except ValueError:
        blocks = _FENCE.findall(text)
        if len(blocks) != 1:
            raise NoAnswer(NOT_ONE) from None
        if _holds_json(_FENCE.sub(" ", text)):
            raise NoAnswer("the agent's reply held JSON outside its one code block") from None
        try:
            value = json.loads(blocks[0])
        except ValueError:
            raise NoAnswer("the agent's code block was not JSON") from None
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


# A FILE GOES BACK BY ITS PATH (staging, Nov 13, the harness e2e walk: Sam drew three logos
# and could not hand them in, "I wasn't allowed to save files here"; Bree's party flyer went
# back only as base64 she wrote out by hand). The agent makes the file in its scratch folder
# and names it in "files" with a "path"; the bridge reads the bytes before the folder is
# removed and sends them as content_base64. Only files inside the scratch folder are read.
FILES_NOTE = ('To hand back a file, make it in the folder you were started in and name it in '
              '"files" as {"filename": "flyer.pdf", "path": "flyer.pdf"}: the harness reads its '
              'bytes and sends them. Never write base64 yourself.')
MAX_FILE_BYTES = 200 * 1024 * 1024  # the bench's own limit for one hand-back file


def attach_files(answer: dict[str, Any], work: Path) -> dict[str, Any]:
    """The answer with every "files" entry that names a "path" in the scratch folder turned
    into {"filename", "content_base64"}. Entries that already carry bytes or a receipt_id are
    left as they are."""
    files = answer.get("files")
    if not isinstance(files, list):
        return answer
    root = Path(work).resolve()
    out: list[Any] = []
    for entry in files:
        if (isinstance(entry, dict) and entry.get("path") and not entry.get("content_base64")
                and not entry.get("receipt_id")):
            named = str(entry["path"])
            target = (root / named).resolve()
            if root not in target.parents:
                raise NoAnswer("a file named in files is outside the agent's working folder")
            if not target.is_file():
                raise NoAnswer(f"the agent named a file it did not make: {Path(named).name}")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise NoAnswer(f"the file {target.name} is larger than the bench takes")
            entry = {k: v for k, v in entry.items() if k != "path"}
            entry.setdefault("filename", target.name)
            entry["content_base64"] = base64.b64encode(target.read_bytes()).decode("ascii")
        out.append(entry)
    return dict(answer, files=out)


# Claude Code woken with no settings of its own (a fresh setup) gets the web and nothing else:
# it is woken with nobody at the keyboard to approve a shell command or a file write, so its
# shell, file and MCP tools stay off, and no session is saved between items. A 0.x agent that
# brings its own settings (extra_args) keeps them. (Another worker's walk, Nov 11: the README's
# wake command "left Claude's shell and file tools on".)
CLAUDE_DEFAULT = ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch",
                  "--strict-mcp-config", "--no-session-persistence"]


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
        # an agent that brings its own tools may write files, in its scratch folder only
        edits = (["--permission-mode", "acceptEdits"]
                 if extra and "--permission-mode" not in extra else [])
        return [binary, "-p", "--output-format", "text", *with_model, *edits,
                *(extra or CLAUDE_DEFAULT)]
    if known.name == "codex":
        where = ["--cd", scratch] if scratch else []
        own_sandbox = any(a in ("-s", "--sandbox") or str(a).startswith("--sandbox=")
                          or "sandbox_mode" in str(a) for a in extra)
        sandbox = [] if own_sandbox else ["--sandbox", "workspace-write"]
        return [binary, "exec", "--skip-git-repo-check", *where, *sandbox,
                "--output-last-message", str(last_message), *with_model, *extra, "-"]
    return [binary, *with_model, *extra]  # gemini: the prompt on stdin, the reply on stdout


def run(agent: str, prompt: str, *, model: str | None = None, program: str | None = None,
        config_dirs: list[str] | None = None, extra: list[str] | None = None,
        finish=None, bring: str | None = None):
    """Run the agent's program once with the prompt on stdin; its reply (or what
    `finish(reply, scratch_folder)` makes of it, while the folder is still there). With several
    Claude accounts, a failed run wakes the next account. `bring` is the folder of files the
    watch fetched for the item: they are moved into the scratch folder first."""
    extra = list(extra or [])
    accounts = list(config_dirs or []) or [None]
    with tempfile.TemporaryDirectory(prefix="toll-wake-") as scratch:
        last = str(Path(scratch) / ".last-message.txt")
        work = Path(scratch) / "work"
        work.mkdir()
        incoming.move_into(bring, work)
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
                reply = done.stdout
                if by_name(agent) is not None and by_name(agent).name == "codex":
                    try:
                        reply = Path(last).read_text(encoding="utf-8")
                    except OSError:
                        pass
                return finish(reply, work) if finish else reply
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
    files_dir = os.environ.pop(incoming.FILES_ENV, None)  # the agent's program never sees it
    try:
        item = json.loads(sys.stdin.read() or "{}")
        if not isinstance(item, dict):
            raise ValueError
    except ValueError:
        print("the item on stdin was not a JSON object", file=sys.stderr)
        return FAILED_EXIT
    brought = incoming.read_brought(files_dir)
    try:
        answer = run(arguments.agent, prompt_for(item, brought), model=arguments.model,
                     program=arguments.program, config_dirs=arguments.config_dir,
                     extra=extra, bring=files_dir if brought else None,
                     finish=lambda reply, work: attach_files(answer_from(reply), work))
    except NoAnswer as error:
        print(str(error), file=sys.stderr)
        return FAILED_EXIT
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
