"""THE AGENT'S OWN RUN, LIVE, ON THE WANT'S LOG (0.75.0).

WHAT FORCED IT (Steven, 2026-11-04). The server keeps one plain-text log per
want (``wants.log``) that follows a want from posting to the end. Everything
the bench saw was on it; what the agent thought was not -- that lived only on
the agent's own machine, in a log nobody on the bench could read. Steven
picked "the harness streams everything": every model turn, live, to the
want's own door.

THE DOOR. ``POST {bench}/api/bench/targets/{target_id}/thinking`` with the
agent's bearer token::

    {"deal_id": ..., "step_id": ..., "run_id": ...,
     "entries": [{"at": <ISO 8601 UTC>, "kind": <kind>, ...}, ...]}   # 1-200

A model turn is, in order: ``model_call`` (model, tokens, cost when the price
table knows the model, duration), ``think`` (the turn's words, when it has
any; a rail's own reasoning text first, marked "(reasoning)"), one
``tool_call`` per call; then one ``tool_result`` per result as each tool
returns. ``note`` is anything else worth a line (a model call that failed).
Nothing is sent without a target id; ``deal_id`` and ``step_id`` ride when
the harness is working a deal or a step.

NEVER IN THE WAY. A background sender batches: every 2 seconds or 50 entries,
whichever comes first, and at the end of a run or a step. Recording an entry
never raises, never touches the network and never waits on the sender: a
failed batch is dropped, counted and said in one local log line (at most one
a minute); the queue holds 2,000 entries and drops the oldest past that,
counted. A 404 (the door is not there yet, or this agent cannot see the want)
quietly stops sending for that want for ten minutes. At the end of the
process the sender gets two seconds to send what is left, and no more.

SECRETS STAY HOME. Bearer tokens, ``sk-``/``rk-``/``pk-`` keys and their
cousins, the agent's own bench token, and anything under a key named like
key / secret / token / password are masked before an entry leaves the
machine (the server masks too). Text, arguments and results are cut at 20,000
characters, saying how many more there were.

THE SWITCH. On by default. ``TOLL_HARNESS_THINKING=0`` (or ``off``,
``false``, ``no``) or ``toll_bench.thinking: false`` in agent.yaml turns it
off; off wins. Read at call time, so the environment can turn a running
stream off.
"""

from __future__ import annotations

import atexit
import contextlib
import contextvars
import json
import logging
import os
import re
import threading
import time
import weakref
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger("toll_harness.thinking")

THINKING_ENV = "TOLL_HARNESS_THINKING"
OFF_VALUES = frozenset({"0", "off", "false", "no", "disabled"})
ON_VALUES = frozenset({"1", "on", "true", "yes", "enabled"})

KINDS = ("model_call", "think", "tool_call", "tool_result", "note")
PLACE_KEYS = ("target_id", "deal_id", "step_id", "run_id")

TEXT_CAP = 20_000  # characters; the server trims past this too
BATCH_SIZE = 50  # entries per POST (the door takes 1-200)
DOOR_MAX_ENTRIES = 200
FLUSH_SECONDS = 2.0  # the oldest queued entry waits no longer than this
QUEUE_CAP = 2_000  # entries; past it the oldest go, counted
UNPLACED_PER_RUN = 200  # entries a run holds before it names its want
UNPLACED_RUNS = 20
NOT_THERE_SECONDS = 600.0  # a 404 pauses that want this long
WARN_EVERY_SECONDS = 60.0  # one local line per this, at most
IDLE_SECONDS = 30.0  # the sender thread goes away after this long with nothing to do
EXIT_SECONDS = 2.0  # what the end of the process gives the sender, in all
REASONING_MARK = "(reasoning) "
MASK = "[REDACTED]"


# ---------------------------------------------------------------------------
# 1. The switch
# ---------------------------------------------------------------------------
def configured_switch(value: Any) -> bool:
    """agent.yaml ``toll_bench.thinking`` as a bool: unset is on.

    A typo is a load error rather than a guess, the way every other agent.yaml
    key is read."""
    if value is None or isinstance(value, bool):
        return value is not False
    word = str(value).strip().lower()
    if word in OFF_VALUES:
        return False
    if word in ON_VALUES:
        return True
    raise ValueError("toll_bench.thinking must be true or false")


