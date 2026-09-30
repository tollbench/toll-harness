"""THE APPROACH FIRST, AND THE THREE ROW SLOTS (0.57.0, bench contract 4.1.3).

WHAT FORCED THESE TESTS. Steven, 2026-09-29: the person now chooses between
proposals on one row each (the "A1 Essential" row), and the row leads with the
agent's APPROACH (three words, one per dial), its CAPABILITIES and a STEP
ESTIMATE. "We need to get them to choose a strategy then design it based on
that. Add that to the onboarding process (optional)." The bench added three
OPTIONAL slots to the proposal door (`approach`, `capabilities`,
`step_estimate`) and an optional `approach` at registration.

So the harness must: ask the model to pick the approach FIRST and write the
rest to follow it; read, settle and send the three slots; keep filing the old
eight-field answer; let `init` store an optional default (agent.yaml
`strategy.approach`) and send it at registration; and never lose a bid or a
registration over one of these optional slots -- a refusal that names one
costs the slot, and the proposal goes once more without it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from tests.unit.test_draft_loop_r241 import _model
from tests.unit.test_intelligence_brand import CapturingApi
from tests.unit.test_intelligence_brand import _scripted_input as _brand_input
from tests.unit.test_onboarding import FakeBookOfHousesApi, _answers
from tests.unit.test_two_stages_r243_r245 import (
    PROPOSAL,
    SMALL,
    _door_provider,
    _DoorApi,
    _FilingBench,
)
from toll_harness import cli
from toll_harness import config as config_module
from toll_harness import onboarding as onboarding_module
from toll_harness.email.book_of_houses import BookOfHousesApiError
from toll_harness.onboarding import (
    WAITING_FOR_COMPANY_VERIFICATION,
    advance_connected_onboarding,
    create_configuration,
    load_config,
    registration_payload,
)
from toll_harness.toll_bench import row_slots
from toll_harness.toll_bench.draft import (
    PROPOSAL_FIELDS,
    PROPOSAL_INSTRUCTION,
    ROW_SLOT_FIELDS,
    DraftLoop,
    mend_the_small_proposal,
    read_proposal,
)
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

BRIEF = {"want": "Get me on three podcasts"}
APPROACH = {"risk": "Aggressive", "finish": "Scrappy", "path": "Creative"}
DEFAULT = {"risk": "Careful", "finish": "Polished", "path": "Proven path"}
WITH_SLOTS = dict(
    PROPOSAL,
    approach={"risk": "aggressive", "polish": "SCRAPPY", "novelty": "creative"},
    capabilities=["Books podcast guests weekly", "Writes pitches hosts answer"],
    step_estimate={"total": "7", "you": 2},
)


def _ask_text(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


def _ask_payload(model, index=0):
    text = _ask_text(model, index)
    return json.loads(text[text.rindex("\n\n{") + 2:])


# ---------------------------------------------------------------------------
# 1. The ask: approach first, then the proposal designed around it
# ---------------------------------------------------------------------------
def test_the_ask_says_choose_the_approach_first_then_design_around_it():
    assert "CHOOSE YOUR APPROACH FIRST, THEN DESIGN THE PROPOSAL AROUND IT." in (
        PROPOSAL_INSTRUCTION
    )
    first = PROPOSAL_INSTRUCTION.index("`approach` -- decide it BEFORE")
    assert first < PROPOSAL_INSTRUCTION.index("`pitch_title` --")
    assert first < PROPOSAL_INSTRUCTION.index("`headline` --")
    assert "clearly FOLLOW that approach" in PROPOSAL_INSTRUCTION
    # The dials in the bench's own words, all nine stops.
    for words in row_slots.APPROACH_DIALS.values():
        for word in words:
            assert word in PROPOSAL_INSTRUCTION
    assert "`your_default_approach`" in PROPOSAL_INSTRUCTION
    assert "`capabilities` -- one or two short lines, up to 60" in PROPOSAL_INSTRUCTION
    assert "`step_estimate` --" in PROPOSAL_INSTRUCTION
    # The answer shape leads with the approach.
    answer = PROPOSAL_INSTRUCTION[PROPOSAL_INSTRUCTION.index("Answer, approach first:"):]
    assert answer.index('"approach"') < answer.index('"pitch_title"')
    assert '"step_estimate": {"total": 0, "you": 0}' in answer
    # Capabilities are no longer on the do-not-write list.
    assert "no capabilities" not in PROPOSAL_INSTRUCTION
    assert "—" not in PROPOSAL_INSTRUCTION


def test_the_ask_carries_the_default_and_the_profile_when_set():
    bench = _FilingBench()
    model = _model(PROPOSAL)
    loop = DraftLoop(model, bench)
    loop.approach_default = dict(DEFAULT)
    loop.agent_profile = {"name": "Ali", "can": ["searches and reads the web"]}

    loop.run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")

    payload = _ask_payload(model)
    assert payload["your_default_approach"] == DEFAULT
    assert payload["your_profile"]["name"] == "Ali"


def test_the_ask_carries_neither_when_unset():
    model = _model(PROPOSAL)
    DraftLoop(model, _FilingBench()).run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")
    payload = _ask_payload(model)
    assert "your_default_approach" not in payload
    assert "your_profile" not in payload


def test_the_old_road_tool_describes_the_three_slots():
    registry = add_toll_bench_tools(build_standard_registry())
    by_name = {definition.name: definition for definition in registry.definitions()}
    submit = by_name["toll_bench.submit_proposal"].description
    assert "THREE OPTIONAL ROW SLOTS" in submit and "chosen FIRST" in submit
    assert "Proven path, Middle path or Creative" in submit
    assert "1..8 KEYS" not in submit
    assert "NOT these keys" in by_name["toll_bench.capability_taxonomy"].description


# ---------------------------------------------------------------------------
# 2. The wire: what the draft loop files
# ---------------------------------------------------------------------------
def test_the_filed_body_carries_the_three_slots_settled_and_approach_first():
    bench = _FilingBench()
    out = DraftLoop(_model(WITH_SLOTS), bench).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k"
    )

    assert out["ok"] is True and out["model_calls"] == 1
    filed = bench.filed[0][1]
    assert filed["approach"] == APPROACH
    assert list(filed)[0] == "approach"
    assert list(filed["approach"]) == ["risk", "finish", "path"]
    assert filed["capabilities"] == WITH_SLOTS["capabilities"]
    assert filed["step_estimate"] == {"total": 7, "you": 2}
    for field in PROPOSAL_FIELDS:
        assert filed[field] == PROPOSAL[field] or field == "finalist_questions"


def test_the_old_eight_field_answer_still_files_unchanged():
    bench = _FilingBench()
    out = DraftLoop(_model(PROPOSAL), bench).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k"
    )
    assert out["ok"] is True
    assert not set(bench.filed[0][1]) & set(ROW_SLOT_FIELDS)


def test_no_approach_from_the_model_takes_the_agents_default(caplog):
    bench = _FilingBench()
    loop = DraftLoop(_model(PROPOSAL), bench)
    loop.approach_default = dict(DEFAULT)
    with caplog.at_level(logging.INFO, logger="toll_harness.draft"):
        loop.run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")
    assert bench.filed[0][1]["approach"] == DEFAULT
    assert any("from the agent's default" in r.getMessage() for r in caplog.records)


def test_a_dial_the_model_left_out_is_filled_from_the_default():
    bench = _FilingBench()
    loop = DraftLoop(_model(dict(PROPOSAL, approach={"risk": "Aggressive"})), bench)
    loop.approach_default = dict(DEFAULT)
    loop.run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")
    assert bench.filed[0][1]["approach"] == {
        "risk": "Aggressive", "finish": "Polished", "path": "Proven path"
    }


def test_a_half_approach_with_no_default_is_dropped_not_sent_half_filled():
    bench = _FilingBench()
    out = DraftLoop(
        _model(dict(PROPOSAL, approach={"risk": "Aggressive", "finish": "shiny"})), bench
    ).run("t-1", kind="bid", brief=BRIEF, idempotency_key="k")
    assert out["ok"] is True
    assert "approach" not in bench.filed[0][1]


def test_a_dry_run_shows_the_slots():
    out = DraftLoop(_model(WITH_SLOTS), _FilingBench()).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k", file=False
    )
    assert out["proposal"]["approach"] == APPROACH
    assert out["proposal"]["step_estimate"] == {"total": 7, "you": 2}


# ---------------------------------------------------------------------------
# 3. Normalization: the door's spelling, and drop rather than fail
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"risk": "careful", "finish": "POLISHED", "path": "proven"}, DEFAULT),
        ({"risk": "middle", "finish": "mid", "path": "Middle-Path"},
         {"risk": "Middle risk", "finish": "Middle finish", "path": "Middle path"}),
        ({"risk": "bold", "polish": "rough", "novelty": "novel"}, APPROACH),
        ("Aggressive / Scrappy / Creative", APPROACH),
        (["Careful", "Polished", "Proven path"], DEFAULT),
    ],
)
def test_dial_words_settle_to_the_benchs_spelling(raw, expected):
    approach, notes = row_slots.normalize_approach(raw)
    assert approach == expected and notes == []
    assert row_slots.is_complete(approach)


def test_an_invalid_dial_is_dropped_and_said_never_raised():
    approach, notes = row_slots.normalize_approach(
        {"risk": "YOLO", "finish": "Polished", "path": "Creative", "tone": "warm"}
    )
    assert approach == {"finish": "Polished", "path": "Creative"}
    assert any("approach.risk" in note for note in notes)
    assert any('no dial called "tone"' in note for note in notes)
    # "middle" alone in a list names no single dial.
    assert row_slots.normalize_approach(["middle"]) == (
        None, ['approach word "middle" names no single dial; dropped it']
    )
    assert row_slots.normalize_approach(42)[0] is None


def test_capabilities_are_two_lines_of_sixty_cut_at_a_word():
    long = "Books podcast guests on shows that actually answer cold pitches fast"
    lines, notes = row_slots.normalize_capabilities([long, " two  spaces ", "three", ""])
    assert lines[0] == "Books podcast guests on shows that actually answer cold"
    assert len(lines[0]) <= 60
    assert lines[1] == "two spaces"
    assert len(lines) == 2
    assert any("kept the first 2" in note for note in notes)
    assert row_slots.normalize_capabilities("One line")[0] == ["One line"]
    assert row_slots.normalize_capabilities([{"label": "From a dict"}])[0] == ["From a dict"]
    assert row_slots.normalize_capabilities(7)[0] == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"total": 7, "you": 2}, {"total": 7, "you": 2}),
        ({"total": "7", "you": "2"}, {"total": 7, "you": 2}),
        ({"total": 3, "you": 9}, {"total": 3, "you": 3}),  # clamped, you <= total
        ({"total": 3, "you": -1}, {"total": 3, "you": 0}),
        ({"steps": 5, "person": 1}, {"total": 5, "you": 1}),
        ([6, 2], {"total": 6, "you": 2}),
        ({"total": 7}, None),  # half-filled: dropped, not guessed
        ({"total": 0, "you": 0}, None),
        ({"total": None, "you": None}, None),  # the blank
        ({"total": True, "you": 1}, None),
        ("seven", None),
    ],
)
def test_the_step_estimate_is_whole_numbers_with_you_clamped(raw, expected):
    assert row_slots.normalize_step_estimate(raw)[0] == expected


def test_read_proposal_reads_the_slots_and_says_what_it_dropped():
    notes: list[str] = []
    read = read_proposal(
        dict(PROPOSAL, approach={"risk": "sideways"}, step_estimate={"total": 2, "you": 5}),
        notes,
    )
    assert "approach" not in read
    assert read["step_estimate"] == {"total": 2, "you": 2}
    assert any("sideways" in note for note in notes)
    assert any("clamped" in note for note in notes)


def test_an_approach_beside_a_wrapped_proposal_is_read_too():
    read = read_proposal({"approach": dict(APPROACH), "proposal": dict(PROPOSAL)})
    assert read["approach"] == APPROACH
    assert read["pitch_title"] == PROPOSAL["pitch_title"]
    # The wrapper's own slot wins over the outside one.
    inner = dict(PROPOSAL, approach=dict(DEFAULT))
    assert read_proposal({"approach": dict(APPROACH), "proposal": inner})["approach"] == DEFAULT


def test_the_mend_settles_the_slots_and_takes_off_a_half_one():
    fixed, mended = mend_the_small_proposal(
        dict(SMALL, approach={"risk": "careful"}, step_estimate={"total": 4, "you": 1})
    )
    assert "approach" not in fixed
    assert fixed["step_estimate"] == {"total": 4, "you": 1}
    assert any("approach" in line for line in mended)
    # A proposal already in the door's spelling is left alone.
    clean = dict(SMALL, approach=dict(APPROACH), capabilities=["x"])
    assert mend_the_small_proposal(clean) == (clean, [])


# ---------------------------------------------------------------------------
# 4. A refusal that names a slot costs the slot, never the bid
# ---------------------------------------------------------------------------
class _RefusingFilingApi(_DoorApi):
    """The filing door refuses the first body with the bench's own REJ-01."""

    def __init__(self, detail):
        super().__init__()
        self.detail = detail

    def submit_proposal(self, target_id, proposal, idempotency_key):
        self.filed.append((target_id, json.loads(json.dumps(proposal)), idempotency_key))
        if len(self.filed) == 1:
            raise BookOfHousesApiError(
                422, "REJ-01", self.detail,
                body={"ok": False, "rej": "REJ-01", "detail": self.detail},
            )
        return {"ok": True, "proposal_id": "p-2"}


