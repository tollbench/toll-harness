"""install-service: the market watch as a background service, one command per OS.

No real systemctl, launchctl or loginctl runs here: every call goes to a fake
runner, and HOME points at a temporary directory so the units land there.
"""

import functools
import plistlib
import subprocess
from pathlib import Path

import pytest
import yaml

from toll_harness import cli, service, updates
from toll_harness.onboarding import READY, InitAnswers, create_configuration

PYTHON = "/opt/agent venv/bin/python"


def _agent(directory, name="Tilly", *, ready=True):
    answers = InitAnswers(
        agent_name=name,
        intelligence="Claude",
        model_id="claude-test",
        company="Toll Bench",
        mode="Autonomous",
        aws_profile=None,
        aws_region="us-west-2",
        connect_toll_bench=True,
        use_book_of_houses_email=False,
        company_url="https://tollbench.com",
        responsible_legal_name="Toll Bench",
        responsible_jurisdiction="Oregon",
        verification_recipient="lab@example.com",
        model_adapter="claude_code",
    )
    path = create_configuration(directory, answers)
    config = yaml.safe_load(path.read_text())
    config["toll_bench"]["status"] = READY if ready else "WAITING_FOR_COMPANY_VERIFICATION"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path.resolve()


class FakeManager:
    """Answers like systemctl / launchctl / loginctl, and remembers every call."""

    def __init__(self, *, linger="no", active=True, bootstrap_code=0):
        self.calls = []
        self.linger = linger
        self.active = active
        self.bootstrap_code = bootstrap_code

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        code, out = 0, ""
        if command[0] == "loginctl":
            if self.linger is None:
                raise FileNotFoundError("loginctl")
            out = f"{self.linger}\n"
        elif command[:3] == ["systemctl", "--user", "is-active"]:
            code, out = (0, "active\n") if self.active else (3, "inactive\n")
        elif command[:3] == ["systemctl", "--user", "is-enabled"]:
            code, out = (0, "enabled\n") if self.active else (1, "disabled\n")
        elif command[:2] == ["launchctl", "bootstrap"]:
            code = self.bootstrap_code
        elif command[:2] == ["launchctl", "print"]:
            code, out = (0, "state = running\n") if self.active else (113, "")
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")

    def verbs(self, tool):
        return [call[2] if tool == "systemctl" else call[1] for call in self.calls
                if call[0] == tool]


def fake_user():
    return service._user()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


ENVIRON = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
    "TOLL_HARNESS_LOG_LEVEL": "DEBUG",
    "TOLL_HARNESS_CONTEXT_BUDGET_TOKENS": "90000",
    "TOLL_HARNESS_AGENT_TOKEN": "tok-should-never-land",
    "ANTHROPIC_API_KEY": "sk-should-never-land",
    "UNRELATED": "not-passed",
}


# -- the unit text --------------------------------------------------------------------


def test_linux_unit_runs_this_interpreter_restarts_on_failure_and_keeps_secrets_out(
    tmp_path, home
):
    path = _agent(tmp_path / "agent files" / "100% Tilly")
    fake = FakeManager()

    report = service.install_service(
        path, runner=fake, platform="linux", environ=ENVIRON, python=PYTHON
    )

    unit_path = home / ".config/systemd/user/toll-harness-tilly.service"
    assert report["unit"] == str(unit_path)
    unit = unit_path.read_text()
    lines = unit.splitlines()
    escaped_dir = str(path.parent).replace("%", "%%")
    assert f"WorkingDirectory={escaped_dir}" in lines
    assert (
        f'ExecStart="{PYTHON}" "-m" "toll_harness.cli" "market" "watch" "{escaped_dir}/agent.yaml"'
        in lines
    )
    assert "Restart=on-failure" in lines
    assert "RestartSec=10" in lines
    assert 'Environment="PYTHONUNBUFFERED=1"' in lines
    assert 'Environment="PATH=/opt/homebrew/bin:/usr/bin:/bin"' in lines
    assert 'Environment="TOLL_HARNESS_LOG_LEVEL=DEBUG"' in lines
    assert 'Environment="TOLL_HARNESS_CONTEXT_BUDGET_TOKENS=90000"' in lines
    assert "should-never-land" not in unit and "UNRELATED" not in unit
    assert report["withheld"] == ["ANTHROPIC_API_KEY", "TOLL_HARNESS_AGENT_TOKEN"]
    log = f"{escaped_dir}/.toll-harness/"
    assert any(line.startswith(f"StandardOutput=append:{log}") for line in lines)
    assert any(line.startswith(f"StandardError=append:{log}") for line in lines)
    assert lines[-1] == "WantedBy=default.target"
    assert fake.verbs("systemctl") == ["daemon-reload", "enable", "restart", "is-active"]
    assert report["active"] is True
    assert report["restart_policy"] == "on-failure"
    assert "systemctl --user status toll-harness-tilly.service" in report["check"]
    assert "journalctl --user -u toll-harness-tilly.service -f" in report["check"]


