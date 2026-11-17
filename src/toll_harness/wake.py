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
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from toll_harness import incoming
from toll_harness.known import PROMPT, by_name

FAILED_EXIT = 3


class NoAnswer(Exception):
    """The agent's reply held no answer; the message is one plain line, one of REASONS."""


# THE REASON IS KEPT (staging, Nov 16: Bree's three picture wakes each ended "the command
# exited with 3" and the line saying why was lost: the bench and the person were told nothing).
# Every line the bridge prints before it exits 3 is one of these, and only these: the watch
# adds a line that matches one to the failure it logs and sends ("the command exited with 3:
# <reason>"). Any other words on stderr (a 0.x command's own) stay on this computer.
REASONS = tuple(re.compile(pattern) for pattern in (
    r"the item on stdin was not a JSON object",
    r"the agent replied with nothing",
    re.escape("the agent's reply was not one JSON object (or exactly one ```json block)"),
    r"the agent's reply held JSON outside its one code block",
    r"the agent's code block was not JSON",
    r"the agent's reply was JSON but not an object",
    r"the agent's reply was an empty 0\.x envelope",
    r"a file named in files is outside the agent's working folder",
    r"the agent named a file it did not make: [^\n]{1,120}",
    r"the file [^\n]{1,120} is larger than the bench takes",
    r"no 0\.x command was given",
    r"the harness does not know an agent named [^\n]{1,200}",
    r"`[^`\n]{1,250}` is not on this computer",
    r"`[^`\n]{1,250}` cannot be run \(permission denied\)",
    r"[^\s]{1,120} exited with -?\d+( on account \d+ of \d+)?",
))


def bridge_reason(line: str) -> str:
    """`line` when it is one of the bridge's own REASONS, else ''."""
    text = (line or "").strip()
    return text if any(pattern.fullmatch(text) for pattern in REASONS) else ""


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
# A LEAD-IN LINE IS NOT A BAD ANSWER (staging, Nov 16, 9:12 pm: Sam's press kit step handed in
# "The PDF renders cleanly on one page. Here is the hand-in." then a blank line and the whole
# answer, after Claude Code's own reminder to say what it was doing; the bench was told "not one
# JSON object" and Steven's step came one failure from being stopped). A few short words BEFORE
# one whole JSON object that starts on its own line and ends the reply are read as that object.
# JSON inside a sentence, words after the object, or JSON in the lead-in are still refused.
LEAD_IN_MOST_CHARS = 400
LEAD_IN_MOST_LINES = 3


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