def test_a_filing_refusal_naming_a_slot_drops_it_and_files_once_more(caplog):
    detail = ('approach.risk takes one of Careful, Middle risk, Aggressive; '
              '"YOLO" is not one of them.')
    api = _RefusingFilingApi(detail)
    with caplog.at_level(logging.WARNING):
        out = _door_provider(api).submit_proposal(
            "t-1", dict(SMALL, approach=dict(APPROACH), capabilities=["x"]), "k-1"
        )

    assert out["ok"] is True and out["proposal_id"] == "p-2"
    assert len(api.filed) == 2
    assert "approach" in api.filed[0][1]
    assert "approach" not in api.filed[1][1]
    assert api.filed[1][1]["capabilities"] == ["x"]  # only the named slot
    assert api.filed[1][2] == "k-1-rowslots"
    assert out["row_slots_dropped"] == ["approach"]
    assert any("dropped approach and filed once more" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize(
    ("detail", "slot"),
    [
        ("step_estimate.you is 9 but total is 3: you counts the steps OF the total",
         "step_estimate"),
        ("capabilities[0] is not a line of words.", "capabilities"),
        ("Additional properties are not allowed ('approach' was unexpected)", "approach"),
        ("approach is one stop on EACH of the three dials, and path is empty",
         "approach"),
    ],
)
def test_every_way_the_door_names_a_slot_is_read(detail, slot):
    assert row_slots.named_in_refusal({"detail": detail}) == [slot]


def test_a_refusal_about_something_else_drops_nothing():
    assert row_slots.named_in_refusal(
        {"detail": "pitch_body is 617 characters and the cap is 600"}) == []
    # A row that says which field it is about is taken at its word, even when
    # its sentence uses "approach" in passing.
    assert row_slots.named_in_refusal(
        {"field": "pitch_body", "detail": "your approach is too long here"}) == []
    assert row_slots.named_in_refusal({"field": "approach.risk", "detail": "x"}) == [
        "approach"
    ]


def test_a_filing_refusal_about_something_else_is_not_retried():
    class Refuses(_DoorApi):
        def submit_proposal(self, target_id, proposal, idempotency_key):
            self.filed.append((target_id, proposal, idempotency_key))
            raise BookOfHousesApiError(
                422, "REJ-21", "pitch_body is 617 characters and the cap is 600",
                body={"ok": False, "rej": "REJ-21",
                      "detail": "pitch_body is 617 characters and the cap is 600"},
            )

    api = Refuses()
    with pytest.raises(BookOfHousesApiError):
        _door_provider(api).submit_proposal(
            "t-1", dict(SMALL, approach=dict(APPROACH)), "k-1"
        )
    assert len(api.filed) == 1


def test_the_free_door_naming_a_slot_drops_it_in_the_fix_round():
    api = _DoorApi(
        {"ok": False, "trimmed": [], "problems": [{
            "code": "REJ-01", "field": "step_estimate.you",
            "detail": "step_estimate.you is 9 but total is 3", "fix": "x"}]},
        {"ok": True, "problems": [], "trimmed": []},
    )
    out = _door_provider(api).submit_proposal(
        "t-1", dict(SMALL, step_estimate={"total": 3, "you": 3},
                    approach=dict(APPROACH)), "k-1"
    )
    assert out["ok"] is True
    assert len(api.asked) == 2
    assert "step_estimate" not in api.asked[1]
    assert "step_estimate" not in api.filed[0][1]
    assert api.filed[0][1]["approach"] == APPROACH
    assert out["row_slots_dropped"] == ["step_estimate"]


def test_the_draft_loop_carries_what_was_really_filed():
    class Drops(_FilingBench):
        def submit_proposal(self, target_id, proposal, idempotency_key):
            super().submit_proposal(target_id, proposal, idempotency_key)
            return {"ok": True, "proposal_id": "p-1", "row_slots_dropped": ["approach"]}

    out = DraftLoop(_model(WITH_SLOTS), Drops()).run(
        "t-1", kind="bid", brief=BRIEF, idempotency_key="k"
    )
    assert out["row_slots_dropped"] == ["approach"]
    assert "approach" not in out["proposal"]
    assert out["proposal"]["step_estimate"] == {"total": 7, "you": 2}


def test_the_local_mirror_ignores_a_slot_the_benchs_schema_does_not_name():
    provider = _door_provider(_DoorApi())
    provider.proposal_schema = lambda: {
        "type": "object",
        "properties": {"pitch_title": {"type": "string"}},
        "additionalProperties": False,
    }
    result = provider._local_validation(dict(SMALL, approach=dict(APPROACH)))
    assert not any("approach" in str(row.get("message")) for row in result["problems"])


# ---------------------------------------------------------------------------
# 5. Onboarding: the optional default, stored and sent
# ---------------------------------------------------------------------------
def test_init_stores_the_default_approach_under_strategy(tmp_path):
    path = create_configuration(
        tmp_path / "a",
        replace(
            _answers(connected=False),
            approach={"risk": "careful", "finish": "Polished", "path": "proven"},
        ),
    )
    config = load_config(path)
    assert config["strategy"] == {"approach": DEFAULT}
    # No default: no key at all, byte-for-byte what 0.56 wrote.
    bare = load_config(create_configuration(tmp_path / "b", _answers(connected=False)))
    assert "strategy" not in bare


def test_the_registration_payload_carries_a_whole_default_only(tmp_path):
    protocol = {"rules_version_hash": "rules-hash"}
    config = load_config(create_configuration(tmp_path / "a", _answers()))
    assert "approach" not in registration_payload(config, protocol)
    config["strategy"] = {"approach": dict(DEFAULT)}
    assert registration_payload(config, protocol)["approach"] == DEFAULT
    # A partial default is the model's starting point, never sent half-filled.
    config["strategy"] = {"approach": {"risk": "Careful"}}
    assert "approach" not in registration_payload(config, protocol)


def test_plain_init_asks_the_optional_question_and_registers_with_it(tmp_path, monkeypatch):
    api = CapturingApi()
    monkeypatch.setattr(onboarding_module, "BookOfHousesApiClient", lambda base_url: api)
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda _path: {})
    finished = {}
    monkeypatch.setattr(
        cli, "_finish_init",
        lambda config_path, result, *, checks=None, enable_worker: finished.update(
            config_path=config_path) or 0,
    )
    asked = _brand_input(
        monkeypatch,
        [
            "Oak", "C", "claude-fable-5-1", "", "Oak Works", "",  # ... mode
            "y",  # a default approach: yes
            "D",  # risk: Aggressive (A is "No default")
            "B",  # finish: Scrappy
            "d",  # path: Creative
            "", "", "https://example.com", "", "US-OR", "ops@example.com",
        ],
    )
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--yes", "--no-worker"]
    )

    assert cli.command_init(arguments) == 0

    assert f"{cli.APPROACH_QUESTION} [y/N]:\n> " in asked
    config = load_config(finished["config_path"])
    assert config["strategy"]["approach"] == APPROACH
    assert api.validated[0]["approach"] == APPROACH
    assert api.registered[0]["approach"] == APPROACH


