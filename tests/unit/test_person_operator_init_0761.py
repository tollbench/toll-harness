"""A PERSON-RUN AGENT GETS A CLEAN INIT (0.76.1).

What forced it (checker, 2026-11-10). A fresh-context checker registered a
throwaway agent on staging with operator_type person and intelligence
Instinct, then ran `toll-harness init --registered`. After the reachability
pings were acked, GET /api/agent-email/mailbox answered 403
COMPANY_URL_REQUIRED ("This agent is not registered to a company with a
URL."), nothing caught it, and init died on a raw traceback with exit 1. The
quickstart names operator_type person as valid, so that is an ordinary agent,
not an edge. The same run found two more things: the CLI could not be
pointed at any bench but bookofhouses.com without a monkeypatch, and every
bench call said `toll-harness/0.1` (one said 0.71), so the bench's new
last-contact stamp recorded the wrong build.
"""

from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from tests.unit.test_onboarding import FakeBookOfHousesApi, _answers
from toll_harness import cli
from toll_harness import onboarding as onboarding_module
from toll_harness.onboarding import (
    AGENT_TOKEN_ENV,
    BASE_URL_ENV,
    DEFAULT_BASE_URL,
    NO_COMPANY_MAILBOX,
    READY,
    WAITING_FOR_COMPANY_VERIFICATION,
    InitAnswers,
    advance_connected_onboarding,
    create_configuration,
    load_config,
    load_onboarding,
    secret_store,
)
from toll_harness.toll_bench.api import USER_AGENT, BookOfHousesApiClient, BookOfHousesApiError

TOKEN = "person-run-own-token"
UUID = "11111111-2222-4333-8444-555555555555"
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class PersonRunBench(FakeBookOfHousesApi):
    """The fake bench for an agent a person fields: no company, no mailbox."""

    def __init__(self, *, mailbox_status=403):
        super().__init__()
        self.expected_token = TOKEN
        self.operator_name = "Pat Example"
        self.mailbox_status = mailbox_status
        self.mailbox_calls = 0

    def me(self):
        body = super().me()
        body["agent"]["handle"] = "FixCInitStub"
        body["system_record"] = {
            "base_models": [{"provider": None, "model": "instinct", "brand": "Instinct"}]
        }
        return body

    def mailbox(self):
        self.mailbox_calls += 1
        if self.mailbox_status == 403:
            raise BookOfHousesApiError(
                403,
                "COMPANY_URL_REQUIRED",
                "This agent is not registered to a company with a URL.",
                body={"ok": False, "error": "COMPANY_URL_REQUIRED"},
            )
        raise BookOfHousesApiError(self.mailbox_status, "http_error", "Bad Gateway")


def _bench(monkeypatch, **kwargs):
    made = []

    def client(base_url):
        api = PersonRunBench(**kwargs)
        api.base_url = base_url
        made.append(api)
        return api

    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", client)
    return made


def _scripted_input(monkeypatch, answers):
    queue = list(answers)

    def fake_input(prompt=""):
        if not queue:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def _run_init(monkeypatch, tmp_path):
    """The checker's run: Enter (a command), `cat`, Enter (Autonomous).

    The model check and the canary are the ones that run for any agent; they
    are stood in for here because this test is about the bench's answers.
    """
    monkeypatch.setenv(AGENT_TOKEN_ENV, TOKEN)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        cli, "_test_model_and_browser", lambda path: {"model_connected": True}
    )
    monkeypatch.setattr(
        cli,
        "_run_init_canary",
        lambda path: {"canary_completed": True, "run_id": "r", "actions": [], "tokens": 0},
    )
    _scripted_input(monkeypatch, ["", "cat", ""])
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--registered", "--no-worker"]
    )
    return cli.command_init(arguments)


