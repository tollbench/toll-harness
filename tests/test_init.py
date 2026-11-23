"""init (only what nobody said yet, register or connect, token in the store, the service, /me
printed), the 0.x move, connect and doctor. The agent picked by name and the sample wake have
their own tests in test_known.py; here no known agent is on the computer and the wake passes."""

from __future__ import annotations

import stat
import sys

import pytest
import yaml

from tests.fakebench import TOKEN, FakeBench
from toll_harness.agent import TOKEN_SECRET, ConfigError, load
from toll_harness.handoff import ANSWERED, HandOff
from toll_harness.known import wake_argv
from toll_harness.onboarding import OWN_TOOLS_LINE, InitError, connect, doctor, init
from toll_harness.secrets import FileSecretStore


def answers(command="my-agent --from-toll-bench", **extra):
    return {"company": "Test Owner Co", "model": "Echo", "command": command, **extra}


def run_init(folder, bench: FakeBench, *, said=None, installed=None, **kwargs):
    said = [] if said is None else said
    installed = [] if installed is None else installed
    kwargs.setdefault("answers", answers())
    kwargs.setdefault("environ", {"TOLL_HARNESS_BASE_URL": bench.url})
    kwargs.setdefault("which", lambda program: None)  # no known agent on this computer
    kwargs.setdefault("wake_check", lambda command, path, limit: HandOff(ANSWERED, {"ok": True}))
    return init(folder, say=said.append,
                install_service=lambda agent: installed.append(agent) or
                {"service": "toll-harness-testecho.service", "running": True},
                **kwargs)


def test_a_new_agent_is_asked_five_things_registers_and_installs_the_service(tmp_path, bench):
    asked, said, installed = [], [], []

    replies = {"Company": "Test Owner Co", "Model's": "Echo",
               "The": "my-agent --flag 'two words'", "Contact": "owner@example.com",
               "Location": "US-OR"}

    def ask(label, default):
        asked.append(label)
        return replies[label.split(" ")[0]]

    agent = run_init(tmp_path / "TestEcho", bench, said=said, installed=installed,
                     answers={}, ask=ask)
    assert [label.split(" ")[0] for label in asked] == [
        "Company", "The", "Model's", "Contact", "Location"]
    assert asked[-1] == "Location (country, and state if in the US)"
    assert not any("urisdiction" in label for label in asked)
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
    # `jurisdiction` is what the bench requires today; `location` rides beside it.
    assert registered["responsible_party"] == {
        "legal_name": "Test Owner Co", "contact_ref": "owner@example.com",
        "jurisdiction": "US-OR", "location": "US-OR"}
    assert [entry.path for entry in installed] == [agent.path]
    text = "\n".join(said)
    for fact in ("A-0999", "Test Owner Co", "testecho@agents.example", "accepted",
                 "looking_for all"):
        assert fact in text
    assert TOKEN not in text
    assert "owner@example.com" not in text  # the contact email is never said back
    assert "owner@example.com" not in agent.path.read_text()
    log = agent.data_dir / "harness.log"
    assert not log.exists() or "owner@example.com" not in log.read_text()


def test_init_registered_reads_the_token_from_the_environment(tmp_path, bench):
    def ask(label, default):  # never contact email or location; company is on the record
        assert label.split(" ")[0] in ("Model's", "The"), label
        return default or "x"

    agent = run_init(tmp_path / "x", bench, registered=True, ask=ask, answers={},
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
    agent = run_init(folder, bench, said=said, environ={}, answers={},
                     ask=lambda label, default: pytest.fail(f"asked {label}"))
    assert bench.registered == []
    assert agent.token() == TOKEN
    assert agent.section["company"] == "Old Co" and agent.section["model"] == "Claude"
    assert agent.section["wake"] == {"agent": "claude"} and "command" not in agent.section
    assert (folder / "agent.0x.yaml").exists()
    assert load(folder).command == wake_argv({"agent": "claude"})
    assert any("as it did under 0.x: Claude Code (claude)" in line for line in said)
    assert OWN_TOOLS_LINE not in said  # a moved agent keeps what it had: nothing new to say


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
                  "--command", f"{sys.executable} -c pass", "--contact-email", "o@example.com",
                  "--location", "US-OR"])
    assert stopped.value.code == 2
    assert "Toll Bench" in capsys.readouterr().err


def test_location_flag_and_the_old_jurisdiction_name_reach_the_registration(tmp_path, bench,
                                                                            monkeypatch, capsys):
    from toll_harness import cli, onboarding

    seen = []
    monkeypatch.setattr(onboarding, "init", lambda directory, **kw: seen.append(kw["answers"]))
    for flag in ("--location", "--jurisdiction"):
        with pytest.raises(SystemExit):
            cli.main(["init", str(tmp_path), "--company", "C", "--model", "M", "--command",
                      "a", "--contact-email", "o@example.com", flag, "US-WA"])
    assert [answers["location"] for answers in seen] == ["US-WA", "US-WA"]
    with pytest.raises(SystemExit):
        cli.main(["init", "--help"])
    shown = capsys.readouterr().out
    assert "--location" in shown and "urisdiction" not in shown


def test_flags_answer_a_new_registration_without_questions(tmp_path, bench):
    agent = run_init(tmp_path / "Flagged", bench, ask=lambda *a: pytest.fail("asked"),
                     answers=answers(contact_email="o@example.com", location="CA"))
    assert agent.token() == TOKEN
    party = bench.registered[0]["responsible_party"]
    assert (party["jurisdiction"], party["location"]) == ("CA", "CA")