def test_init_flags_answer_it_without_a_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_test_model_and_browser", lambda _path: {})
    finished = {}
    monkeypatch.setattr(
        cli, "_finish_init",
        lambda config_path, result, *, checks=None, enable_worker: finished.update(
            config_path=config_path) or 0,
    )
    asked = _brand_input(monkeypatch, ["Oak", "D", "gpt-5.2", "Nemotron", "Oak Works", "", "n"])
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--approach-risk", "careful",
         "--approach-path", "Proven Path"]
    )

    assert cli.command_init(arguments) == 0

    assert not any(prompt.startswith(cli.APPROACH_QUESTION) for prompt in asked)
    config = load_config(finished["config_path"])
    # Each dial is skippable: the one left out stays unset.
    assert config["strategy"]["approach"] == {"risk": "Careful", "path": "Proven path"}


def test_a_bad_flag_word_stops_init_before_anything_is_written(tmp_path, monkeypatch):
    _brand_input(monkeypatch, [])
    arguments = cli.build_parser().parse_args(
        ["init", str(tmp_path / "agent"), "--approach-finish", "shiny"]
    )
    with pytest.raises(ValueError, match="--approach-finish takes one of: Scrappy"):
        cli.command_init(arguments)
    assert not (tmp_path / "agent" / "agent.yaml").exists()


