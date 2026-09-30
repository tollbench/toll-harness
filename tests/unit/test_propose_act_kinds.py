"""RULE 219 — one act door, two kinds.

An approved email act sat unsent while an approved calendar act executed, so
the platform folded them into one door. The harness follows: propose_act is
one tool for every kind, and never invents a second tool for the second kind.
Since 0.56.4 the kinds and the fields are the bench's: an act goes as filed.
"""
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider


class _Api:
    def __init__(self):
        self.calls = []

    def propose_act(self, deal_id, step_id, payload, idempotency_key):
        self.calls.append((deal_id, step_id, payload, idempotency_key))
        return {"ok": True, "act_id": "ap-1", "kind": payload.get("kind")}


def _provider():
    api = _Api()
    return BookOfHousesTollBenchProvider(api), api


def test_a_meeting_is_intent_only_at_the_same_door():
    """RULE 223: the agent says who, how long and roughly when. Nothing else
    reaches the wire, and the platform runs the protocol."""
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {
        'kind': 'meeting', 'with': 'Ruby@Example.com', 'with_name': 'Ruby',
        'duration_min': 30, 'window': 'next week', 'title': 'Catch up',
        'purpose': 'the call the person asked for'}, 'k-3')
    assert out['ok'] is True
    (_deal, _step, payload, key) = api.calls[0]
    assert payload['kind'] == 'meeting' and payload['with'] == 'Ruby@Example.com'
    assert payload['window'] == 'next week' and payload['duration_min'] == 30
    assert key == 'k-3'


def test_a_meeting_needs_the_invitee():
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {'kind': 'meeting', 'title': 'x'}, 'k-4')
    assert out == {'ok': False, 'error': 'missing_act_field', 'field': 'with'}
    assert api.calls == []


def test_a_meeting_never_carries_a_slot_or_a_body():
    """The harness refuses to smuggle a time or an email body onto a meeting
    act: those are the platform's, by rule."""
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {
        'kind': 'meeting', 'with': 'r@x.co', 'start': {'dateTime': 'x'},
        'body_text': 'When: Friday 11'}, 'k-5')
    assert out['ok'] is True
    payload = api.calls[0][2]
    assert 'start' not in payload and 'body_text' not in payload


def test_a_meeting_carries_the_agent_written_invite_message():
    """RULE 223 / contract 2.38: the agent writes the words that OPEN the
    invite email; the platform owns the times, the pick link and the AI
    disclosure. The message rides the meeting act."""
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {
        'kind': 'meeting', 'with': 'r@x.co',
        'message': "Hi Ruby, I'm helping the person set up a quick call."}, 'k-6')
    assert out['ok'] is True
    payload = api.calls[0][2]
    assert payload['message'].startswith('Hi Ruby')


def test_a_runaway_meeting_message_is_capped():
    provider, api = _provider()
    provider.propose_act('d-1', 's-1', {
        'kind': 'meeting', 'with': 'r@x.co', 'message': 'x' * 5000}, 'k-7')
    assert len(api.calls[0][2]['message']) == 4000


def test_an_email_act_still_goes_through_unchanged():
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {
        'kind': 'email', 'contact_ref': 'contact-ruby', 'subject': 'Hello',
        'body_text': 'Hi Ruby', 'purpose': 'the introduction'}, 'k-1')
    assert out['ok'] is True
    (_deal, _step, payload, key) = api.calls[0]
    assert payload['kind'] == 'email' and payload['contact_ref'] == 'contact-ruby'
    assert key == 'k-1'


def test_a_calendar_event_is_an_act_at_the_same_door():
    provider, api = _provider()
    start = {'dateTime': '2026-09-04T18:00:00-07:00',
             'timeZone': 'America/Los_Angeles'}
    end = {'dateTime': '2026-09-04T19:00:00-07:00',
           'timeZone': 'America/Los_Angeles'}
    out = provider.propose_act('d-1', 's-1', {
        'kind': 'calendar_event', 'summary': 'Practice session 1',
        'start': start, 'end': end, 'location': 'The studio',
        'attendees': ['ruby@example.com']}, 'k-2')
    assert out['ok'] is True
    (_deal, _step, payload, _key) = api.calls[0]
    assert payload == {'kind': 'calendar_event',
                       'summary': 'Practice session 1', 'start': start,
                       'end': end, 'location': 'The studio',
                       'attendees': ['ruby@example.com']}


def test_the_shaped_kinds_keep_their_words_and_email_is_the_bench_s():
    """calendar_event keeps its shaping; an email goes to the bench as filed
    (0.56.4): on a loop the bench fills the recipient from the item and on a
    follow-up the subject from the thread, so only the bench can say what is
    missing."""
    provider, api = _provider()
    # 0.57.1: a half-written calendar event goes to the bench too; it answers
    # in `issues`, the harness keeps no copy of the form.
    provider.propose_act('d-1', 's-1', {
        'kind': 'calendar_event', 'summary': 'Practice session 1'}, 'k-3')
    assert api.calls[0][2] == {
        'kind': 'calendar_event', 'summary': 'Practice session 1'}
    api.calls.clear()
    provider.propose_act('d-1', 's-1', {
        'kind': 'email', 'contact_ref': 'contact-ruby'}, 'k-4')
    assert api.calls[0][2] == {'kind': 'email', 'contact_ref': 'contact-ruby'}


def test_a_kind_the_harness_does_not_know_is_the_bench_s_to_name():
    """0.56.4: the kinds are the bench's (list_act_kinds). The harness kept
    its own three and refused the rest; the bench answers with its list."""
    provider, api = _provider()
    provider.propose_act('d-1', 's-1', {'kind': 'carrier_pigeon'}, 'k-5')
    assert api.calls[0][2] == {'kind': 'carrier_pigeon'}


def test_the_tool_offers_both_kinds():
    from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry
    registry = add_toll_bench_tools(build_standard_registry())
    tool = next(d for d in registry.definitions()
                if d.name == 'toll_bench.propose_act')
    assert 'calendar_event' in tool.description
    act = tool.input_schema['properties']['act']
    for field in ('summary', 'start', 'end', 'to', 'subject', 'body_text',
                  'with', 'message'):
        assert field in act['properties'], field
    # required-ness is per kind, so the schema asks only for the kind itself
    assert act['required'] == ['kind']
