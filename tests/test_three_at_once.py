"""Up to three at once (Steven, 16 November 2026, asked whether each agent may work three jobs
at a time: "yes thats fine if yo uthink it is"). Stand-in wakes only: no model is called and
no real agent is woken.

- three stand-in wakes overlap, each answer is handed in once;
- never two items of one lane (one want's items) at once, and their order is kept;
- counts and parks stay per item (the bench's, as before);
- a picture made by wake A is never handed in by wake B (each Codex wake reads only its own
  session's folder);
- a clean stop in the middle of a run stops every wake and reports nothing;
- an answer resent after a wait that ran out is one hand-in (the same Idempotency-Key);
- an agent the harness has not checked to run beside itself stays at one, and says why."""

from __future__ import annotations

import base64
import json
import os
import sys
import textwrap
import threading
import time

import pytest
import yaml

from tests.fakebench import TOKEN, item
from tests.test_known import programs  # noqa: F401 - the fixture
from toll_harness import watch as watch_module
from toll_harness.agent import TOKEN_SECRET, load
from toll_harness.watch import Watch

# A stand-in agent woken by the 0.x prompt bridge: it writes when it started and ended, sleeps
# as long as the item says (SLEEP_<id> in the environment), then answers or fails.
STANDIN = textwrap.dedent("""\
    #!{python}
    import json, os, re, sys, time
    prompt = sys.stdin.read()
    item_id = re.search(r'"id": "(it_[A-Za-z0-9_]+)"', prompt).group(1)
    record = os.environ["RUNS"]
    def note(what):
        with open(record, "a") as handle:
            handle.write(json.dumps({{"id": item_id, "what": what, "at": time.time(),
                                      "pid": os.getpid(), "tmp": os.environ.get("TMPDIR")}})
                         + "\\n")
    note("start")
    time.sleep(float(os.environ.get("SLEEP_" + item_id, "1.0")))
    note("end")
    if os.environ.get("FAIL_" + item_id):
        sys.exit(1)
    print(json.dumps({{"reply": "done " + item_id}}))
""")


@pytest.fixture
def standin(tmp_path, programs, monkeypatch):  # noqa: F811
    (programs / "old-agent").write_text(STANDIN.format(python=sys.executable))
    monkeypatch.setenv("RUNS", str(tmp_path / "runs.jsonl"))
    monkeypatch.setenv("TOLL_HARNESS_WAKES_DIR", str(tmp_path / "wakes"))
    return programs / "old-agent"


def runs(tmp_path) -> list[dict]:
    path = tmp_path / "runs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def spans(tmp_path) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list] = {}
    started: dict[int, float] = {}
    for run in runs(tmp_path):
        if run["what"] == "start":
            started[run["pid"]] = run["at"]
        else:
            out.setdefault(run["id"], []).append((started[run["pid"]], run["at"]))
    return out


def agent_at(tmp_path, bench_url, wake, **section):
    folder = tmp_path / "agent"
    folder.mkdir(exist_ok=True)
    config = {"version": 2, "agent": {"name": "Test Sam", "company": "Test Co", "model": "x",
                                      "wake": wake, "time_limit_minutes": 15, **section},
              "bench": {"url": bench_url}, "update": {"auto": False}}
    (folder / "agent.yaml").write_text(yaml.safe_dump(config))
    agent = load(folder)
    agent.secrets.set(TOKEN_SECRET, TOKEN)
    return agent


def watcher(agent, **kwargs):
    kwargs.setdefault("updater", lambda *a, **k: {"action": "none"})
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("wait", 0)
    kwargs.setdefault("spread", 0)
    return Watch(agent, **kwargs)


def step(item_id, lane=None, line="turn"):
    value = dict(item(item_id), kind="step", line=line)
    if lane:
        value["lane"] = lane
    return value


def prompt_agent(tmp_path, bench, standin, **section):
    return agent_at(tmp_path, bench.url, {"agent": "prompt", "command": [str(standin)]},
                    **section)


