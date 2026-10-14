"""THE WHO HOLE NAMES ITS FILLER, AND RESEARCH IS ONE (0.64.0).

WHAT FORCED THIS FILE. The bench has taken `from_step` on a who step since
contract 4.1.9 (an earlier step's number) and the words "contact_book" and
"found_by_research" since contract 4.1.12 (2026-10-05). On
"found_by_research" the bench stamps a research step in the who step's own
place, the agent hands back its finds as a cards document (name, role, email,
source_url), and the person picks from them. The harness never told its model
any of that and said the opposite: "never plan a step to find, get or list the
people". On prod, 2026-10-12, agent Ali planned "I want Toll Bench featured in
an AI newsletter or podcast" with a who step "Pick 1 from your Contact book"
on a want whose person has no list of editors; Kai's bookstores walk
(2026-10-02) hit the same hole.

Three things are held here: the words say the slot (all three fillers, the
JSON shape beside the plain one); a string `from_step` rides the form to the
door exactly as written; and at walk time the stamped research step's cards
are filed with the field names the step's own payload carries, with the
cards the door took off shown to the model on its next ask.
"""
from __future__ import annotations

import copy
import json

from tests.unit.plan_door import Door, reply
from tests.unit.test_step_ask import (
    ACTIONS,
    OBLIGATION,
    TEXT,
    FakeBench,
    _form,
    _model,
    _obj,
    _payload,
)
from toll_harness.core.runtime import TOLL_BENCH_SYSTEM_INSTRUCTION
from toll_harness.core.types import ModelMessage, ModelResponse
from toll_harness.models.scripted import ScriptedModelAdapter
from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import (
    LAST_FILING_NOTE,
    BookOfHousesTollBenchProvider,
)
from toll_harness.toll_bench.draft import (
    FORM_CHAR_BUDGET,
    FORM_INSTRUCTION,
    WHO_FROM_CONTACT_BOOK,
    WHO_FROM_RESEARCH,
    WHO_STEP_SENTENCE,
    DraftLoop,
    read_form,
)
from toll_harness.toll_bench.step import (
    LAST_FILING_KEY,
    MOVE_INSTRUCTIONS,
    StepAsk,
    step_tail,
    the_move,
)


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# 1. The words say the slot
# ---------------------------------------------------------------------------
def test_the_who_sentence_names_the_three_fillers():
    said = WHO_STEP_SENTENCE
    assert "`from_step`" in said
    assert "an earlier step's number" in said
    assert f'"{WHO_FROM_CONTACT_BOOK}"' in said and "the default" in said
    assert f'"{WHO_FROM_RESEARCH}"' in said
    # Who research is for, in plain words, and what the agent's job is there.
    assert "people the person does not have yet" in said
    for kind in ("reporters", "editors", "buyers", "hosts"):
        assert kind in said
    assert "cards (name, role, email, source_url)" in said
    # The slot, never the wall.
    assert "Do not plan a separate find step: put the word on the who step" in said
    assert "never plan a step to find" not in said


def test_the_who_sentence_keeps_both_json_shapes():
    plain = '{"verb": "who", "who": "person", "declared_odds": <your number>}'
    research = ('{"verb": "who", "who": "person", "declared_odds": <your number>, '
                '"from_step": "found_by_research"}')
    assert plain in WHO_STEP_SENTENCE
    assert research in WHO_STEP_SENTENCE
    assert WHO_STEP_SENTENCE in FORM_INSTRUCTION


def test_the_words_stay_short():
    # The sentence grew by the slot; the form ask was trimmed elsewhere so the
    # whole instruction stays well inside its budget.
    assert len(WHO_STEP_SENTENCE) < 1_100
    assert len(FORM_INSTRUCTION) < 3_100
    assert len(FORM_INSTRUCTION) < FORM_CHAR_BUDGET // 4


def test_the_runtime_sheet_and_the_refusal_fix_say_the_same_slot():
    sheet = _flat(TOLL_BENCH_SYSTEM_INSTRUCTION)
    for words in (sheet, blocks.CONTACT_STEP_SENTENCE):
        assert '"contact_book"' in words
        assert '"found_by_research"' in words
        assert "`from_step`" in words
        assert "do not plan a separate find step: put the word on the who step" in words
        assert "never plan a step to find" not in words


