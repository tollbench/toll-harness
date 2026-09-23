import io
import json
import sys

import pytest
import yaml

from toll_harness import cli, updates
from toll_harness.onboarding import load_config, load_onboarding, save_onboarding


class FakeBench:
    def __init__(self, contract="4.0.8", rules="rules-aaaa1111", harness=None, fail=False):
        self.contract = contract
        self.rules = rules
        self.harness = harness
        self.fail = fail
        self.calls = 0

    def protocol(self):
        self.calls += 1
        if self.fail:
            raise ConnectionError("bench unreachable")
        payload = {
            "protocol_version": "4",
            "contract_version": self.contract,
            "rules_version_hash": self.rules,
        }
        if self.harness is not None:
            payload["harness"] = self.harness
        return payload


class FakePyPI:
    def __init__(self, version="0.51.0", fail=False):
        self.version = version
        self.fail = fail
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        assert request.full_url == updates.PYPI_URL
        assert timeout == updates.NETWORK_TIMEOUT_SECONDS
        if self.fail:
            raise OSError("network is unreachable")
        return io.BytesIO(json.dumps({"info": {"version": self.version}}).encode())


@pytest.fixture
def pypi(monkeypatch):
    monkeypatch.delenv("TOLL_HARNESS_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("TOLL_HARNESS_UPDATE_CHECK_SECONDS", raising=False)
    monkeypatch.setattr(updates.toll_harness, "__version__", "0.50.0")
    fake = FakePyPI()
    monkeypatch.setattr(updates.urllib.request, "urlopen", fake)
    return fake


def _config(tmp_path, *, connected=True):
    path = tmp_path / "agent.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "storage": {"directory": ".toll-harness"},
                "toll_bench": {"connected": connected, "base_url": "https://bench.example"},
            }
        )
    )
    return path


def _state(path):
    return load_onboarding(path, load_config(path))


def test_version_key_orders_releases_pre_releases_and_dev():
    assert updates.is_newer("0.51.0", "0.50.0")
    assert updates.is_newer("0.50.1", "0.50")
    assert updates.is_newer("1.0.0", "1.0.0rc1")
    assert updates.is_newer("1.0.0rc1", "1.0.0b2")
    assert updates.is_newer("1.0.0a1", "1.0.0.dev3")
    assert updates.is_newer("0.50.0", "0.50.0.dev0")
    assert updates.is_newer("0.50.0.post1", "0.50.0")
    assert not updates.is_newer("0.50.0", "0.50.0")
    assert not updates.is_newer("0.50", "0.50.0")
    assert not updates.is_newer("not-a-version", "0.50.0")


def test_behind_reports_and_prints_one_line(tmp_path, pypi):
    path = _config(tmp_path, connected=False)

    result = updates.check_for_updates(path)

    assert result["ok"] is True
    harness = result["harness"]
    assert harness["source"] == "pypi"
    assert harness["behind"] is True and harness["below_minimum"] is False
    assert harness["install_command"] == "pip install -U toll-harness"
    assert result["notices"] == [
        "toll-harness 0.50.0 installed, 0.51.0 available: pip install -U toll-harness"
    ]
    assert result["new"] is True
    assert _state(path)["update_check"]["latest_version"] == "0.51.0"


def test_up_to_date_prints_nothing(tmp_path, pypi):
    pypi.version = "0.50.0"
    result = updates.check_for_updates(_config(tmp_path, connected=False))

    assert result["ok"] is True
    assert result["harness"]["behind"] is False
    assert result["notices"] == []
    assert result["new"] is False


def test_pypi_unreachable_never_raises(tmp_path, pypi):
    pypi.fail = True
    result = updates.check_for_updates(_config(tmp_path, connected=False))

    assert result["ok"] is False
    assert "network is unreachable" in result["harness"]["error"]
    assert result["notices"] == []