def test_three_stand_in_wakes_overlap_and_each_answer_goes_in_once(tmp_path, bench, standin):
    bench.settings["at_once"] = 3
    agent = prompt_agent(tmp_path, bench, standin, at_once=3)
    for name in ("it_a", "it_b", "it_c"):
        bench.add(step(name, lane=f"want:{name}"))
    started = time.monotonic()
    watcher(agent).run(1)
    took = time.monotonic() - started
    answered = sorted(item_id for item_id, _ in bench.answers)
    assert answered == ["it_a", "it_b", "it_c"]  # each once
    got = spans(tmp_path)
    assert max(s for (s, e), in got.values()) < min(e for (s, e), in got.values())  # overlap
    assert took < 2.6  # three one-second wakes, not three seconds in turn
    log = (agent.data_dir / "harness.log").read_text()
    assert "the bench lets Test Sam work up to 3 items at once; this computer runs 3" in log
    assert "(3 of 3 at once)" in log
    assert len({run["tmp"] for run in runs(tmp_path)}) == 3  # each its own folder
    assert all(answer["reply"] == f"done {item_id}" for item_id, answer in bench.answers)


def test_never_two_of_one_want_at_once_and_their_order_is_kept(tmp_path, bench, standin,
                                                               monkeypatch):
    bench.settings["at_once"] = 3
    agent = prompt_agent(tmp_path, bench, standin, at_once=3)
    bench.add(step("it_w1", lane="want:7"))
    bench.add(step("it_w2", lane="want:7"))
    bench.add(step("it_other", lane="want:8"))
    watcher(agent).run(1)
    got = spans(tmp_path)
    (w1_start, w1_end), = got["it_w1"]
    (w2_start, w2_end), = got["it_w2"]
    (o_start, o_end), = got["it_other"]
    assert w2_start >= w1_end  # one want's items in turn, in the bench's order
    assert o_start < w1_end  # another want's item beside it
    assert [i for i, _ in bench.answers].count("it_w1") == 1


def test_a_quick_job_starts_beside_long_steps(tmp_path, bench, standin, monkeypatch):
    bench.settings["at_once"] = 2
    monkeypatch.setenv("SLEEP_it_long1", "2.5")
    monkeypatch.setenv("SLEEP_it_long2", "2.5")
    monkeypatch.setenv("SLEEP_it_bids", "0.2")
    agent = prompt_agent(tmp_path, bench, standin, at_once=3)  # the bench's 2 wins
    bench.add(step("it_long1", lane="want:1"))
    bench.add(step("it_long2", lane="want:2"))
    bench.add(dict(item("it_bids"), kind="open_wants", line="quick"))
    watch = watcher(agent)
    watch.run(1)
    got = spans(tmp_path)
    assert got["it_bids"][0][0] < got["it_long2"][0][0]  # the quick one went first
    assert got["it_bids"][0][1] < got["it_long1"][0][1]  # and ended while a step ran


def test_counts_and_parks_stay_per_item(tmp_path, bench, standin, monkeypatch):
    bench.settings["at_once"] = 3
    monkeypatch.setenv("FAIL_it_bad", "1")
    monkeypatch.setenv("SLEEP_it_bad", "0.1")
    agent = prompt_agent(tmp_path, bench, standin, at_once=3)
    bench.add(step("it_bad", lane="want:1"))
    bench.add(step("it_good", lane="want:2"))
    watch = watcher(agent)
    watch.start()
    for _ in range(4):  # the bench sends the failed one again; each is a new delivery
        watch.cycle()
        watch.wait_idle(20)
    watch.stop_all()
    bad = [answer for item_id, answer in bench.answers if item_id == "it_bad"]
    assert len(bad) == 3 and all("failed" in answer for answer in bad)
    assert bench.parked == ["it_bad"]
    assert [i for i, _ in bench.answers].count("it_good") == 1


def test_a_clean_stop_mid_run_stops_every_wake_and_reports_nothing(tmp_path, bench, standin,
                                                                    monkeypatch):
    bench.settings["at_once"] = 3
    for name in ("it_s1", "it_s2"):
        monkeypatch.setenv("SLEEP_" + name, "30")
        bench.add(step(name, lane=f"want:{name}"))
    agent = prompt_agent(tmp_path, bench, standin, at_once=3)
    watch = watcher(agent)
    watch.start()
    watch.cycle()
    deadline = time.monotonic() + 10
    while len([r for r in runs(tmp_path) if r["what"] == "start"]) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    stopped = time.monotonic()
    watch.stop_all()
    assert time.monotonic() - stopped < 10
    assert bench.answers == []  # nothing reported: the bench hands them over again
    for run in runs(tmp_path):
        with pytest.raises(ProcessLookupError):
            os.kill(run["pid"], 0)
    log = (agent.data_dir / "harness.log").read_text()
    assert "stopping with it_s1, it_s2 still being worked" in log
    state = json.loads((agent.data_dir / "cursor.json").read_text())
    assert state["after"] in (None, "", "0")  # a restart reads both again


