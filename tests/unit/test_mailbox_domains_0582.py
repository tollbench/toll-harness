"""Agent mailboxes on tollbench.com load alongside bookofhouses.com (0.58.2).

WHAT FORCED IT (2026-10-05): the bench began minting new agent mailboxes as
<handle>@tollbench.com while older ones stay on bookofhouses.com. The email
provider refused any address not ending in @bookofhouses.com, so lab agent
DJ-4-Fun (A-0439, dj4fun.tollbench@tollbench.com) could not load its mailbox.
"""

from types import SimpleNamespace

import pytest
import yaml

from toll_harness import config as config_module
from toll_harness.email.book_of_houses import (
    DEFAULT_MAILBOX_DOMAINS,
    EXTRA_MAILBOX_DOMAINS_ENV,
    BookOfHousesEmailProvider,
    BookOfHousesRestMailClient,
    accepted_mailbox_domains,
    mailbox_domain,
)
from toll_harness.onboarding import InitAnswers, create_configuration, secret_store


class FakeClient:
    def __init__(self):
        self.calls = []

    def list_messages(self, mailbox, **parameters):
        self.calls.append(("list", mailbox, parameters))
        return []

    def get_message(self, mailbox, message_id):
        return {"id": message_id, "mailbox": mailbox}

    def send_message(self, mailbox, **message):
        return {"mailbox": mailbox, **message}

    def reply_to_message(self, mailbox, message_id, **message):
        return {"mailbox": mailbox, "in_reply_to": message_id, **message}

    def wait_for_message(self, mailbox, **parameters):
        return {"mailbox": mailbox, **parameters}


@pytest.fixture(autouse=True)
def _no_fleet_wide_extras(monkeypatch):
    monkeypatch.delenv(EXTRA_MAILBOX_DOMAINS_ENV, raising=False)


def test_the_two_platform_domains_are_the_default():
    assert DEFAULT_MAILBOX_DOMAINS == ("bookofhouses.com", "tollbench.com")
    assert accepted_mailbox_domains() == ("bookofhouses.com", "tollbench.com")


@pytest.mark.parametrize(
    "mailbox",
    [
        "dj4fun.tollbench@tollbench.com",
        "DJ4Fun@TollBench.com",
        "sample.agent@bookofhouses.com",
        "Agent@BookOfHouses.COM",
    ],
)
def test_a_mailbox_on_either_platform_domain_loads_and_stays_scoped(mailbox):
    client = FakeClient()
    provider = BookOfHousesEmailProvider(mailbox=mailbox, client=client)

    provider.list(limit=5)
    sent = provider.send(to=["p@example.com"], subject="s", text="t", idempotency_key="k")

    assert provider.mailbox == mailbox
    assert client.calls == [("list", mailbox, {"limit": 5, "unread_only": False})]
    assert sent["mailbox"] == mailbox


@pytest.mark.parametrize(
    "mailbox",
    [
        "agent@example.com",
        "agent@mail.tollbench.com",
        "agent@tollbench.com.example.net",
        "agent@nottollbench.com",
        "agent@mail.bookofhouses.com",
        "tollbench.com",
        "agent@tollbench.com ",
        "",
    ],
)
def test_anything_else_is_refused_and_the_refusal_names_the_accepted_domains(mailbox):
    with pytest.raises(ValueError) as refused:
        BookOfHousesEmailProvider(mailbox=mailbox, client=FakeClient())
    assert "bookofhouses.com" in str(refused.value)
    assert "tollbench.com" in str(refused.value)


def test_the_domain_is_everything_after_the_last_at():
    assert mailbox_domain("a@b@TollBench.com") == "tollbench.com"
    assert mailbox_domain("no-at-sign") == ""
    assert mailbox_domain("") == ""


def test_the_environment_adds_a_domain_and_can_never_take_one_away(monkeypatch):
    monkeypatch.setenv(EXTRA_MAILBOX_DOMAINS_ENV, " @Bench.Example.org., ,typo")
    assert accepted_mailbox_domains() == (
        "bookofhouses.com",
        "tollbench.com",
        "bench.example.org",
        "typo",
    )
    BookOfHousesEmailProvider(mailbox="a@bench.example.org", client=FakeClient())
    BookOfHousesEmailProvider(mailbox="a@bookofhouses.com", client=FakeClient())
    BookOfHousesEmailProvider(mailbox="a@tollbench.com", client=FakeClient())


def test_an_agent_can_add_its_own_domain():
    provider = BookOfHousesEmailProvider(
        mailbox="a@mail.example.org", client=FakeClient(), extra_domains=["mail.example.org"]
    )
    assert provider.accepted_domains == ("bookofhouses.com", "tollbench.com", "mail.example.org")
    with pytest.raises(ValueError, match="mail.example.org"):
        BookOfHousesEmailProvider(
            mailbox="a@other.example.org", client=FakeClient(), extra_domains="mail.example.org"
        )


def test_extra_domains_of_the_wrong_kind_are_refused():
    with pytest.raises(ValueError, match="list of domains"):
        BookOfHousesEmailProvider(mailbox="a@tollbench.com", client=FakeClient(), extra_domains=7)


def _answers():
    return InitAnswers(
        agent_name="DJ",
        intelligence="Claude",
        model_id="example.model",
        company="Toll Bench",
        mode="Autonomous",
        aws_profile="example-profile",
        aws_region="us-west-2",
        connect_toll_bench=True,
        use_book_of_houses_email=True,
        company_url="https://tollbench.com",
        responsible_legal_name="Toll Bench",
        responsible_jurisdiction="US-OR",
        verification_recipient="owner@example.com",
    )


@pytest.mark.parametrize(
    ("address", "extra"),
    [
        ("dj4fun.tollbench@tollbench.com", None),
        ("sample.agent@bookofhouses.com", None),
        ("agent@mail.example.org", ["mail.example.org"]),
    ],
)
def test_build_runtime_loads_a_provisioned_mailbox(tmp_path, monkeypatch, address, extra):
    path = create_configuration(tmp_path / "dj", _answers())
    config = yaml.safe_load(path.read_text())
    secret_store(path, config).set(config["toll_bench"]["token_secret"], "placeholder-token")
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    config["email"]["status"] = "provisioned"
    config["email"]["address"] = address
    if extra is not None:
        config["email"]["extra_domains"] = extra
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(
        config_module,
        "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )

    resources = config_module.build_runtime(path)

    provider = resources.runtime.email_provider
    assert isinstance(provider, BookOfHousesEmailProvider)
    assert provider.mailbox == address
    assert isinstance(provider.client, BookOfHousesRestMailClient)
    assert provider.client.expected_mailbox == address.lower()
    resources.close()


def test_build_runtime_still_refuses_a_mailbox_off_the_accepted_domains(tmp_path, monkeypatch):
    path = create_configuration(tmp_path / "dj", _answers())
    config = yaml.safe_load(path.read_text())
    secret_store(path, config).set(config["toll_bench"]["token_secret"], "placeholder-token")
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    config["email"]["status"] = "provisioned"
    config["email"]["address"] = "agent@example.com"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(
        config_module,
        "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )

    with pytest.raises(ValueError, match="bookofhouses.com, tollbench.com"):
        config_module.build_runtime(path)
