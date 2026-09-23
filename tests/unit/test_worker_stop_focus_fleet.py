"""The agent-side pieces an operator tool (toll-lab) leans on: stopping a
worker, the standing direction (focus.md) read on every open-want scan, and
fleet registration that never dies on a duplicate name."""

import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from toll_harness import cli
from toll_harness.fleet import FleetStore
from toll_harness.onboarding import READY, InitAnswers, create_configuration
from toll_harness.toll_bench.draft import DraftLoop
from toll_harness.worker import (
    FOCUS_LIMIT,
    focus_path,
    market_worker_log_path,
    read_focus,
    stop_market_worker,
)


def _agent(directory, name="Rick"):
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
    config["toll_bench"]["status"] = READY
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path.resolve()


def _data_dir(path):
    return (path.parent / yaml.safe_load(path.read_text())["storage"]["directory"]).resolve()


# -- worker stop ---------------------------------------------------------------


def test_stop_market_worker_on_systemd_disables_and_removes_the_unit(tmp_path):
    path = _agent(tmp_path / "Rick")
    units = tmp_path / "units"
    units.mkdir()
    (units / "toll-harness-rick.service").write_text("[Unit]\n")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        out, code = ("inactive\n", 3) if command[2] == "is-active" else ("", 0)
        return subprocess.CompletedProcess(command, code, stdout=out, stderr="")

    result = stop_market_worker(path, unit_directory=units, runner=runner, platform="linux")

    assert result["removed"] is True and result["active"] is False
    assert ["systemctl", "--user", "disable", "--now", "toll-harness-rick.service"] in calls
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert not (units / "toll-harness-rick.service").exists()


def test_stop_market_worker_on_launchd_boots_out_and_removes_the_plist(tmp_path):
    path = _agent(tmp_path / "Rick")
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (agents / "com.toll-harness.rick.plist").write_text("x")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = stop_market_worker(path, unit_directory=agents, runner=runner, platform="darwin")

    assert result["removed"] is True and result["active"] is False
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert not (agents / "com.toll-harness.rick.plist").exists()


def test_log_and_focus_paths_live_in_the_data_directory(tmp_path):
    path = _agent(tmp_path / "Rick")
    data_dir = _data_dir(path)
    assert market_worker_log_path(path) == data_dir / "market.log"
    assert focus_path(path) == data_dir / "focus.md"


def test_read_focus_strips_caps_and_treats_absent_as_empty(tmp_path):
    assert read_focus(tmp_path) == ""
    (tmp_path / "focus.md").write_text("  Music wants only.  \n")
    assert read_focus(tmp_path) == "Music wants only."
    (tmp_path / "focus.md").write_text("x" * (FOCUS_LIMIT + 10))
    assert len(read_focus(tmp_path)) == FOCUS_LIMIT


def test_cli_parses_the_market_worker_commands():
    parsed = cli.build_parser().parse_args(["market", "worker", "stop", "agent.yaml"])
    assert parsed.worker_command == "stop" and parsed.handler is cli.command_market_worker


def test_the_lab_subcommand_is_gone_from_the_harness():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["lab", "list", "--lab", "/x"])


# -- standing direction hook ---------------------------------------------------


def _scan_resources(tmp_path, *, model=None):
    toll_bench = SimpleNamespace(
        list_targets=lambda: {"targets": [{"target_id": "target-1", "want": "a want"}]},
        submit_proposal=lambda *a, **k: {"ok": True, "proposal_id": "p-1"},
        validate_proposal=lambda *a, **k: {"ok": True},
        read_brief=lambda _target_id: {"brief": {}},
        list_act_kinds=lambda: {},
    )
    runtime = SimpleNamespace(
        enabled_tools=["state.save", "result.complete", "toll_bench.submit_proposal"],
        model=model,
    )
    store = SimpleNamespace(path=tmp_path / "harness.sqlite3")
    return SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None, store=store)


class _Stop(Exception):
    pass


def test_hook_and_helper_agree_on_one_file(tmp_path):
    path = _agent(tmp_path / "Rick")
    focus_path(path).parent.mkdir(parents=True, exist_ok=True)
    focus_path(path).write_text("Short wants only.\n")
    store = SimpleNamespace(path=_data_dir(path) / "harness.sqlite3")
    assert cli._lab_lead_direction(SimpleNamespace(store=store)) == "Short wants only."


