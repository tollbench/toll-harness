"""THE HARNESS CARRIES EVERY STEP FIELD THE BENCH PUBLISHES (0.57.2).

WHAT FORCED THESE TESTS. `read_form` keeps only the step keys on
`FORM_STEP_FIELDS` and drops the rest without a word. The tuple was written by
hand and fell behind the bench: by contract 4.1.11 it still dropped `ask`,
`count`, `from_step`, `fields`, `cost`, `line_item_amount`, `fallback` and the
calendar slots `starts_on` / `ends_on` / `with`, so no harness agent could
fill those holes however well it answered.

The source of truth is the bench's published OpenAPI schema
`components.schemas.PlanFormStep.properties`. A snapshot of that list lives in
`tests/fixtures/bench_plan_form_step.json`; refresh it from the published
`agent-api.openapi.json` with every contract bump. Set
`TOLL_BENCH_OPENAPI=/path/to/agent-api.openapi.json` to also check against a
live copy of the schema.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.unit.test_two_stages_r243_r245 import FILLED_STEP
from toll_harness.toll_bench.draft import FORM_STEP_FIELDS, read_form

SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "bench_plan_form_step.json"

# Step properties the bench publishes that an agent must NEVER send. Every
# entry needs a comment saying why. Empty today: every PlanFormStep property
# is a hole the agent fills.
NEVER_SENT: frozenset[str] = frozenset()


def _published(schema: dict) -> set[str]:
    return set(schema["components"]["schemas"]["PlanFormStep"]["properties"])


def _snapshot() -> set[str]:
    return set(json.loads(SNAPSHOT.read_text())["properties"])


def test_every_published_step_property_is_carried():
    dropped = _snapshot() - NEVER_SENT - set(FORM_STEP_FIELDS)
    assert not dropped, (
        f"read_form would silently drop bench step fields {sorted(dropped)}; "
        "add them to FORM_STEP_FIELDS (or to NEVER_SENT with the reason)")


def test_the_harness_sends_no_step_field_the_bench_does_not_publish():
    extra = set(FORM_STEP_FIELDS) - _snapshot()
    assert not extra, f"FORM_STEP_FIELDS names fields the bench never published: {sorted(extra)}"


def test_never_sent_fields_are_really_withheld():
    assert not NEVER_SENT & set(FORM_STEP_FIELDS)
    assert NEVER_SENT <= _snapshot(), "NEVER_SENT names a field the bench no longer publishes"


@pytest.mark.skipif(not os.environ.get("TOLL_BENCH_OPENAPI"),
                    reason="set TOLL_BENCH_OPENAPI to check a live agent-api.openapi.json")
def test_the_snapshot_matches_the_live_schema():
    live = json.loads(Path(os.environ["TOLL_BENCH_OPENAPI"]).read_text())
    assert _published(live) == _snapshot(), (
        "tests/fixtures/bench_plan_form_step.json is stale; refresh it from the live schema")


def test_each_newer_field_rides_read_form_exactly_as_written():
    values = {
        "ask": [{"kind": "text", "say": "Your mailing address?"}],
        "count": 2,
        "from_step": 1,
        "fields": ["headline", "body"],
        "cost": {"cost_cents": 1500, "cost_note": "one print run"},
        "line_item_amount": 2500,
        "fallback": "agent_account",
        "starts_on": "2026-10-09T15:00",
        "ends_on": "2026-10-09T16:00",
        "with": {"$from": "person.who"},
    }
    read = read_form({"overview": "Scope.", "odds": 0.4, "span_days": 7,
                      "steps": [dict(FILLED_STEP, **values)]})
    step = read["steps"][0]
    for name, value in values.items():
        assert step[name] == value, name