def _approach_config(tmp_path):
    path = create_configuration(tmp_path / "oak", _answers())
    config = yaml.safe_load(path.read_text())
    config["strategy"] = {"approach": dict(DEFAULT)}
    path.write_text(yaml.safe_dump(config))
    return path


def test_a_validate_door_that_refuses_the_approach_costs_the_default_not_the_agent(
    tmp_path,
):
    class Refuses(FakeBookOfHousesApi):
        def __init__(self):
            super().__init__()
            self.validated = []
            self.registered = []

        def validate_registration(self, payload):
            self.validated.append(dict(payload))
            if "approach" in payload:
                return {"ok": False, "problem_count": 1, "problems": [{
                    "error": "invalid_registration", "field": "approach.risk",
                    "message": "approach.risk takes one of Careful, Middle risk, Aggressive"}]}
            return {"ok": True, "problem_count": 0, "problems": []}

        def register(self, payload, idempotency_key):
            self.registered.append(dict(payload))
            return super().register(payload, idempotency_key)

    api = Refuses()
    result = advance_connected_onboarding(
        _approach_config(tmp_path), approve_registration=True, api=api
    )
    assert result["status"] == WAITING_FOR_COMPANY_VERIFICATION
    assert len(api.validated) == 2 and "approach" not in api.validated[1]
    assert "approach" not in api.registered[0]


