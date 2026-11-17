"""`toll-harness watch`: what the always-on service runs.

It holds GET /me/items?after=<cursor>&wait=55 open, so a new item reaches the agent the moment
the bench has it. It answers `ping` items itself, marks each other item `seen`, hands it to
the agent's command (one at a time) and posts the answer. The cursor, the items of the current
batch already handled, and any answer the bench could not take yet live on disk, so a restart
picks up where it stopped and never hands one delivery over twice. (The bench may deliver the
same item again under a newer cursor, after a failure or a `need`: that is a new delivery.)

UP TO THREE AT ONCE (Steven, 16 November 2026, asked whether each agent may work three jobs at
a time: "yes thats fine if yo uthink it is"). When the bench lets an agent work several items
at once (GET /me `settings.at_once`, and agent.yaml's own `at_once` may lower it), the watch
keeps asking for items while wakes run and starts up to that many at once, each in its own
process, its own scratch folder and its own TMPDIR, never two with the same `lane` (one deal's
or want's items stay in order), in the bench's order (an item the bench marks `line: quick`
goes before the long ones still waiting here). Every count, park and send-back is the bench's
per item exactly as before. An agent the harness cannot be sure is safe to run beside itself
(its own command, or an agent type not checked) stays at one, and the log says why.

The protections are the bench's work (Steven, Nov 11: "is this the work of the bench?"). When
the agent's command crashes, prints something that is not JSON or runs past the time limit, the
harness posts {"failed": "<short reason>"} to the item's answer door and moves on; the bench
counts, parks the item at three and stops sending it. The bench's rate limit is the only cap
on answers: a 429 is waited out as the bench says. A refusal ({ok: false, problems}) is not
retried here either: the bench sends a fix item, handed to the agent like any other. What
stays here is what only the harness can do: the time limit on a hung local command, one item
at a time, and backoff with jitter on the line.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness import __version__
from toll_harness.agent import Agent, write_atomic
from toll_harness.bench import LONGEST_WAIT, BenchError, item_path
from toll_harness.handoff import ANSWERED, FAILED, LATER, _stop, hand_off
from toll_harness.incoming import bring_in, files_in
from toll_harness.logs import ActivityLog
from toll_harness.updates import RESTART_EXIT, check_and_install, due, is_newer

_LOCAL = logging.getLogger("toll_harness.watch")  # this computer's stderr, never the bench
TOKEN_REFUSED_PAUSE = 300.0
LONGEST_BACKOFF = 60.0
# SPREAD OUT (Nov 11): a bench restart drops every held line at once. 200 agents reconnecting
# inside 3 seconds overloaded the bench's login step (45 of 200 calls failed); spread over 20
# to 30 seconds they all got through. So the first call after a start, and the first retry
# after a dropped line or a failed call, wait a random 0 to 30 seconds.
SPREAD_SECONDS = 30.0
BUSY_TRIES = 5  # 429s in a row on one answer before it waits in the outbox
# ONE ANSWER IS ONE HAND-IN (lane HARNFIX's leftover, 16 November 2026: the harness waited 30 s
# for the bench's reply to a hand-in, gave up, kept the answer and sent it AGAIN with nothing
# saying it was the same one; a hand-in whose files the bench compiles can take longer than
# that). Every answer now carries an Idempotency-Key, one per hand-in (answer_key), kept with
# the answer in the outbox so a resend carries the same key, and the bench takes one answer per
# key (409 answer_in_progress while the first is still being handled: wait and ask again). The
# wait for the bench's reply is longer, and longer still for an answer that carries files.
ANSWER_WAIT = 90.0
ANSWER_WAIT_FILES = 300.0
IN_PROGRESS_TRIES = 40  # 409 answer_in_progress replies waited out before the outbox keeps it
OUTBOX_MARK = "toll_harness_outbox"  # an outbox file that keeps its hand-in's key
# WHAT A WAKE WROTE BEFORE THE TIME LIMIT IS KEPT (the foreman, Nov 16: "a timed-out wake
# reported as 'ran out of time' with its partial work kept if the agent wrote any"). Each wake
# runs with its own folder as TMPDIR, outside the agent's folder (where its token is); it is
# removed when the wake ends, except after the time limit when it holds files: then it stays,
# named in the log, for KEEP_DAYS.
WAKES_ENV = "TOLL_HARNESS_WAKES_DIR"
KEEP_DAYS = 7
MOST_AT_ONCE = 3  # the most wakes one agent runs at once, whatever the bench says
# The agents checked to run beside themselves on one computer (16 November 2026): Claude Code
# (each `claude -p` its own session; several share one sign-in by design) and Codex (each
# `codex exec` its own session; a picture its tool makes is taken only from that session's own
# folder, wake.py). Anything else stays at one unless agent.yaml says `at_once` itself.
PARALLEL_SAFE = ("claude", "codex")
QUICK_KINDS = ("terms", "ping", "message", "sign_deal", "plan_fix", "plan_request",
               "proposal_request", "open_wants", "notice")


def backoff_delay(failures: int, rand: Callable[[], float] = random.random,
                  spread: float = SPREAD_SECONDS) -> float:
    """First retry: a random 0 to 30 s. Then 2, 4, 8 ... up to 60 s, jittered half either way."""
    if failures <= 1:
        return spread * rand()
    base = min(LONGEST_BACKOFF, 2.0 ** (failures - 1))
    return base * (0.5 + rand())


class Watch:
    def __init__(self, agent: Agent, *, bench: Any = None, log: ActivityLog | None = None,
                 wait: int = LONGEST_WAIT, sleep: Callable[[float], None] = time.sleep,
                 handoff=hand_off, updater=check_and_install,
                 spread: float = SPREAD_SECONDS, rand: Callable[[], float] = random.random):
        self.agent = agent
        self.bench = bench or agent.bench()
        self.log = log or ActivityLog(agent.data_dir)
        self.wait = wait
        self.sleep = sleep
        self.handoff = handoff
        self.updater = updater
        self.spread = spread
        self.rand = rand
        state = agent.read_json("cursor.json", {})
        self.after: str | None = state.get("after")
        self.done: list[str] = list(state.get("done") or [])
        self.problems = 0  # calls in a row the bench did not answer
        self.line_open = False
        self.wakes_root = wakes_root(agent)
        # several at once (only when the bench and this computer both allow more than one)
        self.at_once_bench = 1
        self.lock = threading.RLock()
        self.running: dict[str, dict[str, Any]] = {}  # item id -> {lane, kind, process, thread}
        self.queue: list[dict[str, Any]] = []  # items waiting here, in the bench's order
        self.pages: list[dict[str, Any]] = []  # [{before, ids, open}] for the restart cursor
        self.replay_done = set(self.done)  # finished before a restart: not handed over again
        self.replay_until = state.get("high")  # a restart reads again up to here
        self.replay_first = True  # (an older cursor file: its one batch only)
        self.paged = False  # the several-at-once line holds this agent's items
        self.stopping = False
        self._said_single = ""

    # -- how many at once ----------------------------------------------------------
    def single_reason(self) -> str:
        """Why this agent runs one item at a time whatever the bench says, or ''."""
        own = self.agent.section.get("at_once")
        if isinstance(own, int) and not isinstance(own, bool) and own >= 1:
            return ""  # the operator said it: their number stands (min with the bench's)
        wake = self.agent.wake
        if wake is None:
            return ("it runs its own command, which may share files between runs; set "
                    "at_once in agent.yaml to let it work several at once")
        name = str(wake.get("agent") or "")
        if name not in PARALLEL_SAFE:
            return (f"{name or 'this agent'} has not been checked to run beside itself on one "
                    "computer; set at_once in agent.yaml to let it")
        return ""

    @property
    def at_once(self) -> int:
        number = max(1, min(int(self.at_once_bench or 1), MOST_AT_ONCE))
        own = self.agent.section.get("at_once")
        if isinstance(own, int) and not isinstance(own, bool) and own >= 1:
            number = min(number, own)
        why = self.single_reason()
        if why and number > 1:
            if why != self._said_single:
                self._said_single = why
                self.log.line(f"{self.agent.name} works one item at a time: {why}")
            return 1
        return number

    @property
    def parallel(self) -> bool:
        return self.at_once > 1 or bool(self.running) or bool(self.queue)

    @property
    def outbox_dir(self) -> Path:
        return self.agent.data_dir / "outbox"

    def _save_cursor(self) -> None:
        if not self.paged:
            self.agent.write_json("cursor.json", {"after": self.after, "done": self.done})
            return
        if not self.pages:
            self.agent.write_json("cursor.json", {"after": self.after, "done": []})
            return
        # several at once: a restart reads again from before the oldest page with work still
        # open, and does not hand over again what was finished since (`high`: up to where)
        low = self.pages[0]["before"]
        done = sorted({i for page in self.pages for i in page["ids"] - page["open"]})
        self.agent.write_json("cursor.json", {"after": low, "done": done, "high": self.after})

    def _finish(self, item_id: str) -> None:
        with self.lock:
            if not self.paged:
                if item_id not in self.done:
                    self.done.append(item_id)
                self._save_cursor()
                return
            for page in self.pages:
                page["open"].discard(item_id)
            while self.pages and not self.pages[0]["open"]:
                self.pages.pop(0)
            self._save_cursor()

    # -- the loop --------------------------------------------------------------------
    def start(self) -> int | None:
        """Say hello, read who we are, update if needed. RESTART_EXIT after an update."""
        self.log.line(f"toll-harness {__version__} watching for {self.agent.name}")
        prune_kept(self.wakes_root)
        if self.spread > 0:
            self.sleep(self.spread * self.rand())  # never all at once after a bench restart
        self._apply_me(self._me_with_backoff())
        if self.updater(self.agent, self.bench, self.log).get("installed_now"):
            return RESTART_EXIT
        return None

    def run(self, cycles: int | None = None) -> int:
        stop = self.start()
        if stop is not None:
            return stop
        count = 0
        try:
            while cycles is None or count < cycles:
                count += 1
                stop = self.cycle()
                if stop is not None:
                    return stop
            self.wait_idle(120)  # a counted run (tests): the wakes it started finish first
            return 0
        finally:
            self.stop_all()

    def stop_all(self) -> None:
        """A clean stop: every running wake's program is stopped (its item is handed over
        again by the bench, as when the harness stops in the middle of one), nothing is
        reported for it, and the threads are waited for."""
        with self.lock:
            self.stopping = True
            running = dict(self.running)
            self.queue = []
        if not running:
            return
        self.log.line("stopping with " + ", ".join(sorted(running)) + " still being worked; "
                      "the bench hands them over again")
        for entry in running.values():
            process = entry.get("process")
            if process is not None:
                _stop(process)
        for entry in running.values():
            thread = entry.get("thread")
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=15)

    def wait_idle(self, seconds: float = 30) -> bool:
        """Wait until no wake runs and nothing waits here (tests, and a stop)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self.lock:
                if not self.running and not self.queue:
                    return True
            time.sleep(0.05)
        return False

    def cycle(self) -> int | None:
        if due(self.agent) and self.updater(self.agent, self.bench, self.log).get(
                "installed_now"):
            return RESTART_EXIT
        if not self._flush_outbox():
            self._pause()
            return None
        try:
            # The first call after a start asks without holding, so "live line open" means the
            # bench answered (staging, Nov 11: a held first call logged it only when the first
            # item came, up to 55 s later). Every later call holds the line.
            response = self.bench.items(self.after, self.wait if self.line_open else 0)
        except BenchError as error:
            self._trouble(error, "waiting for items")
            return None
        if not self.line_open:
            # scripts/install_on_agent.sh waits for this line after a restart.
            self.line_open = True
            self.log.line(f"live line open (toll-harness {__version__})")
        elif self.problems:
            self.log.line("the bench answers again")
        self.problems = 0
        me = response.get("me")
        if isinstance(me, dict) and self._apply_me(me) == "now":
            if self.updater(self.agent, self.bench, self.log, force=True).get("installed_now"):
                return RESTART_EXIT
        items = [item for item in response.get("items") or [] if isinstance(item, dict)]
        if self.paged and not self.parallel and not self.pages:
            self.paged = False  # back to one at a time, with nothing left on the line
            self.done = []
        if self.parallel:
            self._take_page(items, response.get("cursor"))
            self.log.flush(self.bench)
            return None
        for item in items:
            self.handle(item)
        cursor = response.get("cursor")
        if cursor and cursor != self.after:
            self.after = str(cursor)
            self.done = []  # handled ids belong to the batch read under the old cursor
            self._save_cursor()
        self.log.flush(self.bench)
        return None

    # -- several at once ---------------------------------------------------------------
    def _replaying(self) -> bool:
        if self.replay_until is None:
            return False
        try:
            return int(self.after or 0) < int(self.replay_until)
        except (TypeError, ValueError):
            return False

    def _take_page(self, items: list[dict[str, Any]], cursor: Any) -> None:
        pings: list[str] = []
        with self.lock:
            if not self.paged:
                self.paged, self.done = True, []
            page = {"before": self.after, "ids": set(), "open": set()}
            replaying = self._replaying() or self.replay_first
            self.replay_first = False
            for item in items:
                item_id = str(item.get("id") or "")
                try:
                    item_path(item_id, "seen")
                except ValueError:
                    self.log.warn("an item with no usable id was skipped")
                    continue
                if str(item.get("kind")) == "ping":
                    pings.append(item_id)  # answered by the harness, outside the lock
                    continue
                if item_id in self.running:
                    self.log.line(f"{item_id} came again while it is being worked; the bench "
                                  "sends it again if it is still owed")
                    continue
                if replaying and item_id in self.replay_done and item.get("seen"):
                    continue  # finished before the restart
                waiting = next((i for i, queued in enumerate(self.queue)
                                if str(queued.get("id")) == item_id), None)
                if waiting is not None:
                    self.queue[waiting] = item  # the newer copy, in the same place
                    continue
                if self._quick(item):
                    at = next((i for i, queued in enumerate(self.queue)
                               if not self._quick(queued)), len(self.queue))
                    self.queue.insert(at, item)
                else:
                    self.queue.append(item)
                page["ids"].add(item_id)
                page["open"].add(item_id)
            if page["open"]:
                self.pages.append(page)
            if cursor and str(cursor) != self.after:
                self.after = str(cursor)
            if not self._replaying():
                self.replay_done = set()
                self.replay_until = None
            self._save_cursor()
        for item_id in pings:
            self.log.line(f"arrived {item_id} kind=ping")
            self._answer(item_id, "ping", {"pong": True})
        self._start_ready()

    @staticmethod
    def _quick(item: dict[str, Any]) -> bool:
        line = item.get("line")
        if line in ("quick", "turn", "after_fail"):
            return line == "quick"
        return str(item.get("kind")) in QUICK_KINDS

    @staticmethod
    def lane(item: dict[str, Any]) -> str:
        """Items of one lane are never worked at once: the bench's `lane` (one want's or
        deal's items), else the want or deal the item is about, else the item alone."""
        if item.get("lane"):
            return str(item["lane"])
        about = item.get("about") if isinstance(item.get("about"), dict) else {}
        for key in ("want_id", "target_id", "goal_id", "deal_id"):
            if about.get(key):
                return f"{key}:{about[key]}"
        return f"item:{item.get('id')}"

    def _start_ready(self) -> None:
        with self.lock:
            while not self.stopping and len(self.running) < self.at_once and self.queue:
                busy = {entry["lane"] for entry in self.running.values()}
                at = next((i for i, item in enumerate(self.queue)
                           if self.lane(item) not in busy), None)
                if at is None:
                    return  # every waiting item shares a lane with one being worked
                item = self.queue.pop(at)
                item_id = str(item.get("id"))
                entry = {"lane": self.lane(item), "kind": str(item.get("kind")),
                         "process": None, "thread": None}
                self.running[item_id] = entry
                thread = threading.Thread(target=self._work, args=(item,),
                                          name=f"wake-{item_id}", daemon=True)
                entry["thread"] = thread
                if len(self.running) > 1:
                    self.log.line(f"{item_id}: started beside "
                                  + ", ".join(sorted(i for i in self.running if i != item_id))
                                  + f" ({len(self.running)} of {self.at_once} at once)")
                thread.start()

    def _work(self, item: dict[str, Any]) -> None:
        item_id = str(item.get("id"))
        try:
            self.handle(item, fresh=True)
        except Exception as error:  # noqa: BLE001 - one wake never stops the others
            self.log.warn(f"{item_id}: the harness broke handling it ({type(error).__name__})")
            _LOCAL.warning("the wake thread for %s failed", item_id, exc_info=True)  # stderr only
        finally:
            with self.lock:
                self.running.pop(item_id, None)
                if not self.stopping:
                    self._finish(item_id)
            if not self.stopping:
                self._start_ready()

    def _started(self, item_id: str, process: Any) -> None:
        with self.lock:
            if item_id in self.running:
                self.running[item_id]["process"] = process
            stopping = self.stopping
        if stopping:
            _stop(process)

    def _me_with_backoff(self) -> dict[str, Any]:
        while True:
            try:
                me = self.bench.me()
                self.problems = 0
                return me
            except BenchError as error:
                self._trouble(error, "reading who this agent is")

    def _apply_me(self, me: dict[str, Any]) -> str | None:
        settings = me.get("settings") if isinstance(me.get("settings"), dict) else {}
        logging_on = bool(settings.get("logging"))
        if logging_on != self.log.to_bench:
            self.log.to_bench = logging_on
            self.log.line(f"logging to the bench is {'on' if logging_on else 'off'}")
        number = settings.get("at_once")
        number = number if isinstance(number, int) and not isinstance(number, bool) else 1
        if number != self.at_once_bench:
            self.at_once_bench = max(1, number)
            self.log.line(f"the bench lets {self.agent.name} work up to {self.at_once_bench} "
                          f"item{'s' if self.at_once_bench != 1 else ''} at once; this "
                          f"computer runs {self.at_once}")
            self._start_ready()
        versions = me.get("harness") or me.get("connector")
        versions = versions if isinstance(versions, dict) else {}
        return "now" if is_newer(versions.get("minimum"), __version__) else None

    def _trouble(self, error: BenchError, doing: str) -> None:
        self.problems += 1
        if error.status in (401, 403):
            self.log.warn(f"the bench refused this agent's token while {doing} "
                          f"({error.status}); trying again in 5 minutes")
            self.sleep(TOKEN_REFUSED_PAUSE)
            return
        if error.status == 429:
            pause = error.retry_after or self._delay()
            self.log.line(f"the bench asks the harness to wait {pause:.0f} s")
            self.sleep(pause)
            return
        if self.problems in (1, 5) or self.problems % 30 == 0:
            self.log.warn(f"no answer from the bench while {doing} ({error.status or 'down'}); "
                          "trying again with backoff")
        self.sleep(self._delay())

    def _pause(self) -> None:
        self.problems += 1
        self.sleep(self._delay())

    def _delay(self) -> float:
        return backoff_delay(self.problems, self.rand, self.spread)

    # -- one item --------------------------------------------------------------------
    def handle(self, item: dict[str, Any], fresh: bool = False) -> str:
        """One item, start to end. `fresh`: the several-at-once line already decided it is a
        new delivery (its own restart rule), so the one-batch `done` list is not read."""
        item_id, kind = str(item.get("id") or ""), str(item.get("kind") or "unknown")[:40]
        try:
            item_path(item_id, "seen")
        except ValueError:
            self.log.warn("an item with no usable id was skipped")
            return "skipped"
        if not fresh and item_id in self.done:
            return "done"
        self.log.line(f"arrived {item_id} kind={kind}")
        if kind == "ping":
            return self._answer(item_id, kind, {"pong": True})
        try:
            self.bench.seen(item_id)
        except BenchError as error:
            self.log.line(f"seen not recorded for {item_id} ({error.status or 'no answer'})")
        shape = item.get("answer") if isinstance(item.get("answer"), dict) else {}
        wants_answer = kind != "notice" and shape.get("needed", True) is not False
        files = self._bring_files(item_id, item)
        limit = self.agent.time_limit_for(kind)
        folder = self._wake_folder(item_id)
        beside = item_id in self.running  # started by the several-at-once line
        result = None
        try:
            result = self.handoff(self.agent.command, item, time_limit=limit,
                                  config_path=str(self.agent.path),
                                  cwd=str(self.agent.path.parent),
                                  **({"files_dir": str(files)} if files else {}),
                                  **({"wake_dir": str(folder)} if folder else {}),
                                  **({"on_start": lambda p: self._started(item_id, p),
                                      "overlapping": self.at_once > 1} if beside else {}))
        finally:
            if files:
                shutil.rmtree(files, ignore_errors=True)  # nothing is kept after the item
            kept = folder and result is not None and getattr(result, "ran_out", False) \
                and _holds_files(folder)
            if folder and not kept:
                shutil.rmtree(folder, ignore_errors=True)
        if self.stopping and beside:
            self.log.line(f"{item_id}: stopped with the harness after {result.seconds:.1f} s; "
                          "not reported")
            return "stopped"
        self.log.line(f"handed off {item_id}: {result.status} after {result.seconds:.1f} s")
        if getattr(result, "ran_out", False):
            self.log.warn(f"{item_id} ran out of time ({int(limit // 60)} min, the limit for "
                          f"a {kind} item)" + (f"; what it wrote so far is kept in {folder}"
                                               if kept else "; it wrote nothing to keep"))
        if result.status == ANSWERED:
            if not wants_answer:
                self._finish(item_id)
                return "read"
            return self._answer(item_id, kind, result.answer or {})
        if result.status == LATER:
            self.log.line(f"{item_id}: the agent will answer later "
                          f"(toll-harness answer {item_id})")
            self._finish(item_id)
            return "later"
        assert result.status == FAILED
        # the bridge's own reason rides along (Nov 16: "exited with 3" alone told nobody why)
        self.log.warn(f"{item_id} failed: {result.reported}; the bench is told and counts it")
        if not wants_answer:
            self._finish(item_id)
            return "failed"
        return self._answer(item_id, kind, {"failed": result.reported})

    def _wake_folder(self, item_id: str) -> Path | None:
        """This wake's own folder (its TMPDIR), made fresh; None when it cannot be made."""
        if self.agent.wake is None:
            return None
        try:
            self.wakes_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            return Path(tempfile.mkdtemp(prefix=f"{item_id}-", dir=self.wakes_root))
        except OSError:
            return None

    def _bring_files(self, item_id: str, item: dict[str, Any]) -> Path | None:
        """For an agent woken by name, the files the item lists, fetched here (the key stays
        in this process) into a folder made for this item; see incoming.py. None when the item
        lists none or the agent runs its own command."""
        if self.agent.wake is None or not files_in(item):
            return None
        folder = None
        try:
            folder = Path(tempfile.mkdtemp(prefix="toll-files-"))
            bring_in(self.bench, item, folder, self.log, label=item_id)
        except Exception as error:  # noqa: BLE001 - a file never fails the item
            self.log.warn(f"{item_id}: files not brought in ({type(error).__name__})")
        return folder

    # -- sending answers -----------------------------------------------------------------
    def _answer(self, item_id: str, kind: str, answer: dict[str, Any]) -> str:
        # one key per hand-in: the same delivery's resend carries it, a new delivery of the
        # same item (the bench sent it again) gets its own, even with the same words
        key = answer_key(item_id, answer)
        outcome = send_answer(self.bench, item_id, answer, self.log, kind=kind,
                              sleep=self.sleep, key=key)
        if outcome == "kept":
            # whole or not at all: with several at once the line may read the outbox meanwhile
            write_atomic(self.outbox_dir / f"{item_id}.json",
                         json.dumps({OUTBOX_MARK: 1, "key": key, "answer": answer}))
        self._finish(item_id)
        return outcome

    def _flush_outbox(self) -> bool:
        """Send answers the bench could not take before. False when it still cannot."""
        if not self.outbox_dir.is_dir():
            return True
        for path in sorted(self.outbox_dir.glob("*.json")):
            try:
                kept = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                path.unlink()
                continue
            key = None
            if isinstance(kept, dict) and kept.get(OUTBOX_MARK) and isinstance(
                    kept.get("answer"), dict):
                key, kept = kept.get("key"), kept["answer"]  # the same hand-in, the same key
            if send_answer(self.bench, path.stem, kept, self.log, sleep=self.sleep,
                           key=key) == "kept":
                return False
            path.unlink()
        return True


