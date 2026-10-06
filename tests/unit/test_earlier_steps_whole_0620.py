"""AN EARLIER STEP'S CARD REACHES EVERY LATER STEP WHOLE, FIELD BY FIELD (0.62.0).

WHAT FORCED THIS FILE. 2026-10-06, lab agent SF Founder House, deal fa84c12c
and its twin, step 5: "Email your match an introduction with the lobby
meeting time". Step 4's approved match card was 982 characters and its
`meet` field (the lobby time) sat near the end. The bench's current-step
answer kept the first 592 characters of the hand-back and said so in
`trimmed` (the whole receipt is steps[].outcome on history_url); the harness
then folded the card onto one line and clipped it at 300; and the email
step's moves (file_act, refile_act) offered no need_tools. So the brain never
saw the time, asked the persons on the thread for it (five messages across
the two deals) and filed both emails without it. The agent's lane had to
carry a workaround: the stored plan moved `meet` up to the second field and
the agent's own program posted the time as a check-in.

Each test holds one line of the fix: the trimmed hand-back is read whole off
the history once, every card field rides on its own line, a long history
trims the oldest steps first and says what it left out, a step too big on
its own loses free text and never a field, and every move that writes from
earlier material can answer need_tools.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from tests.unit.test_step_ask import OBLIGATION, FakeBench, _model, _payload
from tests.unit.test_toll_bench import FakeApi
from toll_harness import cli
from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import (
    EARLIER_STEPS_CHAR_CAP,
    EARLIER_STEPS_KEY,
    EARLIER_STEPS_TRIMMED,
    HAND_BACK_INDENT,
    MOVE_INSTRUCTIONS,
    NEED_TOOLS,
    ROAD_AGENTIC,
    StepAsk,
    render_earlier_steps,
    step_tail,
    step_tools,
    the_move,
)
from toll_harness.tools.registry import _validate, add_toll_bench_tools, build_standard_registry

# The match card as the bench flattens it (har_document: one `field: value`
# line per card field), the late field where it sat that day.
MATCH_FIELDS = [
    "name: Jon Reyes",
    "building: Shiftwise, a staff scheduling and tip-out app for independent "
    "restaurants, sold door to door to 140 East Bay restaurants before its seed round.",
    "email: jon@example.org",
    "why: Jon sold restaurant software to 140 owners one at a time, which is exactly "
    "the first-50-customers sales story you want to learn. His customers keep asking "
    "Shiftwise to do their books, so he wants to meet someone building bookkeeping for "
    "restaurants, and that is you.",
    "meet: Wed Oct 7, 4:00 to 4:20 pm, SF Founder House lobby",
]
MATCH_CARD = "\n".join([
    "Your match this week at SF Founder House",
    "Who you should meet",
    *MATCH_FIELDS,
    "How to spot them",
    "Green baseball cap, gray hoodie, round glasses, usually carrying a coffee and a "
    "laptop covered in stickers.",
    "The place",
    "Meet in the lobby, by the front desk. I could not reach the SF Founder House website "
    "today to confirm the street address, so I have left it off rather than guess. Jon is "
    "free any time this week, and Wednesday afternoon fits your weekday afternoons.",
])
MEET_LINE = HAND_BACK_INDENT + "meet: Wed Oct 7, 4:00 to 4:20 pm, SF Founder House lobby"
# The bench cut it just before the late field, as it did that day.
KEPT = MATCH_CARD[: MATCH_CARD.index("\nmeet:")]
TRIMMED_NOTE = (f"kept the first {len(KEPT)} of {len(MATCH_CARD)} characters; the whole "
                "hand-back is steps[].outcome on history_url")

PROFILE = {
    "number": 3, "step_id": "st-3", "title": "Write your founder profile from your answers",
    "state": "approved", "approved_at": "2026-10-06T17:17:05Z", "approval_cause": "person",
    "hand_back": {
        "receipt_id": "r-profile", "kind": "document", "summary": "Maya's founder profile",
        "text": "Maya's founder profile\nprofile: Maya builds Ledgerline, bookkeeping for "
                "small restaurants.\nhow to spot you: Bright yellow rain jacket, green tote bag.",
        "text_chars": 140, "trimmed": None, "link": None, "file_receipt_id": None,
        "filename": None, "filed_at": "2026-10-06T17:15:06Z",
    },
    "files": [], "act_results": [],
}
MATCH = {
    "number": 4, "step_id": "st-4",
    "title": "Find who on this week's list you should meet, and a time to meet in the lobby",
    "state": "approved", "approved_at": "2026-10-06T20:53:41Z", "approval_cause": "person",
    "hand_back": {
        "receipt_id": "r-match", "kind": "document",
        "summary": "Your match this week at SF Founder House",
        "text": KEPT + " ...", "text_chars": len(MATCH_CARD),
        "trimmed": TRIMMED_NOTE, "link": None, "file_receipt_id": None,
        "filename": None, "filed_at": "2026-10-06T20:52:37Z",
    },
    "files": [{"file_receipt_id": "r-photo", "filename": "photo-of-maya.jpg",
               "sniffed_type": "jpg"}],
    "act_results": [],
}


def _size(lines):
    return sum(len(line) + 1 for line in lines)


class HistoryApi(FakeApi):
    """current-step with `earlier_steps`; `?full=1` (history_url) carries
    steps[].outcome with every receipt's words whole."""

    def __init__(self, rows, *, history_fails=False):
        super().__init__()
        self.rows = rows
        self.history_fails = history_fails
        self.full_reads = 0

    def current_step(self, deal_id, full=False):
        payload = FakeApi.current_step(self, deal_id)
        payload[EARLIER_STEPS_KEY] = copy.deepcopy(self.rows)
        if full:
            self.full_reads += 1
            if self.history_fails:
                raise RuntimeError("history unreachable")
            payload["steps"] = [
                {"index": 3, "outcome": [
                    {"receipt_id": "r-profile", "kind": "document",
                     "words": PROFILE["hand_back"]["text"]}]},
                {"index": 4, "outcome": [
                    {"receipt_id": "r-photo", "kind": "file", "words": None},
                    {"receipt_id": "r-match", "kind": "document", "words": MATCH_CARD}]},
            ]
        return payload


