"""THE AGENT READS ITS OWN APPROVED WORK BEFORE IT DECIDES A FACT IS MISSING (0.58.3).

WHAT FORCED THIS FILE. 2026-10-05, 20:16-20:28 local, lab agent Bree (codex,
gpt-6.1-sol), deal 30bd9ebf step 4: "make a print-ready PDF flyer". Three
runs failed with "the confirmed dinner date, start time, street name and RSVP
instructions are missing ... Available tools and records do not expose the
event details", and the loop guard parked the step. The bench's
current-step answer already carried `earlier_steps` (contract 4.1.14), and
in it step 3's calendar act: "Potluck dinner, Sat Oct 17, 6:00 PM to
9:00 PM", event id and all. The provider's whitelist dropped the field, so it
never reached the model on either road.

And the same runs posted the same ask on the step thread eleven times: every
post came back ok with a message id, but `step_thread.messages` carries only
the person's words since the agent last spoke, the whitelist dropped the
bench's `thread_count`, and the harness's own `messages_total` read 0, so the
model believed each post was lost and posted again.

Each test holds one line of the fix.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.test_market_watch import _completed_run
from tests.unit.test_toll_bench import FakeApi
from toll_harness import cli
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import (
    EARLIER_STEPS_KEY,
)

# The bench's rows for Bree's deal, as current-step answered them that night.
SIGNED = {
    "number": 1, "step_id": "st-1", "title": "Deal signed", "state": "approved",
    "approved_at": "2026-10-06T01:59:31Z", "approval_cause": "person",
    "hand_back": None, "files": [], "act_results": [],
}
CALENDAR = {
    "number": 3, "step_id": "st-3",
    "title": "Add the chosen potluck dinner to your Google Calendar.",
    "state": "approved", "approved_at": "2026-10-06T02:05:57Z",
    "approval_cause": "actions_complete",
    "hand_back": {
        "receipt_id": "r-3", "kind": "text", "summary": "What was done on this step",
        "text": "- Create events on your calendar: Potluck dinner · Sat Oct 17, "
                "6:00 PM to 9:00 PM · Host’s house in Lake Oswego",
        "text_chars": 120, "trimmed": None, "link": None, "file_receipt_id": None,
        "filename": None, "filed_at": "2026-10-06T02:05:57Z",
    },
    "files": [],
    "act_results": [{
        "act_id": "a-3", "kind": "calendar_event", "state": "executed",
        "to": "Google Calendar", "subject": "Potluck dinner",
        "words": "Title: Potluck dinner\nWhen: Sat Oct 17, 6:00 PM to 9:00 PM (Los Angeles)",
        "executed_at": "2026-10-06T02:05:56Z", "scheduled_for": None,
        "event_id": "0dfljrmht0t13s9s10nbkeok2s",
        "starts_at": "2026-10-17T18:00:00", "ends_at": "2026-10-17T21:00:00",
        "time_zone": "America/Los_Angeles", "message_id": None, "meet_link": None,
    }],
}

def _size(lines):
    return sum(len(line) + 1 for line in lines)


# ---------------------------------------------------------------------------
# 3. The provider no longer drops it, nor the thread's true count
# ---------------------------------------------------------------------------
class EarlierApi(FakeApi):
    def __init__(self, rows=None, thread_count=None):
        super().__init__()
        self.rows = rows
        self.thread_count = thread_count

    def current_step(self, deal_id):
        payload = FakeApi.current_step(self, deal_id)
        if self.rows is not None:
            payload["earlier_steps"] = self.rows
        if self.thread_count is not None:
            payload.setdefault("step_thread", {})["thread_count"] = self.thread_count
            payload["step_thread"].setdefault("messages", [])
        return payload


def test_the_provider_hands_the_rows_over_whole():
    """0.74.0: no whitelist and no rendering; the bench's rows as sent."""
    result = BookOfHousesTollBenchProvider(EarlierApi([SIGNED, CALENDAR])).current_step("d1")
    assert result[EARLIER_STEPS_KEY] == [SIGNED, CALENDAR]


def test_the_provider_invents_nothing_for_an_older_bench():
    result = BookOfHousesTollBenchProvider(EarlierApi()).current_step("d1")
    assert EARLIER_STEPS_KEY not in result
    assert "thread_count" not in result["step_thread"]


def test_the_provider_keeps_thread_count_and_says_own_posts_are_not_in_messages():
    result = BookOfHousesTollBenchProvider(EarlierApi(thread_count=11)).current_step("d1")
    thread = result["step_thread"]
    assert thread["thread_count"] == 11
    assert thread["messages"] == []


# ---------------------------------------------------------------------------
# 4. The old road (a file step, Bree's road) reads it too, and is told to ask
# ---------------------------------------------------------------------------
def test_the_old_road_goal_carries_earlier_steps_in_current_step():
    class MailClient:
        def configure_send_context(self, **_kwargs):
            pass

        def resume_pending_send(self):
            return None

    provider = BookOfHousesTollBenchProvider(EarlierApi([CALENDAR], thread_count=4))
    toll_bench = SimpleNamespace(
        ensure_reachable=lambda: {"ok": True},
        attention=lambda wait: {"attention": [
            {"kind": "deal_step", "deal_id": "d1", "proposal_id": "p1", "step_id": "s1"}
        ]},
        list_proposals=lambda: {"proposals": []},
        current_step=provider.current_step,
    )
    observed = {}

    def start(goal, mode):
        observed["goal"] = goal
        return _completed_run(goal, mode)

    runtime = SimpleNamespace(
        email_provider=SimpleNamespace(client=MailClient()),
        enabled_tools=["state.load", "result.complete", "toll_bench.current_step",
                       "toll_bench.reply_step_message"],
        start=start,
    )
    resources = SimpleNamespace(toll_bench=toll_bench, runtime=runtime, agent_identity=None)

    cli._process_market_attention(resources, wait=20)

    goal = observed["goal"]
    data = json.loads(goal[goal.index("\n\n{") + 2:])
    assert data["current_step"][EARLIER_STEPS_KEY] == [CALENDAR]
    assert data["current_step"]["step_thread"]["thread_count"] == 4


def test_the_old_road_says_ask_the_person_once_and_never_fail_over_it():
    words = cli._DEAL_STEP_INSTRUCTION
    assert "current_step.earlier_steps" in words
    assert "toll_bench.reply_step_message" in words
    assert "never result.fail over a fact the person can supply" in words
    assert "thread_count" in words


def test_after_asking_the_old_road_posts_a_check_in_so_a_due_pulse_stops_re_asking():
    # 0.58.4. WHAT FORCED IT: on 0.58.3 Bree read the date off earlier_steps,
    # asked the person for the street and the RSVP once per run and ended
    # each run completed -- but her last pulse was 80 minutes overdue, an
    # overdue pulse re-dispatches the step every cycle, and none of those runs
    # posted one, so the same ask went out three times in 81 seconds and the
    # loop guard parked the step again.
    words = cli._DEAL_STEP_INSTRUCTION
    ask = words.index("ONCE with toll_bench.reply_step_message")
    assert words.index("toll_bench.post_check_in", ask) < words.index("result.complete", ask)
    assert "latest_work_pulse.blocker already says you asked" in words
