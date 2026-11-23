"""Waking an agent known by name (Steven, Nov 11: "we want to be able to wake the agent";
"why doesnt it run the command when its installed"; "we upgrade them all").

One table (known.KNOWN) serves fresh setup and the move from 0.x: init finds the known agent
programs on the computer (one: said and used; several: picked by name; none: a command line
is asked, the only time), wakes the agent once and says it woke, or stops before anything is
registered. A 0.x agent.yaml that names a known agent (or a 0.x external command) is moved by
the watch itself, with no question, keeping its token; one that reached its model with an API
key stops with plain words naming the one thing to do. The programs here are stand-ins: no
model is called."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.fakebench import TOKEN, item
from toll_harness import cli, known, wake
from toll_harness.agent import TOKEN_SECRET, load
from toll_harness.handoff import ANSWERED, FAILED, hand_off
from toll_harness.onboarding import InitError, init
from toll_harness.secrets import FileSecretStore
from toll_harness.watch import Watch

SRC = str(Path(__file__).resolve().parents[1] / "src")

# A stand-in agent program. It writes down its argv and the prompt it was given, then replies
# the way REPLY says (default: one ```json block and a Sources line, as claude -p does).
PROGRAM = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    prompt = sys.stdin.read()
    if os.environ.get("MAKE_FILE"):
        open(os.environ["MAKE_FILE"], "wb").write(b"%PDF-1.4 made by the agent")
    with open(os.environ["PROGRAM_RECORD"], "a") as handle:
        handle.write(json.dumps({{"argv": sys.argv, "prompt": prompt}}) + "\\n")
    fenced = '```json' + chr(10) + '{{"ok": true, "reply": "woke"}}' + chr(10) + '```'
    reply = os.environ.get("REPLY", fenced + chr(10) + 'Sources: none')
    if reply == "EXIT1":
        sys.exit(1)
    if "--output-last-message" in sys.argv:
        path = sys.argv[sys.argv.index("--output-last-message") + 1]
        open(path, "w").write(reply)
        print('{{"type": "event stream, not the answer"}}')
    else:
        print(reply)
""")