def test_missing_config_and_broken_state_never_raise(tmp_path, pypi):
    assert updates.check_for_updates(tmp_path / "nowhere.yaml")["ok"] is False
    path = _config(tmp_path, connected=False)
    state_file = tmp_path / ".toll-harness" / "onboarding.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("{not json")
    result = updates.check_for_updates(path)
    assert result["ok"] is False and result["notices"] == []


def test_bench_harness_block_wins_over_pypi_and_carries_minimum(tmp_path, pypi):
    bench = FakeBench(
        harness={
            "latest_version": "0.52.0",
            "minimum_version": "0.51.0",
            "install": "pip install -U toll-harness",
        }
    )
    result = updates.check_for_updates(_config(tmp_path), api=bench)

    assert pypi.calls == 0
    harness = result["harness"]
    assert harness["source"] == "bench"
    assert harness["latest"] == "0.52.0"
    assert harness["behind"] is True and harness["below_minimum"] is True
    assert result["notices"] == [
        "toll-harness 0.50.0 installed is below the bench's minimum 0.51.0: "
        "pip install -U toll-harness"
    ]


def test_contract_change_is_reported_once_then_snapshot_refreshed(tmp_path, pypi, monkeypatch):
    pypi.version = "0.50.0"
    path = _config(tmp_path)
    bench = FakeBench(contract="4.0.8")
    first = updates.check_for_updates(path, api=bench, force=True)
    assert first["bench"]["changed"] == []  # first sight only records the snapshot
    assert _state(path)["protocol"]["contract_version"] == "4.0.8"

    bench.contract = "4.0.9"
    moved = updates.check_for_updates(path, api=bench, force=True)
    assert moved["bench"]["changed"] == ["contract_version"]
    assert moved["notices"] == ["Toll Bench contract moved 4.0.8 -> 4.0.9 since your last check"]
    assert moved["new"] is True
    assert _state(path)["protocol"]["contract_version"] == "4.0.9"

    again = updates.check_for_updates(path, api=bench, force=True)
    assert again["bench"]["changed"] == [] and again["notices"] == []


def test_rules_change_carries_the_reread_note(tmp_path, pypi):
    pypi.version = "0.50.0"
    path = _config(tmp_path)
    bench = FakeBench(rules="rules-aaaa1111")
    updates.check_for_updates(path, api=bench, force=True)
    bench.rules = "abcd1234ffff"

    result = updates.check_for_updates(path, api=bench, force=True)

    assert result["bench"]["changed"] == ["rules_version_hash"]
    assert "re-reads the guide live" in result["bench"]["note"]
    assert result["notices"] == [
        "The Toll Bench rules changed since your last check (hash abcd1234); "
        "the guide is re-read live"
    ]
    assert updates.check_for_updates(path, api=bench, force=True)["notices"] == []


def test_bench_down_still_checks_the_harness(tmp_path, pypi):
    path = _config(tmp_path)
    result = updates.check_for_updates(path, api=FakeBench(fail=True))

    assert result["bench"]["ok"] is False
    assert result["harness"]["source"] == "pypi"
    assert result["notices"][0].startswith("toll-harness 0.50.0 installed, 0.51.0")


def test_throttle_is_honored_and_force_bypasses_it(tmp_path, pypi, monkeypatch):
    path = _config(tmp_path, connected=False)
    clock = [1_000_000.0]
    monkeypatch.setattr(updates, "_now", lambda: clock[0])

    updates.check_for_updates(path)
    clock[0] += 60
    throttled = updates.check_for_updates(path)
    assert throttled["skipped"] == "throttled"
    assert throttled["notices"] == []
    assert pypi.calls == 1

    forced = updates.check_for_updates(path, force=True)
    assert forced["checked"] is True
    assert pypi.calls == 2

    clock[0] += 3601
    assert updates.check_for_updates(path)["checked"] is True
    assert pypi.calls == 3

    monkeypatch.setenv("TOLL_HARNESS_UPDATE_CHECK_SECONDS", "0")
    assert updates.check_for_updates(path)["checked"] is True


def test_env_off_switch_disables_even_forced_checks(tmp_path, pypi, monkeypatch):
    path = _config(tmp_path, connected=False)
    for value in ("0", "off", "false", "OFF"):
        monkeypatch.setenv("TOLL_HARNESS_UPDATE_CHECK", value)
        result = updates.check_for_updates(path, force=True)
        assert result == {"ok": True, "skipped": "disabled", "notices": []}
    assert pypi.calls == 0
    assert "update_check" not in _state(path)


def test_the_check_keeps_other_onboarding_keys(tmp_path, pypi):
    path = _config(tmp_path, connected=False)
    config = load_config(path)
    save_onboarding(path, config, {"version": 1, "status": "READY", "maker_id": "maker-1"})

    updates.check_for_updates(path)

    state = _state(path)
    assert state["maker_id"] == "maker-1" and state["status"] == "READY"


def test_main_prints_the_notice_and_still_runs_the_command(tmp_path, pypi, monkeypatch, capsys):
    path = _config(tmp_path, connected=False)
    ran = []
    monkeypatch.setattr(
        cli, "command_loop_guard", lambda arguments: ran.append(arguments.config) or 0
    )
    monkeypatch.setattr(sys, "argv", ["toll-harness", "loop-guard", str(path)])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 0
    assert ran == [str(path)]
    assert capsys.readouterr().err.strip().splitlines()[-1] == (
        "toll-harness 0.50.0 installed, 0.51.0 available: pip install -U toll-harness"
    )


def test_main_runs_the_command_when_the_check_fails(tmp_path, pypi, monkeypatch, capsys):
    pypi.fail = True
    path = _config(tmp_path, connected=False)
    ran = []
    monkeypatch.setattr(cli, "command_loop_guard", lambda arguments: ran.append(1) or 0)
    monkeypatch.setattr(sys, "argv", ["toll-harness", "loop-guard", str(path)])

    with pytest.raises(SystemExit):
        cli.main()

    assert ran == [1]
    assert "toll-harness" not in capsys.readouterr().err


def test_init_runs_the_check_forced(tmp_path, pypi, monkeypatch, capsys):
    path = _config(tmp_path, connected=False)
    # A check a minute ago would throttle anything but a forced one.
    updates.check_for_updates(path)
    assert pypi.calls == 1
    monkeypatch.setattr(
        cli, "_run_init_canary", lambda _path: {"canary_completed": True, "actions": []}
    )

    code = cli._finish_init(path, {"status": "READY"}, checks={"ok": True}, enable_worker=False)

    assert code == 0
    assert pypi.calls == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["update_check"]["checked"] is True
    assert "0.51.0 available" in captured.err


def test_update_check_command_says_up_to_date_and_exits_zero(tmp_path, pypi, capsys):
    pypi.version = "0.50.0"
    path = _config(tmp_path, connected=False)
    arguments = cli.build_parser().parse_args(["update-check", "--config", str(tmp_path)])

    assert cli.command_update_check(arguments) == 0
    assert capsys.readouterr().out.strip() == "up to date"
    assert "update_check" in _state(path)

    pypi.fail = True
    assert cli.command_update_check(arguments) == 0
    assert "update check failed" in capsys.readouterr().out


def test_watch_cycle_carries_only_new_findings(tmp_path, pypi):
    path = _config(tmp_path, connected=False)
    first = cli._watch_update_check(str(path))
    assert first is not None and first["notices"]
    # Throttled next cycle: nothing to carry.
    assert cli._watch_update_check(str(path)) is None
