"""TWO SMALL THINGS LANE R ASKED FOR (0.68.0).

1. `model.base_url` in agent.yaml for the openai adapter. A lab model on
   OpenRouter could only be pointed there by an OPENAI_BASE_URL riding an env
   file beside the unit; the endpoint belongs in the agent's own config.
2. "Your market watch is not running as a service" is said once per change,
   as the module always said: it rode every unthrottled check, so a watch
   restarted by its own unit printed it on every start.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from tests.unit.test_update_check import FakeBench, _config, pypi  # noqa: F401
from toll_harness import updates
from toll_harness.config import build_model_adapter
from toll_harness.models.openai import OpenAIModelAdapter


def _fake_openai(monkeypatch):
    made = []

    def client(**options):
        made.append(options)
        return SimpleNamespace(base_url=options.get("base_url", ""), chat=None)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=client))
    return made


def test_model_base_url_points_the_openai_adapter_at_another_endpoint(monkeypatch, tmp_path):
    made = _fake_openai(monkeypatch)
    config = {"model": {"adapter": "openai", "model_id": "deepseek/deepseek-v4.1-flash",
                        "base_url": "https://openrouter.ai/api/v1"}}
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)
    assert isinstance(adapter, OpenAIModelAdapter)
    assert made == [{"base_url": "https://openrouter.ai/api/v1"}]


def test_without_base_url_the_sdk_reads_its_environment_as_before(monkeypatch, tmp_path):
    made = _fake_openai(monkeypatch)
    build_model_adapter({"model": {"adapter": "openai", "model_id": "gpt-x"}},
                        root=tmp_path, data_dir=tmp_path)
    assert made == [{}]


def test_not_running_as_a_service_is_said_once_per_change(tmp_path, pypi, monkeypatch):  # noqa: F811
    path = _config(tmp_path, connected=True)
    state = {"state": "not installed"}
    monkeypatch.setattr(updates, "_service_check", lambda _path, _config: dict(state))

    def lines(check):
        return [line for line in check["notices"] if "service" in line]

    first = updates.check_for_updates(path, api=FakeBench(), force=True)
    again = updates.check_for_updates(path, api=FakeBench(), force=True)
    assert len(lines(first)) == 1 and first["new"] is True
    assert lines(again) == []
    assert again["service"]["state"] == "not installed"  # still reported, just not said
    state["state"] = "installed"
    moved = updates.check_for_updates(path, api=FakeBench(), force=True)
    assert len(lines(moved)) == 1 and "installed but not running" in lines(moved)[0]