def test_the_form_ask_the_model_reads_names_the_research_filler():
    door = _FormDoor()
    model = _scripted({"overview": "x", "odds": 0.4, "span_days": 7,
                       "steps": [dict(WHO_RESEARCH)]})
    DraftLoop(model, door)._fill_the_form("t-1", "plan", reply("outline_round"),
                                          "Get us featured in an AI newsletter", None, {})
    asked = model.invocations[0]["messages"][0].content[0]["text"]
    assert '"from_step": "found_by_research"' in asked
    # The harness's own old clause is gone. (The bench's captured `send`
    # sentence still says "never plan a step to find or list them"; that is
    # the bench's words, carried verbatim, and they mean a separate step.)
    assert "never plan a step to find, get or list the people" not in asked


# ---------------------------------------------------------------------------
# 2. A string from_step rides the form exactly as written
# ---------------------------------------------------------------------------
WHO_RESEARCH = {"verb": "who", "who": "person", "declared_odds": 0.4,
                "from_step": "found_by_research", "count": 2}
PITCH = {"verb": "emails", "who": "agent", "do_line": "Email each editor",
         "hand_over_line": "One pitch to each editor, sent", "need_line": "",
         "declared_odds": 0.45, "proof": "text",
         "repeats": {"cadence": "for_each", "of_step": "who"}}


def _scripted(*answers):
    return ScriptedModelAdapter([
        ModelResponse(message=ModelMessage.text("assistant", json.dumps(a)),
                      text=json.dumps(a), tool_calls=[])
        for a in answers
    ])


class _FormDoor(Door):
    def __init__(self, *answers):
        super().__init__(*answers)
        self.puts = []

    def put_draft(self, target_id, outline, *, kind="plan"):
        self.puts.append(copy.deepcopy(outline))
        return {"ok": True, "ready": True}


def test_read_form_keeps_each_from_step_word_as_written():
    for word in (WHO_FROM_RESEARCH, WHO_FROM_CONTACT_BOOK, 2):
        read = read_form({"steps": [dict(WHO_RESEARCH, from_step=word), PITCH]})
        assert read["steps"][0]["from_step"] == word
    # Not coerced, not refused at home: the door judges a spelling.
    odd = read_form({"steps": [dict(WHO_RESEARCH, from_step="Found By Research")]})
    assert odd["steps"][0]["from_step"] == "Found By Research"


def test_the_form_goes_to_the_door_with_the_word_on_the_who_step():
    door = _FormDoor()
    model = _scripted({"overview": "Two editors pitched.", "odds": 0.4, "span_days": 14,
                       "steps": [dict(WHO_RESEARCH), dict(PITCH)]})

    _answer, error = DraftLoop(model, door)._fill_the_form(
        "t-1", "plan", reply("outline_round"),
        "I want Toll Bench featured in an AI newsletter or podcast", None, {},
    )

    assert error == ""
    assert len(door.puts) == 1
    sent = door.puts[0]["form"]["steps"]
    assert sent[0] == WHO_RESEARCH
    assert sent[0]["from_step"] == "found_by_research"
    # Nothing was inserted, dropped or rewritten in front of it.
    assert [step["verb"] for step in sent] == ["who", "emails"]
    assert door.inserts == [] and door.drops == [] and door.patches == []


# ---------------------------------------------------------------------------
# 3. Walk time: the stamped research step
# ---------------------------------------------------------------------------
# The fields come off the step, never off a list in this package: the phone
# variant (the step after the pick calls) proves nothing is hardcoded.
FIELDS = ["name", "role", "phone", "source_url"]
CLOSES = (
    "The person picks the people to reach from the items you hand back on "
    "this step, or from their Contact book: one tap each, and the pick that "
    "fills the count approves this step. An item whose address is made up "
    "comes off the list (finds_refused on your filing)."
)
CONTROL = {"kind": "pick_people", "shape": "list", "count": 2,
           "binds": "person.who", "own_list": True,
           "quiet_door": "None of these fit", "closes": CLOSES}