def wakes_root(agent: Agent) -> Path:
    """Where this agent's wakes get their own folders: TOLL_HARNESS_WAKES_DIR, else
    ~/.cache/toll-harness/wakes/<agent>. Never inside the agent's own folder."""
    base = os.environ.get(WAKES_ENV) or os.path.join(os.path.expanduser("~"), ".cache",
                                                     "toll-harness", "wakes")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent.name).strip("-.") or "agent"
    return Path(base) / name


def _holds_files(folder: Path) -> bool:
    try:
        return any(path.is_file() for path in folder.rglob("*"))
    except OSError:
        return False


def prune_kept(root: Path, days: float = KEEP_DAYS) -> None:
    """Remove kept wake folders older than `days`; never raises."""
    try:
        cutoff = time.time() - days * 86400
        for path in root.iterdir():
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def where_lines(problems: Any, most: int = 3) -> str:
    """The bench's {where, rule, fix} rows, short, for the log: where each problem is."""
    if not isinstance(problems, list):
        return ""
    parts = []
    for row in problems[:most]:
        if isinstance(row, dict):
            where = str(row.get("where") or row.get("path") or "?")[:60]
            rule = str(row.get("rule") or row.get("code") or row.get("detail") or "")[:120]
            parts.append(f"{where}: {rule}" if rule else where)
    more = len(problems) - most
    return ("; ".join(parts) + (f"; and {more} more" if more > 0 else "")) if parts else ""


