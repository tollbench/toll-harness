"""init (three questions, register or connect, token in the store, the service, /me printed),
the 0.x move, connect and doctor."""

from __future__ import annotations

import stat
import sys

import pytest
import yaml

from tests.fakebench import TOKEN, FakeBench
from toll_harness.agent import TOKEN_SECRET, ConfigError, load
from toll_harness.onboarding import InitError, connect, doctor, init
from toll_harness.secrets import FileSecretStore


def answers(command="my-agent --from-toll-bench", **extra):
    return {"company": "Test Owner Co", "model": "Echo", "command": command, **extra}


def run_init(folder, bench: FakeBench, *, said=None, installed=None, **kwargs):
    said = [] if said is None else said
    installed = [] if installed is None else installed
    kwargs.setdefault("answers", answers())
    kwargs.setdefault("environ", {"TOLL_HARNESS_BASE_URL": bench.url})
    return init(folder, say=said.append,
                install_service=lambda agent: installed.append(agent) or
                {"service": "toll-harness-testecho.service", "running": True},
                **kwargs)


def test_init_asks_three_things_registers_and_installs_the_service(tmp_path, bench):
    asked, said, installed = [], [], []

    replies = {"Company": "Test Owner Co", "Model's": "Echo",
               "Command": "my-agent --flag 'two words'"}

    def ask(label, default):
        asked.append(label)
        return replies[label.split(" ")[0]]

    agent = run_init(tmp_path / "TestEcho", bench, said=said, installed=installed,
                     answers={}, ask=ask)
    assert len(asked) == 3
    assert [label.split(" ")[0] for label in asked] == ["Company", "Model's", "Command"]
    config = yaml.safe_load(agent.path.read_text())
    assert config["agent"]["command"] == ["my-agent", "--flag", "two words"]
    assert config["agent"]["company"] == "Test Owner Co" and config["agent"]["model"] == "Echo"
    assert config["bench"]["url"] == bench.url
    assert TOKEN not in agent.path.read_text()
    token_file = agent.data_dir / "secrets" / TOKEN_SECRET
    assert token_file.read_text() == TOKEN
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    registered = bench.registered[0]
    assert registered["handle"] == "TestEcho"
    assert registered["disclosure"]["operator_label"] == "Test Owner Co"
    assert registered["rules"] == {"accepted": True, "version_hash": "rules-abc"}
    assert [entry.path for entry in installed] == [agent.path]
    text = "\n".join(said)
    for fact in ("A-0999", "Test Owner Co", "testecho@agents.example", "accepted",
                 "looking_for all"):
        assert fact in text
    assert TOKEN not in text


def test_init_registered_reads_the_token_from_the_environment(tmp_path, bench):
    agent = run_init(tmp_path / "x", bench, registered=True,
                     environ={"TOLL_HARNESS_BASE_URL": bench.url,
                              "TOLL_HARNESS_AGENT_TOKEN": TOKEN})
    assert bench.registered == []
    assert agent.name == "TestEcho"  # the handle on the agent's own record
    assert agent.token() == TOKEN


def test_init_registered_without_a_token_says_so(tmp_path, bench):
    with pytest.raises(InitError, match="TOLL_HARNESS_AGENT_TOKEN"):
        run_init(tmp_path / "x", bench, registered=True,
                 environ={"TOLL_HARNESS_BASE_URL": bench.url})


def test_init_moves_a_0x_agent_keeping_its_token(tmp_path, bench):
    folder = tmp_path / "old"
    folder.mkdir()
    old = {
        "version": 1,
        "agent": {"name": "OldAgent", "company": "Old Co", "intelligence": "claude",
                  "intelligence_brand": "Claude"},
        "model": {"adapter": "claude_code"},
        "secrets": {"provider": "file", "directory": ".toll-harness/abc/secrets"},
        "storage": {"directory": ".toll-harness/abc"},
        "toll_bench": {"connected": True, "base_url": bench.url,
                       "token_secret": "book_of_houses_agent_token"},
    }
    (folder / "agent.yaml").write_text(yaml.safe_dump(old))
    FileSecretStore(folder / ".toll-harness/abc/secrets").set(TOKEN_SECRET, TOKEN)
    with pytest.raises(ConfigError, match="0.x"):
        load(folder)
    said = []
    agent = run_init(folder, bench, said=said, environ={}, ask=lambda label, default: default,
                     answers={"command": "claude -p --output-format json"})
    assert bench.registered == []
    assert agent.token() == TOKEN
    assert agent.section["company"] == "Old Co" and agent.section["model"] == "Claude"
    assert (folder / "agent.0x.yaml").exists()
    assert load(folder).command == ["claude", "-p", "--output-format", "json"]


def test_connect_acknowledges_the_ping_until_reachable(made):
    agent = made.agent()
    made.bench.reachable = False
    assert connect(agent.bench()) == {"ok": True, "acknowledgements": 1}


def test_doctor_checks_bench_token_command_and_service(made):
    agent = made.agent()
    said = []
    assert doctor(agent, say=said.append, service_status=lambda _: "running")
    assert len(said) == 4 and all(line.startswith("ok") for line in said)
    assert made.runs()[0]["item"]["kind"] == "doctor"
    assert made.bench.answers == []  # nothing is sent to the bench


def test_doctor_says_fail_when_the_command_fails(made):
    agent = made.agent()
    made.mode("fail")
    said = []
    assert not doctor(agent, say=said.append, service_status=lambda _: "running")
    assert any(line.startswith("FAIL") and "exited with 3" in line for line in said)


def test_a_command_must_be_a_list(made):
    agent = made.agent()
    agent.section["command"] = "echo hi"
    agent.save()
    with pytest.raises(ConfigError):
        load(agent.path)


def test_cli_init_reports_a_bench_refusal_plainly(tmp_path, monkeypatch, capsys):
    from toll_harness import cli

    monkeypatch.setenv("TOLL_HARNESS_BASE_URL", "http://127.0.0.1:9")  # nothing listens
    with pytest.raises(SystemExit) as stopped:
        cli.main(["init", str(tmp_path / "a"), "--company", "C", "--model", "M",
                  "--command", f"{sys.executable} -c pass"])
    assert stopped.value.code == 2
    assert "Toll Bench" in capsys.readouterr().err
