from tests.unit.test_market_watch import _breaker_resources
from toll_harness import cli
from toll_harness.toll_bench.draft import DraftLoop


def test_completed_feedback_runs_once_and_changed_feedback_wakes_again(monkeypatch):
    monkeypatch.setattr(cli, '_OBLIGATION_FAILURES', {})
    obligation = dict(kind='feedback_returned', target_id='spin-target',
                      proposal_id='spin-bid',
                      feedback={'reason': 'Do all the work',
                                'given_at': '2026-09-09'})
    goals=[]
    resources=_breaker_resources(obligation,goals=goals)
    assert cli._process_market_attention(resources,0)['run'] is not None
    for _ in range(10):
        assert cli._process_market_attention(resources,0)['run'] is None
    assert len(goals)==1
    obligation['feedback']={'reason':'Use a different approach','given_at':'2026-09-10'}
    assert cli._process_market_attention(resources,0)['run'] is not None
    assert len(goals)==2
    other=_breaker_resources(obligation,goals=[])
    assert cli._process_market_attention(other,0)['run'] is not None

def test_completed_feedback_does_not_starve_other_obligations(monkeypatch):
    monkeypatch.setattr(cli, '_OBLIGATION_FAILURES', {})
    first=dict(kind='feedback_returned',target_id='first',proposal_id='bid-1')
    second=dict(kind='feedback_returned',target_id='second',proposal_id='bid-2')
    goals=[]
    resources=_breaker_resources(first,goals=goals)
    resources.toll_bench.attention=lambda wait: {'attention':[first,second]}
    cli._process_market_attention(resources,0)
    cli._process_market_attention(resources,0)
    assert len(goals)==2
    assert cli._process_market_attention(resources,0)['attention_count']==0

def test_failed_feedback_still_uses_failure_breaker(monkeypatch):
    monkeypatch.setattr(cli, '_OBLIGATION_FAILURES', {})
    obligation=dict(kind='feedback_returned',target_id='failed',proposal_id='bid-3')
    resources=_breaker_resources(obligation,failure={'error':'temporary'})
    for i in range(2):
        assert cli._process_market_attention(resources,0)['breaker']['consecutive_failures']==i+1

def test_the_same_problem_named_three_times_stops_the_draft():
    """THE GUARD IS THE PROBLEM'S NAME, NOT THE DRAFT'S TEXT (2026-09-11).

    It used to hash the whole draft, so a model that REWORDED the same bad
    field looked like progress and the guard never tripped -- the document
    changed every round while the bench named the same field every round. On
    2026-09-11 GPT-6 Astra died that way on `research_links[0].plan_use` and
    three fleet units burned the full 200-round ceiling on one want. The
    value below is DIFFERENT every round and the draft still stops, because
    the bench keeps naming the same (path, code). The bench counts the same
    way (plan_form.count_tries, three tries per problem), so the two agree.
    """
    loop=DraftLoop(None,None)
    answer=dict(ok=True,remaining=2,draft={},next_fix={'path':'pitch_title','code':'REJ-1','current':'bad'},rounds={'left':100})
    calls=[]
    reworded=iter(['bad in other words','bad again, differently','bad, a third way'])
    loop._ask=lambda *args, **kw: {'patches':[{'path':'pitch_title','value':next(reworded)}]}
    def patch(*args):
        calls.append(args)
        return dict(answer)
    loop._patch=patch
    result=loop._answer_the_fixes('target','plan',answer,'want')
    assert result['error']=='draft_stalled'
    # Named once, answered; named twice, answered; the THIRD naming ends it,
    # so no third patch is ever sent.
    assert len(calls)==2

def test_draft_progress_is_allowed_more_than_three_rounds():
    """Not a cap on a long job: a draft that keeps clearing problems keeps
    going, and only the bench's own rounds stop it."""
    loop=DraftLoop(None,None)
    answer=dict(ok=True,remaining=8,draft={},next_fix={'path':'steps.0.do_line','code':'REJ-1'},rounds={'left':100})
    loop._ask=lambda *args, **kw: {'patches':[{'path':'pitch_title','value':'better'}]}
    def patch(*args):
        answer['remaining']-=1
        # A cleared problem is a DIFFERENT next_fix: the door names the first
        # one still standing.
        answer['next_fix']={'path':'steps.{}.do_line'.format(8-answer['remaining']),
                            'code':'REJ-1'}
        answer['ready']=answer['remaining']==0
        return dict(answer)
    loop._patch=patch
    result=loop._answer_the_fixes('target','plan',answer,'want')
    assert result['ready'] is True

def test_stalled_draft_is_skipped_until_a_new_round(monkeypatch):
    monkeypatch.setattr(cli,'_CLOSED_TARGET_MEMO',set())
    target={'target_id':'stuck','round':2}
    assert cli._remember_closed(target,'draft_stalled')
    assert cli._door_closed_this_round(target)
    assert not cli._door_closed_this_round(dict(target,round=3))