@pytest.fixture
def programs(tmp_path, monkeypatch):
    """A folder of stand-in programs named like the known agents, first on PATH."""
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in ("claude", "codex", "gemini", "old-agent"):
        path = folder / name
        path.write_text(PROGRAM.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PYTHONPATH", SRC)  # the bridge runs as python -m toll_harness.wake
    monkeypatch.setenv("PROGRAM_RECORD", str(tmp_path / "program.jsonl"))
    monkeypatch.delenv("REPLY", raising=False)
    monkeypatch.delenv("MAKE_FILE", raising=False)
    monkeypatch.delenv("TOLL_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("TOLL_HARNESS_BASE_URL", raising=False)
    return folder


def program_runs(tmp_path) -> list[dict]:
    record = tmp_path / "program.jsonl"
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text().splitlines()]


def only(*present):
    return lambda program: f"/usr/bin/{program}" if program in present else None


def fresh(tmp_path, bench, *, ask, which, said=None, **kwargs):
    said = [] if said is None else said
    return init(tmp_path / "TestEcho", ask=ask, say=said.append, which=which,
                environ={"TOLL_HARNESS_BASE_URL": bench.url},
                answers={"contact_email": "o@example.com", "location": "US-OR",
                         **kwargs.pop("answers", {})},
                install_service=lambda agent: {"service": "x", "running": True}, **kwargs)


# -- fresh setup: the agent by name ----------------------------------------------------
def test_one_known_agent_found_is_said_and_used_with_no_command_asked(tmp_path, bench, programs):
    asked, said = [], []

    def ask(label, default):
        asked.append(label)
        return "Test Owner Co"

    agent = fresh(tmp_path, bench, ask=ask, which=only("claude"), said=said)
    assert asked == ["Company"]  # the model's name comes from the agent picked
    assert agent.section["wake"] == {"agent": "claude"} and agent.section["model"] == "Claude"
    assert "Found Claude Code (claude) on this computer: your agent wakes through it." in said
    assert any(line.startswith("Your agent woke and answered a sample item") for line in said)
    run = program_runs(tmp_path)[0]
    assert Path(run["argv"][0]).name == "claude" and run["argv"][1:5] == [
        "-p", "--output-format", "stream-json", "--verbose"]
    assert "toll-harness checking that your agent wakes" in run["prompt"]
    assert bench.registered[0]["disclosure"]["model"] == "Claude"


def test_several_found_the_owner_picks_one_by_name(tmp_path, bench, programs):
    asked = []

    def ask(label, default):
        asked.append(label)
        return "Test Owner Co" if label == "Company" else "gemini"

    agent = fresh(tmp_path, bench, ask=ask, which=only("claude", "gemini"))
    assert asked[1] == ("Which agent answers for you? 1) Claude Code (claude), "
                        "2) Gemini CLI (gemini), 3) something else")
    assert agent.section["wake"] == {"agent": "gemini"} and agent.section["model"] == "Gemini"
    assert Path(program_runs(tmp_path)[0]["argv"][0]).name == "gemini"


def test_several_found_a_number_picks_too_and_something_else_asks_the_command(
        tmp_path, bench, programs):
    replies = iter(["Test Owner Co", "3", f"{programs / 'old-agent'} --raw", "Echo"])
    asked = []

    def ask(label, default):
        asked.append(label.split(" ")[0])
        return next(replies)

    os.environ["REPLY"] = '{"ok": true}'  # a 1.0 command answers JSON itself
    try:
        agent = fresh(tmp_path, bench, ask=ask, which=only("claude", "codex"))
    finally:
        del os.environ["REPLY"]
    assert asked == ["Company", "Which", "The", "Model's"]
    assert agent.section["command"] == [str(programs / "old-agent"), "--raw"]
    assert "wake" not in agent.section


def test_none_found_asks_the_command_line_and_says_why(tmp_path, bench, programs):
    asked, said = [], []
    replies = iter(["Test Owner Co", f"{programs / 'old-agent'}", "Echo"])

    def ask(label, default):
        asked.append(label.split(" ")[0])
        return next(replies)

    os.environ["REPLY"] = '{"ok": true}'
    try:
        agent = fresh(tmp_path, bench, ask=ask, which=only(), said=said)
    finally:
        del os.environ["REPLY"]
    assert asked == ["Company", "The", "Model's"]
    assert ("No agent program the harness knows (claude, codex, gemini, antigravity) is on "
            "this computer."
            in said)
    assert agent.section["command"] == [str(programs / "old-agent")]


def test_the_agent_flag_names_it_with_no_question(tmp_path, bench, programs):
    agent = fresh(tmp_path, bench, ask=lambda label, default: pytest.fail(label),
                  which=only("claude", "codex"),
                  answers={"company": "Test Owner Co", "agent": "codex"})
    assert agent.section["wake"] == {"agent": "codex"}
    run = program_runs(tmp_path)[0]
    assert run["argv"][1:3] == ["exec", "--skip-git-repo-check"] and run["argv"][-1] == "-"


def test_a_sample_wake_that_fails_stops_setup_before_anything_is_registered(
        tmp_path, bench, programs, monkeypatch):
    monkeypatch.setenv("REPLY", "EXIT1")
    with pytest.raises(InitError) as stopped:
        fresh(tmp_path, bench, ask=lambda label, default: "Test Owner Co",
              which=only("claude"))
    words = str(stopped.value)
    assert words.startswith("Your agent did not wake: the command exited with 3 "
                            "(claude exited with 1).")
    assert "Nothing was registered or changed" in words
    assert bench.registered == []
    assert not (tmp_path / "TestEcho" / "agent.yaml").exists()


def test_a_sample_wake_with_no_json_in_the_reply_says_so(tmp_path, bench, programs,
                                                        monkeypatch):
    monkeypatch.setenv("REPLY", 'I am not sure. {"maybe": true}')
    with pytest.raises(InitError, match="was not one JSON object"):
        fresh(tmp_path, bench, ask=lambda label, default: "Test Owner Co",
              which=only("claude"))
    assert bench.registered == []


# -- the bridge --------------------------------------------------------------------------
@pytest.mark.parametrize("reply, answer", [
    ('{"reply": "hi"}', {"reply": "hi"}),
    ('```json\n{"reply": "hi"}\n```', {"reply": "hi"}),
    ('```\n{"reply": "hi"}\n```', {"reply": "hi"}),
    ('```json\n{"reply": "hi"}\n```\n\nSources:\n- https://example.com', {"reply": "hi"}),
    ('Here is my answer:\n```json\n{"reply": "hi"}\n```', {"reply": "hi"}),
    ('{"text": "", "tool_calls": [{"name": "result.complete", "arguments": '
     '{"result": {"reply": "hi"}}}]}', {"reply": "hi"}),  # a 0.x envelope
    ('{"text": "{\\"reply\\": \\"hi\\"}"}', {"reply": "hi"}),
    ('{"tool_calls": [{"name": "result.fail", "arguments": {"reason": "no access"}}]}',
     {"failed": "no access"}),
])
def test_the_answer_is_the_one_json_object_in_the_reply(reply, answer):
    assert wake.answer_from(reply) == answer


@pytest.mark.parametrize("reply", [
    "", "no json here", "[1, 2]", '{"text": ""}',
    'Here it is: {"reply": "hi"} - thanks',  # JSON in prose is never an answer
    'I would send this if you say yes: {"version": "2026-11-12", "how": "owner said yes"}',
    '```json\n{"a": 1}\n```\nor\n```json\n{"b": 2}\n```',  # two blocks
    '```json\n{"a": 1}\n```\nOr maybe {"b": 2}',  # JSON outside the one block
    '```json\nnot json\n```',
])
def test_a_reply_that_is_not_one_object_or_one_block_is_refused_in_plain_words(reply):
    with pytest.raises(wake.NoAnswer):
        wake.answer_from(reply)


def test_a_fresh_claude_wake_gets_the_web_and_no_shell_or_files(tmp_path, programs):
    hand_off(known.wake_argv({"agent": "claude"}), item("it_7"), time_limit=30)
    argv = program_runs(tmp_path)[0]["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    hand_off(known.wake_argv({"agent": "claude", "extra_args": ["--own"]}), item("it_8"),
             time_limit=30)
    assert program_runs(tmp_path)[1]["argv"][-1:] == ["--own"]  # its own settings win


def test_the_prompt_carries_what_to_do_the_context_and_the_shape():
    text = wake.prompt_for(item("it_1", refs=[{"name": "step-1-result", "what": "x"}]))
    for part in ("Reply to the owner's line in one sentence.", "Please post this now.",
                 '"reply": "string"', "step-1-result", '{"need": ["<name>"]}',
                 "EXACTLY one JSON object"):
        assert part in text


def test_a_0x_external_command_gets_the_item_as_a_prompt(tmp_path, programs):
    command = known.wake_argv({"agent": known.PROMPT, "command": [str(programs / "old-agent"),
                                                                "--its-arg"]})
    result = hand_off(command, item("it_2"), time_limit=30)
    assert result.status == ANSWERED and result.answer == {"ok": True, "reply": "woke"}
    run = program_runs(tmp_path)[0]
    assert run["argv"][1:] == ["--its-arg"]
    assert "Reply to the owner's line in one sentence." in run["prompt"]


def test_codex_answers_through_its_last_message_file_in_a_scratch_folder(tmp_path, programs):
    result = hand_off(known.wake_argv({"agent": "codex", "model": "gpt-5"}), item("it_3"),
                      time_limit=30, cwd=str(tmp_path))
    assert result.answer == {"ok": True, "reply": "woke"}
    argv = program_runs(tmp_path)[0]["argv"]
    assert "--model" in argv and "toll-wake-" in argv[argv.index("--cd") + 1]


ACCOUNTS = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    sys.stdin.read()
    with open(os.environ["PROGRAM_RECORD"], "a") as handle:
        handle.write(json.dumps({{"account": os.environ.get("CLAUDE_CONFIG_DIR"),
                                  "cwd": os.getcwd()}}) + "\\n")
    if os.environ.get("CLAUDE_CONFIG_DIR") in os.environ.get("SPENT", "").split(","):
        print("You have hit your usage limit", file=sys.stderr)
        sys.exit(1)
    print('{{"reply": "from ' + os.environ.get("CLAUDE_CONFIG_DIR", "") + '"}}')
""")


def test_claude_accounts_are_tried_in_order_when_one_is_spent(tmp_path, programs,
                                                             monkeypatch):
    claude = programs / "claude"
    claude.write_text(ACCOUNTS.format(python=sys.executable))
    command = known.wake_argv({"agent": "claude", "config_dirs": ["/acct/a", "/acct/b"]})
    monkeypatch.setenv("SPENT", "/acct/a")
    result = hand_off(command, item("it_5"), time_limit=30, cwd=str(tmp_path))
    assert result.answer == {"reply": "from /acct/b"}
    runs = program_runs(tmp_path)
    assert [run["account"] for run in runs] == ["/acct/a", "/acct/b"]
    assert all("toll-wake-" in run["cwd"] for run in runs)  # never the agent's folder
    monkeypatch.setenv("SPENT", "/acct/a,/acct/b")
    result = hand_off(command, item("it_6"), time_limit=30)
    assert result.status == FAILED and result.said == "claude exited with 1 on account 2 of 2"


def test_a_missing_program_is_said_plainly(tmp_path, programs):
    result = hand_off(known.wake_argv({"agent": "claude", "program": "/nowhere/claude"}),
                      item("it_4"), time_limit=30)
    assert result.status == FAILED and result.said == "`/nowhere/claude` is not on this computer"


# -- the move from 0.x: the old file decides, nobody is asked ------------------------------
@pytest.mark.parametrize("model, wake_config", [
    ({"adapter": "claude_code", "model_id": "opus", "claude_config_dirs": ["/a", "/b"]},
     {"agent": "claude", "model": "opus", "config_dirs": ["/a", "/b"]}),
    ({"adapter": "codex", "model_id": "gpt-5-codex", "extra_args": ["--x"]},
     {"agent": "codex", "model": "gpt-5-codex", "extra_args": ["--x"]}),
    ({"adapter": "external", "command": ["/opt/agent", "run"]},
     {"agent": "prompt", "command": ["/opt/agent", "run"]}),
    ({"adapter": "anthropic", "model_id": "claude-opus-4-8"}, None),
    ({"adapter": "openai", "model_id": "qwen", "base_url": "https://x"}, None),
    ({"adapter": "bedrock", "model_id": "m"}, None),
    ({}, None),  # no adapter meant Bedrock in 0.x
])
def test_every_kind_of_0x_setup_maps_to_a_wake_or_to_none(model, wake_config):
    assert known.wake_from_old({"model": model}) == wake_config


def old_agent(folder: Path, bench_url: str, model: dict) -> Path:
    folder.mkdir(parents=True)
    config = {
        "version": 1,
        "agent": {"name": "OldAgent", "company": "Old Co", "intelligence": "Stand-in",
                  "intelligence_brand": "Stand-in", "harness": "Toll Harness 0.76.2"},
        "model": {**model, "timeout_seconds": 600},
        "secrets": {"provider": "file", "directory": ".toll-harness/abc/secrets"},
        "toll_bench": {"connected": True, "base_url": bench_url,
                       "token_secret": "book_of_houses_agent_token"},
    }
    (folder / "agent.yaml").write_text(yaml.safe_dump(config))
    FileSecretStore(folder / ".toll-harness/abc/secrets").set(TOKEN_SECRET, TOKEN)
    return folder / "agent.yaml"


def test_the_0x_service_line_is_the_watch():
    assert cli.from_0x(["market", "watch", "/a/agent.yaml", "--scan-interval", "60"]) == [
        "watch", "/a/agent.yaml"]
    assert cli.from_0x(["market", "watch", "--config", "/b/agent.yaml"]) == [
        "watch", "/b/agent.yaml"]
    assert cli.from_0x(["watch", "/c"]) == ["watch", "/c"]


def test_the_watch_moves_a_0x_agent_by_itself_and_it_answers_an_item(tmp_path, bench,
                                                                     programs):
    path = old_agent(tmp_path / "old", bench.url,
                     {"adapter": "external", "command": [str(programs / "old-agent")]})
    said = []
    assert cli.move_in_place(str(path), say=said.append, sleep=pytest.fail) is None
    agent = load(path)
    assert agent.token() == TOKEN and bench.registered == []  # the same agent
    assert agent.section["wake"]["agent"] == "prompt"
    assert agent.section["time_limit_minutes"] == 15 and agent.section["company"] == "Old Co"
    assert (path.parent / "agent.0x.yaml").exists()
    assert any(line.startswith("Your agent woke and answered") for line in said)
    bench.add(item("it_live"))
    Watch(agent, wait=0, updater=lambda *a, **k: {"action": "none"}, sleep=lambda s: None,
          spread=0).run(1)
    assert bench.answers == [("it_live", {"ok": True, "reply": "woke"})]
    assert cli.move_in_place(str(path), say=said.append) is None  # moved once only


def test_the_watch_moves_a_0x_claude_agent_with_its_model(tmp_path, bench, programs):
    path = old_agent(tmp_path / "old", bench.url,
                     {"adapter": "claude_code", "model_id": "opus"})
    assert cli.move_in_place(str(path), say=lambda line: None, sleep=pytest.fail) is None
    assert load(path).section["wake"] == {"agent": "claude", "model": "opus"}
    argv = program_runs(tmp_path)[0]["argv"]
    assert argv[argv.index("--model") + 1] == "opus"


def test_a_0x_api_key_agent_stops_with_the_one_thing_to_do(tmp_path, bench, programs):
    path = old_agent(tmp_path / "old", bench.url, {"adapter": "anthropic", "model_id": "m"})
    before = path.read_text()
    said = []
    assert cli.move_in_place(str(path), say=said.append, sleep=pytest.fail) == 0
    assert path.read_text() == before and not (path.parent / "agent.0x.yaml").exists()
    text = "\n".join(said)
    assert "reached its model with an API key (anthropic)" in text
    assert f"toll-harness init {path.parent}" in text
    assert program_runs(tmp_path) == []  # nothing was woken, nothing was guessed


def test_a_0x_move_whose_wake_fails_changes_nothing_and_tries_again_later(
        tmp_path, bench, programs, monkeypatch):
    monkeypatch.setenv("REPLY", "EXIT1")
    path = old_agent(tmp_path / "old", bench.url,
                     {"adapter": "external", "command": [str(programs / "old-agent")]})
    before, slept = path.read_text(), []
    assert cli.move_in_place(str(path), say=lambda line: None, sleep=slept.append) == 1
    assert path.read_text() == before and slept == [cli.MOVE_RETRY_SECONDS]


# -- a file goes back by its path (staging, Nov 13, the harness e2e walk) ------------------
def _made(monkeypatch, name, reply):
    monkeypatch.setenv("MAKE_FILE", name)
    monkeypatch.setenv("REPLY", json.dumps(reply))


def test_a_file_the_agent_made_goes_back_as_its_bytes(tmp_path, programs, monkeypatch):
    import base64
    _made(monkeypatch, "flyer.pdf", {"status": "done", "result": "the flyer",
                                     "files": [{"filename": "flyer.pdf", "path": "flyer.pdf"},
                                               {"receipt_id": "r-1"}]})
    result = hand_off(known.wake_argv({"agent": "claude", "extra_args": ["--tools", "default"]}),
                      item("it_f1"), time_limit=30)
    assert result.status == ANSWERED, result.why
    first, second = result.answer["files"]
    assert "path" not in first and first["filename"] == "flyer.pdf"
    assert base64.b64decode(first["content_base64"]) == b"%PDF-1.4 made by the agent"
    assert second == {"receipt_id": "r-1"}


@pytest.mark.parametrize("path", ["../outside.pdf", "/etc/hostname", "never-made.pdf"])
def test_a_file_outside_the_scratch_folder_or_never_made_is_a_failure(tmp_path, programs,
                                                                      monkeypatch, path):
    _made(monkeypatch, "flyer.pdf", {"status": "done", "files": [{"path": path}]})
    result = hand_off(known.wake_argv({"agent": "codex"}), item("it_f2"), time_limit=30)
    assert result.status == FAILED


def test_an_agent_with_its_own_tools_may_write_in_its_scratch_folder(tmp_path, programs):
    hand_off(known.wake_argv({"agent": "claude", "extra_args": ["--tools", "default"]}),
             item("it_p1"), time_limit=30)
    hand_off(known.wake_argv({"agent": "claude", "extra_args": ["--permission-mode", "plan"]}),
             item("it_p2"), time_limit=30)
    hand_off(known.wake_argv({"agent": "codex"}), item("it_p3"), time_limit=30)
    hand_off(known.wake_argv({"agent": "codex", "extra_args": ["-s", "read-only"]}),
             item("it_p4"), time_limit=30)
    runs = [r["argv"] for r in program_runs(tmp_path)]
    assert runs[0][runs[0].index("--permission-mode") + 1] == "acceptEdits"
    assert runs[1].count("--permission-mode") == 1 and "acceptEdits" not in runs[1]
    assert runs[2][runs[2].index("--sandbox") + 1] == "workspace-write"
    assert "--sandbox" not in runs[3] and "read-only" in runs[3]


def test_a_tool_the_operator_plugged_into_codex_needs_no_approval(tmp_path, programs):
    own = ["-c", 'mcp_servers.publish.command="/usr/bin/python3"',
           "-c", 'mcp_servers.publish.args=["/x/publish.py"]',
           "-c", "mcp_servers.lab.enabled=false",
           "-c", 'mcp_servers.pay.url="https://pay.example/mcp"',
           "-c", 'mcp_servers.pay.default_tools_approval_mode="prompt"']
    hand_off(known.wake_argv({"agent": "codex", "extra_args": own}), item("it_t1"),
             time_limit=30)
    hand_off(known.wake_argv({"agent": "codex"}), item("it_t2"), time_limit=30)
    runs = [r["argv"] for r in program_runs(tmp_path)]
    approve = 'mcp_servers.publish.default_tools_approval_mode="approve"'
    assert runs[0].count(approve) == 1 and runs[0][runs[0].index(approve) - 1] == "-c"
    assert runs[0][-1] == "-"                                   # the prompt still on stdin
    assert not any("mcp_servers.lab.default" in a for a in runs[0])    # switched off: left
    assert sum("mcp_servers.pay.default_tools_approval_mode" in a for a in runs[0]) == 1
    assert not any("default_tools_approval_mode" in a for a in runs[1])  # no tools, no line


def test_the_prompt_says_how_a_file_goes_back_when_the_answer_takes_files():
    plain = wake.prompt_for(item("it_n1"))
    with_files = wake.prompt_for(dict(item("it_n2"), answer={"needed": True, "shape": {
        "status": "x", "files": "list"}}))
    assert wake.FILES_NOTE not in plain and wake.FILES_NOTE in with_files


# -- a picture the agent's own tool made goes back too (staging, Nov 16, lane HARNFIX) ------
# Steven asked for six pictures; Bree (Codex, shell off) woke three times and Codex's own
# picture tool saved 14 PNGs under $CODEX_HOME/generated_images/<session>/, outside the scratch
# folder. Every wake ended "the command exited with 3" and nobody was told why. Steven: "Why
# wouldn't someone be able to put a picture in? It shouldn't just be lab agents."
PICTURE = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    sys.stdin.read()
    out = os.path.join(os.environ["CODEX_HOME"], "generated_images",
                       "01a1e6aa-0f0d-79c3-aa9a-197266072db8")
    if os.environ.get("IN_SCRATCH"):  # made in the folder it was started in
        out = os.getcwd()
    os.makedirs(out, exist_ok=True)
    pic = os.path.join(out, "exec-0a02ea46.png")
    open(pic, "wb").write(b"\\x89PNG picture " * int(os.environ.get("PICTURE_REPEAT", "1")))
    if os.environ.get("PRIVATE_FILE"):  # a file made now, but by nobody's picture tool
        open(os.environ["PRIVATE_FILE"], "w").write("the operator's own words")
    if os.environ.get("LINK_TO"):  # a link planted in the tool folder during the wake
        link = os.path.join(out, "exec-link.png")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(os.environ["LINK_TO"], link)
    named = os.environ.get("NAME_AS", "abs")
    path = {{"abs": pic, "plain": "exec-0a02ea46.png"}}.get(named, named)
    reply = json.dumps({{"status": "done", "result": "six pictures",
                        "files": [{{"filename": "cover.png", "path": path}}]}})
    if "--output-last-message" in sys.argv:
        open(sys.argv[sys.argv.index("--output-last-message") + 1], "w").write(reply)
    else:
        print(reply)
""")
STEP = dict(item("it_pic"), kind="step", answer={"needed": True, "shape": {
    "status": "string", "result": "string", "files": "list"}})


