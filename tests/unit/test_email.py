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


def test_the_mailbox_is_scoped_and_a_new_email_gets_the_doors_own_answer(tmp_path):
    """0.74.0: a new email is the step's email act (contract 3.4). The send door
    answers CONTACTS_ACT_REQUIRED and that answer comes back as written; no
    draft is parked, and an old pending-send file is ignored."""
    from toll_harness.email.book_of_houses import BookOfHousesApiError

    class SendDoorRetired(FakeApi):
        def send_email(self, payload):
            self.sent = payload
            raise BookOfHousesApiError(
                409, "CONTACTS_ACT_REQUIRED",
                "File the deal step's email act with exactly one contact_ref.")

    mailbox = "canonical@bookofhouses.com"
    (tmp_path / "pending.json").write_text('{"proposal_id": "p1"}')
    api = SendDoorRetired()
    client = BookOfHousesRestMailClient(api, expected_mailbox=mailbox,
                                        pending_store=tmp_path / "pending.json")
    assert client.list_messages(mailbox) == [{"id": "thread-1"}]
    with pytest.raises(PermissionError):
        client.list_messages("another@bookofhouses.com")
    client.configure_send_context(proposal_id="p1", step_id="s1")
    assert client.resume_pending_send() is None
    answer = client.send_message(mailbox, to=["person@example.com"], subject="Hi",
                                 text="Body", idempotency_key="k")
    assert answer["ok"] is False and answer["error"] == "CONTACTS_ACT_REQUIRED"
    assert "email act" in answer["message"]
    assert api.sent["step_id"] == "s1" and api.sent["to"] == "person@example.com"
    assert client.reply_to_message(mailbox, "thread-1", text="Thanks")["body_text"] == "Thanks"


def test_a_dead_proposal_is_skipped_and_never_asked_again():
    """The bench lists a proposal as accepted after its deal ended; its thread
    read answers PROPOSAL_NOT_ACTIVE. That is not a mail hiccup to log every
    cycle -- the proposal is dead. Skip it, remember it, keep listing the rest."""
    from toll_harness.email.book_of_houses import (
        BookOfHousesApiError,
        BookOfHousesRestMailClient,
    )

    class _Api:
        def __init__(self):
            self.thread_calls = []

        def proposals(self):
            return [{"id": "dead-1", "status": "accepted"},
                    {"id": "live-2", "status": "accepted"}]

        def threads(self, proposal_id, limit=50):
            self.thread_calls.append(proposal_id)
            if proposal_id == "dead-1":
                raise BookOfHousesApiError(
                    409, "PROPOSAL_NOT_ACTIVE", "This proposal is no longer active.")
            return {"threads": [{"id": "t-1", "last_inbound_at": "2026-09-03T00:00:00Z"}]}

    api = _Api()
    client = BookOfHousesRestMailClient.__new__(BookOfHousesRestMailClient)
    client.api = api
    client._check_mailbox = lambda mailbox: None
    out = client.list_messages("agent@bookofhouses.com", limit=20)
    assert [x["id"] for x in out] == ["t-1"]
    assert api.thread_calls == ["dead-1", "live-2"]
    client.list_messages("agent@bookofhouses.com", limit=20)
    assert api.thread_calls == ["dead-1", "live-2", "live-2"], "asked once, never again"
