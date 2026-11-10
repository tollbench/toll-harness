"""THE RECORD NAMES THE INTELLIGENCE; INIT ASKS ONLY THE MEANS (0.76.0).

What forced it (Steven, 2026-11-10): Instinct (A-0137) installed the harness
with its own token, was asked which model thinks for it, read the answer as
the harness having no intelligence of its own, and told its operator the
harness could not work until a model ran on a machine they owned. That was
false and sent the operator chasing a non-problem for a day.

`init --registered` now reads GET /api/bench/me first, says back the
intelligence the agent named at registration, and asks one question: how the
harness gets the agent's answer. A command on this machine is option 1 and
the default for any name the harness has no CLI for; it stores the command
and nothing about a model.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from tests.unit.test_onboarding import FakeBookOfHousesApi
from toll_harness import cli
from toll_harness import onboarding as onboarding_module
from toll_harness.config import build_model_adapter
from toll_harness.core.types import ModelMessage
from toll_harness.models.base import ModelInvocationError
from toll_harness.onboarding import (
    AGENT_TOKEN_ENV,
    intelligence_on_record,
    load_config,
    secret_store,
)
from toll_harness.toll_bench.api import BookOfHousesApiError

TOKEN = "instinct-own-token"
REPO = Path(__file__).resolve().parents[2]
INSTINCT = [{"provider": None, "model": "instinct", "version": None, "brand": "Instinct"}]


class RecordApi(FakeBookOfHousesApi):
    """The fake bench; /me carries the agent's system record."""

    def __init__(self, base_models):
        super().__init__()
        self.expected_token = TOKEN
        self.operator_name = "Instinct Labs"
        self.base_models = base_models
        self.me_calls = 0

    def me(self):
        self.me_calls += 1
        body = super().me()
        body["system_record"] = {"base_models": self.base_models}
        return body


def _bench(monkeypatch, base_models):
    made = []

    def client(base_url):
        api = RecordApi(base_models)
        made.append(api)
        return api

    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", client)
    return made


def _scripted_input(monkeypatch, answers):
    queue = list(answers)
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        if not queue:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return queue.pop(0)

    def no_secret(*_args, **_kwargs):
        raise AssertionError("init --registered never asks for a secret")

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli.getpass, "getpass", no_secret)
    return asked


def _which(monkeypatch, *present):
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None
    )


def _init(monkeypatch, tmp_path):
    monkeypatch.setenv(AGENT_TOKEN_ENV, TOKEN)
    finished = {}

    def fake_finish(config_path, result, *, checks=None, enable_worker):
        finished.update(config_path=config_path, result=result)
        return 0

    monkeypatch.setattr(cli, "_finish_init", fake_finish)
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--registered", "--no-worker"]
    )
    code = cli.command_init(arguments)
    return code, finished


def test_instinct_on_the_record_gets_the_command_as_the_default_and_no_brand_picker(
    tmp_path, monkeypatch, capsys
):
    _bench(monkeypatch, INSTINCT)
    _which(monkeypatch)  # no claude, no codex on this machine
    asked = _scripted_input(monkeypatch, ["", "instinct-run --bench", ""])

    code, _finished = _init(monkeypatch, tmp_path)

    assert code == 0
    out = capsys.readouterr().out
    assert "Your record says the intelligence is Instinct." in out
    assert out.count(cli.MEANS_QUESTION) == 1
    assert "  1) a command on this machine that takes a prompt on stdin and prints the reply" in out
    assert "  2) the claude CLI" in out
    assert "  3) the codex CLI" in out
    assert "  4) an API key for the model named on your record" in out
    # Option 1 is the default: a plain Enter chose it.
    assert asked[0] == "Choose 1-4 [1]:\n> "
    # The means, the command, the mode. No brand picker, no model provider
    # list, no model question.
    assert asked[1:] == [f"{cli.COMMAND_LINE_QUESTION}:\n> ", "Operating mode [Autonomous]:\n> "]
    assert cli.INTELLIGENCE_BRAND_QUESTION not in out + "".join(asked)
    assert "Model provider" not in out
    assert not any("model" in prompt.lower() for prompt in asked)


