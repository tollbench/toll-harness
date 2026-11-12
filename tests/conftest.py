from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.fakebench import TOKEN, FakeBench
from toll_harness.agent import TOKEN_SECRET, load

# The agent's command in these tests: a small script that writes down exactly what it was
# given (its argv, its environment's item id and kind, the item on stdin) and then does what
# its MODE says: answer, say nothing, fail, or sleep past the time limit.
AGENT_SCRIPT = textwrap.dedent("""
    import json, os, sys, time
    record = os.environ["RECORD"]
    item = json.loads(sys.stdin.read())
    with open(record, "a") as handle:
        handle.write(json.dumps({"argv": sys.argv, "id": os.environ.get("TOLL_ITEM_ID"),
                                 "kind": os.environ.get("TOLL_ITEM_KIND"),
                                 "config": os.environ.get("TOLL_AGENT_CONFIG"),
                                 "token_env": "TOLL_HARNESS_AGENT_TOKEN" in os.environ,
                                 "item": item, "at": time.monotonic()}) + "\\n")
    mode = os.environ.get("MODE", "answer")
    if mode == "answer":
        print(json.dumps({"reply": "on it"}))
    elif mode == "fail":
        sys.exit(3)
    elif mode == "sleep":
        time.sleep(30)
    elif mode == "text":
        print("not json at all")
""")


@pytest.fixture
def bench():
    fake = FakeBench().start()
    yield fake
    fake.stop()


class Made:
    def __init__(self, root: Path, bench: FakeBench, monkeypatch):
        self.root = root
        self.bench = bench
        self.record = root / "record.jsonl"
        self.script = root / "agent_script.py"
        self.script.write_text(AGENT_SCRIPT)
        monkeypatch.setenv("RECORD", str(self.record))
        self.monkeypatch = monkeypatch

    def mode(self, value: str) -> None:
        self.monkeypatch.setenv("MODE", value)

    def runs(self) -> list[dict]:
        import json

        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def agent(self, **section):
        folder = self.root / "agent"
        folder.mkdir(exist_ok=True)
        config = {
            "version": 2,
            "agent": {"name": "TestEcho", "company": "Test Owner Co", "model": "Echo",
                      "command": [sys.executable, str(self.script), "--fixed-arg"],
                      "time_limit_minutes": 15, **section},
            "bench": {"url": self.bench.url},
            "update": {"auto": False},
        }
        (folder / "agent.yaml").write_text(yaml.safe_dump(config))
        agent = load(folder)
        agent.secrets.set(TOKEN_SECRET, TOKEN)
        return agent


@pytest.fixture
def made(tmp_path, bench, monkeypatch):
    monkeypatch.delenv("TOLL_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("TOLL_HARNESS_BASE_URL", raising=False)
    return Made(tmp_path, bench, monkeypatch)
