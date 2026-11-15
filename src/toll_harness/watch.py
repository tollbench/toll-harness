"""`toll-harness watch`: what the always-on service runs.

It holds GET /me/items?after=<cursor>&wait=55 open, so a new item reaches the agent the moment
the bench has it. It answers `ping` items itself, marks each other item `seen`, hands it to
the agent's command (one at a time) and posts the answer. The cursor, the items of the current
batch already handled, and any answer the bench could not take yet live on disk, so a restart
picks up where it stopped and never hands one delivery over twice. (The bench may deliver the
same item again under a newer cursor, after a failure or a `need`: that is a new delivery.)

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

import json
import random
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toll_harness import __version__
from toll_harness.agent import Agent
from toll_harness.bench import LONGEST_WAIT, BenchError, item_path
from toll_harness.handoff import ANSWERED, FAILED, LATER, hand_off
from toll_harness.incoming import bring_in, files_in
from toll_harness.logs import ActivityLog
from toll_harness.updates import RESTART_EXIT, check_and_install, due, is_newer

TOKEN_REFUSED_PAUSE = 300.0
LONGEST_BACKOFF = 60.0
# SPREAD OUT (Nov 11): a bench restart drops every held line at once. 200 agents reconnecting
# inside 3 seconds overloaded the bench's login step (45 of 200 calls failed); spread over 20
# to 30 seconds they all got through. So the first call after a start, and the first retry
# after a dropped line or a failed call, wait a random 0 to 30 seconds.
SPREAD_SECONDS = 30.0
BUSY_TRIES = 5  # 429s in a row on one answer before it waits in the outbox


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

    @property
    def outbox_dir(self) -> Path:
        return self.agent.data_dir / "outbox"

    def _save_cursor(self) -> None:
        self.agent.write_json("cursor.json", {"after": self.after, "done": self.done})

    def _finish(self, item_id: str) -> None:
        if item_id not in self.done:
            self.done.append(item_id)
        self._save_cursor()

    # -- the loop --------------------------------------------------------------------
    def start(self) -> int | None:
        """Say hello, read who we are, update if needed. RESTART_EXIT after an update."""
        self.log.line(f"toll-harness {__version__} watching for {self.agent.name}")
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
        while cycles is None or count < cycles:
            count += 1
            stop = self.cycle()
            if stop is not None:
                return stop
        return 0

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
        for item in response.get("items") or []:
            if isinstance(item, dict):
                self.handle(item)
        cursor = response.get("cursor")
        if cursor and cursor != self.after:
            self.after = str(cursor)
            self.done = []  # handled ids belong to the batch read under the old cursor
            self._save_cursor()
        self.log.flush(self.bench)
        return None

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
    def handle(self, item: dict[str, Any]) -> str:
        item_id, kind = str(item.get("id") or ""), str(item.get("kind") or "unknown")[:40]
        try:
            item_path(item_id, "seen")
        except ValueError:
            self.log.warn("an item with no usable id was skipped")
            return "skipped"
        if item_id in self.done:
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
        try:
            result = self.handoff(self.agent.command, item,
                                  time_limit=self.agent.time_limit_seconds,
                                  config_path=str(self.agent.path),
                                  cwd=str(self.agent.path.parent),
                                  **({"files_dir": str(files)} if files else {}))
        finally:
            if files:
                shutil.rmtree(files, ignore_errors=True)  # nothing is kept after the item
        self.log.line(f"handed off {item_id}: {result.status} after {result.seconds:.1f} s")
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
        self.log.warn(f"{item_id} failed: {result.why}; the bench is told and counts it")
        if not wants_answer:
            self._finish(item_id)
            return "failed"
        return self._answer(item_id, kind, {"failed": result.why})

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
        outcome = send_answer(self.bench, item_id, answer, self.log, kind=kind,
                              sleep=self.sleep)
        if outcome == "kept":
            self.outbox_dir.mkdir(parents=True, exist_ok=True)
            (self.outbox_dir / f"{item_id}.json").write_text(json.dumps(answer),
                                                              encoding="utf-8")
        self._finish(item_id)
        return outcome

    def _flush_outbox(self) -> bool:
        """Send answers the bench could not take before. False when it still cannot."""
        if not self.outbox_dir.is_dir():
            return True
        for path in sorted(self.outbox_dir.glob("*.json")):
            try:
                answer = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                path.unlink()
                continue
            if send_answer(self.bench, path.stem, answer, self.log, sleep=self.sleep) == "kept":
                return False
            path.unlink()
        return True


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


def send_answer(bench: Any, item_id: str, answer: dict[str, Any], log: ActivityLog,
                kind: str = "", sleep: Callable[[float], None] = time.sleep) -> str:
    """POST the answer: 'accepted', 'refused', or 'kept' (the bench did not take it yet)."""
    label = f"{item_id}{f' ({kind})' if kind else ''}"
    what = "failure report" if set(answer) == {"failed"} else "answer"
    for busy in range(1, BUSY_TRIES + 1):
        try:
            reply = bench.answer(item_id, answer)
            break
        except BenchError as error:
            if error.status == 429 and busy < BUSY_TRIES:
                pause = error.retry_after or backoff_delay(busy)
                log.line(f"the bench asks the harness to wait {pause:.0f} s before answering")
                sleep(pause)
                continue
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
    if reply.get("ok") is False:
        problems = reply.get("problems")
        count = len(problems) if isinstance(problems, list) else 0
        where = where_lines(problems)
        log.warn(f"{what} for {label} refused ({count} problems); the bench sends what to fix"
                 f"{f' -- {where}' if where else ''}")
        return "refused"
    log.line(f"{'reported failure on' if what != 'answer' else 'answered'} {label}: accepted")
    return "accepted"
