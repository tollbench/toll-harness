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
from tests.unit.test_step_ask import OBLIGATION, _payload
from tests.unit.test_toll_bench import FakeApi
from toll_harness import cli
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.toll_bench.step import (
    EARLIER_STEPS_CHAR_CAP,
    EARLIER_STEPS_KEY,
    EARLIER_STEPS_TRIMMED,
    MOVE_INSTRUCTIONS,
    render_earlier_steps,
    step_tail,
    the_move,
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
EMAILED = {
    "number": 5, "step_id": "st-5", "title": "Email the invite to your friends",
    "state": "approved", "approved_at": "2026-10-07T01:00:00Z",
    "approval_cause": "person",
    "hand_back": {
        "receipt_id": "r-5", "kind": "file", "summary": "The flyer",
        "text": None, "text_chars": None, "trimmed": None, "link": None,
        "file_receipt_id": "fr-5", "filename": "flyer.pdf",
        "filed_at": "2026-10-07T00:59:00Z",
    },
    "files": [{"file_receipt_id": "fr-5", "filename": "flyer.pdf", "sniffed_type": "pdf"}],
    "act_results": [
        {"act_id": "a-5", "kind": "email", "state": "executed", "to": "Grant",
         "subject": "Potluck on the 17th", "words": "Come by at six.",
         "executed_at": "2026-10-07T00:58:00Z", "message_id": "msg-77"},
        {"act_id": "a-6", "kind": "email", "state": "scheduled", "to": "Ana",
         "subject": "Reminder", "words": None, "scheduled_for": "2026-10-16"},
    ],
}


def _size(lines):
    return sum(len(line) + 1 for line in lines)


# ---------------------------------------------------------------------------
# 1. The renderer: present, absent, empty, act lines, trimmed
# ---------------------------------------------------------------------------
def test_present_rows_become_one_line_per_step_with_the_hand_back_words():
    lines = render_earlier_steps([SIGNED, CALENDAR])
    assert lines[0] == "Step 1 (Deal signed): nothing handed back"
    assert lines[1].startswith(
        "Step 3 (Add the chosen potluck dinner to your Google Calendar.): - Create events"
    )
    assert "Sat Oct 17, 6:00 PM to 9:00 PM" in lines[1]


def test_absent_field_renders_nothing_and_an_empty_list_stays_an_empty_list():
    # An older bench sends no field: nothing is rendered, no key is invented.
    assert render_earlier_steps(None) is None
    assert render_earlier_steps({"not": "a list"}) is None
    # The bench's [] means nothing before this step is approved yet, which
    # is not the same as not knowing.
    assert render_earlier_steps([]) == []


def test_an_act_result_is_its_own_did_line_with_the_time_and_the_ids():
    lines = render_earlier_steps([CALENDAR])
    did = lines[1]
    assert did.startswith("  did: calendar_event to Google Calendar \"Potluck dinner\" ")
    assert "2026-10-17T18:00:00..2026-10-17T21:00:00 America/Los_Angeles" in did
    assert "event 0dfljrmht0t13s9s10nbkeok2s" in did
    # The act's own words ride the line, newlines folded.
    assert "Title: Potluck dinner When: Sat Oct 17" in did
    assert "\n" not in did


def test_a_sent_email_names_its_message_and_a_scheduled_one_its_date():
    lines = render_earlier_steps([EMAILED])
    assert lines[0] == "Step 5 (Email the invite to your friends): The flyer"
    assert lines[1] == "  file: flyer.pdf pdf receipt fr-5"
    assert lines[2] == ('  did: email to Grant "Potluck on the 17th" message msg-77'
                        " : Come by at six.")
    assert lines[3] == '  did: email [scheduled] to Ana "Reminder" scheduled for 2026-10-16'


def test_over_the_cap_the_oldest_steps_go_first_and_the_block_says_so():
    long_words = "x" * 280
    rows = [
        {**CALENDAR, "number": n, "step_id": f"st-{n}", "title": f"Step title {n}",
         "hand_back": {**CALENDAR["hand_back"], "text": f"step {n} {long_words}"}}
        for n in range(1, 11)
    ]
    lines = render_earlier_steps(rows)
    assert lines[0] == EARLIER_STEPS_TRIMMED
    assert _size(lines) <= EARLIER_STEPS_CHAR_CAP
    kept = [line for line in lines if line.startswith("Step ")]
    # The newest step is always kept; the oldest is the first to go.
    assert kept[-1].startswith("Step 10 (Step title 10)")
    assert not any(line.startswith("Step 1 (") for line in kept)
    # Whole steps go, never a step's head without its acts.
    assert lines[-1].startswith("  did: calendar_event")


def test_one_step_bigger_than_the_cap_is_cut_to_fit():
    lines = render_earlier_steps([CALENDAR], cap=200)
    assert lines[0] == EARLIER_STEPS_TRIMMED
    assert _size(lines) <= 200
    assert lines[1].startswith("Step 3 (")


def test_under_the_cap_nothing_is_trimmed_and_rendering_twice_changes_nothing():
    lines = render_earlier_steps([SIGNED, CALENDAR, EMAILED])
    assert EARLIER_STEPS_TRIMMED not in lines
    assert render_earlier_steps(lines) == lines
    # A rendered, trimmed block re-trims from its own lines.
    small = render_earlier_steps(lines, cap=400)
    assert small[0] == EARLIER_STEPS_TRIMMED and _size(small) <= 400
    assert render_earlier_steps(small, cap=400) == small


# ---------------------------------------------------------------------------
# 2. The step ask's tail carries it, beside what the person said
# ---------------------------------------------------------------------------
def test_the_step_ask_tail_carries_the_rendered_block():
    payload = _payload(earlier_steps=[SIGNED, CALENDAR])
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    assert tail[EARLIER_STEPS_KEY] == render_earlier_steps([SIGNED, CALENDAR])
    assert "event 0dfljrmht0t13s9s10nbkeok2s" in json.dumps(tail)


def test_the_tail_has_no_earlier_steps_key_when_the_bench_sent_none():
    payload = _payload()
    tail = step_tail(payload, OBLIGATION, the_move(payload), [])
    assert EARLIER_STEPS_KEY not in tail


def test_the_hand_back_move_says_read_earlier_steps_and_ask_the_person():
    words = MOVE_INSTRUCTIONS["hand_back"]
    assert "earlier_steps" in words
    assert "reply_step_message" in words


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


def test_the_provider_hands_the_rendered_block_over():
    result = BookOfHousesTollBenchProvider(EarlierApi([SIGNED, CALENDAR])).current_step("d1")
    assert result[EARLIER_STEPS_KEY] == render_earlier_steps([SIGNED, CALENDAR])


def test_the_provider_invents_nothing_for_an_older_bench():
    result = BookOfHousesTollBenchProvider(EarlierApi()).current_step("d1")
    assert EARLIER_STEPS_KEY not in result
    assert "thread_count" not in result["step_thread"]


def test_the_provider_keeps_thread_count_and_says_own_posts_are_not_in_messages():
    result = BookOfHousesTollBenchProvider(EarlierApi(thread_count=11)).current_step("d1")
    thread = result["step_thread"]
    assert thread["thread_count"] == 11
    assert thread["messages"] == []
    assert "never your own posts" in thread["messages_note"]


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
    assert data["current_step"][EARLIER_STEPS_KEY] == render_earlier_steps([CALENDAR])
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