@pytest.mark.parametrize("focus", ["Only music wants.", None, "   "])
def test_scan_goal_carries_the_standing_direction_only_when_set(tmp_path, focus):
    if focus is not None:
        (tmp_path / "focus.md").write_text(focus)
    resources = _scan_resources(tmp_path)
    seen = {}

    def start(goal, _mode):
        seen["goal"] = goal
        raise _Stop

    resources.runtime.start = start
    with pytest.raises(_Stop):
        cli._process_market_opportunities(resources, {"ok": True})

    if focus and focus.strip():
        assert seen["goal"].startswith("Lab lead standing direction:\nOnly music wants.\n\n")
    else:
        assert seen["goal"].startswith("Respond to the single open Toll Bench want")


def test_draft_road_hands_the_direction_to_the_proposal_call(tmp_path, monkeypatch):
    (tmp_path / "focus.md").write_text("Only music wants.\n")
    seen = {}

    class _DraftLoop:
        def __init__(self, *_args, **_kwargs):
            self.standing_direction = ""

        def run(self, target_id, **kwargs):
            seen["direction"] = self.standing_direction
            return {"ok": True, "filed": True}

    monkeypatch.setattr(cli, "DraftLoop", _DraftLoop)
    resources = _scan_resources(tmp_path, model=object())
    cli._process_market_opportunities(resources, {"ok": True})
    assert seen["direction"] == "Only music wants."


def test_draft_loop_payload_carries_the_direction_only_when_set():
    payloads = []

    def ask(self, instruction, payload, *args, **kwargs):
        payloads.append(payload)
        return {}

    for direction in ("Only music wants.", ""):
        loop = DraftLoop(object(), SimpleNamespace())
        loop.standing_direction = direction
        loop._ask = ask.__get__(loop)
        loop.run("target-1", kind="bid", brief={"want": "a want"}, file=False)
    assert payloads[0]["lab_lead_standing_direction"] == "Only music wants."
    assert "lab_lead_standing_direction" not in payloads[1]


# -- fleet registration ----------------------------------------------------------


def test_fleet_registration_is_idempotent_by_agent_id(tmp_path):
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    fleet.register_agent(agent_id="a-1", name="Rick", config_path=tmp_path / "a.yaml")
    fleet.register_agent(agent_id="a-1", name="Rick", config_path=tmp_path / "a.yaml")
    fleet.register_agent(agent_id="a-1", name="Rick", config_path=tmp_path / "moved.yaml")
    with sqlite3.connect(fleet.path) as connection:
        rows = connection.execute("SELECT agent_id, config_path FROM fleet_agents").fetchall()
    assert rows == [("a-1", str((tmp_path / "moved.yaml").resolve()))]


def test_fleet_same_name_different_agent_gets_a_clear_error(tmp_path):
    fleet = FleetStore(tmp_path / "fleet.sqlite3")
    fleet.register_agent(agent_id="first-id", name="Rick", config_path=tmp_path / "a.yaml")
    with pytest.raises(ValueError) as caught:
        fleet.register_agent(
            agent_id="second-id", name="Rick", config_path=tmp_path / "other/a.yaml"
        )
    message = str(caught.value)
    assert str((tmp_path / "a.yaml").resolve()) in message
    assert str(Path(tmp_path / "other/a.yaml").resolve()) in message
    assert "fleet.database" in message


def test_fleet_old_unique_name_database_still_registers(tmp_path):
    path = tmp_path / "fleet.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE fleet_agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            config_path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        INSERT INTO fleet_agents VALUES ('old-id', 'Rick', '/old/agent.yaml', 't');
        """
    )
    connection.close()
    fleet = FleetStore(path)
    fleet.register_agent(agent_id="old-id", name="Rick", config_path=tmp_path / "a.yaml")
    fleet.register_agent(agent_id="new-id", name="Sam", config_path=tmp_path / "b.yaml")
    with pytest.raises(ValueError, match="already has an agent named 'Rick'"):
        fleet.register_agent(agent_id="dup-id", name="Rick", config_path=tmp_path / "c.yaml")