def _after_lead_in(text: str) -> Any:
    """The one JSON object that ends `text` after a short lead-in of plain words, else raises
    NoAnswer(NOT_ONE). The object starts at the start of a line and runs to the very end."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{" or index == 0 or text[index - 1] != "\n":
            continue
        lead = text[:index].strip()
        if (not lead or len(lead) > LEAD_IN_MOST_CHARS
                or lead.count("\n") + 1 > LEAD_IN_MOST_LINES
                or "```" in lead or _holds_json(lead)):
            break  # past a short lead-in of plain words: nothing later is an answer either
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            continue
        if text[end:].strip() == "" and isinstance(value, dict):
            return value
    raise NoAnswer(NOT_ONE)


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
        if not blocks:
            value = _after_lead_in(text)
        elif len(blocks) != 1:
            raise NoAnswer(NOT_ONE) from None
        elif _holds_json(_FENCE.sub(" ", text)):
            raise NoAnswer("the agent's reply held JSON outside its one code block") from None
        else:
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
#
# A PICTURE THE AGENT'S OWN TOOL MADE GOES BACK TOO (staging, Nov 16: Steven asked for six
# pictures and Bree, a Codex agent with its shell off, woke three times; Codex's own picture
# tool made 14 real PNGs and saved them, as Codex always does, under
# $CODEX_HOME/generated_images/<session>/, outside the scratch folder. Codex tells the model
# "Generated images are saved to <folder> as <path> by default" and to copy one it needs
# elsewhere; with no shell it could not copy, so every wake ended in exit 3 and the person saw
# nothing. Steven: "Why wouldn't someone be able to put a picture in? It shouldn't just be
# lab agents."). So after the agent's program ends, the bridge looks in a short fixed list of
# the folders where a known agent's own tools save what they make (TOOL_FOLDERS), takes only
# regular files written since this wake began, and when the answer names one of them, by its
# file name or by the path the tool gave, copies it into the scratch folder (MADE_FOLDER) and
# sends that copy. Any other path outside the scratch folder is still refused: an agent
# talked into naming the operator's own files hands in nothing.
FILES_NOTE = ('To hand back a file, make it in the folder you were started in and name it in '
              '"files" as {"filename": "flyer.pdf", "path": "flyer.pdf"}: the harness reads its '
              'bytes and sends them. Never write base64 yourself. A picture your own image '
              'tool saved in its own folder while you worked on this item (Codex saves them '
              'in $CODEX_HOME/generated_images) needs no copying: name it by its file name, as '
              '{"filename": "cover.png", "path": "exec-1234.png"}, or by the full path the '
              'tool gave you, and the harness brings it into your folder and sends it.')
MAX_FILE_BYTES = 200 * 1024 * 1024  # the bench's own limit for one hand-back file
MADE_FOLDER = "from_your_tools"  # where a picture the agent's own tool made is copied in
MOST_MADE = 500  # files looked at in the tool folders for one wake, newest folders first


def tool_folders(agent: str, env: dict[str, str] | None = None) -> list[Path]:
    """TOOL_FOLDERS: where a known agent's own tools save what they make outside the folder it
    was started in. Only Codex has one: its built-in picture tool saves every picture under
    $CODEX_HOME/generated_images (~/.codex when CODEX_HOME is not set). Claude Code and the
    Gemini CLI save nothing of their own elsewhere (checked Nov 16 on claude 2.1.325 and the
    gemini-cli on staging). A 0.x command (`prompt`) may be Codex underneath, so it gets
    Codex's folder too."""
    env = os.environ if env is None else env
    known = by_name(agent)
    if agent == PROMPT or (known is not None and known.name == "codex"):
        home = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
        return [Path(home).expanduser() / "generated_images"]
    return []


def made_during(folders: list[Path], since: float) -> list[Path]:
    """Regular files (never a link) in each tool folder or one folder below it, written at or
    after `since`, the moment this wake began (to the whole second, for file systems that keep
    whole seconds). Real paths, so a name is matched exactly."""
    floor = math.floor(since)
    found: list[Path] = []
    for folder in folders:
        try:
            root = folder.resolve(strict=True)
            if not root.is_dir():
                continue
            subs = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
        except OSError:
            continue
        for place in (root, *sorted(subs, key=lambda p: p.stat().st_mtime, reverse=True)):
            try:
                entries = list(place.iterdir())
            except OSError:
                continue
            for path in entries:
                try:
                    facts = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(facts.st_mode) and facts.st_mtime >= floor:
                    found.append(path)
                if len(found) >= MOST_MADE:
                    return found
    return found


def _shown(name: str) -> str:
    return "".join(char for char in name if char.isprintable())[:120] or "?"


def _made_match(named: str, made: list[Path]) -> Path | None:
    """The one file of `made` the agent named: by its plain file name, or by the full path
    the tool gave (~ and $CODEX_HOME written out). None for anything else."""
    text = named.strip()
    if "/" not in text and "\\" not in text:
        hits = [path for path in made if path.name == text]
        return hits[0] if len(hits) == 1 else None
    wanted = Path(os.path.expandvars(os.path.expanduser(text)))
    if not wanted.is_absolute():
        return None
    try:
        wanted = wanted.resolve()
    except OSError:
        return None
    return next((path for path in made if path == wanted), None)


def _bring_made(source: Path, work: Path, used: set[str]) -> Path:
    """Copy a file the agent's own tool made into the scratch folder; never through a link."""
    folder = work / MADE_FOLDER
    folder.mkdir(exist_ok=True)
    if source.lstat().st_size > MAX_FILE_BYTES:
        raise NoAnswer(f"the file {_shown(source.name)} is larger than the bench takes")
    target = folder / incoming._unique(folder, incoming.safe_name(source.name, len(used) + 1),
                                       used)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as reading, open(target, "xb") as writing:
        shutil.copyfileobj(reading, writing)
    return target