def test_the_unit_reads_back_as_watching_this_agent(tmp_path, home):
    path = _agent(tmp_path / "agent files" / "100% Tilly")
    service.install_service(path, runner=FakeManager(), platform="linux", environ={})
    unit = home / ".config/systemd/user/toll-harness-tilly.service"
    assert service.watched_config(unit, service.SYSTEMD) == path


def test_macos_plist_keeps_alive_on_failure_and_bootstraps(tmp_path, home):
    path = _agent(tmp_path / "Tilly")
    fake = FakeManager()

    report = service.install_service(
        path, runner=fake, platform="darwin", environ=ENVIRON, python=PYTHON
    )

    plist_path = home / "Library/LaunchAgents/com.toll-harness.tilly.plist"
    assert report["unit"] == str(plist_path)
    assert report["service"] == "com.toll-harness.tilly"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == "com.toll-harness.tilly"
    assert data["ProgramArguments"] == [
        PYTHON, "-m", "toll_harness.cli", "market", "watch", str(path)
    ]
    assert data["WorkingDirectory"] == str(path.parent)
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["ThrottleInterval"] == 10
    assert data["StandardOutPath"] == data["StandardErrorPath"]
    assert data["StandardOutPath"].endswith("/market.log")
    assert data["StandardOutPath"].startswith(f"{path.parent}/.toll-harness/")
    assert data["EnvironmentVariables"]["PATH"] == ENVIRON["PATH"]
    assert "TOLL_HARNESS_AGENT_TOKEN" not in data["EnvironmentVariables"]
    verbs = fake.verbs("launchctl")
    assert verbs.index("bootout") < verbs.index("bootstrap") < verbs.index("enable")
    assert verbs.index("enable") < verbs.index("kickstart") < verbs.index("print")
    assert "load" not in verbs
    assert report["active"] is True
    assert report["check"][0].startswith("launchctl print gui/")
    assert report["check"][0].endswith("/com.toll-harness.tilly")


def test_macos_without_bootstrap_falls_back_to_load_w(tmp_path, home):
    path = _agent(tmp_path / "Tilly")
    fake = FakeManager(bootstrap_code=5)

    report = service.install_service(path, runner=fake, platform="darwin", environ={})

    plist = str(home / "Library/LaunchAgents/com.toll-harness.tilly.plist")
    assert ["launchctl", "load", "-w", plist] in fake.calls
    assert report["commands"][-1] == f"launchctl load -w {plist}"


# -- dry run, idempotence, uninstall ----------------------------------------------------


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_dry_run_writes_nothing_and_runs_nothing(tmp_path, home, platform):
    path = _agent(tmp_path / "Tilly")

    def refuse(command, **kwargs):
        raise AssertionError(f"a dry run ran {command}")

    report = service.install_service(
        path, dry_run=True, runner=refuse, platform=platform, environ=ENVIRON
    )

    assert not (home / ".config").exists() and not (home / "Library").exists()
    assert not Path(report["log"]).exists()
    assert report["action"] == "dry-run" and report["active"] is False
    assert "market" in report["unit_text"] and "watch" in report["unit_text"]
    assert report["commands"]
    removal = service.uninstall_service(path, dry_run=True, runner=refuse, platform=platform)
    assert removal["commands"] and removal["removed"] == []


def test_install_twice_rewrites_the_same_unit_and_restarts_it(tmp_path, home):
    path = _agent(tmp_path / "Tilly")
    fake = FakeManager()
    service.install_service(path, runner=fake, platform="linux", environ=ENVIRON, python=PYTHON)
    unit = home / ".config/systemd/user/toll-harness-tilly.service"
    first = unit.read_text()

    report = service.install_service(
        path, runner=fake, platform="linux", environ=ENVIRON, python=PYTHON
    )

    assert unit.read_text() == first
    assert fake.verbs("systemctl").count("restart") == 2
    assert report["replaced"] == []
    assert sorted(p.name for p in unit.parent.iterdir()) == ["toll-harness-tilly.service"]


@pytest.mark.parametrize(
    ("platform", "relative"),
    [
        ("linux", ".config/systemd/user/toll-harness-tilly.service"),
        ("darwin", "Library/LaunchAgents/com.toll-harness.tilly.plist"),
    ],
)
def test_uninstall_stops_disables_and_removes(tmp_path, home, platform, relative):
    path = _agent(tmp_path / "Tilly")
    fake = FakeManager()
    service.install_service(path, runner=fake, platform=platform, environ={})
    assert (home / relative).exists()
    fake.calls.clear()

    report = service.uninstall_service(path, runner=fake, platform=platform)

    assert not (home / relative).exists()
    assert report["removed"] == [str(home / relative)]
    if platform == "linux":
        assert fake.calls == [
            ["systemctl", "--user", "disable", "--now", "toll-harness-tilly.service"],
            ["systemctl", "--user", "daemon-reload"],
        ]
    else:
        assert fake.calls[0][:2] == ["launchctl", "bootout"]
    again = service.uninstall_service(path, runner=fake, platform=platform)
    assert again["removed"] == []