@pytest.fixture
def pictures(tmp_path, programs, monkeypatch):
    """codex (and claude) replaced by a stand-in that saves a picture where Codex does."""
    for name in ("codex", "claude", "old-agent"):
        (programs / name).write_text(PICTURE.format(python=sys.executable))
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    for key in ("NAME_AS", "PICTURE_REPEAT", "PRIVATE_FILE", "LINK_TO", "IN_SCRATCH"):
        monkeypatch.delenv(key, raising=False)
    return home / "generated_images" / "01a1e6aa-0f0d-79c3-aa9a-197266072db8"


@pytest.mark.parametrize("name_as", ["abs", "plain"])
def test_a_picture_codex_saved_in_its_own_folder_during_the_wake_goes_back(
        pictures, monkeypatch, name_as):
    import base64
    monkeypatch.setenv("NAME_AS", name_as)
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.status == ANSWERED, (result.why, result.said)
    (sent,) = result.answer["files"]
    assert sent["filename"] == "cover.png" and "path" not in sent
    assert base64.b64decode(sent["content_base64"]) == b"\x89PNG picture "
    assert (pictures / "exec-0a02ea46.png").is_file()  # the tool's own file is left in place


def test_a_picture_rewritten_in_an_old_folder_during_the_wake_goes_back(pictures,
                                                                       monkeypatch):
    pictures.mkdir(parents=True)
    (pictures / "exec-0a02ea46.png").write_bytes(b"yesterday")
    for path in (pictures / "exec-0a02ea46.png", pictures):
        os.utime(path, (path.stat().st_atime - 86400, path.stat().st_mtime - 86400))
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.status == ANSWERED, (result.why, result.said)


