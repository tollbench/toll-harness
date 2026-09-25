"""The intelligence brand: init asks it after the model rail, registration sends it.

The bench ranks the company fielding an agent together with its intelligence
brand (Fable, Astra, Sol, Luna, Muse, Grok, Gemini...); the exact model version
and the harness are recorded on the system record, never ranked.
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
    suggest_intelligence_brand,
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


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("claude-fable-5-1", ("Fable", "Anthropic")),
        ("us.anthropic.claude-fable-5", ("Fable", "Anthropic")),
        ("gpt-6-astra", ("Astra", "OpenAI")),
        ("gpt-5.6-sol", ("Sol", "OpenAI")),
        ("gpt-5.6-luna", ("Luna", "OpenAI")),
        ("muse-spark", ("Muse", "Meta")),
        ("grok-4.7", ("Grok", "xAI")),
        ("grok4", ("Grok", "xAI")),
        ("gemini-3-pro", ("Gemini", "Google")),
        ("mistral.mistral-large-3-675b-instruct", None),
        ("opus", None),
        ("console-model", None),
        ("", None),
        (None, None),
    ],
)
def test_the_suggestion_is_read_off_the_model_id(model_id, expected):
    assert suggest_intelligence_brand(model_id) == expected


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


def test_plain_init_asks_the_brand_after_the_rail_with_the_suggestion_and_sends_it(
    tmp_path, monkeypatch
):
    api = CapturingApi()
    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", lambda base_url: api)
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda _path: {})
    finished = {}

    def fake_finish(config_path, result, *, checks=None, enable_worker):
        finished.update(config_path=config_path, result=result)
        return 0

    monkeypatch.setattr(cli, "_finish_init", fake_finish)
    asked = _scripted_input(
        monkeypatch,
        [
            "Oak",  # agent name
            "C",  # Anthropic API key rail
            "claude-fable-5-1",  # model id
            "",  # brand: take the suggestion
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

    brand_prompts = [p for p in asked if p.startswith(cli.INTELLIGENCE_BRAND_QUESTION)]
    assert brand_prompts == [f"{cli.INTELLIGENCE_BRAND_QUESTION} [Fable]:\n> "]
    # Right after the rail's last question, before the company.
    assert asked.index(brand_prompts[0]) == 3
    assert "Company" in asked[4]

    config = load_config(finished["config_path"])
    assert config["agent"]["intelligence_brand"] == "Fable"
    assert config["agent"]["intelligence_maker"] == "Anthropic"
    for payload in (api.validated[0], api.registered[0]):
        assert payload["intelligence"] == {"brand": "Fable", "maker": "Anthropic"}
        base_model = payload["system_record"]["base_models"][0]
        assert base_model["brand"] == "Fable"
        assert base_model["maker"] == "Anthropic"
        assert base_model["model"] == "claude-fable-5-1"
    assert finished["result"]["status"] == WAITING_FOR_COMPANY_VERIFICATION


def test_plain_init_takes_a_typed_brand_as_given(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda _path: {})
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
        ["Oak", "D", "gpt-5.2", "Nemotron", "Oak Works", "", "n"],
    )
    arguments = cli.build_parser().parse_args(["init", str(tmp_path / "agent")])

    assert cli.command_init(arguments) == 0

    # No suggestion for a model id that names no known brand.
    assert f"{cli.INTELLIGENCE_BRAND_QUESTION}:\n> " in asked
    config = load_config(finished["config_path"])
    assert config["agent"]["intelligence_brand"] == "Nemotron"
    assert config["agent"]["intelligence_maker"] is None


def test_plain_init_with_no_brand_and_no_suggestion_stops_before_writing(tmp_path, monkeypatch):
    _scripted_input(monkeypatch, ["Oak", "D", "gpt-5.2", ""])
    arguments = cli.build_parser().parse_args(["init", str(tmp_path / "agent")])

    with pytest.raises(ValueError, match="intelligence brand"):
        cli.command_init(arguments)

    assert not (tmp_path / "agent" / "agent.yaml").exists()


def test_a_registered_agent_may_leave_the_brand_blank(monkeypatch):
    _scripted_input(monkeypatch, [""])

    assert cli._ask_intelligence_brand("external/custom", required=False) == {
        "intelligence_brand": None,
        "intelligence_maker": None,
    }


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


def test_an_old_config_registers_with_the_brand_its_model_id_names(tmp_path):
    config_path = _old_config(tmp_path, model_id="gpt-5.6-sol")
    api = CapturingApi()

    result = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert result["status"] == WAITING_FOR_COMPANY_VERIFICATION
    assert api.registered[0]["intelligence"] == {"brand": "Sol", "maker": "OpenAI"}
    # The stored config is left as it was; nothing is rewritten behind the operator.
    assert "intelligence_brand" not in load_config(config_path)["agent"]