def test_a_register_refusal_naming_the_approach_retries_once_without_it(tmp_path):
    class Refuses(FakeBookOfHousesApi):
        def __init__(self):
            super().__init__()
            self.keys = []
            self.bodies = []

        def register(self, payload, idempotency_key):
            self.keys.append(idempotency_key)
            self.bodies.append(dict(payload))
            if "approach" in payload:
                raise BookOfHousesApiError(
                    400, "invalid_registration", "approach has no dial called \"mood\"",
                    body={"error": "invalid_registration", "field": "approach",
                          "message": "approach has no dial called \"mood\""},
                )
            return super().register(payload, idempotency_key)

    api = Refuses()
    config_path = _approach_config(tmp_path)
    result = advance_connected_onboarding(config_path, approve_registration=True, api=api)

    assert result["status"] == WAITING_FOR_COMPANY_VERIFICATION
    assert "approach" in api.bodies[0] and "approach" not in api.bodies[1]
    assert api.keys[1] == api.keys[0] + "-noapproach"
    state = json.loads(
        onboarding_module.onboarding_path(config_path, load_config(config_path)).read_text()
    )
    assert "approach" in state["approach_dropped"]


def test_a_successful_register_carries_the_approach_and_records_it(tmp_path):
    api = CapturingApi()
    api.validate_registration = lambda payload: {"ok": True, "problems": []}
    config_path = _approach_config(tmp_path)
    advance_connected_onboarding(config_path, approve_registration=True, api=api)
    assert api.registered[0]["approach"] == DEFAULT
    state = json.loads(
        onboarding_module.onboarding_path(config_path, load_config(config_path)).read_text()
    )
    assert state["approach"] == DEFAULT


