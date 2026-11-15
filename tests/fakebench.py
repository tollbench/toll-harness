"""A small fake Toll Bench that speaks protocol v1's doors, for the connector's tests."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "fake-agent-token-0000"


class FakeBench:
    def __init__(self):
        self.lock = threading.Condition()
        self.items: list[tuple[int, dict]] = []  # (sequence, item)
        self.sequence = 0
        self.seen: list[str] = []
        self.answers: list[tuple[str, dict]] = []
        self.log_lines: list[dict] = []
        self.requests: list[tuple[float, str, str, dict]] = []  # (at, method, path, query)
        self.refuse: dict[str, dict] = {}  # item id -> {ok: false, problems}
        self.down_items = 0  # answer the next N items calls with 503
        self.down_answers = 0
        self.drop_items = 0  # cut the next N items calls without any answer (a restart)
        self.busy_answers = 0  # answer the next N answers with 429 and Retry-After
        self.version_door = "harness"  # or "connector": the older name only
        # The bench's own protection (Steven, Nov 11): it counts {"failed": ...} answers per
        # item, sends the item again until three, then parks it and stops sending it.
        self.failures: dict[str, int] = {}
        self.parked: list[str] = []
        self.settings = {"looking_for": "all", "focus": "", "heat_roster": False,
                         "heat_packs_only": False, "logging": False}
        self.version = {"current": "1.0.0", "minimum": "1.0.0",
                        "install": "pip install -U toll-harness==1.0.0; touch /tmp/never"}
        self.reachable = True
        self.acks = 0
        self.registered: list[dict] = []
        # The materials door: path -> {"body": bytes, "status": 200, "headers": {}, "length":
        # True (send Content-Length), "claims": n (a Content-Length that says n), "trickle": s
        # (16 bytes every s seconds)}. Every GET's Authorization header is written down.
        self.files: dict[str, dict] = {}
        self.auth: list[tuple[str, str | None]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> FakeBench:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def add(self, item: dict) -> None:
        with self.lock:
            self.sequence += 1
            self.items.append((self.sequence, item))
            self.lock.notify_all()

    def count_failure(self, item_id: str) -> dict:
        with self.lock:
            tries = self.failures[item_id] = self.failures.get(item_id, 0) + 1
            if tries >= 3:
                self.parked.append(item_id)
                return {"ok": True, "next": "Parked after three failures; shown on your card."}
            original = next(item for _, item in self.items if item["id"] == item_id)
            self.sequence += 1
            self.items.append((self.sequence, original))  # sent again
            self.lock.notify_all()
        return {"ok": True, "next": f"Failure {tries} of 3 counted; the item comes again."}

    def me(self) -> dict:
        return {
            "agent": {"id": "A-0999", "handle": "TestEcho", "name": "TestEcho"},
            "owner": {"name": "Test Owner Co"}, "email": "testecho@agents.example",
            "terms": {"version": "2026-11-11", "accepted": True, "how": "POST /api/bench/me/terms"},
            "settings": dict(self.settings),
            "connector": {"current": self.version["current"],
                          "minimum": self.version["minimum"]},
            "reachability_test": ({"reachable": True} if self.reachable
                                  else {"reachable": False}),
        }

    def _handler(self):
        bench = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, status: int, body: dict, headers: dict | None = None) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authed(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._send(401, {"error": "invalid_token", "message": "bad token"})
                    return False
                return True

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def _file(self, spec: dict) -> None:
                body = spec.get("body", b"")
                self.send_response(spec.get("status", 200))
                for key, value in (spec.get("headers") or {}).items():
                    self.send_header(key, value)
                if spec.get("length", True):
                    self.send_header("Content-Length", str(spec.get("claims", len(body))))
                self.end_headers()
                try:
                    if spec.get("trickle"):
                        for start in range(0, len(body), 16):
                            self.wfile.write(body[start:start + 16])
                            self.wfile.flush()
                            time.sleep(spec["trickle"])
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the harness gave up on it, as it should

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                bench.requests.append((time.monotonic(), "GET", parsed.path, query))
                bench.auth.append((self.path, self.headers.get("Authorization")))
                if parsed.path == f"/api/bench/{bench.version_door}/version":
                    return self._send(200, bench.version)
                if parsed.path.endswith("/version"):
                    return self._send(404, {"error": "no_door"})
                if parsed.path == "/api/bench/protocol":
                    return self._send(200, {"rules_version_hash": "rules-abc"})
                if not self._authed():
                    return None
                if parsed.path in bench.files:
                    return self._file(bench.files[parsed.path])
                if parsed.path == "/api/bench/me":
                    return self._send(200, bench.me())
                if parsed.path == "/api/bench/me/items":
                    if bench.drop_items > 0:
                        bench.drop_items -= 1
                        self.close_connection = True
                        self.connection.shutdown(2)
                        return None
                    if bench.down_items > 0:
                        bench.down_items -= 1
                        return self._send(503, {"error": "down", "message": "down"})
                    after = int(query.get("after") or 0)
                    wait = min(int(query.get("wait") or 0), 55)
                    deadline = time.monotonic() + wait
                    with bench.lock:
                        while True:
                            fresh = [item for seq, item in bench.items if seq > after]
                            left = deadline - time.monotonic()
                            if fresh or left <= 0:
                                break
                            bench.lock.wait(left)
                        cursor = str(bench.sequence if fresh else after or "")
                    me = bench.me()
                    return self._send(200, {"items": fresh, "cursor": cursor,
                                            "me": {"settings": me["settings"],
                                                   "connector": me["connector"]},
                                            "waiting": len(fresh)})
                return self._send(404, {"error": "no_door"})

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                bench.requests.append((time.monotonic(), "POST", parsed.path, {}))
                body = self._body()
                if parsed.path == "/api/bench/agents/register/validate":
                    return self._send(200, {"ok": True, "problems": []})
                if parsed.path == "/api/bench/agents/register":
                    bench.registered.append(body)
                    return self._send(201, {"maker_id": "m-1", "rest_token": TOKEN})
                if not self._authed():
                    return None
                parts = parsed.path.split("/")
                if parsed.path == "/api/bench/me/pings/ack":
                    bench.acks += 1
                    bench.reachable = True
                    return self._send(200, bench.me())
                if parsed.path == "/api/bench/me/log":
                    bench.log_lines.extend(body.get("lines") or [])
                    return self._send(200, {"ok": True})
                if parsed.path.startswith("/api/bench/me/items/") and len(parts) == 7:
                    item_id, action = parts[5], parts[6]
                    if action == "seen":
                        bench.seen.append(item_id)
                        return self._send(200, {"ok": True})
                    if action == "answer":
                        if bench.down_answers > 0:
                            bench.down_answers -= 1
                            return self._send(502, {"error": "down"})
                        if bench.busy_answers > 0:
                            bench.busy_answers -= 1
                            return self._send(429, {"error": "slow_down"},
                                              {"Retry-After": "7"})
                        bench.answers.append((item_id, body))
                        if "failed" in body:
                            return self._send(200, bench.count_failure(item_id))
                        if item_id in bench.refuse:
                            return self._send(200, bench.refuse[item_id])
                        return self._send(200, {"ok": True, "next": "Thanks."})
                return self._send(404, {"error": "no_door"})

        return Handler


def item(item_id: str, kind: str = "message", **extra) -> dict:
    value = {
        "id": item_id, "kind": kind, "created_at": "2026-11-11T12:00:00Z", "due_at": None,
        "about": {"thread_id": 5}, "do": "Reply to the owner's line in one sentence.",
        "context": {"line": "Please post this now."}, "refs": [],
        "answer": {"needed": True, "shape": {"reply": "string"}, "example": {"reply": "Done."}},
    }
    value.update(extra)
    return value
