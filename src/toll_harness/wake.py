"""The bridge that wakes an agent known by name with one item.

    python -m toll_harness.wake --agent claude [--model M] [--program P] [--config-dir D]
                                [--own-tools] [-- extra]
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
from toll_harness.agent import CONFIG_ENV
from toll_harness.known import PROMPT, by_name

FAILED_EXIT = 3
SHARED_ENV = "TOLL_WAKE_SHARED"  # "1": other wakes of this agent may run beside this one


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
    r"`[^`\n]{1,250}` is not signed in: run `[^`\n]{1,250}` once, with no arguments, to "
    r"sign in",
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


# A PICTURE IS TAKEN ONLY FROM THIS WAKE'S OWN SESSION (lane THREEATONCE, 16 November 2026:
# with several wakes of one agent at once, and the -prod units on the same computer, "written
# since this wake began" could pick another wake's picture). `codex exec` names its session on
# stderr ("session id: <uuid>", checked on codex-cli 0.159.2) and saves its pictures under
# generated_images/<that id>/, so only that folder is read. With no session named, the old rule
# stands only when this wake runs alone (SHARED_ENV unset); beside others, nothing is taken.
_SESSION = re.compile(r"^session id: ([0-9A-Za-z][0-9A-Za-z-]{7,63})\s*$", re.M)


def session_of(stderr: str) -> str | None:
    """The session a `codex exec` run named on stderr, else None."""
    found = _SESSION.findall(stderr or "")
    return found[-1] if found else None


def made_during(folders: list[Path], since: float, session: str | None = None,
                alone: bool = True) -> list[Path]:
    """Regular files (never a link) in each tool folder or one folder below it, written at or
    after `since`, the moment this wake began (to the whole second, for file systems that keep
    whole seconds). With `session`, only the folder of that session. With no session and not
    `alone`, none. Real paths, so a name is matched exactly."""
    floor = math.floor(since)
    found: list[Path] = []
    if session is None and not alone:
        return found
    for folder in folders:
        try:
            root = folder.resolve(strict=True)
            if not root.is_dir():
                continue
            subs = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
            if session is not None:
                subs = [p for p in subs if p.name == session]
                root_files = False
            else:
                root_files = True
        except OSError:
            continue
        places = ([root] if root_files else []) + sorted(subs, key=lambda p: p.stat().st_mtime,
                                                         reverse=True)
        for place in places:
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


# Claude Code woken with no settings of its own by a 1.0 or 1.1 setup gets the web and nothing
# else: its shell, file and MCP tools off, and no session saved between items. A 0.x agent
# that brings its own settings (extra_args) keeps them. (Another worker's walk, Nov 11: the
# README's wake command "left Claude's shell and file tools on".) Kept as it is for every
# agent.yaml already written that way: an existing agent wakes as it did.
CLAUDE_DEFAULT = ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch",
                  "--strict-mcp-config", "--no-session-persistence"]

# ITS OWN TOOLS (Steven, Nov 23: "There are ALL sorts of agents ... they are supposed to have
# their own tools at this point ... why are we getting in the middle"; "We are the ones who
# will decide the defaults to start"). A fresh setup writes `own_tools: true` in its wake, and
# Claude Code then wakes with the owner's own tools, MCP servers and permission settings:
# nothing narrows the tool list, no --strict-mcp-config, no --permission-mode (the owner's
# defaultMode stands). The web stays allowed, as it was at every setup before. Checked on
# claude 2.1.325, Nov 23, against a stand-in model: in -p a tool the owner has not allowed is
# refused at once ("... but you haven't granted it yet"), never left waiting for a yes nobody
# can give; the owner's allow rules let Bash, Write and their own MCP server's tool run; their
# deny rules beat any allow; a read or `cat` outside the scratch folder is refused.
CLAUDE_OWN_TOOLS = ["--allowedTools", "WebSearch,WebFetch", "--no-session-persistence"]


def keep_out(folder: str | None) -> list[str]:
    """THE TOKEN STAYS OUT OF REACH: deny rules for the agent's own folder (agent.yaml and the
    token beside it), which beat the owner's allow rules and even their bypass mode (checked
    Nov 23: Read and a plain `cat` of the token refused under bypassPermissions). A shell the
    owner allowed can still run a program that reads it: that wall is the owner's computer,
    not a flag."""
    if not folder or any(char in folder for char in "()*"):
        return []
    root = "/" + str(Path(folder).resolve()).lstrip("/")
    return ["--disallowedTools", f"Read(/{root}/**)", f"Edit(/{root}/**)"]

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


# ANTIGRAVITY (`agy`), from its own headless documentation (antigravity.google/docs/cli/
# headless, read Nov 23; no agy was run here): with `--input-format stream-json` the prompt goes
# on stdin as one {"event": "user", "message": {"content": ...}} line, so it is never on a
# command line, and agy exits once stdin is closed and the turn is done. With
# `--output-format stream-json` each turn ends with a {"event": "result", "result": {"response":
# ...}} line: the last one's `response` is the reply. A run with no sign-in fails with an
# "authentication required" error (the docs) or "Please sign in to view available models.
# Launch the CLI without arguments to sign in." (seen on an owner's Mac); signing in is running
# `agy` once with no arguments.
AGY_IO = ["--input-format", "stream-json", "--output-format", "stream-json"]
_SIGNED_OUT = re.compile(r"sign in|signed in|authentication required|not authenticated", re.I)


def agy_input(prompt: str) -> str:
    return json.dumps({"event": "user", "message": {"content": prompt}},
                      ensure_ascii=False) + "\n"


def agy_final(stdout: str) -> str:
    """The `response` of the last result event in agy's stream-json output; `stdout` as it is
    when it holds no result event (an operator's own --output-format)."""
    final: str | None = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (isinstance(event, dict) and event.get("event") == "result"
                and isinstance(event.get("result"), dict)):
            final = str(event["result"].get("response") or "")
    return stdout if final is None else final


def signed_out(program: str, stderr: str) -> str:
    """The reason for an agy that is not signed in, else ''."""
    if not _SIGNED_OUT.search(stderr or ""):
        return ""
    return f"`{program}` is not signed in: run `{program}` once, with no arguments, to sign in"


# CODEX, AND THE TOOLS ITS OPERATOR PLUGGED IN (staging, Nov 21 to 23: Bree's operator had
# given it a publish tool, `-c mcp_servers.publish.command=...` in extra_args. `codex exec`
# runs with nobody at the keyboard, so its approval policy is "never", and a tool that
# reaches the open web asks for an approval: Codex's own reviewer rejected every call
# ("Automatic approval review rejected publish_page because approval policy is never") and
# Bree told its person four times that it could not deliver). A tool server the operator
# names in extra_args is one they mean the agent to use, so the harness says so to Codex:
# default_tools_approval_mode "approve" for each such server, unless the operator's own
# settings already say a mode for it or switch it off. Servers from the operator's Codex
# config file are left as they are. A Codex too old to know the setting ignores it.
_CODEX_SERVER = re.compile(r"^mcp_servers\.([A-Za-z0-9_-]+)\.(.+?)\s*=\s*(.*)$", re.S)


def codex_own_tools(extra: list[str]) -> list[str]:
    named: list[str] = []
    settled: set[str] = set()
    for part in extra:
        found = _CODEX_SERVER.match(str(part).strip())
        if not found:
            continue
        name, key, value = found.group(1), found.group(2), found.group(3).strip()
        if key in ("command", "url") and name not in named:
            named.append(name)
        if key == "default_tools_approval_mode" or (key == "enabled" and value == "false"):
            settled.add(name)
    out: list[str] = []
    for name in named:
        if name not in settled:
            out += ["-c", f'mcp_servers.{name}.default_tools_approval_mode="approve"']
    return out


# CODEX'S OWN SANDBOX. `codex exec` is woken with `--sandbox workspace-write` (it may write in
# its scratch folder, and /tmp, never the network) unless the owner chose a sandbox: in
# extra_args, or, for a wake with its own tools, in their own Codex config file
# ($CODEX_HOME/config.toml, or the profile file `--profile <name>` names), which a --sandbox on
# the command line would override (checked on codex-cli 0.162.1, Nov 23: the flag beats the
# file; with neither, exec runs read-only).
_SANDBOX_LINE = re.compile(r"^\s*sandbox_mode\s*=")


def codex_config_sandbox(extra: list[str], env: dict[str, str] | None = None) -> bool:
    """Whether the owner's own Codex config file names a sandbox_mode at its top level."""
    env = os.environ if env is None else env
    home = Path(env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"))
    files = [home / "config.toml"]
    for flag in ("-p", "--profile"):
        if flag in extra[:-1]:
            files.append(home / f"{extra[extra.index(flag) + 1]}.config.toml")
    for path in files:
        try:
            lines = path.expanduser().read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            if line.lstrip().startswith("["):
                break  # past the top level
            if _SANDBOX_LINE.match(line):
                return True
    return False


def argv_for(agent: str, *, model: str | None, program: str | None,
             extra: list[str], last_message: str | None = None,
             scratch: str | None = None, own_tools: bool = False,
             agent_folder: str | None = None) -> list[str]:
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
        default = CLAUDE_OWN_TOOLS + keep_out(agent_folder) if own_tools else CLAUDE_DEFAULT
        return [binary, "-p", *stream, *with_model, *edits, *(extra or default)]
    if known.name == "codex":
        where = ["--cd", scratch] if scratch else []
        own_sandbox = any(a in ("-s", "--sandbox") or str(a).startswith("--sandbox=")
                          or "sandbox_mode" in str(a) for a in extra)
        own_sandbox = own_sandbox or (own_tools and codex_config_sandbox(extra))
        sandbox = [] if own_sandbox else ["--sandbox", "workspace-write"]
        return [binary, "exec", "--skip-git-repo-check", *where, *sandbox,
                "--output-last-message", str(last_message), *with_model, *extra,
                *codex_own_tools(extra), "-"]
    if known.name == "antigravity":
        io = [] if "--input-format" in extra else AGY_IO
        return [binary, *io, *with_model, *extra]
    return [binary, *with_model, *extra]  # gemini: the prompt on stdin, the reply on stdout


def _agent_folder() -> str | None:
    """The agent's own folder: where the agent.yaml the harness named (TOLL_AGENT_CONFIG) is."""
    path = os.environ.get(CONFIG_ENV)
    return str(Path(path).expanduser().resolve().parent) if path else None


def run(agent: str, prompt: str, *, model: str | None = None, program: str | None = None,
        config_dirs: list[str] | None = None, extra: list[str] | None = None,
        finish=None, bring: str | None = None, own_tools: bool = False):
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
                        scratch=str(work), own_tools=own_tools,
                        agent_folder=_agent_folder())
        name = Path(argv[0]).name
        known = by_name(agent)
        agy = known is not None and known.name == "antigravity"
        given = agy_input(prompt) if agy and "--input-format" not in extra else prompt
        problem = ""
        for number, account in enumerate(accounts, 1):
            env = dict(os.environ)
            if account:
                env["CLAUDE_CONFIG_DIR"] = account
            try:
                done = subprocess.run(argv, input=given, capture_output=True, text=True,
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
                if agy:
                    reply = agy_final(reply)
                    if not reply.strip() and signed_out(argv[0], done.stderr):
                        raise NoAnswer(signed_out(argv[0], done.stderr))
                if not finish:
                    return reply
                made = made_during(tool_folders(agent, env), began,
                                   session=session_of(done.stderr),
                                   alone=os.environ.get(SHARED_ENV) != "1")
                return finish(reply, work, made)
            if agy and signed_out(argv[0], done.stderr):
                raise NoAnswer(signed_out(argv[0], done.stderr))
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
    parser.add_argument("--own-tools", action="store_true")
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
                     own_tools=arguments.own_tools,
                     finish=lambda reply, work, made: attach_files(answer_from(reply), work,
                                                                  made))
    except NoAnswer as error:
        print(str(error), file=sys.stderr)
        return FAILED_EXIT
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