def _cards_schema(fields):
    item = _obj({name: TEXT for name in fields}, fields)
    return _obj({
        "note": dict(TEXT, maxLength=280),
        "step_ref": {"const": "s-1"},
        "document": _obj({"title": {"type": "string"}, "blocks": {
            "type": "array", "minItems": 1,
            "allOf": [{"contains": _obj({"type": {"const": "cards"},
                                         "items": {"type": "array", "minItems": 1,
                                                   "items": item}},
                                        ("type", "items"))}]}}, ("blocks",)),
    }, ("note", "step_ref", "document"))


def _research_payload(fields=FIELDS, **over):
    actions = [a for a in ACTIONS if a["action"] != "submit_step_outcome"]
    outcome = _form("submit_step_outcome", _cards_schema(fields))
    outcome["template"] = {"step_ref": "s-1", "note": "", "document": {
        "title": "", "blocks": [{"type": "cards", "items": [dict.fromkeys(fields, "")]}]}}
    payload = _payload(
        current_step={
            "id": "s-1", "number": 1, "state": "agent_working", "ask": "APPROVE",
            "title": "Find up to 10 people who fit this want",
            "outcome_promise": ("Up to 10 people who fit, each with their role or "
                                "outlet, a real phone number and the page it was "
                                "found on"),
            "deliverable": {"channel": "text", "fields": list(fields), "min_count": 1},
            "file_receipts": [],
            "control": dict(CONTROL),
        },
        submission={"completion_recorded_on_outcome": True, "actions": actions + [outcome]},
    )
    payload.update(over)
    return payload


FINDS = {
    "call": "file_outcome",
    "step_ref": "s-1",
    "note": "Two hosts who fit. Pick who to call.",
    "document": {"title": "Podcast hosts", "blocks": [{"type": "cards", "items": [
        {"name": "Ada Host", "role": "host, The AI Hour", "phone": "+1 503 555 0100",
         "source_url": "https://example.org/ai-hour/contact"},
        {"name": "Bo Host", "role": "editor, Model Weekly", "phone": "+1 503 555 0101",
         "source_url": "https://example.org/model-weekly/about"},
    ]}]},
}


def test_the_research_step_is_a_hand_back_and_the_tail_carries_its_readers():
    payload = _research_payload(read_by=[], work_sent_back=[])
    move = the_move(payload, OBLIGATION)
    assert move["move"] == "hand_back"
    tail = step_tail(payload, OBLIGATION, move, [])
    assert tail["step"]["deliverable"]["fields"] == FIELDS
    assert tail["step"]["control"] == CONTROL
    # Empty readers cost the ask nothing.
    assert "read_by" not in tail and "work_sent_back" not in tail
    assert LAST_FILING_KEY not in tail


def test_the_cards_file_with_the_fields_the_step_payload_carries():
    bench = FakeBench()
    ask = StepAsk(_model(FINDS), bench)

    out = ask.run(OBLIGATION, _research_payload(), brief={})

    assert out["ok"] is True, out
    assert out["call"] == "file_outcome"
    (_target, outcome, _key), = bench.outcomes
    items = outcome["document"]["blocks"][0]["items"]
    assert [sorted(item) for item in items] == [sorted(FIELDS)] * 2
    # The template the tool carries is the step's own, field for field.
    tool = ask.model.invocations[0]["tools"]
    described = " ".join(t.description for t in tool if t.name == "file_outcome")
    for name in FIELDS:
        assert f'"{name}"' in described
    # And the model was told what the person does with them.
    asked = ask.model.invocations[0]["messages"][0].content[0]["text"]
    assert "pick_people" in asked and "own_list" in asked
    assert "step.control.closes" in MOVE_INSTRUCTIONS["hand_back"]


def test_a_card_missing_a_field_the_step_named_is_refused_by_the_schema_not_filed():
    bench = FakeBench()
    short = copy.deepcopy(FINDS)
    for item in short["document"]["blocks"][0]["items"]:
        item.pop("phone")
    # Twice short: the ask is made twice and nothing goes to the door.
    out = StepAsk(_model(short, short), bench).run(OBLIGATION, _research_payload(), brief={})
    assert out["ok"] is False
    assert out["failure"] == "invalid_arguments"
    assert bench.outcomes == []


