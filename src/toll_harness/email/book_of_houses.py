"""The agent's platform mailbox: list, read and reply on proposal email
threads. 0.74.0: a new email is the deal step's email act
(`toll_bench.propose_act`); the bench's send and approval doors answer
CONTACTS_ACT_REQUIRED since contract 3.4, so the parked-draft machinery, its
approval probe and the inbound-email wake are gone. The API client lives in
`toll_harness.toll_bench.api`; every name it had here is still exported.
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from toll_harness.email.base import EmailProvider
from toll_harness.toll_bench.api import (  # noqa: F401 - the names this module always exported
    DOOR_WORDS_CHARS,
    GOAL_RUN_ID,
    PACK_INPUT_MAX_BYTES,
    PAID_PACK_FILE_MAX_BYTES,
    BookOfHousesApiClient,
    BookOfHousesApiError,
    _multipart_body,
    _RefusePackInputRedirects,
    door_line,
    door_words,
)

# THE DOMAINS AN AGENT MAILBOX MAY LIVE ON. The bench minted every agent
# mailbox on bookofhouses.com until 2026-10-05, when new ones began minting as
# <handle>@tollbench.com; older mailboxes stay where they were, so both are
# platform mailboxes. WHAT FORCED IT: lab agent DJ-4-Fun (A-0439) was issued
# dj4fun.tollbench@tollbench.com and its email provider refused to load,
# "Book of Houses mailboxes must use the bookofhouses.com domain".
DEFAULT_MAILBOX_DOMAINS: tuple[str, ...] = ("bookofhouses.com", "tollbench.com")

# Fleet-wide lever: more domains, comma separated, accepted IN ADDITION to the
# two above (a bench run on its own domain). A per-agent `email.extra_domains`
# list adds to it. Nothing here can take a default away, so a typo in the
# variable never stops an agent whose mailbox was already accepted.
EXTRA_MAILBOX_DOMAINS_ENV = "TOLL_HARNESS_EXTRA_MAILBOX_DOMAINS"


def _domain_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: list[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ValueError("Extra mailbox domains must be a list of domains")
    domains: list[str] = []
    for item in items:
        domain = str(item).strip().lower().lstrip("@").rstrip(".")
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def accepted_mailbox_domains(extra: Any = None) -> tuple[str, ...]:
    """The two platform domains, then any the environment or `extra` adds."""
    domains = list(DEFAULT_MAILBOX_DOMAINS)
    for value in (os.environ.get(EXTRA_MAILBOX_DOMAINS_ENV), extra):
        for domain in _domain_list(value):
            if domain not in domains:
                domains.append(domain)
    return tuple(domains)


def mailbox_domain(address: str) -> str:
    """Everything after the last @, lowercased; "" when there is no @."""
    _, at, domain = str(address or "").rpartition("@")
    return domain.lower() if at else ""


class BookOfHousesRestMailClient:
    """Adapts one agent token to the proposal-scoped email API: list, read and
    reply on a thread. A NEW email is the deal step's email act (bench
    contract 3.4); `send_message` hands back the send door's own answer."""

    # Refusals that mean a proposal's mail is over: its threads are not read again.
    DEAD_DRAFT_CODES = frozenset({
        "PROPOSAL_NOT_ACTIVE",
        "PROPOSAL_NOT_ACCEPTED",
        "PROPOSAL_NOT_FOUND",
        "AGENT_NOT_ASSIGNED",
        "STEP_NOT_ACTIVE",
    })

    def __init__(
        self,
        api: BookOfHousesApiClient,
        *,
        expected_mailbox: str,
        send_context: dict[str, str] | None = None,
        pending_store: Any = None,  # accepted, unused since 0.74.0
    ):
        self.api = api
        self.expected_mailbox = expected_mailbox.lower()
        self.send_context = dict(send_context or {})
        self.pending_send: dict[str, Any] | None = None

    def configure_send_context(self, *, proposal_id: str, step_id: str) -> None:
        """Deprecated 2026-10-31 (0.74.0): kept so callers still work."""
        self.send_context = {"proposal_id": proposal_id, "step_id": step_id}

    def resume_pending_send(self) -> dict[str, Any] | None:
        """Deprecated 2026-10-31 (0.74.0): no draft is ever parked, so None."""
        return None

    def _check_mailbox(self, mailbox: str) -> None:
        if mailbox.lower() != self.expected_mailbox:
            raise PermissionError("The configured token cannot be used for another mailbox")

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
        """The send door's own answer. Since contract 3.4 it sends nothing and
        says to file the step's email act; that refusal comes back as written."""
        self._check_mailbox(mailbox)
        recipients = message.get("to") or []
        payload = {
            **self.send_context,
            "to": recipients[0] if len(recipients) == 1 else recipients,
            "subject": message.get("subject"),
            "body_text": message.get("text"),
        }
        try:
            return self.api.send_email(payload)
        except BookOfHousesApiError as error:
            return {"ok": False, "success": False, "error": error.code,
                    "status": error.status, "message": error.message}

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

    def __init__(
        self,
        *,
        mailbox: str,
        client: BookOfHousesMailClient,
        extra_domains: Any = None,
    ):
        self.accepted_domains = accepted_mailbox_domains(extra_domains)
        if mailbox_domain(mailbox) not in self.accepted_domains:
            raise ValueError(
                "Book of Houses mailboxes must use one of the accepted domains: "
                + ", ".join(self.accepted_domains)
            )
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
