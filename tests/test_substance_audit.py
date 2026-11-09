"""Prose can vary; evidence, uncertainty and added inference determine strategic eligibility."""
from services import insight_evaluator as ev
from services.substance_audit import judging_view

def audit(**changes):
    a=dict(plain_claim='The buyer faces a two-year switching delay.',ordinary_reading='A supplier was replaced.',
           added_inference='Qualification delays constrain near-term choices.',decision_change='Monitor qualification dates before renewal.',
           alternative_explanation='Price rather than qualification drives the choice.',
           discriminating_evidence='A qualified second supplier becoming available before renewal.',key_assumption='Qualification remains required.',
           facts_supported=True,bridge_supported=True,adds_information=True,uncertainty_preserved=True)
    return dict(a,**changes)

def score(text, substance):
    item=dict(id='I1',kind='strategic_hypothesis',text=text,strategic_type='cross_event',
              evidence=['Supplier qualification requires two years.'],leverage_complete=False)
    row=dict(id='I1',answers={k:True for k,_,_ in ev.RUBRIC},substance=substance)
    return item,ev.parse_rubric({'scores':[row]},[item])['I1']

def test_same_substance_plain_and_dramatic_versions_have_equal_scores():
    plain,one=score('Qualification delays limit the buyer.',audit())
    dramatic,two=score('An unprecedented strategic stranglehold reshapes the buyer ecosystem!',audit())
    assert one['score'] == two['score'] and one['dimensions'] == two['dimensions']
    assert judging_view(dict(plain,**one)) == judging_view(dict(dramatic,**two))
    prompt=ev.pairwise_prompt('supply',dict(plain,**one),dict(dramatic,**two))
    assert 'stranglehold' not in prompt and 'Supplier qualification' in prompt

def test_relabelling_the_same_facts_has_no_information_lift():
    _,out=score('Privatised cyber diplomacy creates quasi-sovereign CEOs.',audit(added_inference='',adds_information=False))
    assert out['dimensions']['informational_lift'] == 0 and out['score'] <= 7

def test_assumption_does_not_establish_a_causal_bridge():
    _,out=score('A caused B, assuming the missing causal bridge.',audit(bridge_supported=False))
    assert out['dimensions']['mechanism'] == 0 and out['dimensions']['informational_lift'] == 0
    assert out['substance']['status'] == 'needs_evidence'

def test_missing_audit_unsupported_facts_or_changed_uncertainty_cannot_score_as_insight():
    for a in ({},audit(facts_supported=False),audit(uncertainty_preserved=False)):
        _,out=score('Assertive but unsupported causal conclusion.',a)
        assert out['score'] <= 3 and out['dimensions']['informational_lift'] == 0

def test_no_evidence_cannot_be_validated_by_the_judges_assertion():
    from services.substance_audit import parse
    assert not parse(audit(),[])['facts_supported']
    assert not parse(audit(),[])['lift_eligible']

def test_rubric_requires_alternative_discriminator_and_preserved_uncertainty():
    item,_=score('The buyer faces a switching delay.',audit())
    prompt=ev.rubric_prompt('supply',[item])
    for field in ('ordinary_reading','alternative_explanation','discriminating_evidence','uncertainty_preserved'):
        assert field in prompt

def test_whole_evaluator_keeps_rhetorical_restatement_out_of_positive_lift():
    report={'strategic_hypotheses':{'verified':[
        {'thesis':'Qualification delays limit the buyer before renewal.', 'supporting_evidence':['Qualification takes two years.']},
        {'thesis':'Unprecedented institutional transformation creates quasi-sovereign diplomacy.',
         'supporting_evidence':['The minister contacted a CEO.']}]}}
    calls=[]
    def judge(prompt):
        calls.append(prompt)
        if '12 strict yes/no checks' in prompt:
            return {'scores':[
                {'id':'I1','answers':{k:True for k,_,_ in ev.RUBRIC},'substance':audit()},
                {'id':'I2','answers':{k:True for k,_,_ in ev.RUBRIC},
                 'substance':audit(plain_claim='The minister contacted a CEO.',added_inference='',adds_information=False)}]}
        raise AssertionError('A zero-lift restatement must not enter the pairwise tournament.')
    out=ev.evaluate(report,'supplier decision',judge)
    assert out['items'][0]['id'] == 'I1'
    assert out['items'][1]['dimensions']['informational_lift'] == 0
    assert len(calls) == 1  # audit rides in the existing rubric call