# ---------------------------------------------------------------------------
# 1. The card the bench trimmed is read whole, once, and rides field by field
# ---------------------------------------------------------------------------
def test_a_card_the_bench_trimmed_reaches_the_later_step_whole_field_by_field():
    api = HistoryApi([PROFILE, MATCH])
    lines = BookOfHousesTollBenchProvider(api).current_step("d1")[EARLIER_STEPS_KEY]
    assert MEET_LINE in lines
    for field in MATCH_FIELDS:
        assert HAND_BACK_INDENT + field in lines
    # The free text after the card rides whole too, each line its own.
    assert HAND_BACK_INDENT + "The place" in lines
    assert any("by the front desk" in line for line in lines)
    assert not any("(cut here:" in line for line in lines)
    assert not lines[0].startswith(EARLIER_STEPS_TRIMMED)
    assert api.full_reads == 1


def test_the_whole_hand_back_is_read_once_per_receipt():
    api = HistoryApi([PROFILE, MATCH])
    provider = BookOfHousesTollBenchProvider(api)
    first = provider.current_step("d1")[EARLIER_STEPS_KEY]
    second = provider.current_step("d1")[EARLIER_STEPS_KEY]
    assert first == second and MEET_LINE in second
    assert api.full_reads == 1
    # A hand-back the bench did not trim costs no history read at all.
    untrimmed = HistoryApi([PROFILE])
    BookOfHousesTollBenchProvider(untrimmed).current_step("d1")
    assert untrimmed.full_reads == 0


def test_when_the_history_cannot_be_read_the_cut_is_shown_where_it_is():
    api = HistoryApi([MATCH], history_fails=True)
    provider = BookOfHousesTollBenchProvider(api)
    lines = provider.current_step("d1")[EARLIER_STEPS_KEY]
    cut = [line for line in lines if "(cut here:" in line]
    assert len(cut) == 1
    assert f"kept the first {len(KEPT)}" in cut[0] and NEED_TOOLS in cut[0]
    assert MEET_LINE not in lines
    # The failed read is not repeated on every cycle.
    provider.current_step("d1")
    assert api.full_reads == 1


def test_an_older_client_with_no_history_read_keeps_the_benchs_words():
    class OldApi(FakeApi):
        def current_step(self, deal_id):
            payload = FakeApi.current_step(self, deal_id)
            payload[EARLIER_STEPS_KEY] = copy.deepcopy([MATCH])
            return payload

    lines = BookOfHousesTollBenchProvider(OldApi()).current_step("d1")[EARLIER_STEPS_KEY]
    assert any("(cut here:" in line for line in lines)


