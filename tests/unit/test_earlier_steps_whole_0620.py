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
from types import SimpleNamespace

from tests.unit.test_step_ask import OBLIGATION, FakeBench, _model, _payload
from tests.unit.test_toll_bench import FakeApi
from toll_harness.email.book_of_houses import BookOfHousesApiClient
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import (
    EARLIER_STEPS_KEY,
    NEED_TOOLS,
    ROAD_AGENTIC,
    StepAsk,
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
# The bench cut it just before the late field, as it did that day.
KEPT = MATCH_CARD[: MATCH_CARD.index("\nmeet:")]

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


def test_need_tools_on_the_email_step_takes_the_old_road():
    model = _model({"call": NEED_TOOLS, "why": "the match card was trimmed; I need its meet field"})
    out = StepAsk(model, FakeBench()).run(OBLIGATION, _email_step_payload([]))
    assert out["road"] == ROAD_AGENTIC and out["model_calls"] == 1
    assert "meet field" in out["why"]


def test_the_current_step_tool_takes_whole_true():
    registry = add_toll_bench_tools(build_standard_registry())
    tool = next(d for d in registry.definitions() if d.name == "toll_bench.current_step")
    _validate({"deal_id": "d1", "whole": True}, tool.input_schema)
    assert "whole true" in tool.description
    rows = [_card_step(n) for n in range(1, 25)]
    provider = BookOfHousesTollBenchProvider(HistoryApi(rows))
    context = SimpleNamespace(toll_bench_provider=provider)
    handler = registry._tools["toll_bench.current_step"].handler
    # 0.74.0: the bench's rows pass through; whole true is the bench's own
    # `?full=1` read, never a re-render here.
    small = handler(context, {"deal_id": "d1"})[EARLIER_STEPS_KEY]
    assert small == rows and provider.api.full_reads == 0
    handler(context, {"deal_id": "d1", "whole": True})
    assert provider.api.full_reads == 1


