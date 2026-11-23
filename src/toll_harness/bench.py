"""The bench's doors, as protocol v1 names them (one door in, one door out).

Kept from 0.76: one request function, one User-Agent naming the installed build, a refusal
that carries the body the bench sent, never a token in a log line, never a redirect followed
with the bearer.
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from toll_harness import __version__

USER_AGENT = f"toll-harness/{__version__}"
LONGEST_WAIT = 55  # seconds a GET /me/items may hold (protocol v1)
_ITEM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# A path on the bench: /api/bench/ and then parts of plain characters only. No scheme, no host,
# no `//`, no query, no percent sign, no backslash, so joined to the bench's own address it can
# only ever name the bench.
_BENCH_PATH = re.compile(r"^/api/bench(/[A-Za-z0-9._~-]+)+$")


def bench_path(value: Any) -> str | None:
    """`value` when it is a path on the bench, else None: the agent's key goes with nothing else."""
    if not isinstance(value, str) or len(value) > 512 or not _BENCH_PATH.fullmatch(value):
        return None
    if any(part in (".", "..") for part in value.split("/")):
        return None
    return value


class BenchError(RuntimeError):
    """A refusal, or no answer (status 0). `body` is what the bench sent, parsed."""

    def __init__(self, status: int, code: str, message: str, body: Any = None,
                 retry_after: float | None = None):
        super().__init__(f"bench answered {status} {code}: {message}" if status
                         else f"bench did not answer: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body if isinstance(body, dict) else {}
        self.retry_after = retry_after  # seconds the bench asked us to wait (a 429)

    @property
    def unreachable(self) -> bool:
        """Worth trying again later: no answer, a 5xx, or too many requests."""
        return self.status == 0 or self.status >= 500 or self.status in (408, 429)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirects())


def _retry_after(headers: Any, body: dict[str, Any]) -> float | None:
    for value in ((headers or {}).get("Retry-After"), body.get("retry_after_seconds"),
                  body.get("retry_after")):
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        return min(max(seconds, 0.0), 3600.0)
    return None


def item_path(item_id: Any, action: str) -> str:
    text = str(item_id or "")
    if not _ITEM_ID.fullmatch(text):
        raise ValueError("an item id is letters, numbers and . _ : - only")
    return f"/api/bench/me/items/{urllib.parse.quote(text, safe='')}/{action}"


