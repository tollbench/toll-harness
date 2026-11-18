"""Sam's long research steps (staging, Nov 16, 8:46, 9:02 and 9:12 pm): three whole answers
refused as "not one JSON object". Claude Code's text output prints every turn's closing words
(Sam started background helpers and said "Still waiting on the librarian search..." before
the list), and one reply opened with "Here is the hand-in." before the answer. The bridge now
reads Claude Code's last turn only and takes a short lead-in line; a step gets 30 minutes; a
wake stopped at the time limit is said as running out of time, and what it wrote is kept.
The programs here are stand-ins: no model is called."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
import yaml

from tests.fakebench import TOKEN, item
from tests.test_known import program_runs, programs  # noqa: F401 - the fixture
from toll_harness import known, wake
from toll_harness.agent import TOKEN_SECRET, load
from toll_harness.handoff import ANSWERED, hand_off
from toll_harness.watch import Watch

SESSION = "0fa1255f-13cb-43c7-bf2e-9dca5707bb35"


def event(kind, **rest):
    return json.dumps({"type": kind, "session_id": SESSION, **rest})


def said(text, parent=None):
    return event("assistant", parent_tool_use_id=parent,
                 message={"content": [{"type": "text", "text": text}]})


ANSWER = {"status": "done", "note": "15 prospects", "items": "set"}
# what claude 2.1.325 printed on staging for a turn that waited on a helper (checked Nov 16)
SAM_STREAM = "\n".join([
    event("system", subtype="init"),
    said("Still waiting on the librarian search. I'll hand in the list once it reports back."),
    said("Elliott Bay, Third Place Books", parent="toolu_01Akr11dFFHuesxB739U92U3"),
    said(json.dumps(ANSWER)),
    event("result", subtype="success", is_error=False,
          result="Still waiting on the librarian search. I'll hand in the list once it reports "
                 "back."),
    event("result", subtype="success", is_error=False, result=json.dumps(ANSWER)),
]) + "\n"


def test_the_last_turn_is_the_answer_not_the_words_while_helpers_worked():
    assert wake.answer_from(wake.claude_final(SAM_STREAM)) == ANSWER


def test_text_output_of_the_same_run_is_still_refused():
    # what --output-format text printed: every turn's closing words, one after another
    text = ("Still waiting on the librarian search.\n\n" + json.dumps(ANSWER))
    # a short lead-in is read; but a turn's words followed by a list of words is not one object
    assert wake.answer_from(text) == ANSWER
    with pytest.raises(wake.NoAnswer):
        wake.answer_from("Still waiting.\n\n" + json.dumps(ANSWER) + "\n\nThe end.")


def test_main_thread_words_after_the_last_result_win_and_a_helpers_words_never_do():
    stream = "\n".join([event("result", result="waiting"), said('{"ok": true}'),
                        said("kiwi", parent="toolu_1")])
    assert wake.claude_final(stream) == '{"ok": true}'


def test_output_with_no_claude_events_is_read_as_it_is():
    assert wake.claude_final('{"reply": "hi"}\n') == '{"reply": "hi"}\n'
    assert wake.claude_final("```json\n{\"a\": 1}\n```") == "```json\n{\"a\": 1}\n```"


@pytest.mark.parametrize("reply", [
    'The PDF renders cleanly on one page. Here is the hand-in.\n\n' + json.dumps(ANSWER),
    'Here is the hand-in.\n' + json.dumps(ANSWER, indent=1),
    'Done.\nAll fifteen are below.\n\n' + json.dumps(ANSWER) + '\n',
])
def test_a_short_lead_in_before_one_whole_object_is_read_as_the_object(reply):
    assert wake.answer_from(reply) == ANSWER


@pytest.mark.parametrize("reply", [
    'Here it is: {"reply": "hi"}',  # JSON inside the sentence
    'I would send this if you say yes: {"version": "2026-11-12"}',
    'Here is the hand-in.\n\n{"a": 1}\n\nSources: example.com',  # words after it
    'Two answers:\n{"a": 1}\n{"b": 2}',
    'Like {"x": 1} but bigger:\n{"a": 1}',  # JSON in the lead-in
    ("word " * 120) + '\n{"a": 1}',  # a long lead-in is not a lead-in
    'one\ntwo\nthree\nfour\n{"a": 1}',  # more than three lines
    'Here:\n[1, 2]',
])
def test_anything_else_is_still_refused(reply):
    with pytest.raises(wake.NoAnswer):
        wake.answer_from(reply)


STREAMING_CLAUDE = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    sys.stdin.read()
    with open(os.environ["PROGRAM_RECORD"], "a") as handle:
        handle.write(json.dumps({{"argv": sys.argv, "prompt": ""}}) + "\\n")
    assert sys.argv[1:4] == ["-p", "--output-format", "stream-json"], sys.argv
    sys.stdout.write(open(os.environ["STREAM_FILE"]).read())
""")


def test_a_claude_wake_reads_its_stream_and_hands_in_the_last_turn(tmp_path, programs,  # noqa: F811
                                                                   monkeypatch):
    (programs / "claude").write_text(STREAMING_CLAUDE.format(python=sys.executable))
    stream = tmp_path / "stream.jsonl"
    stream.write_text(SAM_STREAM)
    monkeypatch.setenv("STREAM_FILE", str(stream))
    command = known.wake_argv({"agent": "claude", "extra_args": ["--tools", "default"]})
    result = hand_off(command, dict(item("it_9d696ed8"), kind="step"), time_limit=30)
    assert result.status == ANSWERED, (result.why, result.said)
    assert result.answer == ANSWER
    argv = program_runs(tmp_path)[0]["argv"]
    assert "--verbose" in argv and argv.count("--output-format") == 1


