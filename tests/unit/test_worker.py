import subprocess
import sys
from pathlib import Path

import yaml

from toll_harness.onboarding import READY, InitAnswers, create_configuration
from toll_harness.worker import install_market_worker, market_worker_status


def _connected_answers():
    return InitAnswers(
        agent_name="Oakleaf",
        intelligence="Amazon Nova",
        model_id="amazon.nova-pro-v1:0",
        company="House of Resolve",
        mode="Autonomous",
        aws_profile="example-bedrock-profile",
        aws_region="us-west-2",
        connect_toll_bench=True,
        use_book_of_houses_email=True,
        company_url="https://bookofhouses.com/house/resolve",
        responsible_legal_name="House of Resolve",
        responsible_jurisdiction="US-OR",
        verification_recipient="houseofresolve@bookofhouses.com",
    )


def test_install_market_worker_writes_restartable_isolated_unit(tmp_path):
    config_path = create_configuration(tmp_path / "agent files" / "oakleaf", _connected_answers())
    config = yaml.safe_load(config_path.read_text())
    config["toll_bench"]["status"] = READY
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        output = "active\n" if command[-2:] == ["is-active", "toll-harness-oakleaf.service"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = install_market_worker(
        config_path,
        unit_directory=tmp_path / "units",
        runner=runner,
    )

    unit = (tmp_path / "units" / "toll-harness-oakleaf.service").read_text()
    escaped_parent = str(config_path.parent).replace(" ", r"\x20")
    assert result["active"] is True
    assert "Restart=always" in unit
    assert "RestartSec=2" in unit
    assert str(config_path.resolve()) in unit
    assert f"WorkingDirectory={escaped_parent}" in unit
    assert f"append:{escaped_parent}" in unit
    assert str(Path(sys.executable).absolute()) in unit
    assert "toll_harness.cli\" \"market\" \"watch" in unit
    assert calls[0] == ["systemctl", "--user", "daemon-reload"]
    assert calls[1] == [
        "systemctl",
        "--user",
        "enable",
        "--now",
        "toll-harness-oakleaf.service",
    ]


def test_market_worker_status_reports_systemd_truth(tmp_path):
    config_path = create_configuration(tmp_path / "oakleaf", _connected_answers())

    def runner(command, **kwargs):
        value = "active\n" if "is-active" in command else "enabled\n"
        return subprocess.CompletedProcess(command, 0, stdout=value, stderr="")

    assert market_worker_status(config_path, runner=runner) == {
        "service": "toll-harness-oakleaf.service",
        "active": True,
        "enabled": True,
    }


def test_install_market_worker_darwin_writes_launchd_plist(tmp_path):
    import plistlib
    from pathlib import Path

    config_path = create_configuration(
        tmp_path / "agent files" / "oakleaf", _connected_answers()
    )
    config = yaml.safe_load(config_path.read_text())
    config["toll_bench"]["status"] = READY
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        output = "state = running" if command[:2] == ["launchctl", "print"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = install_market_worker(
        config_path,
        unit_directory=tmp_path / "LaunchAgents",
        runner=runner,
        platform="darwin",
    )

    plist_path = tmp_path / "LaunchAgents" / "com.toll-harness.oakleaf.plist"
    assert result["service"] == "com.toll-harness.oakleaf"
    assert result["service_manager"] == "launchd"
    assert result["restart_policy"] == "keepalive"
    assert result["active"] is True
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == "com.toll-harness.oakleaf"
    assert data["ProgramArguments"][1:] == [
        "-m", "toll_harness.cli", "market", "watch", str(Path(config_path).resolve())
    ]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] is True
    assert data["StandardOutPath"].endswith("market.log")
    # bootstrapped, enabled, kicked -- and idempotent via a prior bootout.
    verbs = [c[1] for c in commands if c[0] == "launchctl"]
    assert verbs.index("bootout") < verbs.index("bootstrap") < verbs.index("enable")
    assert "kickstart" in verbs


def test_market_worker_status_darwin_reports_launchd_truth(tmp_path):
    config_path = create_configuration(tmp_path / "oakleaf", _connected_answers())
    config = yaml.safe_load(config_path.read_text())
    config["toll_bench"]["status"] = READY
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    def running(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="state = running", stderr=""
        )

    def missing(command, **kwargs):
        return subprocess.CompletedProcess(command, 113, stdout="", stderr="")

    up = market_worker_status(config_path, runner=running, platform="darwin")
    assert up == {
        "service": "com.toll-harness.oakleaf",
        "active": True,
        "enabled": True,
        "service_manager": "launchd",
    }
    down = market_worker_status(config_path, runner=missing, platform="darwin")
    assert down["active"] is False and down["enabled"] is False