def test_a_0x_command_that_is_codex_underneath_hands_its_picture_back_too(pictures,
                                                                         programs):
    command = known.wake_argv({"agent": known.PROMPT, "command": [str(programs / "old-agent")]})
    result = hand_off(command, STEP, time_limit=30)
    assert result.status == ANSWERED, (result.why, result.said)


@pytest.mark.parametrize("in_scratch", ["", "1"])
def test_an_answer_past_the_old_8_mb_stdout_cap_is_not_cut(pictures, monkeypatch, in_scratch):
    # six of Bree's 2.7 MB pictures are about 22 MB as base64: the old 8 MB cut made that
    # "the command printed something that is not JSON", wherever the pictures were made
    monkeypatch.setenv("PICTURE_REPEAT", str(700_000))  # 9.8 MB, 13 MB as base64
    monkeypatch.setenv("NAME_AS", "plain")
    monkeypatch.setenv("IN_SCRATCH", in_scratch)
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=60)
    assert result.status == ANSWERED, (result.why, result.said)
    assert len(result.answer["files"][0]["content_base64"]) > 8 * 1024 * 1024


OUTSIDE = "the command exited with 3: a file named in files is outside the agent's working folder"


def test_a_picture_made_before_the_wake_is_still_outside(pictures, monkeypatch):
    old = pictures.parent / "01a1d5ec-bd0b-7263-b0f5-f5c780002eee" / "exec-old.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"\x89PNG an earlier wake's picture")
    os.utime(old, (old.stat().st_atime - 3600, old.stat().st_mtime - 3600))
    os.utime(old.parent, (old.stat().st_atime, old.stat().st_mtime))
    monkeypatch.setenv("NAME_AS", str(old))
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.status == FAILED and result.reported == OUTSIDE


