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
    assert Path(run["argv"][0]).name == "claude" and run["argv"][1:4] == [
        "-p", "--output-format", "text"]
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
    assert ("No agent program the harness knows (claude, codex, gemini) is on this computer."
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