def attach_files(answer: dict[str, Any], work: Path,
                 made: list[Path] | None = None) -> dict[str, Any]:
    """The answer with every "files" entry that names a "path" in the scratch folder turned
    into {"filename", "content_base64"}. A path that names a file the agent's own tool made
    during this wake (`made`, from made_during) is copied in first. Entries that already
    carry bytes or a receipt_id are left as they are."""
    files = answer.get("files")
    if not isinstance(files, list):
        return answer
    root = Path(work).resolve()
    made = list(made or [])
    used: set[str] = set()
    out: list[Any] = []
    for entry in files:
        if (isinstance(entry, dict) and entry.get("path") and not entry.get("content_base64")
                and not entry.get("receipt_id")):
            named = str(entry["path"])
            target = (root / named).resolve()
            inside = root in target.parents
            if not (inside and target.is_file()):
                source = _made_match(named, made)
                if source is not None:
                    target, inside = _bring_made(source, root, used).resolve(), True
            if not inside:
                raise NoAnswer("a file named in files is outside the agent's working folder")
            if not target.is_file():
                raise NoAnswer("the agent named a file it did not make: "
                               + _shown(Path(named).name))
            if target.stat().st_size > MAX_FILE_BYTES:
                raise NoAnswer(f"the file {_shown(target.name)} is larger than the bench takes")
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

# THE LAST TURN IS THE ANSWER (staging, Nov 16, 8:46 and 9:02 pm: Sam started background
# research helpers for Steven's "find 15 named people" step, ended two turns with "Still
# waiting on the librarian search..." and handed in the whole list in a third turn. `claude -p
# --output-format text` prints EVERY turn's closing words one after another, so the reply was
# prose plus the list and was refused twice as "not one JSON object"). The bridge asks Claude
# Code for its event stream and reads only the closing words of its LAST turn (the last
# "result" event, or main-thread words written after it); checked on claude 2.1.325: one
# "result" event per turn, a helper's own words carry its parent_tool_use_id.
CLAUDE_STREAM = ["--output-format", "stream-json", "--verbose"]


def claude_final(stdout: str) -> str:
    """The closing words of Claude Code's last turn in its stream-json output; `stdout` as it
    is when it holds no Claude Code events (an operator's own --output-format)."""
    final: str | None = None
    words: list[str] = []  # the main thread's words since the last result event
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or "session_id" not in event:
            continue
        if event.get("type") == "result":
            final, words = str(event.get("result") or ""), []
        elif event.get("type") == "assistant" and not event.get("parent_tool_use_id"):
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    words = [block["text"]]
    if words:
        return words[-1]
    return stdout if final is None else final


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
        stream = [] if "--output-format" in extra else CLAUDE_STREAM
        return [binary, "-p", *stream, *with_model, *edits, *(extra or CLAUDE_DEFAULT)]
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
    `finish(reply, scratch_folder, made)` makes of it, while the folder is still there;
    `made` is what the agent's own tool saved in its own folder during this wake). With several
    Claude accounts, a failed run wakes the next account. `bring` is the folder of files the
    watch fetched for the item: they are moved into the scratch folder first."""
    extra = list(extra or [])
    accounts = list(config_dirs or []) or [None]
    began = time.time()  # a picture the agent's own tool made counts only from here
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
                if by_name(agent) is not None and by_name(agent).name == "claude":
                    reply = claude_final(reply)
                if by_name(agent) is not None and by_name(agent).name == "codex":
                    try:
                        reply = Path(last).read_text(encoding="utf-8")
                    except OSError:
                        pass
                if not finish:
                    return reply
                return finish(reply, work, made_during(tool_folders(agent, env), began))
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
                     finish=lambda reply, work, made: attach_files(answer_from(reply), work,
                                                                  made))
    except NoAnswer as error:
        print(str(error), file=sys.stderr)
        return FAILED_EXIT
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
