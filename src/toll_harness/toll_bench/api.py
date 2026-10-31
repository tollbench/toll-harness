"""The Book of Houses bench API client (0.74.0: moved here from
`toll_harness.email.book_of_houses`, which still exports every name).

Every door the harness calls goes through `BookOfHousesApiClient._request`,
which writes one line per answer on the `toll_harness.door` logger.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

GOAL_RUN_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
PACK_INPUT_MAX_BYTES = 10 * 1024 * 1024
PAID_PACK_FILE_MAX_BYTES = 50 * 1024 * 1024


class _RefusePackInputRedirects(urllib.request.HTTPRedirectHandler):
    """Never carry an agent bearer off the exact approved file door."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _goal_check_path(run_id: str, action: str = "") -> str:
    if not isinstance(run_id, str) or GOAL_RUN_ID.fullmatch(run_id) is None:
        raise ValueError("Goal check run_id must be a UUID")
    return f"/api/bench/me/goal-checks/{run_id}{action}"


def _pack_input_path(target_id: str, receipt_id: str, action: str = "") -> str:
    if (not isinstance(target_id, str) or GOAL_RUN_ID.fullmatch(target_id) is None
            or not isinstance(receipt_id, str) or GOAL_RUN_ID.fullmatch(receipt_id) is None):
        raise ValueError("Pack input target_id and receipt_id must be UUIDs")
    return f"/api/bench/targets/{target_id}/pack-inputs/{receipt_id}{action}"


def _pack_drafts_path(source_goal_id: str) -> str:
    if not isinstance(source_goal_id, str) or GOAL_RUN_ID.fullmatch(source_goal_id) is None:
        raise ValueError("Pack draft source_goal_id must be a UUID")
    return f"/api/bench/targets/{source_goal_id}/pack-drafts"


def _paid_pack_path(order_id: str, action: str = "", receipt_id: str | None = None) -> str:
    if not isinstance(order_id, str) or GOAL_RUN_ID.fullmatch(order_id) is None:
        raise ValueError("Paid pack order_id must be a UUID")
    path = f"/api/bench/paid-packs/{order_id}"
    if receipt_id is not None:
        if not isinstance(receipt_id, str) or GOAL_RUN_ID.fullmatch(receipt_id) is None:
            raise ValueError("Paid pack receipt_id must be a UUID")
        path += f"/receipts/{receipt_id}"
    return path + action



def _multipart_body(
    fields: dict[str, str],
    *,
    filename: str,
    content: bytes,
    media_type: str = "application/octet-stream",
    file_field: str = "file",
) -> tuple[bytes, str]:
    """Encode one file plus plain fields as multipart/form-data.

    Written out by hand rather than pulled in: the harness ships with two
    dependencies and an upload is twenty lines. The boundary is random per
    call so it can never appear in the bytes it is separating.
    """
    boundary = "----tollharness" + secrets.token_hex(16)
    marker = f"--{boundary}".encode("ascii")
    safe_name = str(filename or "file").replace("\\", "_").replace('"', "_").replace("\n", "_")
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(marker)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    parts.append(marker)
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"'.encode()
    )
    parts.append(f"Content-Type: {media_type}".encode())
    parts.append(b"")
    parts.append(content)
    parts.append(f"--{boundary}--".encode("ascii"))
    parts.append(b"")
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"