def test_a_person_run_agent_inits_to_exit_0_with_no_mailbox_and_no_traceback(
    tmp_path, monkeypatch, capsys
):
    made = _bench(monkeypatch)

    code = _run_init(monkeypatch, tmp_path)

    assert code == 0
    out = capsys.readouterr().out
    assert NO_COMPANY_MAILBOX in out
    assert out.count(NO_COMPANY_MAILBOX) == 1
    assert "Traceback" not in out
    payload = json.loads(out[out.index("{"):])
    assert payload["status"] == WAITING_FOR_COMPANY_VERIFICATION
    assert payload["company_mailbox"] is False
    assert payload["checks"] == {"model_connected": True}
    assert payload["canary"]["canary_completed"] is True
    bench = made[-1]
    assert bench.mailbox_calls == 1
    assert bench.reachability_acks == 2  # the pings were acked, as before

    config_path = tmp_path / "agent" / "agent.yaml"
    config = load_config(config_path)
    # The email lane is off in exactly the keys build_runtime opens it by.
    assert config["providers"]["email"] == "disabled"
    assert config["email"]["provider"] == "disabled"
    assert config["email"]["status"] == "ineligible"
    assert config["email"]["address"] is None
    assert config["toll_bench"]["maker_id"] == "maker-oak"
    assert config["agent"]["name"] == "FixCInitStub"
    assert config["agent"]["company"] == "Pat Example"
    state = load_onboarding(config_path, config)
    assert state["company_mailbox"] is False
    assert state["mailbox"] is None
    assert TOKEN not in config_path.read_text()
    assert secret_store(config_path, config).get("book_of_houses_agent_token") == TOKEN


def test_a_confirmed_person_run_agent_is_ready_without_a_mailbox(tmp_path):
    config_path = create_configuration(tmp_path / "pat", _answers())
    api = PersonRunBench()
    api.expected_token = "never-persist-in-state"
    api.confirmed = True

    ready = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert ready["status"] == READY
    assert ready["mailbox"] is None
    assert ready["company_mailbox"] is False
    config = yaml.safe_load(config_path.read_text())
    assert config["toll_bench"]["status"] == READY
    assert config["providers"]["email"] == "disabled"
    assert config["email"]["status"] == "ineligible"


def test_a_bench_that_is_down_on_the_mailbox_probe_still_raises(tmp_path):
    config_path = create_configuration(tmp_path / "pat", _answers())
    api = PersonRunBench(mailbox_status=502)
    api.expected_token = "never-persist-in-state"

    with pytest.raises(BookOfHousesApiError) as raised:
        advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert raised.value.status == 502
    config = yaml.safe_load(config_path.read_text())
    assert config["providers"]["email"] == "book_of_houses"


def test_a_company_agent_still_gets_its_mailbox_and_no_line(tmp_path):
    config_path = create_configuration(tmp_path / "oak", _answers())
    api = FakeBookOfHousesApi()
    api.confirmed = True

    ready = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert ready["mailbox"] == api.address
    assert "company_mailbox" not in ready
    config = yaml.safe_load(config_path.read_text())
    assert config["providers"]["email"] == "book_of_houses"
    assert config["email"]["status"] == "provisioned"


# ---------------------------------------------------------------------------
# TOLL_HARNESS_BASE_URL: one name from init to every run
# ---------------------------------------------------------------------------
def test_init_points_at_the_bench_named_in_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(BASE_URL_ENV, "http://127.0.0.1:5001/")
    made = _bench(monkeypatch)

    assert _run_init(monkeypatch, tmp_path) == 0

    # The /me read before anything is written, and every client after it.
    assert [api.base_url for api in made] == ["http://127.0.0.1:5001"] * len(made)
    config = load_config(tmp_path / "agent" / "agent.yaml")
    # Written to the key the worker, --resume and the update check read.
    assert config["toll_bench"]["base_url"] == "http://127.0.0.1:5001"


def test_without_the_variable_init_points_at_the_default_bench(tmp_path):
    config = load_config(create_configuration(tmp_path / "oak", _answers()))
    assert config["toll_bench"]["base_url"] == DEFAULT_BASE_URL == "https://bookofhouses.com"


