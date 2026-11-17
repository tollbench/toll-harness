"""The watch: live wait, ping, the one hand-off, answers, the time limit, failures reported to
the bench (which parks), 429s, reconnect, the cursor across a restart, refusals, the outbox and
the bench log."""

from __future__ import annotations

import io
import json
import sys
import threading
import time

import pytest

from tests.fakebench import item
from toll_harness import cli
from toll_harness.handoff import FAILED, hand_off
from toll_harness.watch import Watch, backoff_delay


def no_update(*args, **kwargs):
    return {"action": "none"}


def watch(agent, **kwargs):
    kwargs.setdefault("updater", no_update)
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("wait", 0)
    kwargs.setdefault("spread", 0)  # the 0-30 s spread has its own tests below
    return Watch(agent, **kwargs)


def test_a_refusal_logs_where_each_problem_is():
    from toll_harness.watch import where_lines

    rows = [{"where": "step 2: verb", "rule": "unknown verb", "fix": "pick one"},
            {"where": "plan", "rule": "odds missing"}, {"where": "a"}, {"where": "b"}]
    assert where_lines(rows) == "step 2: verb: unknown verb; plan: odds missing; a; and 1 more"
    assert where_lines(None) == ""


def test_live_wait_hands_a_new_item_over_within_two_seconds(made):
    agent = made.agent()
    runner = threading.Thread(
        target=lambda: Watch(agent, wait=5, updater=no_update, spread=0).run(2))
    runner.start()
    time.sleep(0.7)  # the watch is now holding GET /me/items open
    pushed = time.monotonic()
    made.bench.add(item("it_live"))
    runner.join(timeout=20)
    runs = made.runs()
    assert [run["id"] for run in runs] == ["it_live"]
    assert runs[0]["at"] - pushed < 2.0
    assert made.bench.answers == [("it_live", {"reply": "on it"})]
    waits = [query.get("wait") for _, method, path, query in made.bench.requests
             if path == "/api/bench/me/items"]
    assert waits[0] == "0" and waits[1:] and all(value == "5" for value in waits[1:])


def test_ping_is_answered_by_the_connector_not_the_agent(made):
    agent = made.agent()
    made.bench.add(item("it_ping", kind="ping"))
    watch(agent).run(1)
    assert made.runs() == []
    assert made.bench.answers == [("it_ping", {"pong": True})]


def test_hand_off_puts_the_item_on_stdin_and_marks_it_seen(made):
    agent = made.agent()
    given = item("it_step", kind="step")
    made.bench.add(given)
    watch(agent).run(1)
    run = made.runs()[0]
    assert run["item"] == given
    assert run["argv"][1:] == ["--fixed-arg"]
    assert (run["id"], run["kind"]) == ("it_step", "step")
    assert run["config"] == str(agent.path)
    assert run["token_env"] is False
    assert made.bench.seen == ["it_step"]
    assert made.bench.answers == [("it_step", {"reply": "on it"})]


def test_item_text_never_reaches_a_command_line(made, tmp_path):
    agent = made.agent()
    nasty = (f'"; touch {tmp_path}/pwned; $(touch {tmp_path}/pwned2) '
             f'`touch {tmp_path}/pwned3` --rm -rf')
    made.bench.add(item("it_nasty", kind="message$(touch x)", do=nasty,
                        context={"line": nasty, "argv": ["--evil"]}))
    watch(agent).run(1)
    run = made.runs()[0]
    assert run["argv"] == agent.command[1:]
    assert nasty not in " ".join(run["argv"])
    assert run["kind"] == "unknown"  # a kind that is not a plain word never rides the env
    assert run["item"]["do"] == nasty  # it arrived, as data, on stdin
    assert not any((tmp_path / name).exists() for name in ("pwned", "pwned2", "pwned3"))


def test_an_item_id_with_a_path_in_it_is_skipped(made):
    agent = made.agent()
    made.bench.add(item("../../admin"))
    watch(agent).run(1)
    assert made.runs() == [] and made.bench.answers == []


def test_empty_stdout_means_the_agent_answers_later_with_the_answer_command(made, monkeypatch):
    agent = made.agent()
    made.mode("quiet")
    made.bench.add(item("it_later"))
    watch(agent).run(1)
    assert len(made.runs()) == 1 and made.bench.answers == []
    monkeypatch.setenv("TOLL_AGENT_CONFIG", str(agent.path))  # as the hand-off sets it
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"reply": "done later"})))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["answer", "it_later"])
    assert stopped.value.code == 0
    assert made.bench.answers == [("it_later", {"reply": "done later"})]