def test_a_new_name_replaces_the_old_one_and_a_taken_name_is_refused(tmp_path, home):
    path = _agent(tmp_path / "Tilly")
    fake = FakeManager()
    units = home / ".config/systemd/user"
    service.install_service(path, runner=fake, platform="linux", environ={})

    report = service.install_service(path, name="Muse", runner=fake, platform="linux", environ={})

    assert report["service"] == "toll-harness-muse.service"
    assert report["replaced"] == [str(units / "toll-harness-tilly.service")]
    assert ["systemctl", "--user", "disable", "--now", "toll-harness-tilly.service"] in fake.calls
    assert sorted(p.name for p in units.iterdir()) == ["toll-harness-muse.service"]
    status = service.service_status(path, runner=fake, platform="linux")
    assert status["service"] == "toll-harness-muse.service" and status["state"] == "running"

    other = _agent(tmp_path / "Other", name="Muse")
    with pytest.raises(service.ServiceError, match="already runs another agent"):
        service.install_service(other, runner=fake, platform="linux", environ={})
    assert service.watched_config(units / "toll-harness-muse.service", "systemd") == path

    # The other Muse never sees Tilly's service as its own, and never removes it.
    fake.calls.clear()
    assert service.service_status(other, runner=fake, platform="linux")["state"] == (
        "not installed"
    )
    assert fake.calls == [["loginctl", "show-user", fake_user(), "--property=Linger", "--value"]]
    removal = service.uninstall_service(other, runner=fake, platform="linux")
    assert removal["removed"] == []
    assert ["systemctl", "--user", "disable", "--now", "toll-harness-muse.service"] not in (
        fake.calls
    )
    assert (units / "toll-harness-muse.service").exists()


def test_an_agent_that_is_not_ready_is_refused_but_can_be_dry_run(tmp_path, home):
    path = _agent(tmp_path / "Tilly", ready=False)
    with pytest.raises(service.ServiceError, match="not a connected, READY"):
        service.install_service(path, runner=FakeManager(), platform="linux", environ={})
    report = service.install_service(path, dry_run=True, platform="linux", environ={})
    assert "not a connected, READY" in report["notes"][0]


def test_a_directory_means_its_agent_yaml_and_a_missing_one_is_a_hole(tmp_path, home):
    path = _agent(tmp_path / "Tilly")
    report = service.install_service(
        path.parent, dry_run=True, platform="linux", environ={}
    )
    assert report["config"] == str(path)
    with pytest.raises(FileNotFoundError, match="no agent.yaml"):
        service.install_service(tmp_path / "nowhere", dry_run=True, platform="linux")


def test_linger_falls_back_to_the_linger_directory(tmp_path, monkeypatch):
    linger = tmp_path / "linger"
    linger.mkdir()
    (linger / "tilly").touch()
    monkeypatch.setattr(service, "LINGER_DIRECTORY", linger)
    assert service.linger_enabled(FakeManager(linger=None), user="tilly") is True
    assert service.linger_enabled(None, user="someone-else") is False
    assert service.linger_enabled(FakeManager(linger="yes"), user="x") is True
    monkeypatch.setattr(service, "LINGER_DIRECTORY", tmp_path / "absent")
    assert service.linger_enabled(None, user="tilly") is None


# -- the command line ---------------------------------------------------------------------


def _cli(monkeypatch, fake, platform, argv):
    for name in ("install_service", "uninstall_service", "service_status"):
        original = getattr(service, name)
        keywords = {"runner": fake, "platform": platform}
        if name == "install_service":
            keywords["environ"] = {}
        monkeypatch.setattr(cli, name, functools.partial(original, **keywords))
    arguments = cli.build_parser().parse_args(argv)
    return arguments.handler(arguments)