def test_the_worker_reads_the_base_url_init_wrote(tmp_path, monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV, "http://127.0.0.1:5001")
    answers = InitAnswers(
        **{**_answers().__dict__, "model_adapter": "external", "model_command": ("cat",)}
    )
    config_path = create_configuration(tmp_path / "oak", answers)
    monkeypatch.delenv(BASE_URL_ENV)
    config = load_config(config_path)
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    secret_store(config_path, config).set("book_of_houses_agent_token", "t")
    from toll_harness.config import build_runtime

    resources = build_runtime(config_path)
    try:
        assert resources.runtime.toll_bench_provider.api.base_url == "http://127.0.0.1:5001"
    finally:
        resources.close()


# ---------------------------------------------------------------------------
# The User-Agent carries the version actually installed, on every bench door
# ---------------------------------------------------------------------------
def _declared() -> str:
    for line in PYPROJECT.read_text().splitlines():
        match = re.match(r'^version\s*=\s*"([^"]+)"\s*$', line.strip())
        if match:
            return match.group(1)
    raise AssertionError("pyproject.toml declares no version")


class _Answer(io.BytesIO):
    status = 200
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_every_bench_door_sends_the_pyproject_version(monkeypatch):
    assert USER_AGENT == f"toll-harness/{_declared()}"
    seen: list[str] = []

    def answer(request, timeout=None):
        seen.append(request.get_header("User-agent"))
        return _Answer(b"{}")

    class Opener:
        def open(self, request, timeout=None):
            return answer(request, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", answer)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    client = BookOfHousesApiClient(base_url="https://bench.test", token="tok")

    client.me()  # _request, through urlopen
    client.get_pack_drafts(UUID)  # _request, through the no-redirect opener
    client.post_thinking("t-1", {"entries": []})
    client.get_pack_input_file(UUID, UUID)
    client.paid_pack_receipt_file(UUID, UUID)
    monkeypatch.setattr(client, "protocol", lambda: {"skill": "/static/agent-skill.md"})
    client.skill()  # _request_text

    assert len(seen) == 6
    assert set(seen) == {f"toll-harness/{_declared()}"}


def test_no_bench_door_hard_codes_a_version():
    source = (
        Path(__file__).resolve().parents[2] / "src" / "toll_harness" / "toll_bench" / "api.py"
    ).read_text()
    assert re.findall(r"toll-harness/\d", source) == []


# ---------------------------------------------------------------------------
# The canary is bounded: a command that echoes its prompt fails it, quickly
# ---------------------------------------------------------------------------
def test_an_echoing_command_fails_the_canary_in_a_few_turns_not_a_traceback(
    tmp_path, monkeypatch
):
    script = tmp_path / "echo.py"
    script.write_text("import sys\nsys.stdout.write(sys.stdin.read())\n")
    answers = InitAnswers(
        agent_name="Echo",
        intelligence="Instinct",
        model_id=None,
        company="Pat Example",
        mode="Autonomous",
        aws_profile=None,
        aws_region="us-west-2",
        connect_toll_bench=False,
        use_book_of_houses_email=False,
        model_adapter="external",
        model_command=(sys.executable, str(script)),
    )
    config_path = create_configuration(tmp_path / "echo", answers)
    config = load_config(config_path)
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    canary = cli._run_init_canary(config_path)

    assert canary["canary_completed"] is False
    assert canary["actions"] == []
    from toll_harness.config import build_runtime

    resources = build_runtime(config_path)
    try:
        events = resources.store.list_events(canary["run_id"])
        turns = [event for event in events if event.kind == "model.response"]
        assert len(turns) <= cli.CANARY_MAX_ITERATIONS
        # The agent's own budget is put back for the runs that follow.
        assert resources.runtime.max_iterations == 20
    finally:
        resources.close()


def test_the_real_clients_403_reaches_the_mailbox_probe_as_no_mailbox(monkeypatch):
    # The bench's own words, through the real client: a 403 is "none".
    body = json.dumps({"ok": False, "error": "COMPANY_URL_REQUIRED",
                       "message": "This agent is not registered to a company with a URL."})

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(body.encode())
        )

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = BookOfHousesApiClient(base_url="https://bench.test", token="tok")
    assert onboarding_module._company_mailbox(client) is None
