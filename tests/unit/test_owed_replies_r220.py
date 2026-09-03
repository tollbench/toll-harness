"""RULE 220 — a reply is owed an answer, and a sent-back act must reach the model.

An invitee replied "Can we adjust the time?" and the agent filed two more
copies of the original invitation. Later the same night the person pressed Send
back on an act with a reason, and the agent idled for hours: `current_step`
carried no acts at all and the idle-step fingerprint ignored them, so nothing
about the step looked different.

Two harness laws are being kept here, both of them scar tissue:
  * a key the server adds and this whitelist drops DOES NOT EXIST to a railed
    model (the reason person_sees_control never landed until v0.16.0), and
  * an input the fingerprint cannot see cannot wake the model.
"""
import glob
import json
import os

import pytest
import yaml

from toll_harness import cli
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Api:
    def __init__(self, current=None, checkin=None):
        self.calls = []
        self._current = current or {}
        self._checkin = checkin or {}

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        self.calls.append(('propose_act', deal_id, step_id, payload, idempotency_key))
        return {"ok": True, "act_id": "ap-1"}

    def dismiss_reply(self, deal_id, step_id, reply_id, payload, idempotency_key):
        self.calls.append(('dismiss_reply', deal_id, step_id, reply_id, payload,
                           idempotency_key))
        return {"ok": True, "reply_id": reply_id}

    def current_step(self, deal_id):
        return self._current

    def post_check_in(self, deal_id, payload, idempotency_key):
        return self._checkin


def _provider(**kw):
    api = _Api(**kw)
    return BookOfHousesTollBenchProvider(api), api


# ---------------------------------------------------------------------------
# Answering is an act
# ---------------------------------------------------------------------------
def test_an_answering_act_sends_only_the_words():
    """The recipient and the subject belong to the thread, not to us -- the
    bench fills them, so in_reply_to + body_text is a complete act."""
    provider, api = _provider()

    out = provider.propose_act('d-1', 's-1', {
        'kind': 'email', 'in_reply_to': 'msg-1',
        'body_text': 'Yes -- would 11:00 work?'}, 'k-1')

    assert out['ok'] is True
    (_name, _deal, _step, payload, key) = api.calls[0]
    assert payload == {'kind': 'email', 'in_reply_to': 'msg-1',
                       'body_text': 'Yes -- would 11:00 work?'}
    assert key == 'k-1'


def test_an_answering_act_still_needs_the_words():
    provider, _api = _provider()

    out = provider.propose_act('d-1', 's-1',
                               {'kind': 'email', 'in_reply_to': 'msg-1'}, 'k-2')

    assert out['error'] == 'missing_act_field'
    assert out['field'] == 'body_text'


def test_an_ordinary_act_is_unchanged_by_the_reply_branch():
    provider, api = _provider()

    provider.propose_act('d-1', 's-1', {
        'kind': 'email', 'to': 'ruby@example.com', 'subject': 'Hello',
        'body_text': 'Hi Ruby'}, 'k-3')

    (_name, _deal, _step, payload, _key) = api.calls[0]
    assert payload['to'] == 'ruby@example.com'
    assert 'in_reply_to' not in payload


# ---------------------------------------------------------------------------
# The dismiss door
# ---------------------------------------------------------------------------
def test_dismissing_a_reply_carries_the_reason():
    provider, api = _provider()

    out = provider.dismiss_reply('d-1', 's-1', 'msg-1',
                                 {'reason': 'An out-of-office autoresponder.'},
                                 'k-4')

    assert out['ok'] is True
    (_name, _deal, _step, reply_id, payload, _key) = api.calls[0]
    assert reply_id == 'msg-1'
    assert payload == {'reason': 'An out-of-office autoresponder.'}


def test_a_dismissal_without_a_reason_never_leaves_the_harness():
    provider, api = _provider()

    assert provider.dismiss_reply('d-1', 's-1', 'msg-1', {}, 'k-5')['error'] \
        == 'missing_dismissal_field'
    assert provider.dismiss_reply('d-1', 's-1', '', {'reason': 'spam'}, 'k-6')[
        'error'] == 'missing_reply_id'
    assert provider.dismiss_reply('d-1', 's-1', 'msg-1',
                                  {'reason': 'spam', 'kind': 'email'}, 'k-7')[
        'error'] == 'invalid_dismissal_fields'
    assert api.calls == []


# ---------------------------------------------------------------------------
# The compaction whitelist
# ---------------------------------------------------------------------------
_SERVER = {
    'ok': True,
    'deal': {'id': 'd-1'},
    'current_step': {'id': 's-1', 'state': 'agent_working'},
    'step_thread': {'messages': [], 'unread_from_person': 0},
    'owed_replies': [{'id': 'msg-1', 'from': 'steven@stevenochs.com',
                      'snippet': 'Can we adjust the time?'}],
    'acts': [{'act_id': 'ap-1', 'state': 'sent_back',
              'note': 'we need the time to be 11-1130'}],
    'drafts_sent_back': [{'approval_id': 'ap-1',
                          'sent_back_reason': 'we need the time to be 11-1130'}],
    'inbound_replies': [],
}


def test_the_current_step_whitelist_keeps_the_three_new_keys():
    provider, _api = _provider(current=_SERVER)

    out = provider.current_step('d-1')

    assert out['owed_replies'][0]['id'] == 'msg-1'
    assert out['acts'][0]['note'] == 'we need the time to be 11-1130'
    assert out['drafts_sent_back'][0]['sent_back_reason'] == \
        'we need the time to be 11-1130'