def test_an_operators_own_output_format_is_kept(tmp_path, programs):  # noqa: F811
    hand_off(known.wake_argv({"agent": "claude", "extra_args": ["--output-format", "json"]}),
             item("it_o1"), time_limit=30)
    argv = program_runs(tmp_path)[0]["argv"]
    assert argv.count("--output-format") == 1 and "stream-json" not in argv


# -- the time limit ----------------------------------------------------------------------
def agent_at(tmp_path, bench_url, **section):
    folder = tmp_path / "agent"
    folder.mkdir(exist_ok=True)
    config = {"version": 2, "agent": {"name": "Test Sam", "company": "Test Co", "model": "x",
                                      "wake": {"agent": "prompt", "command": section.pop(
                                          "command", ["/bin/true"])}, **section},
              "bench": {"url": bench_url}, "update": {"auto": False}}
    (folder / "agent.yaml").write_text(yaml.safe_dump(config))
    agent = load(folder)
    agent.secrets.set(TOKEN_SECRET, TOKEN)
    return agent


def test_a_step_gets_thirty_minutes_and_a_quick_job_keeps_its_limit(tmp_path):
    agent = agent_at(tmp_path, "http://bench.invalid", time_limit_minutes=15)
    assert agent.time_limit_for("step") == 30 * 60
    assert agent.time_limit_for("proposal_request") == 15 * 60
    assert agent.time_limit_for("open_wants") == 15 * 60
    agent.section["time_limit_minutes_by_kind"] = {"step": 45, "plan_request": 20}
    assert agent.time_limit_for("step") == 45 * 60
    assert agent.time_limit_for("plan_request") == 20 * 60
    agent.section["time_limit_minutes_by_kind"] = {"step": 10}  # the operator may lower it
    assert agent.time_limit_for("step") == 10 * 60
    agent.section["time_limit_minutes"] = 40
    agent.section.pop("time_limit_minutes_by_kind")
    assert agent.time_limit_for("step") == 40 * 60  # never shorter than the agent's own


SLOW = textwrap.dedent("""\
    #!{python}
    import os, sys, time, tempfile
    sys.stdin.read()
    if os.environ.get("WRITE_PART"):
        open("draft-list.md", "w").write("1. Elliott Bay Book Company")
    if os.environ.get("SLEEP"):
        time.sleep(30)
    print('{{"ok": true, "tmp": "' + tempfile.gettempdir() + '"}}')
""")


@pytest.fixture
def slow(tmp_path, programs, monkeypatch):  # noqa: F811
    (programs / "old-agent").write_text(SLOW.format(python=sys.executable))
    monkeypatch.setenv("TOLL_HARNESS_WAKES_DIR", str(tmp_path / "wakes"))
    for key in ("WRITE_PART", "SLEEP"):
        monkeypatch.delenv(key, raising=False)
    return programs / "old-agent"


def run_one(agent, bench, item_value):
    bench.add(item_value)
    Watch(agent, wait=0, updater=lambda *a, **k: {"action": "none"}, sleep=lambda s: None,
          spread=0).run(1)


def test_a_wake_that_runs_out_of_time_says_so_and_keeps_what_it_wrote(tmp_path, bench, slow,
                                                                      monkeypatch):
    monkeypatch.setenv("WRITE_PART", "1")
    monkeypatch.setenv("SLEEP", "1")
    agent = agent_at(tmp_path, bench.url, command=[str(slow)],
                     time_limit_minutes_by_kind={"step": 0.03})  # 1.8 seconds
    run_one(agent, bench, dict(item("it_long"), kind="step"))
    (item_id, answer), = bench.answers
    assert item_id == "it_long" and answer["failed"].startswith(
        "no answer within the time limit")  # the bench's own words for it, unchanged
    log = (agent.data_dir / "harness.log").read_text()
    assert "it_long ran out of time (0 min, the limit for a step item)" in log
    kept = [p for p in (tmp_path / "wakes" / "Test-Sam").iterdir()]
    assert len(kept) == 1 and kept[0].name.startswith("it_long-")
    assert f"what it wrote so far is kept in {kept[0]}" in log
    drafts = list(kept[0].rglob("draft-list.md"))
    assert drafts and drafts[0].read_text() == "1. Elliott Bay Book Company"
    assert str(agent.path.parent) not in str(kept[0])  # never inside the agent's folder


def test_a_wake_that_ends_leaves_no_folder_and_ran_in_its_own(tmp_path, bench, slow,
                                                             monkeypatch):
    monkeypatch.setenv("WRITE_PART", "1")
    agent = agent_at(tmp_path, bench.url, command=[str(slow)])
    run_one(agent, bench, dict(item("it_quick"), kind="step"))
    (item_id, answer), = bench.answers
    assert answer["ok"] is True
    assert answer["tmp"].startswith(str(tmp_path / "wakes" / "Test-Sam" / "it_quick-"))
    assert list((tmp_path / "wakes" / "Test-Sam").iterdir()) == []
