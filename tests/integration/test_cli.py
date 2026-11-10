import os
import shlex
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
    observed_turns = []
    runtime = SimpleNamespace(
        enabled_tools=["email.send", "state.save", "result.complete"], max_iterations=20
    )

    def start(_goal, _mode):
        observed_tools.extend(runtime.enabled_tools)
        observed_turns.append(runtime.max_iterations)
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
    # 0.76.1: the canary runs on a few turns; the agent's own budget comes back.
    assert observed_turns == [cli.CANARY_MAX_ITERATIONS]
    assert runtime.max_iterations == 20
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


# An agent reached by its own command: answers the connectivity check, then the
# canary's two calls, in the reply envelope the external rail reads.
_OWN_COMMAND = """\
import json, pathlib, sys
sys.stdin.read()
counter = pathlib.Path(__file__).with_suffix(".count")
turn = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(turn + 1))
if turn == 0:
    reply = {"text": "OK", "tool_calls": []}
elif turn == 1:
    reply = {"text": "", "tool_calls": [
        {"name": "state.save", "arguments": {"checkpoint": {"status": "ready"}}}]}
else:
    reply = {"text": "", "tool_calls": [
        {"name": "result.complete", "arguments": {"summary": "ready"}}]}
print(json.dumps(reply))
"""


def test_plain_init_names_the_intelligence_then_asks_the_means(tmp_path):
    """0.76.2 (Steven 2026-11-10: the harness never demands an intelligence from
    our list; the agent names what it uses). Plain `init`, run the way an
    operator runs it, asks the intelligence as ONE free-text name and then the
    same means question `init --registered` asks. No model provider picker, no
    brand list. A standalone agent reached by its own command, offline."""
    script = tmp_path / "instinct.py"
    script.write_text(_OWN_COMMAND)
    command = shlex.join([sys.executable, str(script)])
    answers = "\n".join(
        [
            "Oak",  # agent name
            "Instinct",  # the intelligence, as typed
            "1",  # the means: a command on this machine
            command,
            "Oak Works",  # company
            "",  # operating mode
            "n",  # connect to Toll Bench: not in this test
        ]
    ) + "\n"
    env = {key: value for key, value in os.environ.items() if not key.startswith("TOLL_HARNESS_")}
    env["TOLL_HARNESS_UPDATE_CHECK"] = "0"
    # The canary builds the runtime, and the runtime opens the fleet database
    # under the home directory: a home of its own keeps this run out of the
    # machine's real fleet.
    env["HOME"] = str(tmp_path / "home")

    completed = subprocess.run(
        [sys.executable, "-m", "toll_harness.cli", "init", str(tmp_path / "agent")],
        input=answers,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    assert out.index(cli.INTELLIGENCE_QUESTION) < out.index(cli.MEANS_QUESTION)
    assert out.count(cli.MEANS_QUESTION) == 1
    for _key, description in cli.MEANS_OPTIONS:
        assert description in out
    assert "Model provider" not in out
    assert "Which intelligence brand" not in out
    assert "Bedrock" not in out
    config = yaml.safe_load((tmp_path / "agent" / "agent.yaml").read_text())
    assert config["agent"]["intelligence"] == "Instinct"
    assert config["agent"]["intelligence_brand"] == "Instinct"
    assert config["model"]["adapter"] == "external"
    assert config["model"]["command"] == [sys.executable, str(script)]
    assert config["model"]["model_id"] is None
    assert config["fleet"]["database"].startswith(str(tmp_path / "home"))