def test_option_1_stores_the_command_and_nothing_about_a_model(tmp_path, monkeypatch):
    _bench(monkeypatch, INSTINCT)
    _which(monkeypatch)
    _scripted_input(monkeypatch, ["1", "instinct-run --bench 'two words'", ""])

    code, finished = _init(monkeypatch, tmp_path)

    assert code == 0
    config = load_config(finished["config_path"])
    assert config["model"] == {
        "adapter": "external",
        "command": ["instinct-run", "--bench", "two words"],
        "model_id": None,
        "timeout_seconds": 600,
    }
    # The name on the record, as written, is the intelligence everywhere.
    assert config["agent"]["intelligence"] == "Instinct"
    assert config["agent"]["intelligence_brand"] == "Instinct"
    assert config["benchmark"]["intelligence"] == "Instinct"
    assert config["agent"]["company"] == "Instinct Labs"
    assert config["toll_bench"]["maker_id"] == "maker-oak"
    assert TOKEN not in finished["config_path"].read_text()


def test_the_command_adapter_round_trips_a_prompt(tmp_path):
    script = tmp_path / "answer.py"
    script.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "said = 'heard the marker' if 'MARKER-7731' in prompt else 'no marker'\n"
        "print(json.dumps({'text': said, 'tool_calls': []}))\n"
    )
    config = {"model": {"adapter": "external", "command": [sys.executable, str(script)]}}
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)

    response = adapter.invoke(
        system="This is a provider connectivity check.",
        messages=[ModelMessage.text("user", "Say MARKER-7731 back.")],
        tools=[],
    )

    assert response.text == "heard the marker"
    assert response.tool_calls == []


def test_a_command_that_prints_a_plain_reply_is_still_heard(tmp_path):
    script = tmp_path / "plain.py"
    script.write_text("import sys\nsys.stdin.read()\nprint('OK')\n")
    config = {"model": {"adapter": "external", "command": [sys.executable, str(script)]}}
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)

    response = adapter.invoke(
        system="check", messages=[ModelMessage.text("user", "Reply with OK.")], tools=[]
    )

    assert response.text == "OK"


def test_a_command_that_exits_nonzero_is_a_model_failure(tmp_path):
    script = tmp_path / "fails.py"
    script.write_text("import sys\nsys.stdin.read()\nsys.stderr.write('no answer')\nsys.exit(3)\n")
    config = {"model": {"adapter": "external", "command": [sys.executable, str(script)]}}
    adapter = build_model_adapter(config, root=tmp_path, data_dir=tmp_path)

    with pytest.raises(ModelInvocationError) as caught:
        adapter.invoke(system="check", messages=[ModelMessage.text("user", "hi")], tools=[])

    assert caught.value.code == "exit_3"
    assert "no answer" in caught.value.message


def test_a_record_that_names_a_known_cli_makes_it_the_default(tmp_path, monkeypatch, capsys):
    _bench(
        monkeypatch,
        [{"provider": "Anthropic", "model": "claude-fable-5-1", "version": "1", "brand": "Fable",
          "maker": "Anthropic"}],
    )
    _which(monkeypatch, "claude")
    asked = _scripted_input(monkeypatch, ["", "", ""])

    code, finished = _init(monkeypatch, tmp_path)

    assert code == 0
    assert "Your record says the intelligence is Fable." in capsys.readouterr().out
    assert asked[0] == "Choose 1-4 [2]:\n> "
    assert asked[1] == "Model, as the claude CLI names it [claude-fable-5-1]:\n> "
    config = load_config(finished["config_path"])
    assert config["model"]["adapter"] == "claude_code"
    assert config["model"]["model_id"] == "claude-fable-5-1"
    assert config["agent"]["intelligence"] == "Fable"
    assert config["agent"]["intelligence_maker"] == "Anthropic"