def test_the_check_in_reply_keeps_them_too():
    provider, _api = _provider(checkin=dict(_SERVER, work_pulse={'id': 'p-1'}))

    out = provider.post_check_in('d-1', {
        'changed': 'x', 'now': 'y', 'next': 'z', 'progress_percent': 25}, 'k-8')

    assert out['owed_replies'][0]['id'] == 'msg-1'
    assert out['acts'][0]['state'] == 'sent_back'
    assert out['drafts_sent_back'][0]['approval_id'] == 'ap-1'


def test_the_keys_are_always_present_even_when_empty():
    provider, _api = _provider(current={'ok': True, 'current_step': {}})

    out = provider.current_step('d-1')

    assert out['owed_replies'] == []
    assert out['acts'] == []
    assert out['drafts_sent_back'] == []


# ---------------------------------------------------------------------------
# The idle-step fingerprint
# ---------------------------------------------------------------------------
def _payload(**over):
    base = {'deal': {'id': 'd-1'},
            'current_step': {'id': 's-1', 'state': 'agent_working'},
            'step_thread': {'messages': [], 'unread_from_person': 0},
            'acts': [{'act_id': 'ap-1', 'state': 'pending', 'note': None}],
            'owed_replies': [],
            'drafts_sent_back': []}
    base.update(over)
    return base


def test_a_send_back_changes_the_fingerprint():
    """This is the whole defect: pressing Send back changed nothing the memo
    could see, so the model was never re-dispatched."""
    before = cli._deal_step_fingerprint(_payload())
    after = cli._deal_step_fingerprint(_payload(
        acts=[{'act_id': 'ap-1', 'state': 'sent_back',
               'note': 'we need the time to be 11-1130'}]))

    assert before != after


def test_a_changed_reason_on_the_same_act_changes_the_fingerprint():
    """A redraft is driven by the WORDS, not just by the state flipping."""
    first = cli._deal_step_fingerprint(_payload(
        acts=[{'act_id': 'ap-1', 'state': 'sent_back', 'note': 'too early'}]))
    second = cli._deal_step_fingerprint(_payload(
        acts=[{'act_id': 'ap-1', 'state': 'sent_back', 'note': 'make it 11-1130'}]))

    assert first != second


def test_a_landing_reply_changes_the_fingerprint():
    before = cli._deal_step_fingerprint(_payload())
    after = cli._deal_step_fingerprint(_payload(
        owed_replies=[{'id': 'msg-1', 'answered_at': None}]))

    assert before != after
    answered = cli._deal_step_fingerprint(_payload(
        owed_replies=[{'id': 'msg-1', 'answered_at': '2026-09-03T04:02:00Z'}]))
    assert answered != after


def test_an_unchanged_step_still_fingerprints_the_same():
    assert cli._deal_step_fingerprint(_payload()) == \
        cli._deal_step_fingerprint(_payload())


# ---------------------------------------------------------------------------
# The dispatch and the allowlists
# ---------------------------------------------------------------------------
def test_the_deal_step_instruction_puts_the_owed_reply_first():
    text = cli._DEAL_STEP_INSTRUCTION

    assert text.startswith('If owed_replies is non-empty')
    assert 'reply_owed' in text
    assert 'in_reply_to' in text
    assert 'dismiss_reply' in text
    assert 'sent_back carries the person' in text


def test_draft_sent_back_has_its_own_dispatch_not_the_fallback():
    """v0.16.0's unknown-kind fallback hands over four instructions and every
    tool at once -- capability, but no aim."""
    entry = cli._OBLIGATION_DISPATCH['draft_sent_back']

    assert 'DEAD' in entry['instruction']
    assert 'never re-file the same words' in entry['instruction'] \
        or 'do NOT re-file the same words' in entry['instruction']
    assert 'toll_bench.propose_act' in entry['tools']
    assert 'toll_bench.current_step' in entry['tools']
    assert 'draft_sent_back' in cli._OBLIGATION_PRIORITY


def test_the_deal_step_dispatch_can_call_both_new_doors():
    tools = cli._OBLIGATION_DISPATCH['deal_step']['tools']

    assert 'toll_bench.propose_act' in tools
    assert 'toll_bench.dismiss_reply' in tools


def test_every_agent_may_call_both_doors():
    """The last two holes on 2026-09-03 were a tool in the registry and in no
    agent's runtime.tools allowlist, so no fleet agent could ever call it."""
    files = sorted(glob.glob(os.path.join(ROOT, 'agents', '*', 'agent.yaml')))
    if not files:
        pytest.skip('the private fleet configs are stripped from the public tree')
    for path in files:
        with open(path) as handle:
            config = yaml.safe_load(handle) or {}
        tools = ((config.get('runtime') or {}).get('tools')) or []
        if 'toll_bench.propose_act' not in tools:
            continue
        assert 'toll_bench.dismiss_reply' in tools, path


def test_the_tool_is_registered_and_carries_the_law():
    """A capability that is not on the tool list does not exist."""
    from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

    registry = add_toll_bench_tools(build_standard_registry())
    tools = {definition.name: definition for definition in registry.definitions()}

    assert 'toll_bench.dismiss_reply' in tools
    dismiss = tools['toll_bench.dismiss_reply']
    assert 'reply_owed' in dismiss.description
    assert 'Never dismiss a real question' in dismiss.description
    act = tools['toll_bench.propose_act']
    assert 'in_reply_to' in json.dumps(act.input_schema)
    assert 'in_reply_to' in act.description