class BookOfHousesApiError(RuntimeError):
    """A refusal, with the body the server actually sent.

    RULE 228 (contract 2.44): a REJ-32 refusal CARRIES THE FORM -- the 422
    body holds `plan_template`, the exact steps to file. This class used to
    keep three strings and drop the body, so the form the server attached to
    the refusal could not reach the code that had to act on it. `body` is the
    parsed JSON (empty dict when there was none) and `rej` is its rejection
    code when it published one.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        body: dict[str, Any] | None = None,
    ):
        super().__init__(f"Book of Houses API error ({status}, {code}): {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body if isinstance(body, dict) else {}
        rejection = self.body.get("rejection")
        # Three shapes carry the same code, because three doors write it: the
        # bid route publishes `rej`, the dry run nests `rejection.code`, and
        # the identity-error envelope puts it in `error`. An agent that can
        # only read one of them cannot act on the other two.
        self.rej = str(
            self.body.get("rej")
            or (rejection.get("code") if isinstance(rejection, dict) else "")
            or (code if re.fullmatch(r"REJ-\d+", str(code or "")) else "")
            or ""
        ) or None
        template = self.body.get("plan_template")
        if template is None and isinstance(rejection, dict):
            template = rejection.get("plan_template")
        self.plan_template = template if isinstance(template, list) else []


# EVERY DOOR ANSWER, ONE LINE (0.68.0). Every road -- the draft loop, the step
# ask, the runtime's tools, the market watch's own polls -- reaches the bench
# through `_request`, so the line is written there once: the door, the want /
# deal / step it is about, the status, the answer's size, a few facts read off
# it and the door's own sentence. Never the body, never a header, never a
# token.
_DOOR_LOG = logging.getLogger("toll_harness.door")
_DOOR_IDS = re.compile(r"/(targets|goals|deals|steps|proposals)/([0-9a-fA-F][0-9a-fA-F-]{7,})")
_DOOR_ID_NAMES = {"targets": "want", "goals": "want", "deals": "deal", "steps": "step",
                  "proposals": "proposal"}
DOOR_WORDS_CHARS = 200


def door_words(body: Any) -> str:
    """The door's own sentence on an answer, on one line, or ""."""
    if not isinstance(body, dict):
        return ""
    fix = body.get("next_fix") if isinstance(body.get("next_fix"), dict) else {}
    for value in (body.get("message"), body.get("closed"), fix.get("say"),
                  body.get("detail"), body.get("error")):
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            if len(text) > DOOR_WORDS_CHARS:
                text = text[: DOOR_WORDS_CHARS - 1] + "\u2026"
            return text
    return ""


def door_line(method: str, path: str, status: int, size: int, body: Any,
              kind: Any = None) -> str:
    """One door answer, as the run log reads it."""
    parts = [f"door answer {method} {path} -> {status} ({size} chars)"]
    parts.extend(f"{_DOOR_ID_NAMES[name]}={value}" for name, value in _DOOR_IDS.findall(path))
    if kind:
        parts.append(f"kind={kind}")
    if isinstance(body, dict):
        for key in ("ok", "ready", "remaining", "error", "code"):
            value = body.get(key)
            if isinstance(value, (bool, int, str)) and value != "":
                parts.append(f"{key}={value}")
        rows = body.get("problems")
        if isinstance(rows, list):
            mine = sum(1 for row in rows if isinstance(row, dict)
                       and row.get("who_fixes") == "agent")
            parts.append(f"red={mine}/{len(rows)}")
    # The door's sentence rides a refusal and a draft-door answer (its next
    # row, or why it closed). A plain read's body is the data it was asked
    # for, and that never goes to the log.
    speaks = isinstance(body, dict) and (
        status >= 400 or status == 0 or body.get("ok") is False
        or "next_fix" in body or bool(body.get("closed"))
    )
    words = door_words(body) if speaks else ""
    if words:
        parts.append(f'says "{words}"')
    return " ".join(parts)


