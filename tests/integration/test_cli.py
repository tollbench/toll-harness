import subprocess
import sys
from types import SimpleNamespace

import yaml

from toll_harness import cli


def test_module_cli_loads():
    result = subprocess.run(
        [sys.executable, "-m", "toll_harness.cli", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    import toll_harness
    assert result.stdout.strip() == toll_harness.__version__


def test_pre_registration_checks_do_not_require_toll_bench_token(tmp_path, monkeypatch):
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "model": {
                    "model_id": "example.model",
                    "profile": "builder",
                    "region": "us-west-2",
                },
                "providers": {"browser": "agentcore"},
                "toll_bench": {
                    "connected": True,
                    "maker_id": None,
                    "token_secret": "missing-token",
                },
            }
        )
    )

    class FakeAdapter:
        model_id = "example.model"

        def __init__(self, *_args, **_kwargs):
            pass

        def invoke(self, **_kwargs):
            return SimpleNamespace(usage=SimpleNamespace(total_tokens=2))

    class FakeBrowser:
        def __init__(self, **_kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        cli, "build_model_adapter", lambda _config, *, root, data_dir: FakeAdapter()
    )
    monkeypatch.setattr(
        "toll_harness.browser.agentcore.AgentCoreBrowserProvider",
        FakeBrowser,
    )
    monkeypatch.setattr(
        cli,
        "build_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not build runtime")),
    )

    result = cli._test_model_and_browser(config_path)

    assert result == {
        "model_connected": True,
        "model_id": "example.model",
        "model_test_tokens": 2,
        "browser_connected": True,
    }


def test_init_canary_exposes_only_state_and_result_tools(tmp_path, monkeypatch):
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(yaml.safe_dump({"version": 1, "runtime": {"autonomy": "autonomous"}}))
    observed_tools = []
    runtime = SimpleNamespace(enabled_tools=["email.send", "state.save", "result.complete"])

    def start(_goal, _mode):
        observed_tools.extend(runtime.enabled_tools)
        return SimpleNamespace(
            run_id="run-1",
            status=SimpleNamespace(value="completed"),
            usage=SimpleNamespace(total_tokens=3),
        )

    runtime.start = start
    store = SimpleNamespace(
        list_events=lambda _run_id: [
            SimpleNamespace(kind="tool.called", payload={"name": "state.save"}),
            SimpleNamespace(kind="tool.called", payload={"name": "result.complete"}),
        ]
    )
    resources = SimpleNamespace(runtime=runtime, store=store, close=lambda: None)
    monkeypatch.setattr(cli, "build_runtime", lambda _path: resources)

    result = cli._run_init_canary(config_path)

    assert observed_tools == ["state.save", "result.complete", "result.fail"]
    assert runtime.enabled_tools == ["email.send", "state.save", "result.complete"]
    assert result["canary_completed"] is True
    assert result["actions"] == ["state.save", "result.complete"]


def test_resume_installs_worker_while_company_confirmation_is_pending(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "agent.yaml"
    config_path.write_text("version: 1\n")
    observed = {}
    pending = {
        "status": cli.WAITING_FOR_COMPANY_VERIFICATION,
        "maker_id": "maker-1",
        "email_status": "pending_verification",
    }

    monkeypatch.setattr(cli, "_configuration_path", lambda _directory: config_path)
    monkeypatch.setattr(
        cli,
        "advance_connected_onboarding",
        lambda _path, approve_registration: pending,
    )
    monkeypatch.setattr(
        cli,
        "_set_worker_preference",
        lambda _path, enabled: observed.update(worker_enabled=enabled),
    )

    def finish(path, result, **kwargs):
        observed.update(path=path, result=result, finish=kwargs)
        return 0

    monkeypatch.setattr(cli, "_finish_init", finish)

    result = cli.command_init(
        SimpleNamespace(directory=tmp_path, resume=True, no_worker=False)
    )

    assert result == 0
    assert observed["worker_enabled"] is True
    assert observed["path"] == config_path
    assert observed["finish"]["enable_worker"] is True
    assert observed["result"]["status"] == cli.WAITING_FOR_COMPANY_VERIFICATION
    assert observed["result"]["next"].endswith("--resume")