def test_a_restart_hands_over_again_only_what_was_not_finished(tmp_path, bench, standin,
                                                               monkeypatch):
    bench.settings["at_once"] = 3
    monkeypatch.setenv("SLEEP_it_slow", "30")
    monkeypatch.setenv("SLEEP_it_fast", "0.1")
    agent = prompt_agent(tmp_path, bench, standin, at_once=3)
    bench.add(step("it_slow", lane="want:1"))
    bench.add(step("it_fast", lane="want:2"))
    first = watcher(agent)
    first.start()
    first.cycle()
    deadline = time.monotonic() + 10
    while not any(i == "it_fast" for i, _ in bench.answers):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    first.stop_all()  # the harness stops while it_slow is still being worked
    monkeypatch.setenv("SLEEP_it_slow", "0.1")
    second = watcher(load(agent.path))
    second.run(1)
    assert [i for i, _ in bench.answers] == ["it_fast", "it_slow"]  # it_fast never twice


def test_an_agent_not_checked_to_run_beside_itself_stays_at_one_and_says_why(tmp_path, bench,
                                                                            standin):
    bench.settings["at_once"] = 3
    agent = prompt_agent(tmp_path, bench, standin)  # a 0.x command, no at_once of its own
    for name in ("it_x", "it_y"):
        bench.add(step(name, lane=f"want:{name}"))
    watcher(agent).run(1)
    got = spans(tmp_path)
    assert got["it_y"][0][0] >= got["it_x"][0][1]  # one after the other
    log = (agent.data_dir / "harness.log").read_text()
    assert "has not been checked to run beside itself" in log


def test_claude_and_codex_take_the_benchs_number_and_the_operator_may_lower_it(tmp_path, bench):
    claude = agent_at(tmp_path, bench.url, {"agent": "claude"})
    w = watcher(claude)
    w.at_once_bench = 3
    assert w.at_once == 3
    claude.section["at_once"] = 1
    assert w.at_once == 1
    claude.section["at_once"] = 2
    assert w.at_once == 2
    w.at_once_bench = 9
    claude.section.pop("at_once")
    assert w.at_once == 3  # never more than three, whatever the bench says


# -- a picture made by one wake is never handed in by another ------------------------------
CODEX = textwrap.dedent("""\
    #!{python}
    import json, os, re, sys, time
    prompt = sys.stdin.read()
    item_id = re.search(r'"id": "(it_[A-Za-z0-9_]+)"', prompt).group(1)
    session = ("01a1e8" + item_id.replace("_", "")[-6:].rjust(6, "0")
               + "-aaaa-bbbb-cccc-dddddddddddd")
    if not os.environ.get("NO_SESSION"):
        print("session id: " + session, file=sys.stderr)
    folder = os.path.join(os.environ["CODEX_HOME"], "generated_images", session)
    os.makedirs(folder, exist_ok=True)
    named = os.environ.get("NAME_" + item_id)
    if not named:  # this wake draws its own picture, and waits a little
        open(os.path.join(folder, "exec-" + item_id + ".png"), "wb").write(
            b"PNG of " + item_id.encode())
        named = "exec-" + item_id + ".png"
        time.sleep(1.5)
    else:  # this wake names another wake's picture, made a moment ago beside it
        time.sleep(0.6)
    reply = json.dumps({{"status": "done", "result": "x",
                         "files": [{{"filename": "cover.png", "path": named}}]}})
    out = sys.argv[sys.argv.index("--output-last-message") + 1]
    open(out, "w").write(reply)
""")


@pytest.fixture
def codex(tmp_path, programs, monkeypatch):  # noqa: F811
    (programs / "codex").write_text(CODEX.format(python=sys.executable))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("TOLL_HARNESS_WAKES_DIR", str(tmp_path / "wakes"))
    monkeypatch.delenv("NO_SESSION", raising=False)
    return programs / "codex"


def picture_step(item_id):
    return dict(step(item_id, lane=f"want:{item_id}"), answer={"needed": True, "shape": {
        "status": "string", "result": "string", "files": "list"}})