def enabled(configured: Any = None) -> bool:
    """On unless the environment or the configuration says off. Off wins."""
    if str(os.environ.get(THINKING_ENV, "")).strip().lower() in OFF_VALUES:
        return False
    try:
        return configured_switch(configured)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 2. Where the agent is working: the want, the deal, the step, the run
# ---------------------------------------------------------------------------
_PLACE: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "toll_harness_thinking_place", default=None
)


def _merge(base: Mapping[str, Any] | None, ids: Mapping[str, Any]) -> dict[str, str]:
    """``ids`` over ``base``. An empty id never erases one; a different want
    drops the deal and step that belonged to the old one."""
    out = {key: str(value) for key, value in (base or {}).items() if value}
    target = ids.get("target_id")
    if target and out.get("target_id") and str(target) != out["target_id"]:
        out.pop("deal_id", None)
        out.pop("step_id", None)
    for key in PLACE_KEYS:
        value = ids.get(key)
        if value:
            out[key] = str(value)
    return out


@contextlib.contextmanager
def working_on(**ids: Any) -> Iterator[None]:
    """Everything recorded inside this block is about these ids, unless the
    recording names its own (which win)."""
    token = _PLACE.set(_merge(_PLACE.get(), ids))
    try:
        yield
    finally:
        _PLACE.reset(token)


def here(**ids: Any) -> dict[str, str]:
    """Where the agent is working: the ambient place (``working_on``) with
    these ids over it."""
    return _merge(_PLACE.get(), ids)


def _turn_place(where: Mapping[str, Any], calls: Iterable[Any]) -> dict[str, str]:
    """The place a model turn is about: the first of its calls that names a
    want, deal or step says so, before the tool has even run (so the turn
    that first names its want is filed under it)."""
    for call in calls:
        arguments = getattr(call, "arguments", None)
        if not isinstance(arguments, dict):
            continue
        named = {key: arguments.get(key) for key in ("target_id", "deal_id", "step_id")}
        named = {key: value for key, value in named.items() if isinstance(value, str) and value}
        if named:
            return _merge(where, named)
    return dict(where)