def test_an_operator_file_is_still_refused_even_one_written_during_the_wake(
        tmp_path, pictures, monkeypatch):
    private = tmp_path / "operator-notes.txt"
    monkeypatch.setenv("PRIVATE_FILE", str(private))
    monkeypatch.setenv("NAME_AS", str(private))
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.status == FAILED and result.reported == OUTSIDE
    monkeypatch.setenv("NAME_AS", "operator-notes.txt")  # its plain name finds nothing either
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.reported == ("the command exited with 3: the agent named a file it did not "
                               "make: operator-notes.txt")


def test_a_link_planted_in_the_tool_folder_is_never_followed(tmp_path, pictures,
                                                            monkeypatch):
    private = tmp_path / "operator-key.txt"
    private.write_text("not for the bench")
    monkeypatch.setenv("LINK_TO", str(private))
    monkeypatch.setenv("NAME_AS", "exec-link.png")
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.status == FAILED
    assert result.reported.endswith("the agent named a file it did not make: exec-link.png")
    monkeypatch.setenv("NAME_AS", str(pictures / "exec-link.png"))
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.status == FAILED and result.reported == OUTSIDE


def test_claude_has_no_tool_folder_so_a_codex_picture_is_not_its_to_hand_in(pictures,
                                                                           monkeypatch):
    monkeypatch.setenv("NAME_AS", "abs")
    result = hand_off(known.wake_argv({"agent": "claude"}), STEP, time_limit=30)
    assert result.status == FAILED and result.reported == OUTSIDE


