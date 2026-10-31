from toll_harness import cli


def test_feedback_returned_is_not_a_kind_the_harness_knows():
    """Contract 3.20 (2026-09-12): the bench never sends feedback_returned;
    a proposal never changes after filing. The RAM-only completed-feedback
    guard that a restart used to forget is gone with the kind."""
    assert 'feedback_returned' not in cli._OBLIGATION_DISPATCH
    assert 'feedback_returned' not in cli._OBLIGATION_PRIORITY
    assert not hasattr(cli, '_FEEDBACK_RETURNED_INSTRUCTION')


def test_stalled_draft_is_skipped_until_a_new_round(monkeypatch):
    monkeypatch.setattr(cli,'_CLOSED_TARGET_MEMO',set())
    target={'target_id':'stuck','round':2}
    assert cli._remember_closed(target,'draft_stalled')
    assert cli._door_closed_this_round(target)
    assert not cli._door_closed_this_round(dict(target,round=3))