# ---------------------------------------------------------------------------
# 3. What leaves the machine: masked, then cut
# ---------------------------------------------------------------------------
_SECRET_WORDS = frozenset({
    "key", "secret", "secrets", "token", "password", "passwd", "passphrase", "pwd",
    "credential", "credentials", "authorization", "auth", "cookie", "cookies", "otp",
    "apikey", "bearer",
})
_SECRET_PARTS = ("secret", "password", "passwd", "apikey", "privatekey", "accesskey",
                 "credential")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_WORDS = re.compile(r"[a-z0-9]+")
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # An Authorization header value, wherever it is written.
    (re.compile(r"(?i)\b(bearer|basic)(\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1\2" + MASK),
    # sk-/rk-/pk- keys (Stripe's sk_live_, Anthropic's sk-ant-, OpenAI's sk-proj-).
    (re.compile(r"\b(?:sk|rk|pk)[-_](?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{8,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr|github_pat)_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    # key=value / "key": "value" written as text (escaped JSON too), for a key
    # named like a secret.
    (re.compile(
        r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|api_?key|access_?token|refresh_?token"
        r"|auth_?token|private_?key|client_?secret)[A-Za-z0-9_]*)"
        r"(\\?\"?'?\s*[:=]\s*\\?\"?'?)([^\s\"'&,;}\\]{4,})"
    ), r"\1\2" + MASK),
)


def secret_key(name: Any) -> bool:
    """True for a key named like a secret: key, secret, token, password and kin."""
    words = _WORDS.findall(_CAMEL.sub(r"\1_\2", str(name)).lower())
    if any(word in _SECRET_WORDS for word in words):
        return True
    joined = "".join(words)
    return any(part in joined for part in _SECRET_PARTS)


def mask_text(text: str, literals: Iterable[str] = ()) -> str:
    for literal in literals:
        if literal and literal in text:
            text = text.replace(literal, MASK)
    for pattern, replacement in _VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def mask(value: Any, literals: Iterable[str] = ()) -> Any:
    """``value`` with every secret it carries masked: under a key named like
    one, in a string that looks like one, or one of ``literals`` exactly."""
    return _mask(value, _literals(literals), 0, True)


def _literals(literals: Iterable[str]) -> tuple[str, ...]:
    return tuple(literal for literal in literals if isinstance(literal, str) and literal)


def _mask(value: Any, literals: tuple[str, ...], depth: int, patterns: bool) -> Any:
    """``patterns`` False masks by key only: the cheap walk an oversized
    value gets before it is cut and its text is read."""
    if depth > 40:
        return "[nested too deep]"
    if isinstance(value, str):
        return mask_text(value, literals) if patterns else value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if secret_key(name) and not (item is None or isinstance(item, (bool, int, float))):
                out[name] = MASK
            else:
                out[name] = _mask(item, literals, depth + 1, patterns)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_mask(item, literals, depth + 1, patterns) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return mask_text(str(value), literals) if patterns else str(value)


def _cut(text: str, keep: int, extra: int = 0) -> str:
    return text[:keep] + f"... ({len(text) - keep + extra} more characters)"


def _fit(text: str, limit: int, measure: Callable[[str], int] = len, extra: int = 0) -> str:
    """The longest head of ``text`` that, with its "... (N more characters)",
    measures at most ``limit``. ``extra`` is what was already left out
    before ``text`` (it is never shorter than the truth)."""
    if not extra and measure(text) <= limit:
        return text
    low, high = 0, min(len(text), limit)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(_cut(text, middle, extra)) <= limit:
            low = middle
        else:
            high = middle - 1
    return _cut(text, low, extra)


def cap_text(text: Any, limit: int = TEXT_CAP) -> str:
    """At most ``limit`` characters, saying how many more there were."""
    return _fit(str(text if text is not None else ""), limit)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _shrink(text: str, limit: int, literals: tuple[str, ...],
            measure: Callable[[str], int] = len) -> str:
    """Masked, then cut. The patterns never read more than twice the cap (a
    5 MB tool result costs the run no more than a 40 KB one), and the count
    of what was left out stays true."""
    room = 2 * limit
    extra = max(0, len(text) - room)
    return _fit(mask_text(text[:room] if extra else text, literals), limit, measure, extra)


def prepare_text(text: Any, limit: int = TEXT_CAP, literals: Iterable[str] = ()) -> str:
    """Words for a think or note line: masked, at most ``limit`` characters."""
    return _shrink(str(text if text is not None else ""), limit, _literals(literals))


def prepare_result(value: Any, limit: int = TEXT_CAP, literals: Iterable[str] = ()) -> Any:
    """A tool result: masked; an object that fits rides as it is, one that
    does not rides as its JSON text, cut."""
    secrets = _literals(literals)
    if isinstance(value, str):
        return _shrink(value, limit, secrets)
    keyed = _mask(value, secrets, 0, False)
    text = _dumps(keyed)
    if len(text) <= 2 * limit:
        masked = _mask(keyed, secrets, 0, True)
        text = _dumps(masked)
        if len(text) <= limit:
            return masked
    return _shrink(text, limit, secrets)


def prepare_arguments(value: Any, limit: int = TEXT_CAP, literals: Iterable[str] = ()) -> dict:
    """Tool arguments, always an object: masked; one that fits rides as it
    is, one that does not rides as ``{"cut": <its JSON text, cut>}``."""
    secrets = _literals(literals)
    if not isinstance(value, Mapping):
        value = {"value": value}
    keyed = _mask(value, secrets, 0, False)
    text = _dumps(keyed)
    if len(text) <= 2 * limit:
        masked = _mask(keyed, secrets, 0, True)
        text = _dumps(masked)
        if len(text) <= limit:
            return masked
    return {"cut": _shrink(text, limit, secrets, lambda head: len(_dumps({"cut": head})))}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 4. The stream
# ---------------------------------------------------------------------------
Transport = Callable[[str, dict[str, Any]], Any]
_LIVE: weakref.WeakSet[ThinkingStream] = weakref.WeakSet()


class ThinkingStream:
    """Records a run's entries and sends them in the background.

    ``transport(target_id, payload)`` posts one batch and returns the door's
    answer, raising on a refusal (an exception with ``status`` 404 means the
    door or the want is not there). The bench client's ``post_thinking`` is
    the real one; the tests pass a fake.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        secrets: Iterable[str] = (),
        batch_size: int = BATCH_SIZE,
        flush_seconds: float = FLUSH_SECONDS,
        queue_cap: int = QUEUE_CAP,
        text_cap: int = TEXT_CAP,
        not_there_seconds: float = NOT_THERE_SECONDS,
        warn_every_seconds: float = WARN_EVERY_SECONDS,
        idle_seconds: float = IDLE_SECONDS,
        logger: logging.Logger | None = None,
    ):
        self._transport = transport
        self._secrets = tuple(secret for secret in secrets if isinstance(secret, str) and secret)
        self.batch_size = max(1, min(int(batch_size), DOOR_MAX_ENTRIES))
        self.flush_seconds = max(0.0, float(flush_seconds))
        self.queue_cap = max(1, int(queue_cap))
        self.text_cap = max(100, int(text_cap))
        self.not_there_seconds = float(not_there_seconds)
        self.warn_every_seconds = float(warn_every_seconds)
        self.idle_seconds = max(0.05, float(idle_seconds))
        self.log = logger or _LOGGER
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        # (key, entry, queued_at); key is (target_id, deal_id, step_id, run_id).
        self._queue: deque[tuple[tuple[str, str, str, str], dict[str, Any], float]] = deque()
        self._unplaced: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self._not_there: dict[str, float] = {}
        self._sender: threading.Thread | None = None
        self._in_flight = 0
        self._flush_asked = False
        self._closed = False
        self._last_warn = float("-inf")
        self._unsaid = 0
        self.counts: dict[str, int] = {
            "recorded": 0,
            "sent_batches": 0,
            "sent_entries": 0,
            "failed_batches": 0,
            "dropped_failed": 0,
            "dropped_full": 0,
            "dropped_not_there": 0,
            "dropped_closed": 0,
            "written": 0,
            "trimmed": 0,
            "not_written": 0,
            "dropped_past_cap": 0,
        }
        _LIVE.add(self)

    # -- recording (the agent's thread: cheap, never raises) ----------------
    def model_turn(
        self,
        response: Any,
        *,
        place: Mapping[str, Any] | None = None,
        model: str | None = None,
        duration_ms: Any = None,
        spend: Iterable[Mapping[str, Any]] | None = None,
        audit: Callable[[str, Any], Any] | None = None,
    ) -> None:
        """One model turn: model_call, the rail's reasoning and native work
        when it reports them, think, then one tool_call per call."""
        try:
            at = _now()
            calls = list(getattr(response, "tool_calls", None) or [])
            entries = [self._model_call(at, response, model, duration_ms, spend)]
            metadata = getattr(response, "raw_metadata", None)
            metadata = metadata if isinstance(metadata, dict) else {}
            reasoning = metadata.get("reasoning_text")
            if isinstance(reasoning, str) and reasoning.strip():
                entries.append(self._think(at, REASONING_MARK + reasoning.strip()))
            for item in metadata.get("trail") or ():
                entry = self._trail_entry(at, item)
                if entry is not None:
                    entries.append(entry)
            text = str(getattr(response, "text", "") or "")
            if text.strip():
                entries.append(self._think(at, text.strip()))
            for call in calls:
                arguments = getattr(call, "arguments", None)
                if audit is not None:
                    try:
                        arguments = audit(str(getattr(call, "name", "")), arguments)
                    except Exception:  # noqa: BLE001 - an audit view never costs the turn
                        arguments = {}
                entries.append(self._tool_call(at, getattr(call, "name", ""), arguments))
            self._put(_turn_place(place or {}, calls), entries)
        except Exception:  # noqa: BLE001 - the stream never fails the run
            self._internal_error()

    def tool_call(
        self, tool: Any, arguments: Any, *, place: Mapping[str, Any] | None = None
    ) -> None:
        try:
            self._put(place or {}, [self._tool_call(_now(), tool, arguments)])
        except Exception:  # noqa: BLE001
            self._internal_error()

    def tool_result(
        self,
        tool: Any,
        result: Any,
        *,
        place: Mapping[str, Any] | None = None,
        is_error: bool = False,
        duration_ms: Any = None,
    ) -> None:
        try:
            entry: dict[str, Any] = {
                "at": _now(),
                "kind": "tool_result",
                "tool": str(tool or "?"),
                "result": prepare_result(result, self.text_cap, self._secrets),
                "is_error": bool(is_error),
            }
            if duration_ms is not None:
                entry["duration_ms"] = _int(duration_ms)
            self._put(place or {}, [entry])
        except Exception:  # noqa: BLE001
            self._internal_error()

    def note(self, text: Any, *, place: Mapping[str, Any] | None = None) -> None:
        try:
            self._put(place or {}, [{
                "at": _now(), "kind": "note",
                "text": prepare_text(text or "", self.text_cap, self._secrets),
            }])
        except Exception:  # noqa: BLE001
            self._internal_error()

    def flush(self, run_id: str | None = None) -> None:
        """Send what is queued now, without waiting for it. With ``run_id``,
        the run is over: what it recorded before naming a want is let go."""
        try:
            with self._lock:
                if run_id:
                    self._unplaced.pop(str(run_id), None)
                if self._queue or self._in_flight:
                    self._flush_asked = True
                    self._wake.notify_all()
        except Exception:  # noqa: BLE001
            self._internal_error()

    def drain(self, timeout: float = EXIT_SECONDS) -> bool:
        """Wait, at most ``timeout`` seconds, for everything queued to be sent
        (or dropped). True when nothing is left. Only for the end of a
        process and for tests: a run never calls it."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._lock:
            self._flush_asked = True
            self._wake.notify_all()
            while self._queue or self._in_flight:
                if not self._sender_alive():
                    if not self._queue or not self._start_sender():
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._wake.wait(remaining)
            return not self._queue and not self._in_flight

    def close(self, timeout: float = EXIT_SECONDS) -> None:
        """Send what is left (at most ``timeout`` seconds), then stop."""
        try:
            self.drain(timeout)
        finally:
            with self._lock:
                self._closed = True
                left = len(self._queue)
                self._queue.clear()
                self._unplaced.clear()
                self.counts["dropped_closed"] += left
                self._wake.notify_all()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self.counts, queued=len(self._queue))

    # -- entries -------------------------------------------------------------
    def _think(self, at: str, text: str) -> dict[str, Any]:
        return {"at": at, "kind": "think",
                "text": prepare_text(text, self.text_cap, self._secrets)}

    def _tool_call(self, at: str, tool: Any, arguments: Any) -> dict[str, Any]:
        return {"at": at, "kind": "tool_call", "tool": str(tool or "?"),
                "arguments": prepare_arguments(arguments if arguments is not None else {},
                                               self.text_cap, self._secrets)}

    def _trail_entry(self, at: str, item: Any) -> dict[str, Any] | None:
        """One step of the work a CLI rail did inside its own call."""
        if not isinstance(item, Mapping):
            return None
        kind = item.get("kind")
        if kind == "think":
            text = str(item.get("text") or "").strip()
            if not text:
                return None
            return self._think(at, (REASONING_MARK if item.get("reasoning") else "") + text)
        if kind == "tool_call":
            return self._tool_call(at, item.get("tool"), item.get("arguments"))
        if kind == "tool_result":
            entry = {"at": at, "kind": "tool_result", "tool": str(item.get("tool") or "?"),
                     "result": prepare_result(item.get("result"), self.text_cap, self._secrets),
                     "is_error": bool(item.get("is_error"))}
            if item.get("duration_ms") is not None:
                entry["duration_ms"] = _int(item.get("duration_ms"))
            return entry
        if kind == "note":
            text = str(item.get("text") or "").strip()
            return {"at": at, "kind": "note",
                    "text": prepare_text(text, self.text_cap, self._secrets)} if text else None
        return None

    def _model_call(
        self, at: str, response: Any, model: str | None, duration_ms: Any,
        spend: Iterable[Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        """What the call was: the model that answered, its tokens, its cost in
        cents when the price table knows the model (null when it does not),
        and how long it took."""
        from toll_harness.models.pricing import price_usd  # the adapters import this module

        rows = [row for row in (spend or ()) if isinstance(row, Mapping)]
        metadata = getattr(response, "raw_metadata", None)
        reported = metadata.get("model") if isinstance(metadata, dict) else None
        named = next((str(row.get("model")) for row in rows if row.get("model")), "")
        ran = named or (reported if isinstance(reported, str) and reported else "") or model or "?"
        cost: float | None = None
        if rows:
            tokens_in = sum(_int(row.get("fresh_in")) + _int(row.get("cache_read"))
                            + _int(row.get("cache_write")) for row in rows)
            tokens_out = sum(_int(row.get("out")) for row in rows)
            dollars = [price_usd(row.get("model") or ran, fresh_in=_int(row.get("fresh_in")),
                                 cache_read=_int(row.get("cache_read")),
                                 cache_write=_int(row.get("cache_write")),
                                 out=_int(row.get("out"))) for row in rows]
            if all(value is not None for value in dollars):
                cost = round(sum(dollars) * 100, 4)  # type: ignore[arg-type]
        else:
            usage = getattr(response, "usage", None)
            tokens_in = _int(getattr(usage, "input_tokens", 0))
            tokens_out = _int(getattr(usage, "output_tokens", 0))
        return {"at": at, "kind": "model_call", "model": str(ran)[:200],
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cost_cents": cost, "duration_ms": _int(duration_ms)}

    # -- the queue -------------------------------------------------------------
    def _put(self, where: Mapping[str, Any], entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        run_id = str(where.get("run_id") or "")
        target = str(where.get("target_id") or "")
        dropped = 0
        with self._lock:
            if self._closed:
                self.counts["dropped_closed"] += len(entries)
                return
            self.counts["recorded"] += len(entries)
            if not target:
                # A run that has not named its want yet: held, so the turn
                # that names it carries what came before. Never sent without one.
                if run_id:
                    held = self._unplaced.get(run_id)
                    if held is None:
                        held = self._unplaced[run_id] = deque(maxlen=UNPLACED_PER_RUN)
                        while len(self._unplaced) > UNPLACED_RUNS:
                            self._unplaced.popitem(last=False)
                    held.extend(entries)
                return
            deal = str(where.get("deal_id") or "")
            held = self._unplaced.pop(run_id, None) if run_id else None
            if held:
                early = (target, deal, "", run_id)
                for entry in held:
                    dropped += self._enqueue(early, entry)
            key = (target, deal, str(where.get("step_id") or ""), run_id)
            for entry in entries:
                dropped += self._enqueue(key, entry)
            self._start_sender()
            self._wake.notify_all()
        if dropped:
            self._warn(f"the queue was full; {dropped} oldest entr{'y' if dropped == 1 else 'ies'}"
                       " dropped")

    def _enqueue(self, key: tuple[str, str, str, str], entry: dict[str, Any]) -> int:
        dropped = 0
        while len(self._queue) >= self.queue_cap:
            self._queue.popleft()
            dropped += 1
        self.counts["dropped_full"] += dropped
        self._queue.append((key, entry, time.monotonic()))
        return dropped

    # -- the sender (its own daemon thread) --------------------------------------
    def _sender_alive(self) -> bool:
        return self._sender is not None and self._sender.is_alive()

    def _start_sender(self) -> bool:
        """Under the lock. False when no thread could be started."""
        if self._sender_alive():
            return True
        try:
            thread = threading.Thread(target=self._send_loop, name="toll-harness-thinking",
                                      daemon=True)
            thread.start()
        except Exception:  # noqa: BLE001 - no thread, no stream; the run goes on
            return False
        self._sender = thread
        return True

    def _next_batch(self) -> tuple[tuple[str, str, str, str], list[dict[str, Any]]] | None:
        """Under the lock: the next batch due, waiting for one; None when the
        sender should stop (closed, or idle for a while)."""
        idle_since = time.monotonic()
        while True:
            if self._queue:
                waited = time.monotonic() - self._queue[0][2]
                if (len(self._queue) >= self.batch_size or self._flush_asked or self._closed
                        or waited >= self.flush_seconds):
                    key = self._queue[0][0]
                    entries: list[dict[str, Any]] = []
                    while (self._queue and self._queue[0][0] == key
                           and len(entries) < self.batch_size):
                        entries.append(self._queue.popleft()[1])
                    if not self._queue:
                        self._flush_asked = False
                    return key, entries
                self._wake.wait(max(0.001, self.flush_seconds - waited))
                idle_since = time.monotonic()
                continue
            self._flush_asked = False
            idle = time.monotonic() - idle_since
            if self._closed or idle >= self.idle_seconds:
                return None
            self._wake.wait(self.idle_seconds - idle)

    def _send_loop(self) -> None:
        while True:
            with self._lock:
                batch = self._next_batch()
                if batch is None:
                    if self._sender is threading.current_thread():
                        self._sender = None
                    self._wake.notify_all()
                    return
                self._in_flight += 1
            try:
                self._send(*batch)
            except Exception:  # noqa: BLE001 - belt and braces: the loop never dies on a batch
                self._internal_error()
            finally:
                with self._lock:
                    self._in_flight -= 1
                    self._wake.notify_all()

    def _send(self, key: tuple[str, str, str, str], entries: list[dict[str, Any]]) -> None:
        target, deal, step, run_id = key
        until = self._not_there.get(target)
        if until is not None and time.monotonic() < until:
            with self._lock:
                self.counts["dropped_not_there"] += len(entries)
            return
        payload: dict[str, Any] = {"run_id": run_id, "entries": entries}
        if deal:
            payload["deal_id"] = deal
        if step:
            payload["step_id"] = step
        try:
            answer = self._transport(target, payload)
        except Exception as error:  # noqa: BLE001 - a failed batch is dropped and counted
            status = getattr(error, "status", None)
            if status == 404:
                self._not_there[target] = time.monotonic() + self.not_there_seconds
                with self._lock:
                    self.counts["dropped_not_there"] += len(entries)
                self.log.debug(
                    "thinking stream: no door for want %s yet (404); not sending for it "
                    "for %d seconds", target, int(self.not_there_seconds))
                return
            with self._lock:
                self.counts["failed_batches"] += 1
                self.counts["dropped_failed"] += len(entries)
            self._warn(f"{type(error).__name__}"
                       + (f" {status}" if isinstance(status, int) and status else ""))
            return
        self._not_there.pop(target, None)
        refused = isinstance(answer, dict) and answer.get("ok") is False
        with self._lock:
            if refused:
                self.counts["failed_batches"] += 1
                self.counts["dropped_failed"] += len(entries)
            else:
                self.counts["sent_batches"] += 1
                self.counts["sent_entries"] += len(entries)
            if isinstance(answer, dict):
                for name in ("written", "trimmed", "not_written", "dropped_past_cap"):
                    value = answer.get(name)
                    if isinstance(value, list):
                        value = len(value)
                    if isinstance(value, int) and not isinstance(value, bool):
                        self.counts[name] += max(0, value)
        if refused:
            self._warn(f"the door said no ({answer.get('error') or 'ok: false'})")

    # -- saying so, quietly ------------------------------------------------------
    def _warn(self, why: str) -> None:
        """One local line, at most one a minute, with what it has not said yet."""
        now = time.monotonic()
        with self._lock:
            self._unsaid += 1
            if now - self._last_warn < self.warn_every_seconds:
                return
            self._last_warn = now
            unsaid, self._unsaid = self._unsaid, 0
            counts = dict(self.counts)
        lost = counts["dropped_failed"] + counts["dropped_full"]
        self.log.warning(
            "thinking stream: %s; %d problem(s) since the last line, %d entries not sent in "
            "all; the run goes on", why, unsaid, lost)

    def _internal_error(self) -> None:
        try:
            self._warn("an entry could not be recorded")
        except Exception:  # noqa: BLE001
            pass


@atexit.register
def _send_what_is_left() -> None:
    """The process is ending: every live stream gets a share of two seconds."""
    deadline = time.monotonic() + EXIT_SECONDS
    for stream in list(_LIVE):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            stream.drain(remaining)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 5. The hooks the three roads call (the runtime, the step ask, the draft loop)
# ---------------------------------------------------------------------------
def stream_of(holder: Any) -> ThinkingStream | None:
    """The stream a model adapter carries (``model.thinking``), or None when
    there is none or the switch is off."""
    stream = getattr(holder, "thinking", None)
    if isinstance(stream, ThinkingStream) and enabled():
        return stream
    return None


def _swap_meter(model: Any, meter: Any) -> Any:
    try:
        before = getattr(model, "_spend_meter", None)
        model._spend_meter = meter
        return before
    except Exception:  # noqa: BLE001 - an adapter that takes no attribute is unmetered
        return None


def invoke(
    model: Any,
    *,
    system: str,
    messages: Any,
    tools: Any,
    place: Mapping[str, Any] | None = None,
    audit: Callable[[str, Any], Any] | None = None,
) -> Any:
    """``model.invoke(...)``, and the turn recorded on the stream it carries.

    With no stream (or the switch off) this is exactly the plain call. The
    call's exception, if any, is the caller's, unchanged."""
    stream = stream_of(model)
    if stream is None:
        return model.invoke(system=system, messages=messages, tools=tools)
    where = place if place is not None else here()
    meter: list[dict[str, Any]] = []
    before = _swap_meter(model, meter)
    started = time.monotonic()
    try:
        response = model.invoke(system=system, messages=messages, tools=tools)
    except Exception as error:
        elapsed = int((time.monotonic() - started) * 1000)
        stream.note(f"model call failed after {elapsed} ms: {type(error).__name__}", place=where)
        raise
    finally:
        _swap_meter(model, before)
    elapsed = int((time.monotonic() - started) * 1000)
    model_id = None
    with contextlib.suppress(Exception):
        model_id = str(model.model_id)
    stream.model_turn(response, place=where, model=model_id, duration_ms=elapsed,
                      spend=meter, audit=audit)
    return response


def tool_call(holder: Any, tool: Any, arguments: Any, *,
              place: Mapping[str, Any] | None = None) -> None:
    stream = stream_of(holder)
    if stream is not None:
        stream.tool_call(tool, arguments, place=place if place is not None else here())


def tool_result(
    holder: Any, tool: Any, result: Any, *, is_error: bool = False, duration_ms: Any = None,
    place: Mapping[str, Any] | None = None,
) -> None:
    stream = stream_of(holder)
    if stream is not None:
        stream.tool_result(tool, result, is_error=is_error, duration_ms=duration_ms,
                           place=place if place is not None else here())


def note(holder: Any, text: Any, *, place: Mapping[str, Any] | None = None) -> None:
    stream = stream_of(holder)
    if stream is not None:
        stream.note(text, place=place if place is not None else here())


def flush(holder: Any, run_id: str | None = None) -> None:
    """The end of a run or a step: send now, do not wait."""
    stream = getattr(holder, "thinking", None)
    if isinstance(stream, ThinkingStream):
        stream.flush(run_id)