def test_a_name_never_made_says_which_in_the_reason(pictures, monkeypatch):
    monkeypatch.setenv("NAME_AS", "picture-1.png")
    result = hand_off(known.wake_argv({"agent": "codex"}), STEP, time_limit=30)
    assert result.why == "the command exited with 3"  # the prefix the bench keys on, kept
    assert result.reported == ("the command exited with 3: the agent named a file it did not "
                               "make: picture-1.png")


def test_only_the_bridges_own_reasons_ride_along(tmp_path):
    script = tmp_path / "own.py"
    script.write_text("import sys\nsys.stdin.read()\n"
                      "print('my secret plan for the owner', file=sys.stderr)\nsys.exit(3)\n")
    result = hand_off([sys.executable, str(script)], STEP, time_limit=30)
    assert result.said == "my secret plan for the owner"
    assert result.told == "" and result.reported == "the command exited with 3"
    script.write_text("import sys\nsys.stdin.read()\n"
                      "print('the agent replied with nothing', file=sys.stderr)\nsys.exit(1)\n")
    assert hand_off([sys.executable, str(script)], STEP,
                    time_limit=30).reported == "the command exited with 1"


@pytest.mark.parametrize("line", [
    "the item on stdin was not a JSON object", "the agent replied with nothing", wake.NOT_ONE,
    "the agent's reply held JSON outside its one code block",
    "the agent's code block was not JSON", "the agent's reply was JSON but not an object",
    "the agent's reply was an empty 0.x envelope",
    "a file named in files is outside the agent's working folder",
    "the agent named a file it did not make: cover.png",
    "the file cover.png is larger than the bench takes", "no 0.x command was given",
    "the harness does not know an agent named 'robot'", "`/nowhere/claude` is not on this computer",
    "`/usr/bin/codex` cannot be run (permission denied)", "codex exited with 1",
    "claude exited with 1 on account 2 of 2",
    "`agy` is not signed in: run `agy` once, with no arguments, to sign in",
])
def test_every_line_the_bridge_prints_is_one_of_its_fixed_reasons(line):
    assert wake.bridge_reason(line) == line