def test_a_picture_from_wake_a_is_never_handed_in_by_wake_b(tmp_path, bench, codex,
                                                            monkeypatch):
    bench.settings["at_once"] = 3
    monkeypatch.setenv("NAME_it_b", "exec-it_a.png")  # B names A's picture
    agent = agent_at(tmp_path, bench.url, {"agent": "codex"})
    bench.add(picture_step("it_a"))
    bench.add(picture_step("it_b"))
    watcher(agent).run(1)
    answers = dict(bench.answers)
    (sent,) = answers["it_a"]["files"]
    assert base64.b64decode(sent["content_base64"]) == b"PNG of it_a"  # A's own picture
    assert answers["it_b"] == {"failed": "the command exited with 3: the agent named a file "
                                         "it did not make: exec-it_a.png"}


def test_with_no_session_named_a_wake_beside_others_takes_no_tool_picture(tmp_path, bench,
                                                                          codex, monkeypatch):
    bench.settings["at_once"] = 3
    monkeypatch.setenv("NO_SESSION", "1")
    agent = agent_at(tmp_path, bench.url, {"agent": "codex"})
    bench.add(picture_step("it_a"))
    watcher(agent).run(1)
    (item_id, answer), = bench.answers
    assert answer["failed"].endswith("the agent named a file it did not make: exec-it_a.png")
    # alone (one at a time), the older rule still finds it
    bench.settings["at_once"] = 1
    bench.add(picture_step("it_c"))
    watcher(load(agent.path)).run(1)
    assert "files" in dict(bench.answers)["it_c"]


# -- one answer is one hand-in ----------------------------------------------------------------
def test_an_answer_resent_after_the_wait_ran_out_is_one_hand_in(tmp_path, bench, standin,
                                                               monkeypatch):
    monkeypatch.setattr(watch_module, "ANSWER_WAIT", 0.5)
    monkeypatch.setenv("SLEEP_it_slowbench", "0")
    agent = prompt_agent(tmp_path, bench, standin)
    bench.answer_delay = 1.5  # the bench takes longer than the harness waits, once
    bench.add(step("it_slowbench"))
    watch = watcher(agent)
    watch.run(1)  # the wait runs out: the answer waits in the outbox
    assert (agent.data_dir / "outbox" / "it_slowbench.json").exists()
    time.sleep(1.5)
    watch.cycle()  # the outbox sends it again, with the same key
    assert [i for i, _ in bench.answers] == ["it_slowbench"]  # handed in once
    keys = [k for k in bench.answer_keys if k]
    assert len(keys) == 2 and keys[0] == keys[1] and keys[0].startswith("ans-")
    assert not (agent.data_dir / "outbox" / "it_slowbench.json").exists()
    log = (agent.data_dir / "harness.log").read_text()
    assert "the bench had this answer already (same key); not handed in twice" in log


def test_the_same_answer_while_the_first_is_still_taken_waits_for_it(tmp_path, bench):
    from toll_harness.logs import ActivityLog
    from toll_harness.watch import answer_key, send_answer
    agent = agent_at(tmp_path, bench.url, {"agent": "claude"})
    bench.add(step("it_twice"))
    bench.answer_delay = 1.0
    log = ActivityLog(agent.data_dir, echo=False)
    key = answer_key("it_twice", {"reply": "x"})  # one hand-in, sent twice
    results = []
    first = threading.Thread(target=lambda: results.append(
        send_answer(agent.bench(), "it_twice", {"reply": "x"}, log, key=key)))
    first.start()
    time.sleep(0.3)
    second = send_answer(agent.bench(), "it_twice", {"reply": "x"}, log, key=key,
                         sleep=lambda s: time.sleep(0.2))
    first.join()
    assert results == ["accepted"] and second == "accepted"
    assert [i for i, _ in bench.answers] == ["it_twice"]
    # a new hand-in with the same words is a new key: the bench counts it
    assert answer_key("it_twice", {"reply": "x"}) != key


def test_a_second_delivery_with_the_same_words_is_a_second_hand_in(tmp_path, bench, standin,
                                                                   monkeypatch):
    monkeypatch.setenv("FAIL_it_again", "1")
    monkeypatch.setenv("SLEEP_it_again", "0")
    agent = prompt_agent(tmp_path, bench, standin)
    bench.add(step("it_again"))
    watcher(agent).run(3)  # failed, sent again, failed, sent again, failed: parked
    assert [i for i, _ in bench.answers] == ["it_again"] * 3
    assert bench.parked == ["it_again"]
    assert len(set(bench.answer_keys)) == 3
