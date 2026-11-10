"""The intelligence: plain init asks its name, registration sends it.

The bench ranks the company fielding an agent together with its intelligence
brand (Fable, Astra, Sol, Luna, Muse, Grok, Gemini...); the exact model version
and the harness are recorded on the system record, never ranked.

0.76.2 (Steven, 2026-11-10: the harness never demands an intelligence from our
list; the agent names what it uses): plain init asks the name as ONE free-text
answer, "What is the intelligence? (the name its maker publishes, or your own
name if you are it)", then the same one means question `init --registered`
asks. The six-way model provider picker is gone from init.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from tests.unit.test_onboarding import FakeBookOfHousesApi, _answers
from toll_harness import cli
from toll_harness import onboarding as onboarding_module
from toll_harness.onboarding import (
    WAITING_FOR_COMPANY_VERIFICATION,
    advance_connected_onboarding,
    create_configuration,
    load_config,
    registration_payload,
    secret_store,
)

PROTOCOL = {"rules_version_hash": "rules-hash"}


class CapturingApi(FakeBookOfHousesApi):
    def __init__(self):
        super().__init__()
        self.validated = []
        self.registered = []

    def validate_registration(self, payload):
        self.validated.append(payload)
        return {"ok": True, "problem_count": 0, "problems": []}

    def register(self, payload, idempotency_key):
        self.registered.append(payload)
        return super().register(payload, idempotency_key)


def _scripted_input(monkeypatch, answers):
    queue = list(answers)
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        if not queue:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_args, **_kwargs: "sk-test-key")
    return asked


def _which(monkeypatch, *present):
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None
    )


def test_plain_init_asks_the_name_then_the_means_and_sends_it(
    tmp_path, monkeypatch, capsys
):
    api = CapturingApi()
    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", lambda base_url: api)
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda _path: {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    _which(monkeypatch)  # no claude CLI here, so the record's key is the default
    finished = {}

    def fake_finish(config_path, result, *, checks=None, enable_worker):
        finished.update(config_path=config_path, result=result)
        return 0

    monkeypatch.setattr(cli, "_finish_init", fake_finish)
    asked = _scripted_input(
        monkeypatch,
        [
            "Oak",  # agent name
            "Fable",  # the intelligence, typed
            "",  # the means: Enter takes the default, the key for the model named
            "claude-fable-5-1",  # model id, as the API names it
            "Oak Works",  # company
            "",  # mode
            "",  # connect
            "",  # agent email
            "https://example.com",
            "",  # responsible party legal name
            "US-OR",
            "ops@example.com",
        ],
    )
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--yes", "--no-worker"]
    )

    assert cli.command_init(arguments) == 0

    out = capsys.readouterr().out
    # One free-text name, right after the agent's name; then the one means
    # question, the same four lines `init --registered` prints.
    assert asked[1] == f"{cli.INTELLIGENCE_QUESTION}:\n> "
    assert cli.INTELLIGENCE_QUESTION == (
        "What is the intelligence? (the name its maker publishes, or your own name if "
        "you are it)"
    )
    assert asked[2] == "Choose 1-4 [4]:\n> "
    assert out.count(cli.MEANS_QUESTION) == 1
    for _key, description in cli.MEANS_OPTIONS:
        assert description in out
    assert "Company" in asked[4]
    # No picker, no brand list, no Bedrock.
    assert "Model provider" not in out
    assert "Which intelligence brand" not in out + "".join(asked)
    assert "Bedrock" not in out + "".join(asked)

    config = load_config(finished["config_path"])
    assert config["agent"]["intelligence"] == "Fable"
    assert config["agent"]["intelligence_brand"] == "Fable"
    assert config["agent"]["intelligence_maker"] is None
    assert config["model"]["adapter"] == "anthropic"
    for payload in (api.validated[0], api.registered[0]):
        assert payload["intelligence"] == {"brand": "Fable"}
        base_model = payload["system_record"]["base_models"][0]
        assert base_model["brand"] == "Fable"
        assert "maker" not in base_model
        assert base_model["model"] == "claude-fable-5-1"
    assert finished["result"]["status"] == WAITING_FOR_COMPANY_VERIFICATION


def test_plain_init_takes_a_typed_name_as_given_and_its_own_command(tmp_path, monkeypatch):
    api = CapturingApi()
    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", lambda base_url: api)
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda _path: {})
    _which(monkeypatch, "claude", "codex")
    finished = {}
    monkeypatch.setattr(
        cli,
        "_finish_init",
        lambda config_path, result, *, checks=None, enable_worker: finished.update(
            config_path=config_path
        )
        or 0,
    )
    asked = _scripted_input(
        monkeypatch,
        [
            "Oak", "Instinct",
            "",  # a name with no CLI of its own: the command is the default
            "instinct-run --bench",
            "Oak Works", "", "", "", "https://example.com", "", "US-OR", "ops@example.com",
        ],
    )
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--yes", "--no-worker"]
    )

    assert cli.command_init(arguments) == 0

    assert asked[2] == "Choose 1-4 [1]:\n> "
    assert not any("model" in prompt.lower() for prompt in asked)
    config = load_config(finished["config_path"])
    assert config["agent"]["intelligence_brand"] == "Instinct"
    assert config["model"]["adapter"] == "external"
    assert config["model"]["command"] == ["instinct-run", "--bench"]
    assert config["model"]["model_id"] is None
    # Nothing is stored about a model, and the bench needs one named: the
    # agent's own name for its intelligence is that answer.
    payload = api.registered[0]
    assert payload["disclosure"]["model"] == "Instinct"
    assert payload["system_record"]["base_models"][0]["model"] == "Instinct"
    assert payload["intelligence"] == {"brand": "Instinct"}


def test_plain_init_with_no_name_stops_before_writing(tmp_path, monkeypatch):
    _scripted_input(monkeypatch, ["Oak", ""])
    arguments = cli.build_parser().parse_args(["init", str(tmp_path / "agent")])

    with pytest.raises(ValueError, match="name of the intelligence"):
        cli.command_init(arguments)

    assert not (tmp_path / "agent" / "agent.yaml").exists()


def test_plain_init_refuses_a_name_over_forty_characters_before_writing(tmp_path, monkeypatch):
    _scripted_input(monkeypatch, ["Oak", "x" * 41])
    arguments = cli.build_parser().parse_args(["init", str(tmp_path / "agent")])

    with pytest.raises(ValueError, match="40 characters"):
        cli.command_init(arguments)

    assert not (tmp_path / "agent" / "agent.yaml").exists()


def test_payload_carries_intelligence_and_omits_an_unknown_maker(tmp_path):
    answers = replace(_answers(), intelligence_brand="Nemotron")
    config = load_config(create_configuration(tmp_path / "oak", answers))

    payload = registration_payload(config, PROTOCOL)

    assert payload["intelligence"] == {"brand": "Nemotron"}
    assert payload["system_record"]["base_models"][0]["brand"] == "Nemotron"
    assert "maker" not in payload["system_record"]["base_models"][0]
    # The version and harness stay recorded on the system record.
    assert payload["system_record"]["harness"] == "Toll Harness"
    assert payload["system_record"]["harness_version"]


def test_a_brand_longer_than_forty_characters_is_refused_before_writing(tmp_path):
    answers = replace(_answers(), intelligence_brand="x" * 41)

    with pytest.raises(ValueError, match="40 characters"):
        create_configuration(tmp_path / "long", answers)

    assert not (tmp_path / "long" / "agent.yaml").exists()


def _old_config(tmp_path, *, model_id):
    """An agent.yaml written before the brand question existed."""
    config_path = create_configuration(tmp_path / "old", _answers())
    config = yaml.safe_load(config_path.read_text())
    del config["agent"]["intelligence_brand"]
    del config["agent"]["intelligence_maker"]
    config["model"]["model_id"] = model_id
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    return config_path


def test_an_old_config_without_the_brand_still_loads_and_runs(tmp_path, monkeypatch):
    from toll_harness import config as config_module
    from toll_harness.config import build_runtime, load_agent_identity

    config_path = _old_config(tmp_path, model_id="mistral.mistral-large-3-675b-instruct")
    config = load_config(config_path)
    assert "intelligence_brand" not in config["agent"]

    identity = load_agent_identity(config)
    assert identity is not None and identity.name == "Kori"

    secret_store(config_path, config).set(config["toll_bench"]["token_secret"], "a-token")
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(
        config_module,
        "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )
    assert build_runtime(config_path) is not None

    # Registration still goes out; with no brand to state, the slot is left
    # for the bench's validation to name rather than guessed.
    payload = registration_payload(config, PROTOCOL)
    assert "intelligence" not in payload
    assert "brand" not in payload["system_record"]["base_models"][0]


def test_an_old_config_registers_with_no_guessed_brand(tmp_path):
    # 0.74.0: nothing is read off the model id; the bench names the slot.
    config_path = _old_config(tmp_path, model_id="gpt-5.6-sol")
    api = CapturingApi()

    result = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert result["status"] == WAITING_FOR_COMPANY_VERIFICATION
    assert "intelligence" not in api.registered[0]
    # The stored config is left as it was; nothing is rewritten behind the operator.
    assert "intelligence_brand" not in load_config(config_path)["agent"]