def test_the_client_reads_history_url_with_full():
    seen = []
    client = BookOfHousesApiClient(base_url="https://bench.example", token="t")
    client._request = lambda method, path, **kwargs: seen.append((method, path)) or {"ok": True}
    client.current_step("d 1")
    client.current_step("d 1", full=True)
    assert seen == [("GET", "/api/bench/deals/d%201/current-step"),
                    ("GET", "/api/bench/deals/d%201/current-step?full=1")]


# ---------------------------------------------------------------------------
# 2. The email step's one-call ask carries the late field
# ---------------------------------------------------------------------------
def _email_step_payload(lines):
    return _payload(
        declared_acts=[{"kind": "email", "filed": 0, "held": 0, "executed": 0}],
        **{EARLIER_STEPS_KEY: lines},
    )


def test_the_late_field_rides_the_email_steps_one_call_ask():
    lines = BookOfHousesTollBenchProvider(HistoryApi([PROFILE, MATCH])).current_step(
        "d1")[EARLIER_STEPS_KEY]
    payload = _email_step_payload(lines)
    assert the_move(payload)["move"] == "file_act"
    bench = FakeBench()
    model = _model({"call": "propose_act", "kind": "email", "contact_ref": "c-jon",
                    "subject": "Meet Maya at SF Founder House",
                    "body_text": "Maya will meet you Wed Oct 7, 4:00 to 4:20 pm in the lobby."})
    out = StepAsk(model, bench).run(OBLIGATION, payload, changed=["first look"])
    assert out["ok"] is True and out["call"] == "propose_act"
    [invocation] = model.invocations
    asked = "".join(block["text"] for block in invocation["messages"][0].content)
    assert "meet: Wed Oct 7, 4:00 to 4:20 pm, SF Founder House lobby" in asked
    assert "how to spot you: Bright yellow rain jacket, green tote bag." in asked
    # And it is told the card is already its own, and how to fetch more.
    assert "never ask the person for it" in asked and NEED_TOOLS in asked
    assert NEED_TOOLS in [tool.name for tool in invocation["tools"]]


# ---------------------------------------------------------------------------
# 3. A long history trims the oldest first and names what it left out
# ---------------------------------------------------------------------------
def _card_step(number, extra=""):
    card = "\n".join([f"Card {number}", f"name: Person {number}",
                      f"why: {'a reason worth reading ' * 12}{extra}",
                      f"meet: Day {number}, 4:00 pm, lobby"])
    return {**PROFILE, "number": number, "step_id": f"st-{number}", "title": f"Find {number}",
            "hand_back": {**PROFILE["hand_back"], "receipt_id": f"r-{number}",
                          "text": card, "text_chars": len(card)}}


def test_a_long_history_trims_oldest_first_and_names_what_was_trimmed():
    rows = [_card_step(n) for n in range(1, 25)]
    lines = render_earlier_steps(rows)
    assert _size(lines) <= EARLIER_STEPS_CHAR_CAP
    marker = lines[0]
    assert marker.startswith(EARLIER_STEPS_TRIMMED)
    assert "left out step 1, step 2" in marker
    assert NEED_TOOLS in marker and "whole true" in marker
    heads = [line for line in lines if line.startswith("Step ")]
    left_out = [n for n in range(1, 25) if not any(h.startswith(f"Step {n} (") for h in heads)]
    # Oldest first, whole steps only, and the marker names exactly those.
    assert left_out == list(range(1, len(left_out) + 1))
    assert ", ".join(f"step {n}" for n in left_out) in marker
    # Every kept step is whole: its last field is there.
    for head in heads:
        number = head.split()[1]
        assert HAND_BACK_INDENT + f"meet: Day {number}, 4:00 pm, lobby" in lines
    # Rendering the trimmed block again changes nothing.
    assert render_earlier_steps(lines) == lines
    # Whole: nothing left out, no marker.
    whole = render_earlier_steps(rows, cap=None)
    assert not whole[0].startswith(EARLIER_STEPS_TRIMMED)
    assert sum(line.startswith("Step ") for line in whole) == 24


def test_one_step_too_big_keeps_every_field_whole_and_shortens_its_free_text():
    paragraph = "Meet in the lobby by the front desk. " * 80
    row = {**MATCH, "hand_back": {**MATCH["hand_back"], "trimmed": None,
                                  "text": MATCH_CARD + "\n" + paragraph}}
    lines = render_earlier_steps([PROFILE, row], cap=1_200)
    marker = lines[0]
    assert "left out step 3" in marker and "free text shortened on step 4" in marker
    for field in MATCH_FIELDS:
        assert HAND_BACK_INDENT + field in lines
    short = [line for line in lines if line.startswith(HAND_BACK_INDENT + "Meet in the lobby")]
    assert short and all(len(line) <= len(HAND_BACK_INDENT) + 160 for line in short)
    assert render_earlier_steps(lines, cap=1_200) == lines


