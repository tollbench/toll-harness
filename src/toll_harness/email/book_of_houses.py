from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from toll_harness.email.base import EmailProvider


class BookOfHousesApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"Book of Houses API error ({status}, {code}): {message}")
        self.status = status
        self.code = code
        self.message = message


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
        authenticated: bool = False,
        idempotency_key: str | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
            if self.maker_id:
                headers["X-Maker-Id"] = self.maker_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = {}
            raise BookOfHousesApiError(
                error.code,
                str(detail.get("error") or "http_error"),
                str(detail.get("message") or detail.get("detail") or error.reason),
            ) from None
        except urllib.error.URLError as error:
            raise BookOfHousesApiError(0, "connection_error", str(error.reason)) from None

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

    def authenticated(self, token: str, maker_id: str) -> BookOfHousesApiClient:
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

    def proposal_schema(self) -> dict[str, Any]:
        return self._request("GET", "/static/agent-proposal.schema.json")

    def ack_reachability_ping(self) -> dict[str, Any]:
        return self._request("POST", "/api/bench/me/pings/ack", payload={}, authenticated=True)

    def attention(self, *, wait: int = 0) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/bench/me/attention",
            authenticated=True,
            query={"wait": min(max(int(wait), 0), 20)},
        )

    def events(self, *, after: str | None = None, wait: int = 0) -> dict[str, Any]:
        query: dict[str, Any] = {"wait": min(max(int(wait), 0), 20)}
        if after:
            query["after"] = after
        return self._request("GET", "/api/bench/events", authenticated=True, query=query)

    def open_targets(self) -> dict[str, Any]:
        return self._request("GET", "/api/bench/targets/open", authenticated=True)

    def target_brief(self, target_id: str) -> dict[str, Any]:
        target = urllib.parse.quote(target_id, safe="")
        return self._request("GET", f"/api/bench/targets/{target}/brief", authenticated=True)

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

    def current_step(self, deal_id: str) -> dict[str, Any]:
        deal = urllib.parse.quote(deal_id, safe="")
        return self._request(
            "GET", f"/api/bench/deals/{deal}/current-step", authenticated=True
        )

    def post_step_message(
        self,
        deal_id: str,
        step_id: str,
        reply: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        deal = urllib.parse.quote(deal_id, safe="")
        step = urllib.parse.quote(step_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/deals/{deal}/steps/{step}/messages",
            payload={"reply": reply},
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

    def mailbox(self) -> dict[str, Any]:
        return self._request("GET", "/api/agent-email/mailbox", authenticated=True)

    def proposals(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/api/bench/proposals/mine", authenticated=True)
        return list(result.get("proposals") or [])

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


class BookOfHousesRestMailClient:
    """Adapts one agent token to the production proposal-scoped email API."""

    def __init__(
        self,
        api: BookOfHousesApiClient,
        *,
        expected_mailbox: str,
        send_context: dict[str, str] | None = None,
        pending_store: str | Path | None = None,
    ):
        self.api = api
        self.expected_mailbox = expected_mailbox.lower()
        self.send_context = dict(send_context or {})
        self.pending_store = Path(pending_store).resolve() if pending_store else None
        self.pending_send = self._load_pending_send()
        if self.pending_send:
            self.send_context = {
                key: str(self.pending_send[key])
                for key in ("proposal_id", "step_id", "approval_id")
                if self.pending_send.get(key)
            }

    def _load_pending_send(self) -> dict[str, Any] | None:
        if self.pending_store is None or not self.pending_store.exists():
            return None
        value = json.loads(self.pending_store.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Pending email send storage must contain an object")
        required = {"proposal_id", "step_id", "approval_id", "to", "subject", "body_text"}
        if required - value.keys():
            raise ValueError("Pending email send storage is incomplete")
        return value

    def _persist_pending_send(self) -> None:
        if self.pending_store is None or self.pending_send is None:
            return
        self.pending_store.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pending_store.with_name(
            f".{self.pending_store.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(self.pending_send, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.pending_store)

    def _clear_pending_send(self) -> None:
        self.pending_send = None
        if self.pending_store is not None:
            self.pending_store.unlink(missing_ok=True)

    def configure_send_context(self, *, proposal_id: str, step_id: str) -> None:
        next_context = {"proposal_id": proposal_id, "step_id": step_id}
        if self.pending_send and any(
            str(self.pending_send.get(key) or "") != value
            for key, value in next_context.items()
        ):
            raise RuntimeError("A different deal step has an unresolved pending email send")
        if any(self.send_context.get(key) != value for key, value in next_context.items()):
            self.send_context = next_context

    def _check_mailbox(self, mailbox: str) -> None:
        if mailbox.lower() != self.expected_mailbox:
            raise PermissionError("The configured token cannot be used for another mailbox")

    @staticmethod
    def _with_send_receipt(
        result: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Attach honest evidence without overstating inbox delivery."""
        receipt = result.get("send_receipt")
        if not isinstance(receipt, dict):
            receipt = {
                "evidence_type": "provider_acceptance",
                "status": "accepted",
                "message_id": result.get("message_id"),
                "thread_id": result.get("thread_id"),
                "from": result.get("from"),
                "to": payload.get("to"),
                "subject": payload.get("subject"),
                "accepted_at": result.get("sent_at"),
                "inbox_delivery_confirmed": False,
            }
        return {**result, "send_receipt": receipt}

    def list_messages(self, mailbox: str, **parameters: Any) -> list[dict[str, Any]]:
        self._check_mailbox(mailbox)
        limit = min(max(int(parameters.get("limit", 20)), 1), 100)
        threads: list[dict[str, Any]] = []
        for proposal in self.api.proposals():
            if proposal.get("status") != "accepted":
                continue
            proposal_id = str(proposal.get("id") or "")
            if not proposal_id:
                continue
            result = self.api.threads(proposal_id, limit=limit)
            for item in result.get("threads") or []:
                if isinstance(item, dict):
                    threads.append(item)
            if len(threads) >= limit:
                break
        return threads[:limit]

    def get_message(self, mailbox: str, message_id: str) -> dict[str, Any]:
        self._check_mailbox(mailbox)
        return self.api.thread(message_id)

    def send_message(self, mailbox: str, **message: Any) -> dict[str, Any]:
        self._check_mailbox(mailbox)
        required = {"proposal_id", "step_id"}
        missing = sorted(required - self.send_context.keys())
        if missing:
            raise RuntimeError(
                "Book of Houses email.send requires an accepted proposal and current human "
                f"approval context; missing: {', '.join(missing)}"
            )
        recipients = message.get("to") or []
        if len(recipients) != 1:
            raise ValueError("Book of Houses permits exactly one external recipient")
        purpose = "Complete the accepted Toll Bench email delivery step."
        classification = "operational"
        if not self.send_context.get("approval_id"):
            approval = self.api.request_email_approval(
                {
                    **self.send_context,
                    "approval_type": "individual",
                    "to": recipients[0],
                    "subject": message.get("subject"),
                    "body_text": message.get("text"),
                    "purpose": purpose,
                    "message_classification": classification,
                }
            )
            self.send_context["approval_id"] = approval.get("approval_id")
            self.pending_send = {
                **self.send_context,
                "purpose": purpose,
                "message_classification": classification,
                "to": recipients[0],
                "subject": message.get("subject"),
                "body_text": message.get("text"),
            }
            self._persist_pending_send()
            return {
                "ok": False,
                "success": False,
                "status": "pending_human_approval",
                "approval_id": approval.get("approval_id"),
                "message": "Exact recipient and email content are waiting for person approval.",
            }
        payload = {
            **self.send_context,
            "purpose": purpose,
            "message_classification": classification,
            "to": recipients[0],
            "subject": message.get("subject"),
            "body_text": message.get("text"),
        }
        try:
            result = self.api.send_email(payload)
            self._clear_pending_send()
            return self._with_send_receipt(result, payload)
        except BookOfHousesApiError as error:
            if error.code in {"EMAIL_REQUIRES_HUMAN_APPROVAL", "EMAIL_APPROVAL_PENDING"}:
                return {
                    "ok": False,
                    "success": False,
                    "status": "pending_human_approval",
                    "approval_id": self.send_context.get("approval_id"),
                    "message": "Exact recipient and email content are waiting for person approval.",
                }
            raise

    def resume_pending_send(self) -> dict[str, Any] | None:
        if self.pending_send is None:
            return None
        payload = dict(self.pending_send)
        try:
            result = self.api.send_email(payload)
        except BookOfHousesApiError as error:
            if error.code in {"EMAIL_REQUIRES_HUMAN_APPROVAL", "EMAIL_APPROVAL_PENDING"}:
                return {
                    "ok": False,
                    "success": False,
                    "status": "pending_human_approval",
                    "approval_id": self.send_context.get("approval_id"),
                }
            raise
        self._clear_pending_send()
        return self._with_send_receipt(result, payload)

    def reply_to_message(self, mailbox: str, message_id: str, **message: Any) -> dict[str, Any]:
        self._check_mailbox(mailbox)
        return self.api.reply_email(message_id, str(message.get("text") or ""))

    def wait_for_message(self, mailbox: str, **parameters: Any) -> dict[str, Any]:
        self._check_mailbox(mailbox)
        messages = self.list_messages(mailbox, limit=1)
        return {"message": messages[0] if messages else None, "timed_out": not messages}


class BookOfHousesMailClient(Protocol):
    """Boundary implemented by the existing or future Book of Houses Mail API client."""

    def list_messages(self, mailbox: str, **parameters: Any) -> list[dict[str, Any]]: ...

    def get_message(self, mailbox: str, message_id: str) -> dict[str, Any]: ...

    def send_message(self, mailbox: str, **message: Any) -> dict[str, Any]: ...

    def reply_to_message(self, mailbox: str, message_id: str, **message: Any) -> dict[str, Any]: ...

    def wait_for_message(self, mailbox: str, **parameters: Any) -> dict[str, Any]: ...


class BookOfHousesEmailProvider(EmailProvider):
    """Mailbox-constrained adapter boundary; it contains no mail-server implementation."""

    def __init__(self, *, mailbox: str, client: BookOfHousesMailClient):
        if not mailbox.lower().endswith("@bookofhouses.com"):
            raise ValueError("Book of Houses mailboxes must use the bookofhouses.com domain")
        self.mailbox = mailbox
        self.client = client

    def list(self, *, limit: int = 20, unread_only: bool = False) -> list[dict[str, Any]]:
        return self.client.list_messages(
            self.mailbox, limit=min(max(limit, 1), 100), unread_only=unread_only
        )

    def read(self, message_id: str) -> dict[str, Any]:
        return self.client.get_message(self.mailbox, message_id)

    def send(
        self, *, to: list[str], subject: str, text: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self.client.send_message(
            self.mailbox,
            to=to,
            subject=subject,
            text=text,
            idempotency_key=idempotency_key,
        )

    def reply(self, *, message_id: str, text: str, idempotency_key: str) -> dict[str, Any]:
        return self.client.reply_to_message(
            self.mailbox,
            message_id,
            text=text,
            idempotency_key=idempotency_key,
        )

    def wait(self, *, after: str | None = None, timeout_seconds: int = 30) -> dict[str, Any]:
        return self.client.wait_for_message(
            self.mailbox, after=after, timeout_seconds=min(max(timeout_seconds, 1), 300)
        )
