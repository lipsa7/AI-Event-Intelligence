"""Regression checks for the final B/E review, without network or model calls."""
import sys

from services import hypothesis_tier as tier, insight_funnel
from tests.test_plan_fix_b_focus_before_briefing import orchestrator, DEVELOPMENTS, GENERAL
from tests.test_plan_fix_e_hypotheses_to_investigate import _dashboard, hypothesis, sections


def test_early_exception_after_research_does_not_pay_for_a_second_attempt(monkeypatch):
    obj, calls = orchestrator(monkeypatch)
    original = obj._focus_research

    def fail_after_research(*args):
        original(*args)  # Fake service records a call and supplies cases; then processing fails.
        raise RuntimeError('processing failed after research')

    obj._focus_research = fail_after_research
    state = {'gap_context_records': [GENERAL]}
    obj._focus_research_before_briefing(DEVELOPMENTS, state)
    obj._focus_research_before_briefing(DEVELOPMENTS, state)
    result = obj._actor_moves(topic='ai', developments=DEVELOPMENTS, evidence=[], state=state)
    assert len(calls) == 1
    assert state['focus_research_early']['attempted'] is True
    assert result['focus_organisation']['research']['status'] == 'failed:RuntimeError'
    assert result['focus_organisation']['research']['position'] == 'before_briefing'
    assert 'case_found_after_briefing' not in state


def overflow_item():
    item = hypothesis('A')
    item['grounded_claims'][1]['unsupported_parts'] = ['Assumption '+str(i) for i in range(5)]
    item['grounded_claims'] += [
        {'claim_id': 'LAB-A-FIELD'+str(i), 'claim_text': 'Unresolved claim '+str(i),
         'grounding_status':'unsupported'} for i in range(6)]
    item['grounded_claims'][-1]['claim_text'] = 'Long qualifier ' + 'x'*300 + ' MATERIAL LIMIT AT THE END'
    item['grounded_claims'][1]['unsupported_parts'][-1] += ' ' + 'y'*300 + ' NECESSARY ASSUMPTION END'
    return item


def restore_dashboard(saved):
    for name, value in saved.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


def test_details_include_every_point_beyond_the_short_preview():
    dashboard, _, saved = _dashboard()
    try:
        item=overflow_item()
        preview='\n'.join(dashboard.investigation_lines(item))
        details='\n'.join(dashboard.investigation_extra_lines(item))
        combined=preview+'\n'+details
        for point in tier.assumptions(item)+tier.open_points(item):
            assert point in combined
        assert 'MATERIAL LIMIT AT THE END' in details
        assert 'NECESSARY ASSUMPTION END' in details
    finally:
        restore_dashboard(saved)


def test_renderer_exposes_an_explicit_remaining_count_and_expandable_details():
    dashboard, calls, saved = _dashboard()
    try:
        item=overflow_item()
        remaining=len(tier.assumptions(item)[3:])+len(tier.open_points(item)[4:])
        dashboard.render_hypotheses_to_investigate([item])
        assert ('expander', f'{remaining} more assumptions and unresolved points') in calls
        assert any('MATERIAL LIMIT AT THE END' in str(value) for method,value in calls if method=='markdown')
    finally:
        restore_dashboard(saved)


def test_short_hypothesis_does_not_get_an_empty_details_expander():
    dashboard, calls, saved = _dashboard()
    try:
        dashboard.render_hypotheses_to_investigate([hypothesis('A')])
        assert not any(method=='expander' for method,_ in calls)
    finally:
        restore_dashboard(saved)


def test_funnel_labels_display_counts_as_unrated_default_not_actual_user_layout():
    result=insight_funnel.build(sections(hypothesis('A')))
    assert result['stages']['hypothesis_lab']['display_selection_basis']=='default_unrated_selection'
    assert insight_funnel.summary(result)['hypothesis_lab']['display_selection_basis']=='default_unrated_selection'