def answer_key(item_id: str, answer: dict[str, Any]) -> str:
    """The Idempotency-Key of ONE hand-in: the item, the answer and a nonce made when the hand-in
    is made, so its resend (the outbox keeps the key) is the same hand-in and a later delivery of
    the same item with the same words is a new one (a second failure report still counts)."""
    text = json.dumps(answer, sort_keys=True, ensure_ascii=False, default=str)
    nonce = secrets.token_hex(8)
    digest = hashlib.sha256(f"{item_id}\n{nonce}\n{text}".encode("utf-8")).hexdigest()
    return "ans-" + digest[:40]


def _carries_files(answer: dict[str, Any]) -> bool:
    files = answer.get("files")
    return isinstance(files, list) and any(
        isinstance(entry, dict) and entry.get("content_base64") for entry in files)


def send_answer(bench: Any, item_id: str, answer: dict[str, Any], log: ActivityLog,
                kind: str = "", sleep: Callable[[float], None] = time.sleep,
                key: str | None = None) -> str:
    """POST the answer: 'accepted', 'refused', or 'kept' (the bench did not take it yet).
    `key`: this hand-in's Idempotency-Key (a new one when none is given)."""
    label = f"{item_id}{f' ({kind})' if kind else ''}"
    what = "failure report" if set(answer) == {"failed"} else "answer"
    key = key or answer_key(item_id, answer)
    wait = ANSWER_WAIT_FILES if _carries_files(answer) else ANSWER_WAIT
    busy, in_progress = 0, 0
    while True:
        try:
            reply = _answer_call(bench, item_id, answer, key, wait)
            break
        except BenchError as error:
            if error.status == 409 and error.code == "answer_in_progress" \
                    and in_progress < IN_PROGRESS_TRIES:
                in_progress += 1
                pause = error.retry_after or 10.0
                if in_progress == 1:
                    log.line(f"{label}: the bench is still taking this answer; asking again "
                             "with the same key")
                sleep(pause)
                continue
            if error.status == 429 and busy + 1 < BUSY_TRIES:
                busy += 1
                pause = error.retry_after or backoff_delay(busy)
                log.line(f"the bench asks the harness to wait {pause:.0f} s before answering")
                sleep(pause)
                continue
            if error.status == 409 and error.code == "answer_in_progress":
                log.warn(f"{what} for {label} kept; the bench is still taking it")
                return "kept"
            if error.unreachable:
                log.warn(f"{what} for {label} kept; it goes when the bench answers again")
                return "kept"
            problems = error.body.get("problems")
            count = len(problems) if isinstance(problems, list) else 0
            where = where_lines(problems)
            log.warn(f"{what} for {label} refused ({error.status} {error.code}"
                     f"{f', {count} problems' if count else ''}); the bench sends what to fix"
                     f"{f' -- {where}' if where else ''}")
            return "refused"
    if reply.get("again") and reply.get("ok") is not False:
        log.line(f"{label}: the bench had this answer already (same key); not handed in twice")
    if reply.get("ok") is False:
        problems = reply.get("problems")
        count = len(problems) if isinstance(problems, list) else 0
        where = where_lines(problems)
        log.warn(f"{what} for {label} refused ({count} problems); the bench sends what to fix"
                 f"{f' -- {where}' if where else ''}")
        return "refused"
    log.line(f"{'reported failure on' if what != 'answer' else 'answered'} {label}: accepted")
    return "accepted"


def _answer_call(bench: Any, item_id: str, answer: dict[str, Any], key: str,
                 wait: float) -> dict[str, Any]:
    return bench.answer(item_id, answer, key=key, timeout=wait)