def test_option_4_reads_the_key_from_the_environment_never_typed(tmp_path, monkeypatch):
    _bench(monkeypatch, [{"provider": "OpenAI", "model": "gpt-6.1-sol", "brand": "Sol"}])
    _which(monkeypatch)  # no codex: the key is the default
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    asked = _scripted_input(monkeypatch, ["", "", ""])

    code, finished = _init(monkeypatch, tmp_path)

    assert code == 0
    assert asked[0] == "Choose 1-4 [4]:\n> "
    assert asked[1] == "Model id, as the API names it [gpt-6.1-sol]:\n> "
    config_path = finished["config_path"]
    config = load_config(config_path)
    assert config["model"]["adapter"] == "openai"
    assert config["model"]["model_id"] == "gpt-6.1-sol"
    assert "base_url" not in config["model"]
    assert secret_store(config_path, config).get("openai_api_key") == "sk-from-the-environment"
    assert "sk-from-the-environment" not in config_path.read_text()


def test_option_4_without_the_key_stops_before_anything_is_written(tmp_path, monkeypatch):
    _bench(monkeypatch, INSTINCT)
    _which(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _scripted_input(monkeypatch, ["4", "instinct-1", ""])

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        _init(monkeypatch, tmp_path)

    assert not (tmp_path / "agent" / "agent.yaml").exists()


class SilentBench(FakeBookOfHousesApi):
    def __init__(self):
        super().__init__()
        self.expected_token = TOKEN

    def me(self):
        raise BookOfHousesApiError(0, "connection_error", "the bench did not answer")


def test_a_me_that_does_not_answer_is_one_line_and_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", lambda base_url: SilentBench())
    _scripted_input(monkeypatch, [])

    code, _finished = _init(monkeypatch, tmp_path)

    assert code == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("GET /api/bench/me did not answer for this token")
    assert TOKEN not in err
    assert not (tmp_path / "agent" / "agent.yaml").exists()


def test_the_record_reader_takes_the_name_as_written():
    assert intelligence_on_record({})["name"] is None
    assert intelligence_on_record({"system_record": None})["name"] is None
    assert intelligence_on_record({"intelligence": {"brand": "Instinct"}})["name"] == "Instinct"
    record = intelligence_on_record(
        {"system_record": {"base_models": [{"provider": "Perplexity", "model": "sonar-pro"}]}}
    )
    assert record["name"] == "sonar-pro"  # no brand stated: the model, never a guess
    assert record["brand"] is None
    assert intelligence_on_record({"system_record": {"base_models": ["my-own-agent"]}}) == {
        "name": "my-own-agent",
        "brand": None,
        "maker": None,
        "provider": None,
        "model": "my-own-agent",
    }


# THE WORDS (0.76.0): nothing the harness prints or publishes calls the agent's
# intelligence missing. Docstrings and comments are not output; the one
# HTML attribute name the browser reads is code.
BANNED = re.compile(r"brain|placeholder|can'?t think|needs a model|machine you own", re.I)


def _output_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_attribute"
        ):
            skip.update(id(arg) for arg in node.args)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    ]


def test_no_src_string_or_doc_page_says_brain_or_placeholder():
    found = []
    for path in sorted((REPO / "src" / "toll_harness").rglob("*.py")):
        for line, text in _output_strings(path):
            if BANNED.search(text):
                found.append(f"{path.relative_to(REPO)}:{line}: {text[:80]!r}")
    for page in [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]:
        for number, text in enumerate(page.read_text().splitlines(), start=1):
            if BANNED.search(text):
                found.append(f"{page.relative_to(REPO)}:{number}: {text[:80]!r}")
    assert found == []


def test_no_page_says_a_hosted_agent_does_not_need_the_harness():
    pages = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
    text = "\n".join(" ".join(page.read_text().split()) for page in pages)
    assert "the API is enough" not in text
    assert "do not need this harness" not in text
    assert "does not need the harness" not in text
    readme = " ".join((REPO / "README.md").read_text().split())
    assert (
        "If you can keep a process running on a machine, run the harness. If you cannot, the "
        "bench's 201 carries the same instructions as a list under `on_your_own`, and the "
        "skill page has the full version." in readme
    )


def test_the_record_line_and_role_line_carry_the_ruling():
    line = cli._record_line({"name": "Instinct"})
    assert line == "Your record says the intelligence is Instinct."
    assert cli._record_line({"name": None}) == "Your record names no intelligence yet."
    assert "the thinking is yours" in cli.HARNESS_ROLE_LINE