# ---------------------------------------------------------------------------
# 4. need_tools on every move that writes from earlier material
# ---------------------------------------------------------------------------
def test_need_tools_is_offered_on_every_move_that_writes_from_earlier_material():
    file_act = _email_step_payload([])
    refile = _payload(acts=[{"act_id": "a1", "kind": "email", "state": "sent_back",
                             "note": "Put the lobby time in"}])
    reply = _payload(owed_replies=[{"id": "r1"}])
    for payload, name in ((file_act, "file_act"), (refile, "refile_act"),
                          (reply, "answer_reply"), (_payload(), "hand_back")):
        move = the_move(payload)
        assert move["move"] == name
        assert NEED_TOOLS in step_tools(move, payload), name
        assert "earlier_steps" in MOVE_INSTRUCTIONS[name]
    # Chat with the person is not a deliverable and takes no detour.
    spoke = _payload(step_thread={"messages": [{"id": "m1", "who": "person", "text": "hi"}],
                                  "unread_from_person": 1, "unanswered_elsewhere": []})
    move = the_move(spoke)
    assert move["move"] == "answer_person" and NEED_TOOLS not in step_tools(move, spoke)


def test_need_tools_on_the_email_step_takes_the_old_road():
    model = _model({"call": NEED_TOOLS, "why": "the match card was trimmed; I need its meet field"})
    out = StepAsk(model, FakeBench()).run(OBLIGATION, _email_step_payload([]))
    assert out["road"] == ROAD_AGENTIC and out["model_calls"] == 1
    assert "meet field" in out["why"]


# ---------------------------------------------------------------------------
# 5. The old road reads it all whole, and the tool fetches it whole
# ---------------------------------------------------------------------------
def test_the_old_road_goal_carries_every_earlier_step_whole_when_the_ask_trimmed_them():
    rows = [_card_step(n) for n in range(1, 25)]
    provider = BookOfHousesTollBenchProvider(HistoryApi(rows))
    state = provider.current_step("d1")
    assert state[EARLIER_STEPS_KEY][0].startswith(EARLIER_STEPS_TRIMMED)
    resources = SimpleNamespace(toll_bench=provider)
    goal_state = cli._with_whole_earlier_steps(resources, OBLIGATION, state)
    assert goal_state[EARLIER_STEPS_KEY] == render_earlier_steps(rows, cap=None)
    # A copy: what the dispatch remembers of the step is untouched.
    assert state[EARLIER_STEPS_KEY][0].startswith(EARLIER_STEPS_TRIMMED)
    # Nothing trimmed: the very same state, no extra work.
    small = BookOfHousesTollBenchProvider(HistoryApi([PROFILE])).current_step("d1")
    assert cli._with_whole_earlier_steps(resources, OBLIGATION, small) is small


def test_the_current_step_tool_takes_whole_true():
    registry = add_toll_bench_tools(build_standard_registry())
    tool = next(d for d in registry.definitions() if d.name == "toll_bench.current_step")
    _validate({"deal_id": "d1", "whole": True}, tool.input_schema)
    assert "whole true" in tool.description
    rows = [_card_step(n) for n in range(1, 25)]
    provider = BookOfHousesTollBenchProvider(HistoryApi(rows))
    context = SimpleNamespace(toll_bench_provider=provider)
    handler = registry._tools["toll_bench.current_step"].handler
    trimmed = handler(context, {"deal_id": "d1"})[EARLIER_STEPS_KEY]
    whole = handler(context, {"deal_id": "d1", "whole": True})[EARLIER_STEPS_KEY]
    assert trimmed[0].startswith(EARLIER_STEPS_TRIMMED)
    assert whole == render_earlier_steps(rows, cap=None)


def test_the_tail_carries_the_whole_card_within_the_cap():
    lines = BookOfHousesTollBenchProvider(HistoryApi([PROFILE, MATCH])).current_step(
        "d1")[EARLIER_STEPS_KEY]
    payload = _email_step_payload(lines)
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    assert tail[EARLIER_STEPS_KEY] == lines
    assert "meet: Wed Oct 7, 4:00 to 4:20 pm" in json.dumps(tail)
