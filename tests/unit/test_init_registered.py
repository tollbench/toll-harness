"""`init --registered`: connect an agent that already entered at the bench's door.

The token comes only from TOLL_HARNESS_AGENT_TOKEN in the process environment,
goes straight into the secret store, and /me fills the ids. Nobody is ever
prompted for it, and the model-rail picker offers `external`.
"""

import pytest
import yaml

from tests.unit.test_onboarding import FakeBookOfHousesApi, _answers
from toll_harness import cli
from toll_harness import onboarding as onboarding_module
from toll_harness.onboarding import (
    AGENT_TOKEN_ENV,
    READY,
    WAITING_FOR_COMPANY_VERIFICATION,
    InitAnswers,
    connect_registered_agent,
    create_configuration,
    load_config,
    secret_store,
)

TOKEN = "door-issued-token"


def _registered_answers(**overrides):
    values = dict(
        agent_name="placeholder",
        intelligence="External",
        model_id="external/custom",
        company="",
        mode="Autonomous",
        aws_profile=None,
        aws_region="us-west-2",
        connect_toll_bench=True,
        use_book_of_houses_email=True,
        model_adapter="external",
        model_command=("my-wrapper", "--flag"),
        registered=True,
    )
    values.update(overrides)
    return InitAnswers(**values)


def _fake_api(confirmed=False):
    api = FakeBookOfHousesApi()
    api.expected_token = TOKEN
    api.confirmed = confirmed
    return api


def test_connect_registered_stores_token_and_me_fills_the_ids(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())
    api = _fake_api()

    result = connect_registered_agent(config_path, TOKEN, api=api)

    assert api.register_calls == 0
    assert result["status"] == WAITING_FOR_COMPANY_VERIFICATION
    config = load_config(config_path)
    assert config["toll_bench"]["maker_id"] == "maker-oak"
    assert config["toll_bench"]["registry_no"] == "A-001"
    assert config["toll_bench"]["status"] == WAITING_FOR_COMPANY_VERIFICATION
    store = secret_store(config_path, config)
    assert store.get(config["toll_bench"]["token_secret"]) == TOKEN
    for path in config_path.parent.rglob("*"):
        if path.is_file() and "secrets" not in path.parts:
            assert TOKEN not in path.read_text(errors="ignore"), path


def test_connect_registered_reaches_ready_when_contact_is_confirmed(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())

    result = connect_registered_agent(config_path, TOKEN, api=_fake_api(confirmed=True))

    assert result["status"] == READY
    assert result["maker_id"] == "maker-oak"


def test_registered_needs_no_company_details_and_external_writes_the_command(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())

    config = yaml.safe_load(config_path.read_text())

    assert config["model"]["adapter"] == "external"
    assert config["model"]["command"] == ["my-wrapper", "--flag"]
    assert config["toll_bench"]["connected"] is True
    assert config["toll_bench"]["responsible_party"] is None


def test_external_without_a_command_is_refused(tmp_path):
    with pytest.raises(ValueError, match="model.command"):
        create_configuration(tmp_path / "x", _registered_answers(model_command=None))


def test_a_command_on_another_rail_is_refused(tmp_path):
    with pytest.raises(ValueError, match="external adapter"):
        create_configuration(
            tmp_path / "x",
            _registered_answers(model_adapter="claude_code", model_id="opus"),
        )


def _scripted_input(monkeypatch, answers):
    queue = list(answers)
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        if not queue:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def _no_secret_prompt(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("init --registered must never prompt for a secret")

    monkeypatch.setattr(cli.getpass, "getpass", refuse)


def test_cli_registered_reads_env_picks_external_and_never_prompts_for_a_token(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv(AGENT_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(
        onboarding_module, "BookOfHousesApiClient", lambda base_url: _fake_api()
    )
    finished = {}

    def fake_finish(config_path, result, *, checks=None, enable_worker):
        finished.update(config_path=config_path, result=result, enable_worker=enable_worker)
        return 0

    monkeypatch.setattr(cli, "_finish_init", fake_finish)
    _no_secret_prompt(monkeypatch)
    asked = _scripted_input(
        monkeypatch,
        ["F", "my-wrapper --flag 'two words'", "", "", ""],
    )
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--registered", "--no-worker"]
    )

    assert cli.command_init(arguments) == 0

    out = capsys.readouterr().out
    assert cli.NO_PROCESS_LINE in out
    assert "F. Any other agent or model" in out
    assert TOKEN not in out
    assert not any("token" in prompt.lower() for prompt in asked)
    config_path = finished["config_path"]
    config = load_config(config_path)
    assert config["model"]["adapter"] == "external"
    assert config["model"]["command"] == ["my-wrapper", "--flag", "two words"]
    assert config["toll_bench"]["maker_id"] == "maker-oak"
    assert config["toll_bench"]["registry_no"] == "A-001"
    assert TOKEN not in config_path.read_text()
    assert secret_store(config_path, config).get(config["toll_bench"]["token_secret"]) == TOKEN
    assert finished["result"]["status"] == WAITING_FOR_COMPANY_VERIFICATION


def test_cli_registered_without_the_env_var_is_one_sentence_and_exit_2(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv(AGENT_TOKEN_ENV, raising=False)
    _scripted_input(monkeypatch, [])
    _no_secret_prompt(monkeypatch)
    arguments = cli.build_parser().parse_args(["init", str(tmp_path / "agent"), "--registered"])

    assert cli.command_init(arguments) == 2

    captured = capsys.readouterr()
    assert captured.err.strip() == cli.REGISTERED_TOKEN_MISSING
    assert AGENT_TOKEN_ENV in captured.err
    assert captured.err.strip().count(". ") == 0
    assert not (tmp_path / "agent" / "agent.yaml").exists()


def test_plain_init_first_screen_names_the_one_door(tmp_path, monkeypatch, capsys):
    def stop(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", stop)
    arguments = cli.build_parser().parse_args(["init", str(tmp_path / "agent")])

    with pytest.raises(KeyboardInterrupt):
        cli.command_init(arguments)

    out = capsys.readouterr().out
    assert "POST /api/bench/agents/register" in out
    assert "init --registered" in out


def test_operator_answers_still_build_a_connected_config(tmp_path):
    config_path = create_configuration(tmp_path / "op", _answers())
    assert load_config(config_path)["toll_bench"]["responsible_party"]["legal_name"]
