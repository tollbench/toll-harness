from __future__ import annotations

import json
import os
import time
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
        extra_headers: dict[str, str] | None = None,
        return_headers: bool = False,
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
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
            if self.maker_id:
                headers["X-Maker-Id"] = self.maker_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                data = json.loads(raw) if raw else {}
                if return_headers:
                    return data, dict(response.headers)
                return data
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

    def capability_taxonomy(self) -> dict[str, Any]:
        # The closed twenty-key capability list (rule 110). A bid's
        # `capabilities` block (rule 226) must draw its keys from here, so this
        # is a read an agent makes BEFORE it files, not a curiosity.
        return self._request("GET", "/api/bench/capabilities", authenticated=True)

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

    def withdraw_proposal(self, proposal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        proposal = urllib.parse.quote(proposal_id, safe="")
        return self._request(
            "POST",
            f"/api/bench/proposals/{proposal}/withdraw",
            payload=payload,
            authenticated=True,
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

    def _reanchor_pending_send(self, *, proposal_id: str, step_id: str) -> None:
        """Carry an approval-pending email to the deal\'s new current step.

        The intelligence already chose these exact bytes and a person may be
        reviewing them, so the harness preserves the content verbatim and only
        re-binds it to the live step, requesting approval for the identical
        recipient, subject, and body. It never regenerates the message.
        """
        if self.pending_send is None:
            return
        approval = self.api.request_email_approval(
            {
                "proposal_id": proposal_id,
                "step_id": step_id,
                "approval_type": "individual",
                "to": self.pending_send["to"],
                "subject": self.pending_send["subject"],
                "body_text": self.pending_send["body_text"],
                "purpose": self.pending_send.get(
                    "purpose", "Complete the accepted Toll Bench email delivery step."
                ),
                "message_classification": self.pending_send.get(
                    "message_classification", "operational"
                ),
                **(
                    {"attachment_file_ids": self.pending_send["attachment_file_ids"]}
                    if self.pending_send.get("attachment_file_ids")
                    else {}
                ),
            }
        )
        approval_id = approval.get("approval_id")
        self.pending_send = {**self.pending_send, "step_id": step_id, "approval_id": approval_id}
        self.send_context = {
            "proposal_id": proposal_id,
            "step_id": step_id,
            "approval_id": approval_id,
        }
        self._persist_pending_send()

    def configure_send_context(self, *, proposal_id: str, step_id: str) -> None:
        next_context = {"proposal_id": proposal_id, "step_id": step_id}
        if self.pending_send:
            pending_proposal = str(self.pending_send.get("proposal_id") or "")
            pending_step = str(self.pending_send.get("step_id") or "")
            if pending_proposal != proposal_id:
                # A pending send parked on a *different deal* must not be
                # retargeted onto this one; defer this deal for the cycle.
                raise RuntimeError("A different deal step has an unresolved pending email send")
            if pending_step != step_id:
                # Same deal advanced to a new step. Carry the already drafted and
                # approval-pending email forward to the live step unchanged
                # instead of regenerating it or deferring forever.
                self._reanchor_pending_send(proposal_id=proposal_id, step_id=step_id)
                return
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
        dead = self.__dict__.setdefault("_dead_proposals", set())
        for proposal in self.api.proposals():
            if proposal.get("status") != "accepted":
                continue
            proposal_id = str(proposal.get("id") or "")
            if not proposal_id or proposal_id in dead:
                continue
            try:
                result = self.api.threads(proposal_id, limit=limit)
            except BookOfHousesApiError as error:
                # The bench still lists the proposal as accepted but its deal is
                # over: every thread read answered PROPOSAL_NOT_ACTIVE, once a
                # cycle, forever (Cindy, 2026-09-03: 1,342 of them after a
                # restart). Remember it and stop asking; nothing here is owed.
                if error.code in self.DEAD_DRAFT_CODES:
                    dead.add(proposal_id)
                    continue
                raise
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
        # Released-file attachments ride the approval and the send as one
        # set. The key is included only when non-empty so a text-only email
        # keeps the exact payload shape servers before 2026-08-28 accept.
        attachments = [
            str(item) for item in (message.get("attachment_file_ids") or []) if str(item)
        ]
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
                    **({"attachment_file_ids": attachments} if attachments else {}),
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
                **({"attachment_file_ids": attachments} if attachments else {}),
            }
            self._persist_pending_send()
            return {
                "ok": False,
                "success": False,
                "status": "pending_human_approval",
                "approval_id": approval.get("approval_id"),
                "message": "Exact recipient and email content are waiting for person approval.",
            }
        # H5: the person approved exact bytes, and those are the only bytes
        # that can go out. If the model redrafted between approval and send,
        # send the approved content and say so in the result; never the rewrite.
        approved = self.pending_send
        content_rewritten = bool(approved) and (
            recipients[0] != approved["to"]
            or (message.get("subject") or "") != (approved["subject"] or "")
            or (message.get("text") or "") != (approved["body_text"] or "")
            or attachments != list(approved.get("attachment_file_ids") or [])
        )
        payload = {
            **self.send_context,
            "purpose": purpose,
            "message_classification": classification,
            "to": approved["to"] if approved else recipients[0],
            "subject": approved["subject"] if approved else message.get("subject"),
            "body_text": approved["body_text"] if approved else message.get("text"),
        }
        approved_attachments = (
            list(approved.get("attachment_file_ids") or []) if approved else attachments
        )
        if approved_attachments:
            payload["attachment_file_ids"] = approved_attachments
        try:
            result = self.api.send_email(payload)
            self._clear_pending_send()
            result = self._with_send_receipt(result, payload)
            if content_rewritten:
                result["approved_content_enforced"] = True
            return result
        except BookOfHousesApiError as error:
            if error.code == "EMAIL_APPROVAL_REJECTED":
                return self._handle_rejected_approval(error)
            if error.code in {"EMAIL_REQUIRES_HUMAN_APPROVAL", "EMAIL_APPROVAL_PENDING"}:
                return {
                    "ok": False,
                    "success": False,
                    "status": "pending_human_approval",
                    "approval_id": self.send_context.get("approval_id"),
                    "message": "Exact recipient and email content are waiting for person approval.",
                }
            raise

    # Refusals that mean the parked draft is DEAD, not waiting: nothing the
    # person does can ever approve it, so keeping it wedges the agent.
    DEAD_DRAFT_CODES = frozenset({
        "PROPOSAL_NOT_ACTIVE",
        "PROPOSAL_NOT_ACCEPTED",
        "PROPOSAL_NOT_FOUND",
        "AGENT_NOT_ASSIGNED",
        "STEP_NOT_ACTIVE",
    })

    def _handle_dead_draft(self, error: BookOfHousesApiError) -> dict[str, Any]:
        """The parked draft belongs to a deal that is over (or was never live).
        Drop the draft and the stale approval id so the watch cycle proceeds to
        the work that is actually owed; report what was dropped and why."""
        dropped = dict(self.pending_send or {})
        approval_id = self.send_context.get("approval_id")
        self._clear_pending_send()
        self.send_context.pop("approval_id", None)
        return {
            "ok": False,
            "success": False,
            "status": "dropped_dead_draft",
            "approval_id": approval_id,
            "code": error.code,
            "reason": error.message,
            "dropped": {
                k: dropped.get(k)
                for k in ("to", "subject", "proposal_id", "step_id")
                if k in dropped
            },
        }

    def _handle_rejected_approval(self, error: BookOfHousesApiError) -> dict[str, Any]:
        """The person rejected the parked draft. A rejection is an answer, not
        a wait state: drop the pending send and the stale approval id so the
        next send starts a fresh draft/approval cycle. The refusal message
        carries the person's feedback verbatim; return it so the caller (and
        the model) can write a different draft instead of retrying this one."""
        rejected_approval = self.send_context.get("approval_id")
        self._clear_pending_send()
        self.send_context.pop("approval_id", None)
        return {
            "ok": False,
            "success": False,
            "status": "rejected_by_person",
            "approval_id": rejected_approval,
            "reason": error.message,
        }

    # A send parked on human approval resolves at human speed. Probing it by
    # re-POSTing every watch cycle produced 8,294 refused sends in six hours
    # (2026-08-27, Marcia), so probes are spaced: at most one attempt per
    # RESUME_PROBE_SECONDS. Worst-case latency after the person approves is
    # one probe interval; rejection feedback also arrives on the next probe.
    RESUME_PROBE_SECONDS = 300.0

    def resume_pending_send(self) -> dict[str, Any] | None:
        if self.pending_send is None:
            return None
        if time.monotonic() < getattr(self, "_next_resume_probe", 0.0):
            return {
                "ok": False,
                "success": False,
                "status": "pending_human_approval",
                "approval_id": self.send_context.get("approval_id"),
                "probe_deferred": True,
            }
        payload = dict(self.pending_send)
        try:
            result = self.api.send_email(payload)
        except BookOfHousesApiError as error:
            if error.code == "EMAIL_APPROVAL_REJECTED":
                self._next_resume_probe = 0.0
                return self._handle_rejected_approval(error)
            if error.code in self.DEAD_DRAFT_CODES:
                # The draft can never be sent: its proposal/step is no longer an
                # active countersigned deal (or was never this agent's). Before
                # 2026-09-03 this code fell through to `raise`, which killed
                # every watch cycle before any obligation was selected -- and
                # because the draft is persisted, a restart did not help. One
                # agent (Kari) re-probed a dead draft 2,678 times in 26 hours
                # while five live obligations waited. Drop it and move on.
                self._next_resume_probe = 0.0
                return self._handle_dead_draft(error)
            if error.code in {"EMAIL_REQUIRES_HUMAN_APPROVAL", "EMAIL_APPROVAL_PENDING"}:
                self._next_resume_probe = time.monotonic() + self.RESUME_PROBE_SECONDS
                return {
                    "ok": False,
                    "success": False,
                    "status": "pending_human_approval",
                    "approval_id": self.send_context.get("approval_id"),
                }
            raise
        self._next_resume_probe = 0.0
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
        self,
        *,
        to: list[str],
        subject: str,
        text: str,
        idempotency_key: str,
        attachment_file_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.client.send_message(
            self.mailbox,
            to=to,
            subject=subject,
            text=text,
            idempotency_key=idempotency_key,
            attachment_file_ids=attachment_file_ids,
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
