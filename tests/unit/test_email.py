import pytest

from toll_harness.email.book_of_houses import (
    BookOfHousesEmailProvider,
    BookOfHousesRestMailClient,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def list_messages(self, mailbox, **parameters):
        self.calls.append(("list", mailbox, parameters))
        return [{"id": "m1"}]

    def get_message(self, mailbox, message_id):
        return {"id": message_id, "mailbox": mailbox}

    def send_message(self, mailbox, **message):
        return {"id": "sent", "mailbox": mailbox, **message}

    def reply_to_message(self, mailbox, message_id, **message):
        return {"id": "reply", "in_reply_to": message_id, "mailbox": mailbox, **message}

    def wait_for_message(self, mailbox, **parameters):
        return {"mailbox": mailbox, **parameters}


def test_book_of_houses_adapter_is_mailbox_scoped():
    client = FakeClient()
    provider = BookOfHousesEmailProvider(mailbox="agent@bookofhouses.com", client=client)

    assert provider.list(limit=500) == [{"id": "m1"}]
    assert client.calls == [
        ("list", "agent@bookofhouses.com", {"limit": 100, "unread_only": False})
    ]
    sent = provider.send(
        to=["user@example.com"], subject="Hello", text="Body", idempotency_key="k1"
    )
    assert sent["mailbox"] == "agent@bookofhouses.com"


def test_book_of_houses_adapter_rejects_other_domains():
    with pytest.raises(ValueError):
        BookOfHousesEmailProvider(mailbox="agent@example.com", client=FakeClient())


class FakeApi:
    def __init__(self):
        self.approvals = []

    def proposals(self):
        return [{"id": "p1", "status": "accepted"}, {"id": "p2", "status": "filed"}]

    def threads(self, proposal_id, limit=50):
        assert proposal_id == "p1"
        return {"threads": [{"id": "thread-1"}]}

    def thread(self, thread_id):
        return {"thread": {"id": thread_id}, "messages": []}

    def send_email(self, payload):
        return {"success": True, "payload": payload}

    def request_email_approval(self, payload):
        self.approvals.append(payload)
        return {"success": True, "approval_id": "approval-1", "status": "pending"}

    def reply_email(self, thread_id, body_text):
        return {"success": True, "thread_id": thread_id, "body_text": body_text}


def test_production_client_enforces_mailbox_and_approval_context():
    mailbox = "canonical@bookofhouses.com"
    client = BookOfHousesRestMailClient(FakeApi(), expected_mailbox=mailbox)

    assert client.list_messages(mailbox) == [{"id": "thread-1"}]
    with pytest.raises(PermissionError):
        client.list_messages("another@bookofhouses.com")
    with pytest.raises(RuntimeError, match="human approval context"):
        client.send_message(
            mailbox,
            to=["person@example.com"],
            subject="Test",
            text="Body",
            idempotency_key="test",
        )

    approved = BookOfHousesRestMailClient(
        FakeApi(),
        expected_mailbox=mailbox,
        send_context={
            "proposal_id": "p1",
            "step_id": "s1",
            "approval_id": "approval-1",
        },
    )
    result = approved.send_message(
        mailbox,
        to=["person@example.com"],
        subject="Test",
        text="Body",
        idempotency_key="test",
    )
    assert result["payload"]["approval_id"] == "approval-1"
    assert result["send_receipt"] == {
        "evidence_type": "provider_acceptance",
        "status": "accepted",
        "message_id": None,
        "thread_id": None,
        "from": None,
        "to": "person@example.com",
        "subject": "Test",
        "accepted_at": None,
        "inbox_delivery_confirmed": False,
    }


def test_email_send_requests_exact_human_approval_before_delivery():
    api = FakeApi()
    mailbox = "canonical@bookofhouses.com"
    client = BookOfHousesRestMailClient(api, expected_mailbox=mailbox)
    client.configure_send_context(proposal_id="p1", step_id="s1")

    pending = client.send_message(
        mailbox,
        to=["person@example.com"],
        subject="Hello",
        text="Approved body",
        idempotency_key="send-1",
    )
    sent = client.send_message(
        mailbox,
        to=["person@example.com"],
        subject="Hello",
        text="Approved body",
        idempotency_key="send-2",
    )

    assert pending["status"] == "pending_human_approval"
    assert api.approvals == [
        {
            "proposal_id": "p1",
            "step_id": "s1",
            "approval_type": "individual",
            "to": "person@example.com",
            "subject": "Hello",
            "body_text": "Approved body",
            "purpose": "Complete the accepted Toll Bench email delivery step.",
            "message_classification": "operational",
        }
    ]
    assert sent["success"] is True
    assert sent["send_receipt"]["evidence_type"] == "provider_acceptance"
    assert sent["send_receipt"]["inbox_delivery_confirmed"] is False


def test_resume_pending_send_returns_provider_acceptance_receipt():
    api = FakeApi()
    mailbox = "canonical@bookofhouses.com"
    client = BookOfHousesRestMailClient(api, expected_mailbox=mailbox)
    client.configure_send_context(proposal_id="p1", step_id="s1")

    pending = client.send_message(
        mailbox,
        to=["person@example.com"],
        subject="Hello",
        text="Approved body",
        idempotency_key="send-1",
    )
    resumed = client.resume_pending_send()

    assert pending["status"] == "pending_human_approval"
    assert resumed["success"] is True
    assert resumed["send_receipt"]["to"] == "person@example.com"
    assert resumed["send_receipt"]["subject"] == "Hello"
    assert resumed["send_receipt"]["inbox_delivery_confirmed"] is False


def test_pending_send_survives_worker_restart_in_isolated_storage(tmp_path):
    api = FakeApi()
    mailbox = "canonical@bookofhouses.com"
    pending_store = tmp_path / "agent-id" / "pending-email-send.json"
    first_client = BookOfHousesRestMailClient(
        api,
        expected_mailbox=mailbox,
        pending_store=pending_store,
    )
    first_client.configure_send_context(proposal_id="p1", step_id="s1")

    pending = first_client.send_message(
        mailbox,
        to=["person@example.com"],
        subject="Hello",
        text="Approved body",
        idempotency_key="send-1",
    )

    assert pending["status"] == "pending_human_approval"
    assert pending_store.exists()
    assert pending_store.stat().st_mode & 0o777 == 0o600

    restarted_client = BookOfHousesRestMailClient(
        api,
        expected_mailbox=mailbox,
        pending_store=pending_store,
    )
    restarted_client.configure_send_context(proposal_id="p1", step_id="s1")
    resumed = restarted_client.resume_pending_send()

    assert resumed["success"] is True
    assert resumed["send_receipt"]["to"] == "person@example.com"
    assert not pending_store.exists()


def test_pending_send_refuses_context_switch(tmp_path):
    api = FakeApi()
    mailbox = "canonical@bookofhouses.com"
    client = BookOfHousesRestMailClient(
        api,
        expected_mailbox=mailbox,
        pending_store=tmp_path / "pending.json",
    )
    client.configure_send_context(proposal_id="p1", step_id="s1")
    client.send_message(
        mailbox,
        to=["person@example.com"],
        subject="Hello",
        text="Approved body",
        idempotency_key="send-1",
    )

    with pytest.raises(RuntimeError, match="unresolved pending email"):
        client.configure_send_context(proposal_id="p2", step_id="s2")
