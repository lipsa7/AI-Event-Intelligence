import pytest
from services import strategic_hypothesis_lab as lab


def item():
    return {'id':'H1','thesis':'A reporting delay is possible.','mechanism':'Access to logs constrains discovery.',
            'leverage':{'holder':'Vendor A','counterparty':'Agency B','control_point':'internal agent logs',
                        'dependency':'discovering an incident','outside_option':'Independent audit takes six months.',
                        'holder_dependence':'A small government contract.'}}


def grounded(control='supported',dependency='inference_supported'):
    states={'THESIS':'inference_supported','MECH':'inference_supported','CONTROL':control,
            'DEPENDENCY':dependency,'ALTERNATIVES':'inference_supported','ASYMMETRY':'inference_supported'}
    return [{'claim_id':'LAB-H1-'+k,'grounding_status':v} for k,v in states.items()]


def test_control_and_dependency_are_separate_atomic_checks():
    claims={c['claim_id']:c for c in lab.grounding_claims(item(),['E1','E2'])}
    assert claims['LAB-H1-CONTROL']['epistemic_type']=='fact'
    assert 'depends' not in claims['LAB-H1-CONTROL']['claim_text']
    assert claims['LAB-H1-DEPENDENCY']['epistemic_type']=='connection'
    assert claims['LAB-H1-DEPENDENCY']['evidence_ids']==['E1','E2']
    assert 'LAB-H1-LEVERAGE' not in claims


def test_supported_dependency_inference_can_pass_without_verbatim_conclusion():
    result=lab.finalize(item(),grounded(),[])
    assert result['leverage_test']=='passed'
    assert result['field_checks']['leverage']=='inference_supported'


@pytest.mark.parametrize('control,dependency',[
    ('supported','unsupported'),('supported','partially_supported'),
    ('supported','contradicted'),('inference_supported','supported'),
    ('unsupported','inference_supported'),('contradicted','supported')])
def test_unsupported_components_never_pass(control,dependency):
    assert lab.finalize(item(),grounded(control,dependency),[])['leverage_test']!='passed'


def test_missing_dependency_cannot_borrow_legacy_success():
    claims=[c for c in grounded() if not c['claim_id'].endswith('-DEPENDENCY')]
    claims.append({'claim_id':'LAB-H1-LEVERAGE','grounding_status':'supported'})
    assert lab.finalize(item(),claims,[])['leverage_test']!='passed'


def test_incomplete_fields_do_not_generate_an_invented_dependency():
    value=item();value['leverage']['control_point']=''
    claims=lab.grounding_claims(value,['E1'])
    assert not any(c['claim_id'].endswith(('-CONTROL','-DEPENDENCY')) for c in claims)
    assert lab.finalize(value,grounded(),[])['leverage_test']!='passed'


def test_split_does_not_override_contradicted_core_thesis():
    claims=grounded();claims[0]['grounding_status']='contradicted'
    assert lab.finalize(item(),claims,[])['status']=='promising_unverified'