def test_the_prompt_says_where_a_tool_made_picture_is_and_to_name_it():
    with_files = wake.prompt_for(STEP)
    assert "$CODEX_HOME/generated_images" in with_files and "by its file name" in with_files


# -- Antigravity (`agy`), as its headless docs describe it ----------------------------------
# A stand-in agy: the prompt comes on stdin as one stream-json user event; the reply goes out as
# stream-json events, the last result event's `response` being the answer. AGY=signed_out
# prints the sign-in error an owner's Mac showed and exits 1.
AGY = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    raw = sys.stdin.read()
    with open(os.environ["PROGRAM_RECORD"], "a") as handle:
        handle.write(json.dumps({{"argv": sys.argv, "stdin": raw}}) + "\\n")
    if os.environ.get("AGY") == "signed_out":
        print("Error: Please sign in to view available models. Launch the CLI without "
              "arguments to sign in.", file=sys.stderr)
        sys.exit(1)
    event = json.loads(raw.splitlines()[0])
    assert event["event"] == "user" and "toll-harness" in event["message"]["content"]
    print(json.dumps({{"event": "init", "conversation_id": "c1", "init": {{"cwd": os.getcwd()}}}}))
    print(json.dumps({{"event": "result", "result": {{"conversation_id": "c1",
          "status": "SUCCESS", "response": "Thinking first.", "num_turns": 1}}}}))
    print(json.dumps({{"event": "result", "result": {{"conversation_id": "c1",
          "status": "SUCCESS", "response": '{{"ok": true, "reply": "agy woke"}}',
          "num_turns": 2}}}}))