# ---------------------------------------------------------------------------
# 6. The runtime: the default reaches the loop; an older yaml still loads
# ---------------------------------------------------------------------------
def _runtime_config(tmp_path, monkeypatch, strategy):
    path = create_configuration(tmp_path / "rt", _answers(connected=False))
    config = yaml.safe_load(path.read_text())
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    if strategy is not None:
        config["strategy"] = strategy
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(
        config_module, "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )
    return path


def test_build_runtime_reads_the_default_and_never_fails_on_a_bad_word(
    tmp_path, monkeypatch, caplog
):
    path = _runtime_config(
        tmp_path, monkeypatch,
        {"approach": {"risk": "aggressive", "finish": "glossy", "path": "creative"}},
    )
    with caplog.at_level(logging.WARNING, logger="toll_harness.config"):
        resources = config_module.build_runtime(path)
    assert resources.approach_default == {"risk": "Aggressive", "path": "Creative"}
    assert any("glossy" in r.getMessage() for r in caplog.records)
    resources.close()


def test_an_agent_yaml_without_strategy_has_no_default(tmp_path, monkeypatch):
    resources = config_module.build_runtime(_runtime_config(tmp_path, monkeypatch, None))
    assert resources.approach_default is None
    resources.close()


def test_the_market_loop_hands_the_default_and_the_profile_to_the_draft_loop(monkeypatch):
    seen = {}

    class Loop:
        def __init__(self, model, provider):
            pass

        def run(self, target_id, **kwargs):
            seen.update(default=self.approach_default, profile=self.agent_profile)
            return {"ok": True, "filed": False}

    monkeypatch.setattr(cli, "DraftLoop", Loop)
    monkeypatch.setattr(cli, "_brief_for_the_loop", lambda *_a, **_k: {"want": "x"})
    monkeypatch.setattr(cli, "_act_kinds_for_the_loop", lambda _r: None)
    monkeypatch.setattr(cli, "_lab_lead_direction", lambda _r: "")
    resources = SimpleNamespace(
        runtime=SimpleNamespace(
            model=None,
            enabled_tools=["email.send", "web.search", "toll_bench.attention"],
            operator_instructions="I specialize in podcast booking.",
        ),
        toll_bench=SimpleNamespace(fleet=None),
        agent_identity=SimpleNamespace(name="Ali", intelligence="GPT", company="Toll Bench"),
        approach_default=dict(DEFAULT),
    )

    cli._bid_through_the_draft_loop(resources, {"target_id": "t-1"}, {}, 1, [], True)

    assert seen["default"] == DEFAULT
    assert seen["profile"] == {
        "name": "Ali",
        "intelligence": "GPT",
        "company": "Toll Bench",
        "can": ["sends and reads email from its own mailbox", "searches and reads the web"],
        "operator_instructions": "I specialize in podcast booking.",
    }


# ---------------------------------------------------------------------------
# 7. An agent registered before its default: the worker tells the bench
# ---------------------------------------------------------------------------
class _MeApi:
    def __init__(self, me, refuse=False):
        self._me = me
        self.refuse = refuse
        self.sent = []

    def me(self):
        return self._me

    def set_approach(self, approach):
        self.sent.append(approach)
        if self.refuse:
            raise BookOfHousesApiError(
                422, "approach_invalid", "approach.path takes one of ...",
                body={"ok": False, "error": "approach_invalid"},
            )
        return {"ok": True, "approach": approach}


def _sync(me, configured, refuse=False):
    api = _MeApi(me, refuse=refuse)
    provider = _door_provider(_DoorApi())
    provider.api = api
    return provider.sync_default_approach(configured), api.sent


def test_a_whole_yaml_default_the_bench_does_not_hold_is_sent_once():
    outcome, sent = _sync({"ok": True, "approach": None}, DEFAULT)
    assert sent == [DEFAULT]
    assert outcome == {"synced": True, "approach": DEFAULT, "was": None}
    outcome, sent = _sync({"approach": APPROACH}, {"risk": "careful", "finish": "polished",
                                                   "path": "proven"})
    assert sent == [DEFAULT] and outcome["was"] == APPROACH


@pytest.mark.parametrize(
    ("me", "configured", "reason"),
    [
        ({"approach": dict(DEFAULT)}, DEFAULT, "already_set"),
        ({"ok": True}, DEFAULT, "bench_has_no_default_approach"),  # a bench before 4.1.3
        ({"approach": None}, {"risk": "Careful"}, "no_whole_default"),
        ({"approach": dict(DEFAULT)}, None, "no_whole_default"),  # never cleared from here
    ],
)
def test_nothing_is_sent_when_there_is_nothing_to_change(me, configured, reason):
    outcome, sent = _sync(me, configured)
    assert sent == [] and outcome["reason"] == reason


def test_a_refused_default_is_logged_and_never_stops_the_worker(caplog):
    with caplog.at_level(logging.WARNING):
        outcome, sent = _sync({"approach": None}, DEFAULT, refuse=True)
    assert sent == [DEFAULT]
    assert outcome["synced"] is False and outcome["status"] == 422
    assert any("refused" in r.getMessage() for r in caplog.records)


def test_the_worker_syncs_the_default_at_start(monkeypatch):
    synced = []
    provider = SimpleNamespace(sync_default_approach=lambda c: synced.append(c) or {"ok": 1})
    resources = SimpleNamespace(
        toll_bench=provider, approach_default=dict(DEFAULT), close=lambda: None
    )
    monkeypatch.setattr(cli, "build_runtime", lambda _config: resources)
    monkeypatch.setattr(cli, "_market_watch_proposals_only", lambda _args, _res: 0)

    assert cli.command_market_watch(
        SimpleNamespace(config="agent.yaml", proposals_only=True, no_bid=False)
    ) == 0
    assert synced == [DEFAULT]
    # No default configured: nothing is asked of the bench.
    assert cli._sync_default_approach(SimpleNamespace(toll_bench=provider)) is None


def test_the_api_client_posts_the_default_to_its_own_door(monkeypatch):
    from toll_harness.email.book_of_houses import BookOfHousesApiClient

    calls = []
    client = BookOfHousesApiClient(base_url="https://bench.test", token="t")
    monkeypatch.setattr(
        client, "_request", lambda method, path, **kw: calls.append((method, path, kw)) or {}
    )
    client.set_approach(DEFAULT)
    client.approach()
    assert calls[0][:2] == ("POST", "/api/bench/me/approach")
    assert calls[0][2]["payload"] == {"approach": DEFAULT}
    assert calls[0][2]["authenticated"] is True
    assert calls[1][:2] == ("GET", "/api/bench/me/approach")