def test_time_limit_stops_the_command(made):
    started = time.monotonic()
    made.mode("sleep")
    result = hand_off([sys.executable, str(made.script)], item("it_slow"), time_limit=1.0)
    assert result.status == FAILED and "time limit" in result.why
    assert time.monotonic() - started < 10


def test_a_failed_command_is_reported_and_the_bench_parks_it_at_three(made):
    agent = made.agent()
    made.mode("fail")
    made.bench.add(item("it_bad"))
    watch(agent).run(4)
    assert len(made.runs()) == 3  # delivered three times by the bench, then parked there
    assert made.bench.answers == [("it_bad", {"failed": "the command exited with 3"})] * 3
    assert made.bench.parked == ["it_bad"]
    assert not (agent.data_dir / "parked").exists()  # parking is the bench's work


def test_a_command_past_the_time_limit_is_stopped_and_reported(made):
    agent = made.agent(time_limit_minutes=0.02)  # 1.2 seconds
    made.mode("sleep")
    made.bench.add(item("it_hung"))
    started = time.monotonic()
    watch(agent).run(1)
    assert time.monotonic() - started < 10
    (item_id, answer), = made.bench.answers
    assert item_id == "it_hung" and "time limit" in answer["failed"]


def test_output_that_is_not_json_is_reported_as_a_failure(made):
    agent = made.agent()
    made.mode("text")
    made.bench.add(item("it_text"))
    watch(agent).run(1)
    assert made.bench.answers == [("it_text", {"failed": "the command printed something "
                                                         "that is not JSON"})]


def test_a_429_is_waited_out_as_the_bench_says(made):
    agent = made.agent()
    made.bench.busy_answers = 1
    made.bench.add(item("it_busy"))
    pauses = []
    watch(agent, sleep=pauses.append).run(1)
    assert pauses == [7.0]
    assert made.bench.answers == [("it_busy", {"reply": "on it"})]


def test_reconnects_with_backoff_when_the_bench_is_down(made):
    agent = made.agent()
    made.bench.down_items = 3
    made.bench.add(item("it_after_outage"))
    pauses = []
    watch(agent, sleep=pauses.append, spread=30, rand=lambda: 0.5).run(4)
    # start spread, then the first retry (random 0-30 s), then backoff 2 s, 4 s
    assert pauses == [15.0, 15.0, 2.0, 4.0]
    assert made.bench.answers == [("it_after_outage", {"reply": "on it"})]
    assert backoff_delay(99, rand=lambda: 1.0) == 90.0  # 60 s at most, jitter on top


def test_starts_and_first_retries_spread_over_thirty_seconds():
    import random as chance

    draws = chance.Random(7)
    firsts = [backoff_delay(1, rand=draws.random) for _ in range(200)]
    assert all(0 <= value <= 30 for value in firsts)
    # 200 agents land across the whole window, never inside a 3-second burst
    assert max(firsts) - min(firsts) > 25
    assert max(sum(1 for value in firsts if start <= value < start + 3)
               for start in range(0, 28)) < 40


def test_a_dropped_line_waits_a_random_spread_before_reconnecting(made):
    agent = made.agent()
    made.bench.drop_items = 1  # the bench restarts: the held call is cut with no answer
    made.bench.add(item("it_after_drop"))
    pauses = []
    watched = watch(agent, sleep=pauses.append, spread=30, rand=lambda: 0.25)
    watched.run(2)
    assert pauses == [7.5, 7.5]  # the start, then the reconnect after the drop
    assert made.bench.answers == [("it_after_drop", {"reply": "on it"})]
    log = (agent.data_dir / "harness.log").read_text()
    assert "no answer from the bench while waiting for items" in log


def test_the_cursor_survives_a_restart(made):
    agent = made.agent()
    made.bench.add(item("it_one"))
    watch(agent).run(1)
    made.bench.add(item("it_two"))
    watch(agent).run(1)  # a new process: reads cursor.json
    assert [run["id"] for run in made.runs()] == ["it_one", "it_two"]
    afters = [query.get("after") for _, _, path, query in made.bench.requests
              if path == "/api/bench/me/items"]
    assert afters[-1] == "1"


def test_a_crash_mid_batch_never_hands_the_same_item_over_twice(made):
    agent = made.agent()
    made.bench.add(item("it_a"))
    made.bench.add(item("it_b"))
    first = watch(agent)
    first.start()
    response = first.bench.items(None, 0)
    first.handle(response["items"][0])  # then the process dies before the cursor moves
    watch(agent).run(1)
    assert [run["id"] for run in made.runs()] == ["it_a", "it_b"]