def test_the_version_is_written_the_same_in_both_places():
    import re
    from pathlib import Path

    from toll_harness import __version__

    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == __version__


def test_what_the_agent_has_leaves_out_empty_settings():
    from toll_harness.onboarding import describe_me

    lines = describe_me({"agent": {"id": "A-1"}, "settings": {
        "logging": False, "focus": "", "tools": [], "skip_crowded_at": None,
        "looking_for": "all"}})
    assert lines[-1] == "Settings: logging False, looking_for all"


# -- the Agent Terms at setup: the owner is at the keyboard ---------------------------------
TERMS_LINE = "Toll Bench Agent Terms of Cooperation, version 2026-11-12, are not accepted"


def new_agent(**extra):
    return answers(contact_email="o@example.com", location="US-OR", **extra)


def test_the_owners_yes_at_init_accepts_the_terms_once_with_the_benchs_version(tmp_path,
                                                                              bench):
    bench.terms_accepted = False
    asked, said = [], []

    def ask(label, default):
        asked.append((label, default))
        return "yes"

    run_init(tmp_path / "Yes", bench, said=said, ask=ask, answers=new_agent(), interactive=True)
    assert asked == [("Do you, the owner, accept the Toll Bench Agent Terms of Cooperation, "
                      "version 2026-11-12? (yes or no)", "no")]
    assert bench.terms_accepts == [{"version": "2026-11-12",
                                    "how": "the owner said yes at the keyboard during "
                                           "toll-harness init"}]
    text = "\n".join(said)
    assert TERMS_LINE in text and f"Read them: {bench.url}/api/bench/terms" in text
    assert "Accepted the Toll Bench Agent Terms of Cooperation, version 2026-11-12" in text
    assert "Terms:    version 2026-11-12, accepted" in text  # /me read again after


@pytest.mark.parametrize("reply", ["no", "", "n", "maybe"])
def test_anything_but_yes_accepts_nothing(tmp_path, bench, reply):
    bench.terms_accepted = False
    said = []
    run_init(tmp_path / "No", bench, said=said, ask=lambda label, default: reply,
             answers=new_agent(), interactive=True)
    assert bench.terms_accepts == []
    assert 'accept them yourself on Toll Bench under "Your agents"' in "\n".join(said)


def test_with_no_one_to_ask_nothing_is_accepted_and_nothing_asked(tmp_path, bench):
    bench.terms_accepted = False
    said = []
    run_init(tmp_path / "Script", bench, said=said, answers=new_agent(),
             ask=lambda *a: pytest.fail("asked"))
    assert bench.terms_accepts == []
    text = "\n".join(said)
    assert TERMS_LINE in text and "Your agents" in text and "NOT accepted yet" in text


def test_the_accept_terms_flag_is_the_owners_yes_without_a_question(tmp_path, bench):
    bench.terms_accepted = False
    run_init(tmp_path / "Flag", bench, answers=new_agent(accept_terms=True),
             ask=lambda *a: pytest.fail("asked"))
    assert bench.terms_accepts == [{"version": "2026-11-12",
                                    "how": "the owner gave --accept-terms to toll-harness init"}]


def test_registered_agent_with_terms_waiting_is_asked_too(tmp_path, bench):
    bench.terms_accepted = False
    run_init(tmp_path / "Reg", bench, registered=True, interactive=True,
             answers=answers(), ask=lambda label, default: "yes",
             environ={"TOLL_HARNESS_BASE_URL": bench.url, "TOLL_HARNESS_AGENT_TOKEN": TOKEN})
    assert [body["version"] for body in bench.terms_accepts] == ["2026-11-12"]


def test_terms_already_accepted_are_not_asked_or_sent(tmp_path, bench):
    run_init(tmp_path / "Done", bench, answers=new_agent(accept_terms=True), interactive=True,
             ask=lambda *a: pytest.fail("asked"))
    assert bench.terms_accepts == []


def test_a_refused_acceptance_never_fails_init(tmp_path, bench):
    bench.terms_accepted = False
    bench.refuse_terms = True
    said = []
    agent = run_init(tmp_path / "Refused", bench, said=said,
                     answers=new_agent(accept_terms=True))
    assert agent.token() == TOKEN and len(bench.terms_accepts) == 1
    line = next(line for line in said if line.startswith("Toll Bench did not take"))
    assert "422 terms_version_mismatch" in line and "Your agents" in line
    assert "Terms:    version 2026-11-12, NOT accepted yet" in "\n".join(said)


def test_the_accept_terms_flag_reaches_init_and_says_it_is_the_owners_yes(tmp_path,
                                                                         monkeypatch, capsys):
    from toll_harness import cli, onboarding

    seen = []
    monkeypatch.setattr(onboarding, "init", lambda directory, **kw: seen.append(kw))
    for extra in ([], ["--accept-terms"]):
        with pytest.raises(SystemExit):
            cli.main(["init", str(tmp_path), "--command", "a", *extra])
    assert [kw["answers"]["accept_terms"] for kw in seen] == [False, True]
    assert all(kw["interactive"] is False for kw in seen)  # pytest's stdin is no terminal
    with pytest.raises(SystemExit):
        cli.main(["init", "--help"])
    shown = " ".join(capsys.readouterr().out.split())
    assert "--accept-terms" in shown and "your own yes, as the agent's owner" in shown