""")


@pytest.fixture
def agy(programs, monkeypatch):
    path = programs / "agy"
    path.write_text(AGY.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.delenv("AGY", raising=False)
    return path


def test_antigravity_is_known_by_name_and_by_its_program():
    assert known.by_name("antigravity") is known.by_name("agy") is known.by_name("Antigravity")
    assert known.by_name("agy").model == "Gemini"


def test_antigravity_found_on_path_wakes_with_its_prompt_on_stdin(tmp_path, bench, agy):
    said = []
    agent = fresh(tmp_path, bench, ask=lambda label, default: "Test Owner Co",
                  which=only("agy"), said=said)
    assert agent.section["wake"] == {"agent": "antigravity"}
    assert agent.section["model"] == "Gemini"
    assert "Found Antigravity (agy) on this computer: your agent wakes through it." in said
    run = program_runs(tmp_path)[0]
    assert run["argv"][1:] == ["--input-format", "stream-json", "--output-format",
                               "stream-json"]
    assert not any("toll-harness" in part for part in run["argv"])  # never on a command line
    assert "toll-harness checking that your agent wakes" in json.loads(
        run["stdin"])["message"]["content"]


def test_antigravity_answers_with_its_last_result(tmp_path, agy):
    result = hand_off(known.wake_argv({"agent": "antigravity", "model": "gemini-3-pro"}),
                      item("it_agy"), time_limit=30)
    assert result.status == ANSWERED and result.answer == {"ok": True, "reply": "agy woke"}
    argv = program_runs(tmp_path)[0]["argv"]
    assert argv[argv.index("--model") + 1] == "gemini-3-pro"


def test_antigravity_inside_the_desktop_app_is_found_and_woken_by_its_path(tmp_path):
    inside = "/Applications/Gemini.app/Contents/Helpers/agy"
    wake_ = known.wake_for(known.by_name("antigravity"), lambda program: None,
                           lambda path: path == inside)
    assert wake_ == {"agent": "antigravity", "program": inside}
    assert known.by_name("antigravity") in known.found(lambda program: None,
                                                       lambda path: path == inside)
    assert known.wake_for(known.by_name("antigravity"), only("agy"),
                          lambda path: True) == {"agent": "antigravity"}  # PATH first


def test_antigravity_not_signed_in_stops_init_with_the_one_command_to_run(
        tmp_path, bench, agy, monkeypatch):
    monkeypatch.setenv("AGY", "signed_out")
    with pytest.raises(InitError) as stopped:
        fresh(tmp_path, bench, ask=lambda label, default: "Test Owner Co",
              which=only("agy"))
    assert str(stopped.value).startswith(
        "Your agent did not wake: the command exited with 3 (`agy` is not signed in: run "
        "`agy` once, with no arguments, to sign in). Nothing was registered or changed.")
    assert bench.registered == []
