"""RULE 219 — one act door, two kinds.

An approved email act sat unsent while an approved calendar act executed, so
the platform folded them into one door. The harness follows: propose_act takes
an email or a calendar event, validates what that kind actually needs, and
never invents a second tool for the second kind.
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


def test_an_email_act_still_goes_through_unchanged():
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {
        'kind': 'email', 'to': 'ruby@example.com', 'subject': 'Hello',
        'body_text': 'Hi Ruby', 'purpose': 'the introduction'}, 'k-1')
    assert out['ok'] is True
    (_deal, _step, payload, key) = api.calls[0]
    assert payload['kind'] == 'email' and payload['to'] == 'ruby@example.com'
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


def test_each_kind_is_held_to_its_own_words():
    provider, api = _provider()
    assert provider.propose_act('d-1', 's-1', {
        'kind': 'calendar_event', 'summary': 'Practice session 1'},
        'k-3')['error'] == 'missing_act_field'
    assert provider.propose_act('d-1', 's-1', {
        'kind': 'email', 'to': 'ruby@example.com'},
        'k-4')['error'] == 'missing_act_field'
    assert api.calls == [], 'a half-written act reached the bench'


def test_a_kind_the_door_does_not_have_is_named_not_guessed():
    provider, api = _provider()
    out = provider.propose_act('d-1', 's-1', {'kind': 'carrier_pigeon'}, 'k-5')
    assert out['error'] == 'unknown_act_kind'
    assert out['kinds'] == ['email', 'calendar_event']
    assert api.calls == []


def test_the_tool_offers_both_kinds():
    from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry
    registry = add_toll_bench_tools(build_standard_registry())
    tool = next(d for d in registry.definitions()
                if d.name == 'toll_bench.propose_act')
    assert 'calendar_event' in tool.description
    act = tool.input_schema['properties']['act']
    for field in ('summary', 'start', 'end', 'to', 'subject', 'body_text'):
        assert field in act['properties'], field
    # required-ness is per kind, so the schema asks only for the kind itself
    assert act['required'] == ['kind']