def test_later_readers_and_the_persons_send_back_ride_the_tail():
    read_by = [{"step": 3, "kind": "who", "note": "Step 3 is the who step, and the "
                "person picks the people it reaches from what this step hands back."}]
    sent_back = [{"receipt_id": "r-1", "title": "Podcast hosts", "was_document": True,
                  "reason": "None of these fit: I want newsletters, not podcasts.",
                  "expected": "a changed list"}]
    payload = _research_payload(read_by=read_by, work_sent_back=sent_back)
    tail = step_tail(payload, OBLIGATION, the_move(payload, OBLIGATION), [])
    assert tail["read_by"] == read_by
    assert tail["work_sent_back"] == [{"receipt_id": "r-1", "title": "Podcast hosts",
                                       "reason": sent_back[0]["reason"],
                                       "expected": "a changed list"}]


# ---------------------------------------------------------------------------
# 4. The provider: control through the whitelist, the cards that came off
# ---------------------------------------------------------------------------
REFUSED = [{"card": 2, "name": "Bo Host",
            "sentence": "Card 2's address is a stand-in, not a find."}]


class _ResearchApi:
    """The bench's current-step and outcome doors, for one research step."""

    def __init__(self, *, finds_refused=None, root_control=False):
        self.finds_refused = list(finds_refused or [])
        self.root_control = root_control
        self.outcomes = []

    def current_step(self, deal_id):
        step = {"id": "s-1", "number": 1, "state": "agent_working", "ask": "APPROVE",
                "title": "Find up to 10 people who fit this want",
                "deliverable": {"channel": "text", "fields": list(FIELDS),
                                "min_count": 1}}
        body = {"ok": True, "deal": {"id": deal_id, "target_goal_id": "t1"},
                "current_step": step, "read_by": [],
                "work_sent_back": [], "work_sent_back_count": 0,
                "control": dict(CONTROL)}
        if not self.root_control:
            step["control"] = dict(CONTROL)
        return body

    def file_outcome(self, target_id, payload, idempotency_key):
        self.outcomes.append(payload)
        return {"ok": True, "receipt_id": "r-9",
                "finds_refused": list(self.finds_refused), "finds_taken": []}


def _outcome():
    return {key: value for key, value in FINDS.items() if key != "call"}


def test_control_read_by_and_work_sent_back_survive_the_whitelist():
    for root in (False, True):
        payload = BookOfHousesTollBenchProvider(_ResearchApi(root_control=root)).current_step("d1")
        assert payload["current_step"]["control"] == CONTROL
        assert payload["read_by"] == []
        assert payload["work_sent_back"] == [] and payload["work_sent_back_count"] == 0


def test_the_cards_the_door_took_off_ride_the_next_read_of_that_step():
    api = _ResearchApi(finds_refused=REFUSED)
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("d1")

    filed = provider.file_outcome("t1", _outcome(), "k-1")

    assert filed["ok"] is True and filed["finds_refused"] == REFUSED
    again = provider.current_step("d1")
    assert again[LAST_FILING_KEY] == {"finds_refused": REFUSED, "note": LAST_FILING_NOTE}
    tail = step_tail(again, OBLIGATION, the_move(again, OBLIGATION), [])
    assert tail[LAST_FILING_KEY]["finds_refused"] == REFUSED

    # A filing with nothing off clears it.
    api.finds_refused = []
    provider.file_outcome("t1", _outcome(), "k-2")
    assert LAST_FILING_KEY not in provider.current_step("d1")


def test_the_home_mirror_reads_the_steps_own_fields():
    provider = BookOfHousesTollBenchProvider(_ResearchApi())
    provider.current_step("d1")
    blank = _outcome()
    blank["document"] = copy.deepcopy(blank["document"])
    blank["document"]["blocks"][0]["items"][0]["phone"] = ""

    refused = provider.file_outcome("t1", blank, "k-3")

    assert refused["ok"] is False
    assert "phone" in json.dumps(refused)
