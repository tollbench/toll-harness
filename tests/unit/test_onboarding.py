import json

import yaml

from toll_harness.onboarding import (
    READY,
    WAITING_FOR_COMPANY_VERIFICATION,
    InitAnswers,
    advance_connected_onboarding,
    create_configuration,
    load_config,
)


class FakeBookOfHousesApi:
    def __init__(self):
        self.confirmed = False
        self.register_calls = 0
        self.reachability_acks = 0
        self.address = "production-returned@bookofhouses.com"

    def protocol(self):
        return {
            "protocol_version": "test",
            "contract_version": "test",
            "rules_version_hash": "rules-hash",
        }

    def validate_registration(self, payload):
        assert payload["handle"] == "Kori"
        assert payload["rules"]["version_hash"] == "rules-hash"
        return {"ok": True, "problem_count": 0, "problems": []}

    def register(self, payload, idempotency_key):
        self.register_calls += 1
        assert idempotency_key.startswith("toll-harness-register-")
        return {
            "ok": True,
            "maker_id": "maker-oak",
            "registry_no": "A-001",
            "email_mailbox": {
                "address": self.address,
                "enabled": True,
                "outbound_enabled": False,
            },
            "rest_token": "never-persist-in-state",
        }

    def authenticated(self, token, maker_id):
        assert token == "never-persist-in-state"
        assert maker_id == "maker-oak"
        return self

    def me(self):
        return {
            "responsible_party_contact": {
                "confirmed": self.confirmed,
                "sent_at": "2026-08-24T20:02:17Z",
            },
            "reachability_test": {
                "reachable": self.reachability_acks >= 2,
                "ping": min(self.reachability_acks + 1, 2),
            },
        }

    def ack_reachability_ping(self):
        self.reachability_acks += 1
        return self.me()

    def mailbox(self):
        return {
            "mailbox": {
                "address": self.address,
                "enabled": True,
                "outbound_enabled": self.confirmed,
            }
        }


def _answers(*, connected=True, use_email=None):
    if use_email is None:
        use_email = connected
    return InitAnswers(
        agent_name="Kori",
        intelligence="Mistral",
        model_id="mistral.mistral-large-3-675b-instruct",
        company="House of Play",
        mode="Autonomous",
        aws_profile="example-bedrock-profile",
        aws_region="us-west-2",
        connect_toll_bench=connected,
        use_book_of_houses_email=use_email,
        company_url="https://bookofhouses.com/house/play" if connected else None,
        responsible_legal_name="House of Play" if connected else None,
        responsible_jurisdiction="US-OR" if connected else None,
        verification_recipient=("houseofplay@bookofhouses.com" if connected else None),
    )


def test_connected_onboarding_persists_pending_and_resumes_without_secret_leak(tmp_path):
    config_path = create_configuration(tmp_path / "oak", _answers())
    api = FakeBookOfHousesApi()

    pending = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert pending["status"] == WAITING_FOR_COMPANY_VERIFICATION
    assert pending["confirmation_issued_at"] == "2026-08-24T20:02:17Z"
    assert api.reachability_acks == 2
    assert api.register_calls == 1
    config = yaml.safe_load(config_path.read_text())
    data_dir = config_path.parent / config["storage"]["directory"]
    onboarding_text = (data_dir / "onboarding.json").read_text()
    config_text = config_path.read_text()
    assert "never-persist-in-state" not in onboarding_text
    assert "never-persist-in-state" not in config_text
    assert config["email"]["address"] is None
    assert config["email"]["status"] == "pending_provisioning"
    assert "toll_bench.attention" in config["runtime"]["tools"]

    api.confirmed = True
    ready = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert ready == {
        "status": READY,
        "mailbox": api.address,
        "outbound_enabled": True,
        "maker_id": "maker-oak",
        "registry_no": "A-001",
    }
    assert api.register_calls == 1
    config = yaml.safe_load(config_path.read_text())
    assert config["email"]["address"] == api.address
    assert config["email"]["status"] == "provisioned"
    state = json.loads((data_dir / "onboarding.json").read_text())
    assert state["status"] == READY


def test_standalone_configuration_has_no_toll_bench_dependency(tmp_path):
    config_path = create_configuration(tmp_path / "standalone", _answers(connected=False))
    config = yaml.safe_load(config_path.read_text())

    assert config["toll_bench"]["connected"] is False
    assert config["providers"]["email"] == "disabled"
    assert config["email"]["address"] is None
    assert not any(tool.startswith("toll_bench.") for tool in config["runtime"]["tools"])


def test_connected_without_email_does_not_activate_mailbox(tmp_path):
    config_path = create_configuration(
        tmp_path / "connected-no-email", _answers(connected=True, use_email=False)
    )
    api = FakeBookOfHousesApi()

    pending = advance_connected_onboarding(config_path, approve_registration=True, api=api)
    assert pending["status"] == WAITING_FOR_COMPANY_VERIFICATION

    api.confirmed = True
    ready = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert ready["status"] == READY
    config = yaml.safe_load(config_path.read_text())
    assert config["toll_bench"]["token_secret"] == "book_of_houses_agent_token"
    assert config["providers"]["email"] == "disabled"
    assert config["email"]["status"] == "ineligible"
    assert config["email"]["address"] is None


def test_claude_subscription_configuration_carries_no_credentials(tmp_path):
    from dataclasses import replace

    answers = replace(
        _answers(connected=False),
        model_adapter="claude_code",
        model_id="opus",
        aws_profile=None,
    )
    config_path = create_configuration(tmp_path / "subscription", answers)

    text = config_path.read_text()
    config = load_config(config_path)
    assert config["model"] == {"adapter": "claude_code", "model_id": "opus", "timeout_seconds": 600}
    # The subscription rail must reference no key or AWS credential material.
    assert "api_key" not in text.lower()
    assert "aws_profile" not in text.lower()
    # No AWS-credentialed browser default on a non-Bedrock rail.
    assert config["providers"]["browser"] == "disabled"


def test_pasted_api_key_lands_only_in_the_secret_store(tmp_path):
    from dataclasses import replace

    answers = replace(
        _answers(connected=False),
        model_adapter="anthropic",
        model_id="claude-opus-4-8",
        model_api_key="sk-ant-test-1234567890",
    )
    config_path = create_configuration(tmp_path / "keyed", answers)

    assert "sk-ant-test-1234567890" not in config_path.read_text()
    config = load_config(config_path)
    assert config["model"]["api_key_secret"] == "anthropic_api_key"
    from toll_harness.onboarding import secret_store

    assert secret_store(config_path, config).get("anthropic_api_key") == "sk-ant-test-1234567890"


def test_pasted_key_is_refused_on_a_subscription_rail(tmp_path):
    from dataclasses import replace

    import pytest

    answers = replace(
        _answers(connected=False), model_adapter="claude_code", model_api_key="sk-oops"
    )
    with pytest.raises(ValueError, match="pasted API key"):
        create_configuration(tmp_path / "wrong", answers)


def test_agent_yaml_is_owner_only_and_stamps_real_harness_version(tmp_path):
    # agent.yaml carries the verification contact, so it is 0600 like every
    # other private file; and the harness identifies as its installed version,
    # not a hardcoded "0.1".
    import os
    import stat

    from toll_harness import __version__

    config_path = create_configuration(tmp_path / "stamped", _answers(connected=False))

    mode = stat.S_IMODE(os.stat(config_path).st_mode)
    assert mode == 0o600
    config = load_config(config_path)
    assert config["agent"]["harness"] == f"Toll Harness {__version__}"
    assert config["benchmark"]["harness"] == f"Toll Harness {__version__}"
