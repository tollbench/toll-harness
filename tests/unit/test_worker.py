import subprocess
import sys
from pathlib import Path

import yaml

from toll_harness.onboarding import READY, InitAnswers, create_configuration
from toll_harness.worker import install_market_worker, market_worker_status


def _connected_answers():
    return InitAnswers(
        agent_name="Tanjiro",
        intelligence="Amazon Nova",
        model_id="amazon.nova-pro-v1:0",
        company="House of Resolve",
        mode="Autonomous",
        aws_profile="toll-harness-builder",
        aws_region="us-west-2",
        connect_toll_bench=True,
        use_book_of_houses_email=True,
        company_url="https://bookofhouses.com/house/resolve",
        responsible_legal_name="House of Resolve",
        responsible_jurisdiction="US-OR",
        verification_recipient="houseofresolve@bookofhouses.com",
    )


def test_install_market_worker_writes_restartable_isolated_unit(tmp_path):
    config_path = create_configuration(tmp_path / "agent files" / "tanjiro", _connected_answers())
    config = yaml.safe_load(config_path.read_text())
    config["toll_bench"]["status"] = READY
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        output = "active\n" if command[-2:] == ["is-active", "toll-harness-tanjiro.service"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    result = install_market_worker(
        config_path,
        unit_directory=tmp_path / "units",
        runner=runner,
    )

    unit = (tmp_path / "units" / "toll-harness-tanjiro.service").read_text()
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
        "toll-harness-tanjiro.service",
    ]


def test_market_worker_status_reports_systemd_truth(tmp_path):
    config_path = create_configuration(tmp_path / "tanjiro", _connected_answers())

    def runner(command, **kwargs):
        value = "active\n" if "is-active" in command else "enabled\n"
        return subprocess.CompletedProcess(command, 0, stdout=value, stderr="")

    assert market_worker_status(config_path, runner=runner) == {
        "service": "toll-harness-tanjiro.service",
        "active": True,
        "enabled": True,
    }