class Bench:
    def __init__(self, url: str, token: str | None = None, timeout: float = 30):
        self.url = url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def call(self, method: str, path: str, *, payload: Any = None, query: dict | None = None,
             auth: bool = True, timeout: float | None = None,
             idempotency_key: str | None = None) -> dict[str, Any]:
        if auth and not self._token:
            raise BenchError(401, "missing_agent_token", "this agent has no token yet")
        url = self.url + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with _OPENER.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                raw = error.read() or b""
            except (OSError, http.client.HTTPException):
                raw = b""
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {}
            detail = detail if isinstance(detail, dict) else {}
            word = detail.get("code") or detail.get("error") or "http_error"
            sentence = detail.get("message") or detail.get("detail") or error.reason
            raise BenchError(error.code, str(word), str(sentence), detail,
                             _retry_after(error.headers, detail)) from None
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as error:
            # HTTPException: a reply cut off partway (IncompleteRead) or one that is not
            # HTTP at all. It is not an OSError, so until 1.0.2 it went past this and
            # stopped the whole watch. It is a dropped line like any other.
            reason = getattr(error, "reason", None) or type(error).__name__
            raise BenchError(0, "connection_error", str(reason)) from None
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            raise BenchError(0, "not_json", "the bench answered something that is not JSON") \
                from None
        return data if isinstance(data, dict) else {"value": data}

    # -- protocol v1 ---------------------------------------------------------------
    def me(self) -> dict[str, Any]:
        return self.call("GET", "/api/bench/me")

    def items(self, after: str | None, wait: int) -> dict[str, Any]:
        wait = min(max(int(wait), 0), LONGEST_WAIT)
        query: dict[str, Any] = {"wait": wait}
        if after:
            query["after"] = after
        # The call is held up to `wait` seconds, so the socket waits longer than that.
        return self.call("GET", "/api/bench/me/items", query=query, timeout=wait + 20)

    def seen(self, item_id: str) -> dict[str, Any]:
        return self.call("POST", item_path(item_id, "seen"), payload={})

    def answer(self, item_id: str, answer: dict[str, Any], *, key: str | None = None,
               timeout: float | None = None) -> dict[str, Any]:
        """POST one answer. `key` rides as Idempotency-Key: the bench takes one answer per key,
        so a resend after a wait that ran out is never a second hand-in."""
        return self.call("POST", item_path(item_id, "answer"), payload=answer,
                         idempotency_key=key, timeout=timeout)

    def log(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        return self.call("POST", "/api/bench/me/log", payload={"lines": lines}, timeout=10)

    def download(self, path: str, write: Callable[[bytes], Any], *, most: int, seconds: float,
                 timeout: float = 30, chunk: int = 64 * 1024) -> int:
        """Stream the bytes at a bench path to `write`; the count. The key goes only with a path
        on this bench (bench_path), and a redirect is never followed. Bytes are counted as they
        arrive: past `most` it stops with code "too_large" (body {"length": n} when the bench
        said a Content-Length over it), past `seconds` with "too_slow"."""
        clean = bench_path(path)
        url = self.url + clean if clean else ""
        if (not clean or urllib.parse.urlsplit(url).netloc
                != urllib.parse.urlsplit(self.url).netloc):
            raise BenchError(0, "not_a_bench_path", "only a path on the bench is fetched")
        if not self._token:
            raise BenchError(401, "missing_agent_token", "this agent has no token yet")
        headers = {"Accept": "*/*", "User-Agent": USER_AGENT,
                   "Authorization": f"Bearer {self._token}"}
        request = urllib.request.Request(url, headers=headers, method="GET")
        deadline = time.monotonic() + seconds
        count = 0
        try:
            with _OPENER.open(request, timeout=max(0.1, min(timeout, seconds))) as response:
                length = str(response.headers.get("Content-Length") or "").strip()
                if length.isdigit() and int(length) > most:
                    raise BenchError(0, "too_large", "over the limit", {"length": int(length)})
                while True:
                    if time.monotonic() > deadline:
                        raise BenchError(0, "too_slow", "past the time for one file")
                    block = response.read1(chunk)
                    if not block:
                        if length.isdigit() and count < int(length):
                            # The line closed before the bench's own Content-Length: a short
                            # file is never taken as the whole one.
                            raise BenchError(0, "connection_error", "the file was cut off partway")
                        return count
                    count += len(block)
                    if count > most:
                        raise BenchError(0, "too_large", "over the limit")
                    write(block)
        except urllib.error.HTTPError as error:  # a refusal, or a redirect that was not followed
            error.close()
            raise BenchError(error.code, "http_error", str(error.reason)) from None
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as error:
            # HTTPException: a reply cut off partway (IncompleteRead) or one that is not
            # HTTP at all. It is not an OSError, so until 1.0.2 it went past this and
            # stopped the whole watch. It is a dropped line like any other.
            reason = getattr(error, "reason", None) or type(error).__name__
            raise BenchError(0, "connection_error", str(reason)) from None

    def version(self) -> dict[str, Any]:
        """The harness versions: /harness/version, else the older name /connector/version."""
        try:
            return self.call("GET", "/api/bench/harness/version", auth=False, timeout=10)
        except BenchError as error:
            if error.status != 404:
                raise
        return self.call("GET", "/api/bench/connector/version", auth=False, timeout=10)

    # -- registration and the reachability handshake (kept from 0.76) ---------------
    def protocol(self) -> dict[str, Any]:
        return self.call("GET", "/api/bench/protocol", auth=False)

    def validate_registration(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("POST", "/api/bench/agents/register/validate", payload=payload,
                         auth=False)

    def register(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        return self.call("POST", "/api/bench/agents/register", payload=payload, auth=False,
                         idempotency_key=key)

    def ack_ping(self) -> dict[str, Any]:
        return self.call("POST", "/api/bench/me/pings/ack", payload={})

    def with_token(self, token: str) -> Bench:
        return Bench(self.url, token=token, timeout=self.timeout)
