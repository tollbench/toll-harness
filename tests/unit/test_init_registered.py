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
from toll_harness.core.types import ModelMessage, ModelResponse, ModelUsage, ToolCall
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.models.scripted import ScriptedModelAdapter
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


def _fake_api(confirmed=False, operator_name=None):
    api = FakeBookOfHousesApi()
    api.expected_token = TOKEN
    api.confirmed = confirmed
    api.operator_name = operator_name
    return api


def test_connect_registered_stores_token_and_me_fills_the_ids(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())
    api = _fake_api()

    result = connect_registered_agent(
        config_path, TOKEN, api=api, ask_company=lambda: "Oak Works"
    )

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

    result = connect_registered_agent(
        config_path, TOKEN, api=_fake_api(confirmed=True), ask_company=lambda: "Oak Works"
    )

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
        ["F", "my-wrapper --flag 'two words'", "", "", "Grok", "", "Oak Works"],
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
    # The brand question follows the rail; a registered agent keeps it locally.
    assert any(prompt.startswith(cli.INTELLIGENCE_BRAND_QUESTION) for prompt in asked)
    assert config["agent"]["intelligence_brand"] == "Grok"
    assert config["agent"]["intelligence_maker"] == "xAI"
    # /me names no company, so init asked once, with no default.
    assert f"{onboarding_module.COMPANY_QUESTION}:\n> " in asked
    assert config["agent"]["company"] == "Oak Works"
    assert config["benchmark"]["company"] == "Oak Works"
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


def test_the_company_comes_from_the_bench_attribution(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())
    api = _fake_api()
    api.operator_name = "Nine Doors Labs"

    def never_asked():
        raise AssertionError("the attribution named the company; nothing to ask")

    connect_registered_agent(config_path, TOKEN, api=api, ask_company=never_asked)

    config = load_config(config_path)
    assert config["agent"]["company"] == "Nine Doors Labs"
    assert config["benchmark"]["company"] == "Nine Doors Labs"


class FakeApiAttributionFails(FakeBookOfHousesApi):
    def attribution(self):
        raise BookOfHousesApiError(503, "unavailable", "try later")


def test_a_failed_attribution_call_falls_back_to_the_one_question(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())
    api = FakeApiAttributionFails()
    api.expected_token = TOKEN
    asked = []

    connect_registered_agent(
        config_path, TOKEN, api=api, ask_company=lambda: asked.append(1) or "Oak Works"
    )

    assert asked == [1]
    assert load_config(config_path)["agent"]["company"] == "Oak Works"


def test_no_company_anywhere_is_refused_rather_than_written_empty(tmp_path):
    config_path = create_configuration(tmp_path / "door", _registered_answers())

    with pytest.raises(ValueError, match="company"):
        connect_registered_agent(config_path, TOKEN, api=_fake_api(), ask_company=lambda: "  ")

    assert load_config(config_path)["toll_bench"]["maker_id"] is None


def _text_reply(text):
    return ModelResponse(
        message=ModelMessage.text("assistant", text),
        text=text,
        tool_calls=[],
        usage=ModelUsage(input_tokens=3, output_tokens=1, total_tokens=4),
        stop_reason="end_turn",
    )


def _tool_reply(name, arguments):
    call = ToolCall("call-1", name, arguments)
    return ModelResponse(
        message=ModelMessage(
            "assistant",
            [{"type": "tool_call", "id": call.id, "name": name, "arguments": arguments}],
        ),
        text="",
        tool_calls=[call],
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        stop_reason="tool_use",
    )


@pytest.mark.parametrize(
    ("operator_name", "answers", "company"),
    [
        # The bench's attribution names the company: no question is shown.
        ("Nine Doors Labs", [], "Nine Doors Labs"),
        # The attribution names none: the one question appears.
        (None, ["Oak Works"], "Oak Works"),
    ],
)
def test_cli_registered_end_to_end_loads_through_the_identity_loader(
    tmp_path, monkeypatch, capsys, operator_name, answers, company
):
    """No step of init is replaced: the connectivity check, the canary (which
    builds the runtime through the identity loader) and the update check all
    run. Only the brain is scripted, and the bench is a fake."""
    from toll_harness import config as config_module
    from toll_harness.config import build_runtime, load_agent_identity

    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # the fleet ledger lands here
    monkeypatch.setenv(AGENT_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(
        onboarding_module,
        "BookOfHousesApiClient",
        lambda base_url: _fake_api(operator_name=operator_name),
    )
    brains = [
        [_text_reply("OK")],  # init's model connectivity check
        [  # the init canary
            _tool_reply("state.save", {"checkpoint": {"status": "ready"}}),
            _tool_reply("result.complete", {"summary": "ready"}),
        ],
    ]
    monkeypatch.setattr(
        config_module,
        "_build_model",
        lambda _config, *, root, data_dir: ScriptedModelAdapter(brains.pop(0)),
    )
    _no_secret_prompt(monkeypatch)
    asked = _scripted_input(
        monkeypatch,
        ["F", "my-wrapper", "", "", "Grok", "", *answers],
    )
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--registered", "--no-worker"]
    )

    assert cli.command_init(arguments) == 0

    out = capsys.readouterr().out
    assert '"canary_completed": true' in out
    assert TOKEN not in out
    config_path = tmp_path / "agent" / "agent.yaml"
    config = load_config(config_path)
    identity = load_agent_identity(config)
    assert identity is not None
    assert identity.company == company
    assert config["benchmark"]["company"] == company
    company_prompts = [p for p in asked if p.startswith(onboarding_module.COMPANY_QUESTION)]
    assert len(company_prompts) == (0 if operator_name else 1)
    assert identity.name == config["agent"]["name"]
    assert config["agent"]["company"] != ""
    brains.append([])
    build_runtime(config_path).close()