def test_a_refused_answer_is_not_retried(made):
    agent = made.agent()
    made.bench.refuse["it_plan"] = {"ok": False, "problems": [
        {"where": "block 3", "rule": "r", "fix": "f"}]}
    made.bench.add(item("it_plan", kind="plan_request"))
    watch(agent).run(2)
    assert len(made.runs()) == 1 and len(made.bench.answers) == 1


def test_an_answer_the_bench_cannot_take_waits_in_the_outbox(made):
    agent = made.agent()
    made.bench.down_answers = 1
    made.bench.add(item("it_kept"))
    watched = watch(agent)
    watched.run(1)
    assert (agent.data_dir / "outbox" / "it_kept.json").exists()
    watched.cycle()
    assert made.bench.answers == [("it_kept", {"reply": "on it"})]
    assert not (agent.data_dir / "outbox" / "it_kept.json").exists()
    assert len(made.runs()) == 1  # the agent was not asked twice


def test_logging_on_sends_activity_lines_without_contents(made):
    agent = made.agent()
    made.bench.settings["logging"] = True
    made.bench.add(item("it_logged"))
    watch(agent).run(1)
    lines = [entry["line"] for entry in made.bench.log_lines]
    assert any("arrived it_logged" in line for line in lines)
    assert any("answered it_logged" in line for line in lines)
    text = json.dumps(made.bench.log_lines)
    assert "Please post this now" not in text and "on it" not in text
    assert "fake-agent-token" not in text
    local = (agent.data_dir / "harness.log").read_text()
    assert "Please post this now" not in local and "fake-agent-token" not in local


def test_logging_off_sends_nothing(made):
    agent = made.agent()
    made.bench.add(item("it_quiet"))
    watch(agent).run(1)
    assert made.bench.log_lines == []
    assert "arrived it_quiet" in (agent.data_dir / "harness.log").read_text()


def test_a_notice_is_handed_over_but_never_answered(made):
    agent = made.agent()
    made.bench.add(item("it_note", kind="notice", answer={"needed": False}))
    watch(agent).run(1)
    assert len(made.runs()) == 1 and made.bench.answers == []


def test_the_live_line_open_line_is_written_once(made):
    agent = made.agent()
    watch(agent).run(3)
    text = (agent.data_dir / "harness.log").read_text()
    assert text.count("live line open (toll-harness 1.0.0)") == 1


def test_the_install_script_waits_for_the_line_the_watch_writes():
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "install_on_agent.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text()
    assert "ready_pattern='live line open'" in text and "ready_pattern='attention -> 200'" in text


def test_live_line_open_is_said_when_the_bench_first_answers_not_when_an_item_comes(made):
    agent = made.agent()
    started = time.monotonic()
    Watch(agent, wait=3, updater=no_update, spread=0).run(1)  # one call, nothing waiting
    assert time.monotonic() - started < 2.5  # the first call did not hold the line
    assert "live line open" in (agent.data_dir / "harness.log").read_text()


def test_the_bridges_reason_is_logged_and_sent_as_the_failed_reason(made, tmp_path):
    """Staging, Nov 16 (lane HARNFIX): Bree's three picture wakes each logged and sent only
    "the command exited with 3"; the bridge's line saying why was dropped. Now it rides along
    after the same prefix, and other words on stderr never do."""
    bridge = tmp_path / "bridge_like.py"
    bridge.write_text("import sys\nsys.stdin.read()\nprint('a file named in files is outside "
                      "the agent' + chr(39) + 's working folder', file=sys.stderr)\nsys.exit(3)\n")
    agent = made.agent(command=[sys.executable, str(bridge)])
    made.bench.add(item("it_pic"))
    watch(agent).run(1)
    reason = ("the command exited with 3: a file named in files is outside the agent's "
              "working folder")
    assert made.bench.answers == [("it_pic", {"failed": reason})]
    log = (agent.data_dir / "harness.log").read_text()
    assert f"it_pic failed: {reason}; the bench is told and counts it" in log


def test_other_words_on_stderr_are_never_sent(made, tmp_path):
    own = tmp_path / "own.py"
    own.write_text("import sys\nsys.stdin.read()\nprint('the owner said his address is 5 Elm', "
                   "file=sys.stderr)\nsys.exit(3)\n")
    agent = made.agent(command=[sys.executable, str(own)])
    made.bench.add(item("it_own"))
    watch(agent).run(1)
    assert made.bench.answers == [("it_own", {"failed": "the command exited with 3"})]
    assert "5 Elm" not in (agent.data_dir / "harness.log").read_text()
