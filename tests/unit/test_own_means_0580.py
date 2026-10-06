"""WHAT AN AGENT DOES BY ITS OWN MEANS (agent.yaml `agent.own_means`, 0.58.0).

WHAT FORCED THESE TESTS (staging want 1d0a67d5, 2026-10-05). Lab agent DJ 4-Fun
records songs with its own program beside the harness: the program writes a
brief, a recorder on the owner's computer makes two versions, the person picks
one. The harness had no way to know. The plan form's "NEVER GIVE YOURSELF WORK
ON A SERVICE YOU HAVE NO TOOL FOR ... Make it the PERSON's step" made the model
hand the recording to the person as a Suno errand (`do_ask` suno.com/create)
on both plans it wrote (proposals caca5178 and dd6823bb), and the agent's
focus.md ("recording is my work") never reached the plan form at all.

So agent.yaml takes an optional list of {name, can}. It is a DECLARATION that
shapes prompts and never a tool: every ask that weighs "do I have a tool for
this" (the proposal, the plan form, the step ask's hand-back and outside-work
moves, the runtime's agent payload) reads it as tools the agent has, so the
plan step stays `who: agent`. With no entry, every ask is byte for byte what it
was.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from tests.unit.test_onboarding import _answers
from tests.unit.test_outside_act_w29 import OutsideBench, _outside
from tests.unit.test_step_ask import BRIEF as STEP_BRIEF
from tests.unit.test_step_ask import OBLIGATION, FakeBench, _payload
from tests.unit.test_step_ask import _model as _step_model
from tests.unit.test_two_stages_r243_r245 import (
    FILLED_STEP,
    PROPOSAL,
    FormBench,
    _FilingBench,
    _model,
)
from toll_harness import cli
from toll_harness import config as config_module
from toll_harness.core.own_means import (
    OWN_MEANS_CAN_MAX,
    OWN_MEANS_LEAD,
    OWN_MEANS_MAX,
    OWN_MEANS_NAME_MAX,
    OWN_MEANS_PLAN_RULE,
    OWN_MEANS_RULE,
    OwnMeans,
    own_means_from_config,
    own_means_line,
    parse_own_means,
    with_own_means,
)
from toll_harness.onboarding import create_configuration
from toll_harness.toll_bench import draft
from toll_harness.toll_bench.step import MOVE_INSTRUCTIONS, StepAsk

RECORD_SONG = {
    "name": "record_song",
    "can": (
        "Record a song from lyrics and a sound brief: two versions per recording; "
        "the person picks one. Done by my own program beside the harness, never "
        "by the person."
    ),
}
DJ = (OwnMeans(name=RECORD_SONG["name"], can=RECORD_SONG["can"]),)
# A want that publishes a tool list, so the W29 pass is live on it.
TOOLED_BRIEF = {
    "want": "A hip hop song for my wife",
    "tools": [{"tool": "platform.draft", "provider": "", "one_line": "write a draft"}],
}


def _ask_text(model, index=0):
    return model.invocations[index]["messages"][0].content[0]["text"]


# ---------------------------------------------------------------------------
# 1. The key: parsed, validated, capped; absent is ()
# ---------------------------------------------------------------------------
def test_the_list_parses_and_its_whitespace_is_one_line():
    parsed = parse_own_means([
        dict(RECORD_SONG),
        {"name": "  mix_stems ", "can": "Mix stems\n  into one track"},
    ])

    assert parsed == (
        DJ[0],
        OwnMeans(name="mix_stems", can="Mix stems into one track"),
    )


def test_no_key_is_no_own_means():
    assert parse_own_means(None) == ()
    assert own_means_from_config({"version": 1}) == ()
    assert own_means_from_config({"version": 1, "agent": {"name": "x"}}) == ()


def test_an_empty_list_is_no_own_means():
    assert parse_own_means([]) == ()


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("record songs", "must be a list"),
        ({"name": "x", "can": "y"}, "must be a list"),
        (["record_song"], "must be {name, can}"),
        ([{"name": "x"}], "can must be a string"),
        ([{"can": "y"}], "name must be a string"),
        ([{"name": 7, "can": "y"}], "name must be a string"),
        ([{"name": "x", "can": ["y"]}], "can must be a string"),
        ([{"name": "  ", "can": "y"}], "name must not be empty"),
        ([{"name": "x", "can": "\n"}], "can must not be empty"),
        ([{"name": "x", "can": "y", "does": "z"}], "unknown key(s): does"),
        ([{"name": "Song", "can": "y"}, {"name": "song", "can": "z"}], "repeats"),
    ],
)
def test_a_bad_entry_is_a_load_error_that_names_it(value, words):
    with pytest.raises(ValueError) as caught:
        parse_own_means(value)
    assert words in str(caught.value)
    assert "agent.own_means" in str(caught.value)


def test_the_caps_are_forty_two_hundred_eighty_and_twelve():
    assert (OWN_MEANS_NAME_MAX, OWN_MEANS_CAN_MAX, OWN_MEANS_MAX) == (40, 280, 12)
    at_cap = [{"name": f"{i:02d}".ljust(40, "n"), "can": "c" * 280} for i in range(12)]
    assert len(parse_own_means(at_cap)) == 12

    with pytest.raises(ValueError, match="name is 41 characters; the cap is 40"):
        parse_own_means([{"name": "n" * 41, "can": "c"}])
    with pytest.raises(ValueError, match="can is 281 characters; the cap is 280"):
        parse_own_means([{"name": "n", "can": "c" * 281}])
    with pytest.raises(ValueError, match="13 entries; at most 12"):
        parse_own_means(at_cap + [{"name": "one_more", "can": "c"}])


# ---------------------------------------------------------------------------
# 2. The sentence
# ---------------------------------------------------------------------------
def test_the_line_lists_each_one_under_the_lead_and_ends_with_the_rule():
    line = own_means_line(DJ)

    assert line == (
        "Things you can do by your own means (treat them as tools you have): "
        "record_song -- Record a song from lyrics and a sound brief: two versions "
        "per recording; the person picks one. Done by my own program beside the "
        "harness, never by the person. "
        "Work one of these does is yours, never the person's errand."
    )
    two = own_means_line(DJ + (OwnMeans("mix", "Mix the stems"),), OWN_MEANS_PLAN_RULE)
    assert two.startswith(OWN_MEANS_LEAD) and two.endswith(OWN_MEANS_PLAN_RULE)
    # A `can` with no full stop gets one, so the next entry reads as a sentence.
    assert "mix -- Mix the stems. A step that does one" in two


def test_no_own_means_is_no_line_and_the_instruction_unchanged():
    assert own_means_line(()) == "" and own_means_line(None) == ""
    instruction = "Hand this step back."
    assert with_own_means(instruction, ()) is instruction
    assert with_own_means(instruction, DJ) == instruction + "\n" + own_means_line(DJ)


# ---------------------------------------------------------------------------
# 3. The plan form: the line stands right under the no-tool sentence
# ---------------------------------------------------------------------------
def test_the_form_instruction_is_unchanged_without_own_means():
    assert draft.form_instruction(()) is draft.FORM_INSTRUCTION
    assert draft.form_instruction(None) is draft.FORM_INSTRUCTION
    # The sentence still stands where it stood: under the optional picks,
    # above "write as many steps".
    assert (draft.NO_TOOL_SENTENCE + "WRITE AS MANY STEPS") in draft.FORM_INSTRUCTION


def test_the_form_instruction_says_the_own_means_right_under_the_no_tool_sentence():
    said = draft.form_instruction(DJ)
    line = own_means_line(DJ, OWN_MEANS_PLAN_RULE)

    assert (draft.NO_TOOL_SENTENCE + line + "\nWRITE AS MANY STEPS") in said
    assert said.replace(line + "\n", "", 1) == draft.FORM_INSTRUCTION
    assert '`who`: "agent", with no `tool`, no `service:` and no `do_ask`' in said


def _plan_run(own_means=None, steps=None, brief=None):
    bench = FormBench()
    model = _model({"steps": steps or [dict(FILLED_STEP)] * 3, "span_days": 14})
    loop = draft.DraftLoop(model, bench)
    if own_means is not None:
        loop.own_means = own_means
    out = loop.run("t-1", kind="plan", proposal_id="p-9",
                   brief=brief or {"want": "A hip hop song for my wife"},
                   idempotency_key="k")
    return out, bench, model


def test_the_plan_form_ask_carries_the_own_means():
    out, _bench, model = _plan_run(DJ)

    assert out["ok"] is True, out
    ask = _ask_text(model)
    assert own_means_line(DJ, OWN_MEANS_PLAN_RULE) in ask
    assert draft.NO_TOOL_SENTENCE + own_means_line(DJ, OWN_MEANS_PLAN_RULE) in ask


def test_the_plan_form_ask_is_unchanged_without_own_means():
    _out, _bench, model = _plan_run()
    _out2, _bench2, model2 = _plan_run(())

    ask = _ask_text(model)
    assert draft.FORM_INSTRUCTION in ask
    assert "by your own means" not in ask
    assert _ask_text(model2) == ask


def test_a_step_that_names_its_own_means_stays_the_agents():
    # The safety net beside W29: the model wrote the name as a tool. The bench
    # has no row for it, so the name comes off and the step stays the agent's.
    recording = dict(FILLED_STEP, do_line="Record two versions", tool="record_song")
    on_who = dict(FILLED_STEP, do_line="Record it again", who="service:Record_Song")
    _out, bench, _model = _plan_run(DJ, steps=[dict(FILLED_STEP), recording, on_who],
                                    brief=TOOLED_BRIEF)

    put = bench.puts[1]["form"]["steps"]
    assert put[1]["who"] == "agent" and "tool" not in put[1]
    assert put[1]["do_line"] == "Record two versions"
    assert put[2]["who"] == "agent"


def test_without_own_means_that_step_is_still_handed_to_the_person():
    # W29 is unchanged for every agent that declared nothing.
    recording = dict(FILLED_STEP, do_line="Record two versions", tool="record_song")
    _out, bench, _model = _plan_run(steps=[dict(FILLED_STEP), recording],
                                    brief=TOOLED_BRIEF)

    put = bench.puts[1]["form"]["steps"]
    assert put[1]["who"] == "person" and "tool" not in put[1]


def test_own_means_stay_yours_leaves_every_other_step_alone():
    form = {"steps": [
        {"who": "person", "tool": "record_song"},
        {"who": "agent", "tool": ["platform.draft", "record_song"]},
        {"who": "agent", "tool": "platform.draft"},
        "not a step",
    ]}
    out, kept = draft.own_means_stay_yours(json.loads(json.dumps(form)), DJ)

    assert kept == ["step 2 (record_song)"]
    assert out["steps"][0] == {"who": "person", "tool": "record_song"}
    assert out["steps"][1] == {"who": "agent", "tool": ["platform.draft"]}
    assert out["steps"][2] == {"who": "agent", "tool": "platform.draft"}
    assert draft.own_means_stay_yours(json.loads(json.dumps(form)), ()) == (form, [])


# ---------------------------------------------------------------------------
# 4. The proposal ask
# ---------------------------------------------------------------------------
def _bid(own_means=None):
    bench = _FilingBench()
    model = _model(PROPOSAL)
    loop = draft.DraftLoop(model, bench)
    if own_means is not None:
        loop.own_means = own_means
    loop.run("t-1", kind="bid", brief={"want": "A hip hop song for my wife"},
             idempotency_key="k")
    return _ask_text(model)


def test_the_proposal_ask_carries_the_own_means():
    ask = _bid(DJ)
    assert draft.PROPOSAL_INSTRUCTION + "\n" + own_means_line(DJ) in ask


def test_the_proposal_ask_is_unchanged_without_own_means():
    ask = _bid()
    assert draft.PROPOSAL_INSTRUCTION in ask and "by your own means" not in ask
    assert _bid(()) == ask


# ---------------------------------------------------------------------------
# 5. The step ask: the two moves that weigh "do I have a tool for this"
# ---------------------------------------------------------------------------
def _step(payload, own_means=None, answer=None):
    model = _step_model(answer or {"call": "reply_step_message", "reply": "On it."})
    ask = StepAsk(model, FakeBench())
    if own_means is not None:
        ask.own_means = own_means
    ask.run(OBLIGATION, payload, brief=STEP_BRIEF)
    return _ask_text(model)


def test_the_hand_back_move_carries_the_own_means():
    text = _step(_payload(), DJ)
    assert MOVE_INSTRUCTIONS["hand_back"] + "\n" + own_means_line(DJ) in text
    assert OWN_MEANS_RULE in text


def test_the_hand_back_move_is_unchanged_without_own_means():
    text = _step(_payload())
    assert MOVE_INSTRUCTIONS["hand_back"] in text and "by your own means" not in text
    assert _step(_payload(), ()) == text


def test_the_outside_work_move_carries_the_own_means():
    # "IF YOU CANNOT DO IT AT ALL -- no tool for that service": the own means
    # are what the agent has before it reaches for report_parked.
    model = _step_model({"call": "file_outside_evidence", "summary": "Recorded it."})
    ask = StepAsk(model, OutsideBench())
    ask.own_means = DJ
    ask.run(OBLIGATION, _outside(), brief=STEP_BRIEF)

    assert MOVE_INSTRUCTIONS["outside_work"] + "\n" + own_means_line(DJ) in _ask_text(model)


def test_answering_the_person_carries_no_own_means():
    spoke = _payload(step_thread={
        "messages": [{"id": "m1", "who": "person", "text": "Can it be faster?"}],
        "unread_from_person": 1,
        "unanswered_elsewhere": [],
    })
    assert "by your own means" not in _step(spoke, DJ)


# ---------------------------------------------------------------------------
# 6. The runtime: carried like operator_instructions; the cli hands it on
# ---------------------------------------------------------------------------
def _config_path(tmp_path, monkeypatch, own_means="absent"):
    path = create_configuration(tmp_path / "rt", _answers(connected=False))
    config = yaml.safe_load(path.read_text())
    config["fleet"]["database"] = str(tmp_path / "fleet.sqlite3")
    config["providers"]["browser"] = None
    if own_means != "absent":
        config["agent"]["own_means"] = own_means
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    monkeypatch.setattr(
        config_module, "_build_model",
        lambda _config, *, root, data_dir: SimpleNamespace(model_id="example.model"),
    )
    return path


def test_build_runtime_carries_the_own_means_and_the_payload_says_them(tmp_path, monkeypatch):
    resources = config_module.build_runtime(
        _config_path(tmp_path, monkeypatch, [dict(RECORD_SONG)]))
    try:
        assert resources.runtime.own_means == DJ
        assert resources.runtime._agent_payload()["own_means"] == own_means_line(DJ)
        assert cli._own_means(resources) == DJ
        # A declaration, never a door: the tool list is what it was.
        assert not any("record_song" in name for name in resources.runtime.enabled_tools)
    finally:
        resources.close()


def test_a_bad_own_means_stops_the_load(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="agent.own_means"):
        config_module.build_runtime(
            _config_path(tmp_path, monkeypatch, [{"name": "n" * 41, "can": "c"}]))


def test_an_agent_yaml_without_the_key_loads_exactly_as_before(tmp_path, monkeypatch):
    # PARITY. No key, a null and an empty list are all today's agent: the same
    # identity, no own_means on the runtime, and an agent payload whose keys
    # are exactly the ones every release before 0.58.0 sent.
    before = (
        "agent_id", "agent_name", "intelligence", "company", "harness", "autonomy",
        "email_status", "email_verification_recipient", "email_address",
    )
    payloads = []
    for index, value in enumerate(("absent", None, [])):
        root = tmp_path / f"case-{index}"
        root.mkdir()
        path = _config_path(root, monkeypatch, value)
        config = yaml.safe_load(path.read_text())
        assert config_module.load_agent_identity(config) == config_module.load_agent_identity(
            {**config, "agent": {k: v for k, v in config["agent"].items() if k != "own_means"}}
        )
        resources = config_module.build_runtime(path)
        try:
            assert resources.runtime.own_means == ()
            assert cli._own_means(resources) == ()
            payload = resources.runtime._agent_payload()
            assert tuple(payload) == before
            payloads.append({k: v for k, v in payload.items() if k != "agent_id"})
        finally:
            resources.close()
    assert payloads[0] == payloads[1] == payloads[2]


def test_the_identity_checks_hold_with_the_key_set(tmp_path, monkeypatch):
    path = _config_path(tmp_path, monkeypatch, [dict(RECORD_SONG)])
    config = yaml.safe_load(path.read_text())
    bare = {**config, "agent": {k: v for k, v in config["agent"].items() if k != "own_means"}}
    assert config_module.load_agent_identity(config) == config_module.load_agent_identity(bare)


def test_an_older_runtime_without_the_attribute_reads_as_none():
    assert cli._own_means(SimpleNamespace(runtime=SimpleNamespace())) == ()
    assert cli._own_means(SimpleNamespace()) == ()


def test_the_cli_hands_the_own_means_to_the_proposal_and_plan_loops(monkeypatch):
    seen = []

    class Loop:
        def __init__(self, model, provider):
            self.own_means = ()

        def run(self, target_id, **kwargs):
            seen.append((kwargs.get("kind"), self.own_means))
            return {"ok": False, "error": "not_found", "status": 404}

    monkeypatch.setattr(cli, "DraftLoop", Loop)
    monkeypatch.setattr(cli, "_brief_for_the_loop", lambda *_a, **_k: {"want": "x"})
    monkeypatch.setattr(cli, "_act_kinds_for_the_loop", lambda _r: None)
    monkeypatch.setattr(cli, "_lab_lead_direction", lambda _r: "")
    resources = SimpleNamespace(
        runtime=SimpleNamespace(
            model=None,
            enabled_tools=["toll_bench.submit_informed_plan"],
            own_means=DJ,
        ),
        toll_bench=SimpleNamespace(fleet=None),
        agent_identity=None,
        approach_default=None,
    )

    cli._bid_through_the_draft_loop(resources, {"target_id": "t-1"}, {}, 1, [], True)
    cli._file_the_informed_plan_from_draft(
        resources, {"target_id": "t-1", "proposal_id": "p-1"}, {"ok": True}, 1, 5)

    assert seen == [("bid", DJ), ("plan", DJ)]


def test_the_cli_hands_the_own_means_to_the_step_ask(monkeypatch):
    seen = []

    class Ask:
        def __init__(self, model, provider):
            self.own_means = ()

        def run(self, obligation, payload, **kwargs):
            seen.append(self.own_means)
            return {"ok": False, "road": "agentic", "why": "test"}

    monkeypatch.setattr(cli, "StepAsk", Ask)
    monkeypatch.setattr(cli, "_IDLE_STEP_MEMO", {})
    monkeypatch.setattr(cli, "_STEP_REFUSALS", {})
    monkeypatch.setattr(cli, "_act_kinds_for_the_loop", lambda _r: None)
    resources = SimpleNamespace(
        runtime=SimpleNamespace(model=None, enabled_tools=[], own_means=DJ),
        toll_bench=SimpleNamespace(),
        store=None,
    )
    step = {"current_step": {"id": "s-1", "number": 2}, "deal": {"id": "d-1"}}

    asked = cli._the_step_ask(
        resources, {"kind": "deal_step", "deal_id": "d-1", "step_id": "s-1"},
        step, {"ok": True}, 1, 5)

    assert asked is None and seen == [DJ]
