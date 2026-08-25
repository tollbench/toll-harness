import pytest

from toll_harness.core.types import AutonomyMode
from toll_harness.operator.channel import OperatorChannel
from toll_harness.storage.local import SQLiteStore


def test_autonomous_run_rejects_operator_coaching(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.AUTONOMOUS, "model")
    channel = OperatorChannel(store, store)

    with pytest.raises(PermissionError):
        channel.message(run.id, "try another approach")


def test_supported_run_becomes_observed_supported_after_message(tmp_path):
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    run = store.create_run("goal", AutonomyMode.SUPPORTED, "model")
    channel = OperatorChannel(store, store)

    assert channel.observe(run.id)["run"]["observed_mode"] == "autonomous"
    channel.message(run.id, "the relevant account is 42")

    observation = channel.observe(run.id)
    assert observation["run"]["observed_mode"] == "supported"
    assert observation["events"][-1]["kind"] == "operator.message"