class BookOfHousesApiClient:
    """Narrow HTTP client for public onboarding and one agent's authenticated APIs."""

    def __init__(
        self,
        *,
        base_url: str = "https://bookofhouses.com",
        token: str | None = None,
        maker_id: str | None = None,
        timeout_seconds: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.maker_id = maker_id
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        authenticated: bool = False,
        idempotency_key: str | None = None,
        query: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        return_headers: bool = False,
        refuse_redirects: bool = False,
    ) -> Any:
        if authenticated and not self._token:
            raise BookOfHousesApiError(401, "missing_agent_token", "Agent token is not configured")
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json", "User-Agent": "toll-harness/0.1"}
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw_body is not None:
            # RULE 230: the artifact route takes multipart/form-data, so the
            # body arrives already encoded and carries its own boundary.
            body = raw_body
            headers["Content-Type"] = content_type or "application/octet-stream"
            headers["Content-Length"] = str(len(raw_body))
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
            if self.maker_id:
                headers["X-Maker-Id"] = self.maker_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        # Which draft a draft-door call is about: the query on a read, the
        # body on a write.
        kind = (query or {}).get("kind") or (
            (payload or {}).get("kind") if path.endswith("/proposals/draft") else None
        )
        try:
            open_request = (urllib.request.build_opener(_RefusePackInputRedirects()).open
                            if refuse_redirects else urllib.request.urlopen)
            with open_request(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                data = json.loads(raw) if raw else {}
                status = getattr(response, "status", None) or 200
                _DOOR_LOG.info("%s", door_line(method, path, status, len(raw or b""),
                                               data, kind))
                if return_headers:
                    return data, dict(response.headers)
                return data
        except urllib.error.HTTPError as error:
            raw = error.read() or b""
            try:
                detail = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = {}
            _DOOR_LOG.info("%s", door_line(method, path, error.code, len(raw), detail, kind))
            # The act doors (acts, evidence) answer {ok, code, error} where
            # `code` is the word and `error` is the sentence (ActError.as_body);
            # the identity envelope puts the word in `error`; a bid refusal
            # publishes only `rej`. Read the word wherever it is, and never
            # let a sentence become the code (found on the outside-act walk,
            # 2026-09-05: not_allowed_yet arrived as its own sentence).
            _word = detail.get("code") if isinstance(detail, dict) else None
            raise BookOfHousesApiError(
                error.code,
                str(_word or detail.get("error") or detail.get("rej") or "http_error"),
                str(detail.get("message") or detail.get("detail")
                    or (detail.get("error") if _word else None) or error.reason),
                body=detail if isinstance(detail, dict) else {},
            ) from None
        except urllib.error.URLError as error:
            _DOOR_LOG.warning("%s", door_line(method, path, 0, 0,
                                              {"error": "connection_error"}, kind))
            raise BookOfHousesApiError(0, "connection_error", str(error.reason)) from None
        except TimeoutError:
            # A read that times out is the bench not answering, like a refused
            # connection (0.74.0): status 0, so the watch backs off and no
            # blame line rides the next ask. Found on the 0.74.0 smoke: a /me
            # read past 20 seconds escaped as a raw TimeoutError.
            _DOOR_LOG.warning("%s", door_line(method, path, 0, 0,
                                              {"error": "connection_error"}, kind))
            raise BookOfHousesApiError(0, "connection_error", "the bench did not answer in "
                                       f"{self.timeout_seconds} seconds") from None

    def _request_text(self, path: str) -> str:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Accept": "text/plain", "User-Agent": "toll-harness/0.1"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise BookOfHousesApiError(error.code, "http_error", str(error.reason)) from None
        except urllib.error.URLError as error:
            raise BookOfHousesApiError(0, "connection_error", str(error.reason)) from None

    def protocol(self) -> dict[str, Any]:
        return self._request("GET", "/api/bench/protocol")

    def skill(self) -> str:
        protocol = self.protocol()
        return self._request_text(str(protocol.get("skill") or "/static/agent-skill.md"))

    def authenticated(self, token: str, maker_id: str | None = None) -> BookOfHousesApiClient:
        return BookOfHousesApiClient(
            base_url=self.base_url,
            token=token,
            maker_id=maker_id,
            timeout_seconds=self.timeout_seconds,
        )

    def validate_registration(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/bench/agents/register/validate", payload=payload)

    def register(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/bench/agents/register",
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/bench/me", authenticated=True)

    def approach(self) -> dict[str, Any]:
        # The agent's DEFAULT approach (contract 4.1.3): {ok, approach, dials}.
        return self._request("GET", "/api/bench/me/approach", authenticated=True)

    def set_approach(self, approach: dict[str, str] | None) -> dict[str, Any]:
        # Set, replace or clear (None) the default approach; idempotent.
        return self._request(
            "POST", "/api/bench/me/approach", payload={"approach": approach}, authenticated=True
        )

    def attribution(self) -> dict[str, Any]:
        # Who runs the agent and what it runs on: {ok, attribution
        # {operator_name, operator_verified, model, affiliation, ...}}.
        return self._request("GET", "/api/bench/me/attribution", authenticated=True)

    def proposal_schema(self) -> dict[str, Any]:
        return self._request("GET", "/static/agent-proposal.schema.json")

    def capability_taxonomy(self) -> dict[str, Any]:
        # The closed twenty-key capability list (rule 110). A bid's
        # `capabilities` block (rule 226) must draw its keys from here, so this
        # is a read an agent makes BEFORE it files, not a curiosity.
        return self._request("GET", "/api/bench/capabilities", authenticated=True)

    def ack_reachability_ping(self) -> dict[str, Any]:
        return self._request("POST", "/api/bench/me/pings/ack", payload={}, authenticated=True)

    def payout_sync(self, idempotency_key: str) -> dict[str, Any]:
        # Re-read Stripe for this agent's payout account (bench, since 2.13).
        return self._request(
            "POST",
            "/api/bench/payout-account/sync",
            payload={},
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def payout_status(self) -> dict[str, Any]:
        # Payout status in plain words (bench, 2026-10-12): {ready,
        # payout_account_ready, operator_verified, still_needed, ...}.
        return self._request("GET", "/api/bench/me/payout-account", authenticated=True)

    def attention(self, *, wait: int = 0) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/bench/me/attention",
            authenticated=True,
            query={"wait": min(max(int(wait), 0), 20)},
        )

    def goal_check(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", _goal_check_path(run_id), authenticated=True)

    def claim_goal_check(self, run_id: str) -> dict[str, Any]:
        return self._request(
            "POST", _goal_check_path(run_id, "/claim"), payload={}, authenticated=True
        )

    def submit_goal_check(
        self, run_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", _goal_check_path(run_id, "/submit"), payload=payload,
            authenticated=True, idempotency_key=idempotency_key
        )

    def events(self, *, after: str | None = None, wait: int = 0) -> dict[str, Any]:
        query: dict[str, Any] = {"wait": min(max(int(wait), 0), 20)}
        if after:
            query["after"] = after
        return self._request("GET", "/api/bench/events", authenticated=True, query=query)

    def act_kinds(self) -> dict[str, Any]:
        """The act registry, kind by kind (contract 2.44): `wanted_when`,
        `declaration` (the bid-time fields) and `template` (the step)."""
        return self._request("GET", "/api/bench/acts/kinds", authenticated=True)

    def open_targets(self) -> dict[str, Any]:
        return self._request("GET", "/api/bench/targets/open", authenticated=True)

    def operator_reply(self, target_id: str, message_id: str, reply: str,
                       revise_plan: bool) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        return self._request(
            "POST", f"/api/bench/targets/{target}/operator-reply", authenticated=True,
            payload={"message_id": message_id, "reply": reply, "revise_plan": revise_plan},
        )

    def target_brief(self, target_id: str) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        # ?tools=1 (bench 2026-09-09): the brief went slim for raw agents that hold
        # the cacheable prefix; this runtime builds its own prefix from the tool
        # index and copies blocks whole out of block_templates, so it asks for
        # both inline. Older benches ignore the flag.
        return self._request(
            "GET", f"/api/bench/targets/{target}/brief?tools=1", authenticated=True
        )

    def get_pack_input(self, target_id: str, receipt_id: str) -> dict[str, Any]:
        """Read one released predecessor receipt through the successor's scoped door."""
        return self._request(
            "GET", _pack_input_path(target_id, receipt_id), authenticated=True
        )

    def get_pack_input_file(self, target_id: str, receipt_id: str) -> bytes:
        """Fetch at most 10 MiB from the same authenticated, server-checked edge."""
        path = _pack_input_path(target_id, receipt_id, "/file")
        if not self._token:
            raise BookOfHousesApiError(401, "missing_agent_token", "Agent token is not configured")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Accept": "application/octet-stream", "Authorization": f"Bearer {self._token}",
                     "User-Agent": "toll-harness/0.1"},
            method="GET",
        )
        try:
            with urllib.request.build_opener(_RefusePackInputRedirects()).open(
                request, timeout=self.timeout_seconds
            ) as response:
                data = response.read(PACK_INPUT_MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise BookOfHousesApiError(
                error.code, "pack_input_file_refused", str(error.reason)
            ) from None
        except urllib.error.URLError as error:
            raise BookOfHousesApiError(0, "connection_error", str(error.reason)) from None
        if len(data) > PACK_INPUT_MAX_BYTES:
            raise BookOfHousesApiError(
                413, "pack_input_file_too_large", "Pack input exceeds 10 MiB"
            )
        return data

    def get_pack_drafts(self, source_goal_id: str) -> dict[str, Any]:
        """Read only connected owner-reserved slots after an approved handoff."""
        return self._request("GET", _pack_drafts_path(source_goal_id),
                             authenticated=True, refuse_redirects=True)

    def suggest_pack_draft(
        self, source_goal_id: str, slot_id: str, draft: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if (not isinstance(slot_id, str) or GOAL_RUN_ID.fullmatch(slot_id) is None
                or not isinstance(idempotency_key, str)
                or GOAL_RUN_ID.fullmatch(idempotency_key) is None):
            raise ValueError("Pack draft slot_id and idempotency key must be UUIDs")
        return self._request("POST", _pack_drafts_path(source_goal_id),
                             payload={"slot_id": slot_id, "draft": draft},
                             authenticated=True, idempotency_key=idempotency_key,
                             refuse_redirects=True)

    def paid_pack_job(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", _paid_pack_path(order_id, "/job"),
                             authenticated=True, refuse_redirects=True)

    def read_paid_pack_input(
        self, order_id: str, index: int, offset: int = 0,
    ) -> dict[str, Any]:
        """Read one needed page of a named signed buyer input, not the whole book."""
        if (isinstance(index, bool) or not isinstance(index, int) or index < 0
                or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
            raise ValueError("Paid-pack input index and offset must be nonnegative integers")
        return self._request("GET", _paid_pack_path(order_id, f"/inputs/{index}"),
                             query={"offset": offset}, authenticated=True,
                             refuse_redirects=True)

    def submit_paid_pack_result(
        self, order_id: str, *, card_number: int, submit_key: str,
        title: str, note: str = "", content: bytes | None = None,
        filename: str | None = None, content_type: str | None = None,
    ) -> dict[str, Any]:
        path = _paid_pack_path(order_id, "/results")
        if not isinstance(submit_key, str) or GOAL_RUN_ID.fullmatch(submit_key) is None:
            raise ValueError("Paid pack submit_key must be a UUID")
        if isinstance(card_number, bool) or not isinstance(card_number, int) or card_number < 1:
            raise ValueError("Paid pack card_number must be a positive integer")
        fields = {"card_number": str(card_number), "submit_key": submit_key,
                  "title": title, "note": note}
        if content is None:
            return self._request("POST", path, payload={**fields, "card_number": card_number},
                                 authenticated=True, refuse_redirects=True)
        if not isinstance(content, bytes) or not 1 <= len(content) <= PAID_PACK_FILE_MAX_BYTES:
            raise ValueError("Paid pack file must contain at most 50 MiB")
        if not filename:
            raise ValueError("Paid pack file needs its filename")
        body, media_type = _multipart_body(
            fields, filename=filename, content=content,
            media_type=content_type or "application/octet-stream",
        )
        return self._request("POST", path, raw_body=body, content_type=media_type,
                             authenticated=True, refuse_redirects=True)

    def paid_pack_receipt(self, order_id: str, receipt_id: str) -> dict[str, Any]:
        return self._request("GET", _paid_pack_path(order_id, receipt_id=receipt_id),
                             authenticated=True, refuse_redirects=True)

    def paid_pack_receipt_file(self, order_id: str, receipt_id: str) -> bytes:
        path = _paid_pack_path(order_id, "/file", receipt_id=receipt_id)
        if not self._token:
            raise BookOfHousesApiError(401, "missing_agent_token", "Agent token is not configured")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Accept": "application/octet-stream", "Authorization": f"Bearer {self._token}",
                     "User-Agent": "toll-harness/0.71"}, method="GET",
        )
        try:
            with urllib.request.build_opener(_RefusePackInputRedirects()).open(
                request, timeout=self.timeout_seconds
            ) as response:
                data = response.read(PAID_PACK_FILE_MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise BookOfHousesApiError(
                error.code, "paid_pack_file_refused", str(error.reason)
            ) from None
        except urllib.error.URLError as error:
            raise BookOfHousesApiError(0, "connection_error", str(error.reason)) from None
        if len(data) > PAID_PACK_FILE_MAX_BYTES:
            raise BookOfHousesApiError(
                413, "paid_pack_file_too_large", "Paid pack file exceeds 50 MiB"
            )
        return data

    def paid_pack_accept(self, order_id: str, receipt_id: str) -> dict[str, Any]:
        return self._request("POST", _paid_pack_path(order_id, "/accept", receipt_id),
                             payload={}, authenticated=True, refuse_redirects=True)

    def paid_pack_reject(self, order_id: str, receipt_id: str, reason: str) -> dict[str, Any]:
        return self._request("POST", _paid_pack_path(order_id, "/reject", receipt_id),
                             payload={"reason": reason}, authenticated=True,
                             refuse_redirects=True)

    def paid_pack_handoff(self, order_id: str, receipt_id: str) -> dict[str, Any]:
        return self._request("POST", _paid_pack_path(order_id, "/handoff"),
                             payload={"receipt_id": receipt_id}, authenticated=True,
                             refuse_redirects=True)

    def plan_example(self, key: str) -> dict[str, Any]:
        """One worked program, in full, by key.

        The brief carries an INDEX of the bench's programs and the nearest one
        inline (0.34.0); this is the door to any of the others, and to the
        chosen one when the bench sent the index alone. A bench that does not
        publish the route answers 404 and the caller keeps what the brief gave
        it -- reading a program is never worth a failed run.
        """
        program = urllib.parse.quote(str(key), safe="")
        return self._request(
            "GET", f"/api/bench/plan-examples/{program}", authenticated=True
        )

    def validate_proposal(self, target_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call 3 of six (contract 3.0): every problem at once, files nothing.

        Same bearer and same body as filing a bid. It answers 200 whatever the
        plan says -- a plan with problems is a successful answer to this
        question -- so only an unknown or closed target is a 404. It never
        writes a row, never records a refusal and never counts against the bid
        cap, which is why the harness may call it before every filing.
        """
        target = urllib.parse.quote(str(target_id), safe="")
        return self._request(
            "POST",
            f"/api/bench/targets/{target}/proposals/validate",
            payload=payload,
            authenticated=True,
        )

    # THE DRAFT DOOR (rule 241, contract 3.11; the PLAN door's reply is
    # contract 4.0). Three calls under the same bearer and the same
    # `proposals:write` scope as filing a bid, and none of them file anything:
    # the outline in, one piece back at a time, and a read that costs no
    # round. MCP twins: put_proposal_draft, patch_proposal_draft,
    # get_proposal_draft -- named the same here so an operator reading one
    # surface recognises the other.
    def put_proposal_draft(
        self, target_id: str, outline: dict[str, Any], kind: str = "bid"
    ) -> dict[str, Any]:
        """The OUTLINE in. Replaces any draft here and starts the rounds at zero."""
        target = urllib.parse.quote(str(target_id), safe="")
        payload = dict(outline or {})
        payload["kind"] = kind
        return self._request(
            "PUT",
            f"/api/bench/targets/{target}/proposals/draft",
            payload=payload,
            authenticated=True,
        )

    def drop_proposal_draft_step(
        self, target_id: str, step: int, kind: str = "plan"
    ) -> dict[str, Any]:
        """TAKE ONE STEP OUT. `{"kind": kind, "drop": {"step": N}}` on the same
        PATCH door as a patch: the bench removes the step, renumbers what is
        left and answers with its next problem. Spends one round, exactly like
        a patch does. `step` counts from ONE, like insert's `before`.

        The door publishes this beside `reorder_call` as `drop_call`.
        """
        return self.patch_proposal_draft(
            target_id, None, kind, document={"drop": {"step": int(step)}}
        )

    def insert_proposal_draft_step(
        self, target_id: str, before: int, step: dict[str, Any], kind: str = "plan"
    ) -> dict[str, Any]:
        """PUT ONE STEP IN. `{"kind": kind, "insert": {"before": N, "step": {...}}}`
        on the same PATCH door as a patch: the bench re-expands the plan and
        slides every `only_if.step` and `repeats.of_step` at or past `before`
        up by one. `before` counts from ONE like drop, and `before` = steps + 1
        appends. Spends one round, exactly like a patch does.

        The door publishes this beside `reorder_call` and `drop_call` as
        `insert_call`, and it is the answer to `missing_who`: the who step is
        the agent's pick (rule 238, amended 2026-09-12).
        """
        return self.patch_proposal_draft(
            target_id,
            None,
            kind,
            document={"insert": {"before": int(before), "step": dict(step or {})}},
        )

    def patch_proposal_draft(
        self,
        target_id: str,
        patches: list[dict[str, Any]] | None = None,
        kind: str = "bid",
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """ONE PIECE BACK. Merge by path, re-validate, answer. Spends one
        round.

        A PLAN answers in the ONE row shape (contract 4.0): `problems` always
        a list, `next_fix` the row to start with, `form_steps` what goes in
        each step. A BID still answers the way it always has.
        """
        target = urllib.parse.quote(str(target_id), safe="")
        payload: dict[str, Any] = {"kind": kind}
        if patches is not None:
            payload["patches"] = list(patches)
        if document:
            payload.update(document)
        return self._request(
            "PATCH",
            f"/api/bench/targets/{target}/proposals/draft",
            payload=payload,
            authenticated=True,
        )

    def get_proposal_draft(self, target_id: str, kind: str = "bid") -> dict[str, Any]:
        """Read the draft back. Costs no round."""
        target = urllib.parse.quote(str(target_id), safe="")
        return self._request(
            "GET",
            f"/api/bench/targets/{target}/proposals/draft",
            authenticated=True,
            query={"kind": kind},
        )

    def submit_proposal(
        self, target_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/targets/{target}/proposals",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def finalist_answers(self, target_id: str, proposal_id: str) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        proposal = urllib.parse.quote(proposal_id, safe="")
        return self._request(
            "GET",
            f"/api/bench/targets/{target}/proposals/{proposal}/answers",
            authenticated=True,
        )

    def submit_informed_plan(
        self,
        target_id: str,
        proposal_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        proposal = urllib.parse.quote(proposal_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/targets/{target}/proposals/{proposal}/plan",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def withdraw_proposal(
        self,
        proposal_id: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """THE PUBLIC EXIT, and it is an idempotent write like every other.

        WHAT FORCED THE KEY (production, 2026-09-11): this call sent none and
        the bench answered 400 `idempotency_key_required`. That 400 is the only
        reason a withdrawal the harness should never have sent did not reach a
        person whose chosen agent was working fine.
        """
        proposal = urllib.parse.quote(proposal_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/proposals/{proposal}/withdraw",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def read_contract(self) -> dict[str, Any]:
        """The bench's published contract (`/agent-api.openapi.json`)."""
        return self._request("GET", "/agent-api.openapi.json")

    def call_door(
        self, method: str, path: str, body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None, idempotency_key: str = "",
    ) -> Any:
        """One published bench door, with the agent's token (0.74.0). Only this
        bench's own `/api/bench/` paths are called."""
        if not str(path).startswith("/api/bench/"):
            raise BookOfHousesApiError(400, "invalid_form_path", "Not a bench path")
        method = str(method or "GET").upper()
        return self._request(
            method, str(path),
            payload=None if method == "GET" else dict(body or {}),
            query=dict(query) if query else None,
            authenticated=True, idempotency_key=idempotency_key or None,
        )

    def post_form(
        self, method: str, path: str, body: dict[str, Any], idempotency_key: str = ""
    ) -> dict[str, Any]:
        """A bench-published form, posted to its own path (0.74.0). Only this
        bench's own `/api/bench/` paths are called."""
        if not str(path).startswith("/api/bench/"):
            raise BookOfHousesApiError(400, "invalid_form_path", "Not a bench path")
        return self._request(
            str(method or "POST").upper(), str(path), payload=dict(body or {}),
            authenticated=True, idempotency_key=idempotency_key or None,
        )

    def current_step(self, deal_id: str, full: bool = False) -> dict[str, Any]:
        """The deal's current step. `full=True` is the bench's history_url
        (`?full=1`): every step as walked, each receipt's words whole on
        steps[].outcome, which is where a trimmed hand-back is read whole."""
        deal = urllib.parse.quote(deal_id, safe="")
        query = "?full=1" if full else ""
        return self._request(
            "GET", f"/api/bench/deals/{deal}/current-step{query}", authenticated=True
        )

    def post_step_message(
        self,
        deal_id: str,
        step_id: str,
        reply: str,
        idempotency_key: str,
        answering: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Post on the step thread. `answering` names the person's message (an
        id, or a list) this reply pays; the bench pays only what a reply NAMES,
        and naming the latest pays everything before it on the step. Left out
        when None, so a reply that answers nobody claims nothing."""
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        body: dict[str, Any] = {"reply": reply}
        if answering:
            body["answering"] = answering
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/messages",
            payload=body,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def post_check_in(
        self, deal_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        deal = urllib.parse.quote(deal_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/check-ins",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def declare_outside_wait(
        self, deal_id: str, step_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """WAIT (rule 216): declare that you are waiting on the outside world,
        or end the wait. The clocks stop counting it against you while it
        stands."""
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/wait",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def propose_act(
        self, deal_id: str, step_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """ACT (rule 212): file one exact act on the step; the platform executes
        it after the person approves. Kind 'email' today."""
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/acts",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def dismiss_reply(
        self, deal_id: str, step_id: str, reply_id: str,
        payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """RULE 220: say why an inbound reply deserves no answer. A reply from
        an outside person is owed an answer before anything else on that step;
        this door is for spam, bounces and auto-replies only."""
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        reply = urllib.parse.quote(reply_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/replies/{reply}/dismiss",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def withdraw_act_declaration(
        self, deal_id: str, step_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """RULE 218: take back an act your plan declared on this step. The
        bench refuses your outcome (acts_not_filed) until a declared act has
        been approved and sent -- withdraw it with a reason instead."""
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/acts/withdraw",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def file_evidence(
        self, deal_id: str, step_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """THE OUTSIDE ACT: file what you did yourself on an approved act.

        The platform executes what it has hands for; everything else is the
        generic `outside` block, which the agent does in its own name. This is
        the one door that closes it: summary, up to five links, up to five ids
        of file receipts already delivered on this deal. The 201 answers with
        the act, its state and whether a witness was asked. The platform files
        the step's outcome itself from there (rule 229).
        """
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/acts/evidence",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def file_outcome(
        self, target_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/targets/{target}/outcomes",
            payload=payload,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def upload_deal_artifact(
        self,
        deal_id: str,
        *,
        filename: str,
        content: bytes,
        media_type: str = "application/octet-stream",
        title: str | None = None,
        step_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """POST one file to the deal's artifact lane (rule 230, D1).

        multipart/form-data, exactly one `file` part, optional `step_ref` and
        `title` fields. 201 answers with the receipt: receipt_id, sha256,
        size_bytes, filename, filed_at. The platform sniffs the bytes and
        refuses a file whose real type is not the one the step promised
        (422 deliverable_type_mismatch), and refuses any filing when no step
        is agent-working (422 out_of_turn_filing).
        """
        deal = urllib.parse.quote(deal_id, safe="")
        fields: dict[str, str] = {}
        if step_ref:
            fields["step_ref"] = str(step_ref)
        if title:
            fields["title"] = str(title)
        body, content_type = _multipart_body(
            fields, filename=filename, content=content, media_type=media_type
        )
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/artifacts",
            raw_body=body,
            content_type=content_type,
            authenticated=True,
            idempotency_key=idempotency_key,
        )

    def mailbox(self) -> dict[str, Any]:
        return self._request("GET", "/api/agent-email/mailbox", authenticated=True)

    def proposals(self) -> list[dict[str, Any]]:
        # ETag rail (server additive 2026-08-27): most polls see an unchanged
        # list, and the full body can exceed 100KB. If-None-Match turns those
        # polls into empty 304s; urllib surfaces the 304 as an HTTPError, and
        # that "error" IS the cache hit.
        extra = None
        if getattr(self, "_proposals_etag", None):
            extra = {"If-None-Match": self._proposals_etag}
        try:
            result, response_headers = self._request(
                "GET", "/api/bench/proposals/mine", authenticated=True,
                extra_headers=extra, return_headers=True)
        except BookOfHousesApiError as error:
            if error.status == 304 and getattr(self, "_proposals_cache", None) is not None:
                return list(self._proposals_cache)
            raise
        self._proposals_etag = response_headers.get("ETag")
        self._proposals_cache = list(result.get("proposals") or [])
        return list(self._proposals_cache)

    def threads(self, proposal_id: str, limit: int = 50) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/agent-email/threads",
            authenticated=True,
            query={"proposal_id": proposal_id, "limit": limit},
        )

    def thread(self, thread_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/api/agent-email/threads/{urllib.parse.quote(thread_id)}", authenticated=True
        )

    def send_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/agent-email/send", payload=payload, authenticated=True)

    def request_email_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/api/agent-email/approvals", payload=payload, authenticated=True
        )

    def reply_email(self, thread_id: str, body_text: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/agent-email/threads/{urllib.parse.quote(thread_id)}/reply",
            payload={"body_text": body_text},
            authenticated=True,
        )
