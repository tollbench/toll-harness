"""TWO SMALL THINGS LANE R ASKED FOR (0.68.0).

1. `model.base_url` in agent.yaml for the openai adapter. A lab model on
   OpenRouter could only be pointed there by an OPENAI_BASE_URL riding an env
   file beside the unit; the endpoint belongs in the agent's own config.
2. "Your market watch is not running as a service" is said once per change
   at the start of a command too: the start printed every notice it was
   handed, so a watch restarted by its own unit printed it on every start
   (the watch loop itself already printed only news).
"""
from __future__ import annotations

import sys
from argparse import Namespace
from types import SimpleNamespace

from tests.unit.test_update_check import FakeBench, _config, pypi  # noqa: F401
from toll_harness import cli, updates
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


def test_the_check_says_whether_the_service_line_is_news(tmp_path, pypi, monkeypatch):  # noqa: F811
    path = _config(tmp_path, connected=True)
    state = {"state": "not installed"}
    monkeypatch.setattr(updates, "_service_check", lambda _path, _config: dict(state))

    first = updates.check_for_updates(path, api=FakeBench(), force=True)
    again = updates.check_for_updates(path, api=FakeBench(), force=True)
    state["state"] = "installed"
    moved = updates.check_for_updates(path, api=FakeBench(), force=True)

    assert first["service_new"] is True and first["service_notice"] in first["notices"]
    # Still on the list and still reported; it is just not news.
    assert again["service_new"] is False and again["service_notice"] in again["notices"]
    assert moved["service_new"] is True and "installed but not running" in (
        moved["service_notice"])


def test_the_start_of_a_command_leaves_an_unchanged_service_line_out(monkeypatch, capsys):
    harness = "toll-harness 0.67.0 installed, 0.68.0 available: pip install -U toll-harness"
    service = "Your market watch is not running as a service, so ..."
    check = {"notices": [harness, service], "new": True, "service_notice": service,
             "service_new": False}
    monkeypatch.setattr(cli, "check_for_updates", lambda config: dict(check))
    arguments = Namespace(handler=lambda arguments: 0, config="agent.yaml")

    cli._startup_update_check(arguments)
    assert capsys.readouterr().err.splitlines() == [harness]

    check["service_new"] = True
    cli._startup_update_check(arguments)
    assert capsys.readouterr().err.splitlines() == [harness, service]
