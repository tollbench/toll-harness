"""The always-on service: one unit shape per OS, the watch command, no secret in the unit."""

from __future__ import annotations

import plistlib
import subprocess

from toll_harness import service


def fake_runner(calls):
    def run(command, **kwargs):
        calls.append(command)
        out = "active\n" if command[-2:-1] == ["is-active"] else ""
        return subprocess.CompletedProcess(command, 0, out, "")
    return run


def test_systemd_unit_runs_the_watch_and_holds_no_secret(made, tmp_path, monkeypatch):
    monkeypatch.setenv("TOLL_HARNESS_AGENT_TOKEN", "do-not-copy")
    monkeypatch.setenv("TOLL_HARNESS_BASE_URL", "https://app.example")
    agent = made.agent()
    calls = []
    report = service.install(agent, runner=fake_runner(calls), platform="linux",
                             directory=tmp_path / "units")
    text = (tmp_path / "units" / "toll-harness-testecho.service").read_text()
    assert f'"watch" "{agent.path}"' in text and "Restart=on-failure" in text
    assert "do-not-copy" not in text and "TOLL_HARNESS_BASE_URL=https://app.example" in text
    assert ["systemctl", "--user", "restart", "toll-harness-testecho.service"] in calls
    assert report["service"] == "toll-harness-testecho.service"


def test_launchd_plist(made, tmp_path):
    agent = made.agent()
    calls = []
    service.install(agent, runner=fake_runner(calls), platform="darwin",
                    directory=tmp_path / "agents")
    plist = plistlib.loads((tmp_path / "agents" / "com.toll-harness.testecho.plist").read_bytes())
    assert plist["ProgramArguments"][-2:] == ["watch", str(agent.path)]
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
