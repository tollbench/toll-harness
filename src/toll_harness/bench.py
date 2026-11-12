"""The bench's doors, as protocol v1 names them (one door in, one door out).

Kept from 0.76: one request function, one User-Agent naming the installed build, a refusal
that carries the body the bench sent, never a token in a log line, never a redirect followed
with the bearer.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from toll_harness import __version__

USER_AGENT = f"toll-harness/{__version__}"
LONGEST_WAIT = 55  # seconds a GET /me/items may hold (protocol v1)
_ITEM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


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
            raw = error.read() or b""
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {}
            detail = detail if isinstance(detail, dict) else {}
            word = detail.get("code") or detail.get("error") or "http_error"
            sentence = detail.get("message") or detail.get("detail") or error.reason
            raise BenchError(error.code, str(word), str(sentence), detail,
                             _retry_after(error.headers, detail)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
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

    def answer(self, item_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        return self.call("POST", item_path(item_id, "answer"), payload=answer)

    def log(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        return self.call("POST", "/api/bench/me/log", payload={"lines": lines}, timeout=10)

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
