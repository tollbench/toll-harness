from toll_harness import cli
from toll_harness.toll_bench.draft import DraftLoop


def test_feedback_returned_is_not_a_kind_the_harness_knows():
    """Contract 3.20 (2026-09-12): the bench never sends feedback_returned;
    a proposal never changes after filing. The RAM-only completed-feedback
    guard that a restart used to forget is gone with the kind."""
    assert 'feedback_returned' not in cli._OBLIGATION_DISPATCH
    assert 'feedback_returned' not in cli._OBLIGATION_PRIORITY
    assert not hasattr(cli, '_FEEDBACK_RETURNED_INSTRUCTION')

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
