"""update: the version door (new name, older name), the decision, and the install seam against
a local wheel in a throwaway venv. Nothing here reaches a package index."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from tests.fakebench import item
from toll_harness.logs import ActivityLog
from toll_harness.updates import (
    RESTART_EXIT,
    check_and_install,
    decide,
    install,
    installed_version,
    pip_command,
)
from toll_harness.watch import Watch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import local_wheel  # noqa: E402


def test_decide():
    assert decide({"current": "1.0.0", "minimum": "1.0.0"}, "1.0.0")["action"] == "none"
    assert decide({"current": "1.0.1", "minimum": "1.0.0"}, "1.0.0")["action"] == "update"
    assert decide({"current": "1.2.0", "minimum": "1.1.0"}, "1.0.0")["action"] == "now"
    assert decide({"current": "1.0.1; rm -rf ~"}, "1.0.0")["action"] == "none"


def test_the_bench_install_sentence_is_never_run(tmp_path):
    command = pip_command("1.0.1", str(tmp_path))
    assert command[-1] == "toll-harness==1.0.1" and "--no-index" in command
    assert not any("touch" in part or ";" in part for part in command)
    with pytest.raises(ValueError):
        pip_command("1.0.1 && touch x")


def test_the_version_door_falls_back_to_the_older_name(made):
    made.bench.version_door = "connector"
    assert made.agent().bench().version()["current"] == "1.0.0"
    made.bench.version_door = "harness"
    assert made.agent().bench().version()["current"] == "1.0.0"


def test_below_the_minimum_updates_at_once_even_with_automatic_updates_off(made):
    agent = made.agent()
    made.bench.version = {"current": "9.0.0", "minimum": "9.0.0"}
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        out = "9.0.0\n" if "-c" in command else ""
        return subprocess.CompletedProcess(command, 0, out, "")

    result = check_and_install(agent, agent.bench(), ActivityLog(agent.data_dir), runner=runner)
    assert result["installed_now"] and calls[0][-1] == "toll-harness==9.0.0"


def test_newer_with_automatic_updates_off_only_says_so(made):
    agent = made.agent()
    made.bench.version = {"current": "1.99.0", "minimum": "1.0.0"}
    result = check_and_install(agent, agent.bench(), ActivityLog(agent.data_dir),
                               runner=lambda *a, **k: pytest.fail("installed"))
    assert result.get("off")


def test_the_watch_restarts_after_an_update(made):
    agent = made.agent()
    made.bench.add(item("it_never"))
    exit_code = Watch(agent, wait=0, spread=0,
                      updater=lambda *a, **k: {"installed_now": True}).run(1)
    assert exit_code == RESTART_EXIT and made.runs() == []


def test_install_from_a_local_wheel_in_a_throwaway_venv(tmp_path):
    wheels = tmp_path / "wheels"
    old = local_wheel.build(wheels, "1.0.0")
    local_wheel.build(wheels, "1.0.1")
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(tmp_path / "venv")
    python = str(tmp_path / "venv" / "bin" / "python")
    subprocess.run([python, "-m", "pip", "install", "--no-index", "--no-deps", "--quiet",
                    str(old)], check=True)
    assert installed_version(python) == "1.0.0"
    worked, line = install("1.0.1", source=str(wheels), python=python)
    assert worked, line
    assert installed_version(python) == "1.0.1"
    clean = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    shown = subprocess.run([str(tmp_path / "venv" / "bin" / "toll-harness"), "--version"],
                           capture_output=True, text=True, check=True, env=clean)
    assert shown.stdout.strip() == "toll-harness 1.0.1"