def test_cli_install_tells_the_person_to_turn_linger_on(tmp_path, home, monkeypatch, capsys):
    path = _agent(tmp_path / "Tilly")

    code = _cli(monkeypatch, FakeManager(linger="no"), "linux", ["install-service", str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Installed toll-harness-tilly.service, a systemd user service: running." in out
    assert "Linger is off" in out and "loginctl enable-linger" in out
    assert "systemctl --user status toll-harness-tilly.service" in out
    assert "journalctl --user -u toll-harness-tilly.service" in out
    assert "Ctrl-C" in out


def test_cli_install_says_nothing_about_linger_when_it_is_on(
    tmp_path, home, monkeypatch, capsys
):
    path = _agent(tmp_path / "Tilly")
    _cli(monkeypatch, FakeManager(linger="yes"), "linux", ["install-service", str(path)])
    assert "Linger" not in capsys.readouterr().out


def test_cli_dry_run_prints_the_unit_and_the_commands(tmp_path, home, monkeypatch, capsys):
    path = _agent(tmp_path / "Tilly")

    def refuse(command, **kwargs):
        raise AssertionError(f"a dry run ran {command}")

    code = _cli(monkeypatch, refuse, "linux", ["install-service", str(path), "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("Dry run: nothing written, nothing run.")
    assert "Restart=on-failure" in out
    assert "  systemctl --user daemon-reload" in out
    assert not (home / ".config").exists()


def test_cli_on_windows_prints_the_equivalent_and_exits_2(tmp_path, home, monkeypatch, capsys):
    path = _agent(tmp_path / "Tilly")

    def refuse(command, **kwargs):
        raise AssertionError(f"Windows ran {command}")

    code = _cli(monkeypatch, refuse, "win32", ["install-service", str(path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "schtasks /Create" in captured.err and "nssm install toll-harness-tilly" in captured.err
    assert "market watch" in captured.err
    assert list(home.iterdir()) == []


def test_cli_uninstall_and_service_status_exit_codes(tmp_path, home, monkeypatch, capsys):
    path = _agent(tmp_path / "Tilly")
    fake = FakeManager(active=False)

    assert _cli(monkeypatch, fake, "linux", ["service-status", str(path)]) == 1
    out = capsys.readouterr().out
    assert "toll-harness-tilly.service: not installed" in out
    assert "toll-harness install-service" in out

    fake.active = True
    assert _cli(monkeypatch, fake, "linux", ["install-service", str(path)]) == 0
    assert _cli(monkeypatch, fake, "linux", ["service-status", str(path)]) == 0
    assert "toll-harness-tilly.service: running" in capsys.readouterr().out

    fake.active = False
    assert _cli(monkeypatch, fake, "linux", ["service-status", str(path)]) == 1
    assert "installed but not running" in capsys.readouterr().out

    assert _cli(monkeypatch, fake, "linux", ["install-service", str(path), "--uninstall"]) == 0
    assert "Removed toll-harness-tilly.service" in capsys.readouterr().out
    assert _cli(monkeypatch, fake, "linux", ["service-status", str(path)]) == 1
    assert _cli(monkeypatch, fake, "win32", ["service-status", str(path)]) == 2


# -- the update check says it --------------------------------------------------------------


def test_the_update_check_says_the_watch_is_not_a_service_once_per_change(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TOLL_HARNESS_UPDATE_CHECK", raising=False)
    current = {
        "ok": True, "installed": "0.56.0", "latest": "0.56.0", "minimum": None,
        "behind": False, "below_minimum": False, "install_command": "pip install -U toll-harness",
    }
    monkeypatch.setattr(updates, "_harness_check", lambda protocol: dict(current))

    class Bench:
        def protocol(self):
            return {"protocol_version": "4", "contract_version": "4.1.2", "rules_version_hash": "r"}

    path = _agent(tmp_path / "Tilly")
    state = {"state": "not installed"}
    monkeypatch.setattr(updates, "_service_check", lambda p, config: dict(state))

    first = updates.check_for_updates(path, Bench(), force=True)
    again = updates.check_for_updates(path, Bench(), force=True)
    state["state"] = "running"
    running = updates.check_for_updates(path, Bench(), force=True)

    line = f"toll-harness install-service {path}"
    assert first["new"] is True and any(line in notice for notice in first["notices"])
    assert again["new"] is False and any(line in notice for notice in again["notices"])
    assert running["notices"] == [] and running["new"] is False


def test_doctor_reports_the_service_and_says_when_the_watch_is_not_one(
    tmp_path, monkeypatch, capsys
):
    path = _agent(tmp_path / "Tilly")
    config = yaml.safe_load(path.read_text())
    config["toll_bench"]["connected"] = True
    path.write_text(yaml.safe_dump(config, sort_keys=False))

    def no_aws(**kwargs):
        raise RuntimeError("no AWS here")

    monkeypatch.setattr(cli, "BedrockProbe", no_aws)
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda config_path: {})
    monkeypatch.setattr(
        cli, "market_worker_status", lambda config_path: {"active": False, "enabled": False}
    )
    monkeypatch.setattr(
        cli,
        "service_status",
        lambda config_path: {"state": "not installed", "config": str(path)},
    )
    arguments = cli.build_parser().parse_args(["doctor", str(path)])

    arguments.handler(arguments)

    captured = capsys.readouterr()
    assert '"state": "not installed"' in captured.out
    assert f"toll-harness install-service {path}" in captured.err
