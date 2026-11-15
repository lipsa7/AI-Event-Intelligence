import csv,json
from services.experiment_controls import problems,snapshot
import compare_runs as compare

def test_model_changes_and_checkpoint_edits_refuse_comparison():
    a=dict(control_signature='x',controls_stable=True,replay_input_hash='a')
    assert problems(a,dict(a)) == []
    assert 'different code or model settings' in problems(a,dict(a,control_signature='y'))
    assert 'different checkpoint contents' in problems(a,dict(a,replay_input_hash='b'))
    assert problems(a,dict(a,controls_stable=False))

def test_control_capture_does_not_read_or_emit_secrets():
    first=snapshot(environ={'GEMINI_API_KEY':'secret','GEMINI_BRIEFING_MODEL':'a'})
    second=snapshot(environ={'GEMINI_API_KEY':'another','GEMINI_BRIEFING_MODEL':'a'})
    assert first == second and 'secret' not in json.dumps(first)
    assert first['signature'] != snapshot(environ={'GEMINI_BRIEFING_MODEL':'b'})['signature']

def report(rid,evidence):
    return dict(report_id=rid,run_id=rid,sections={'insight_evaluation':{'items':[
        dict(ref='x',text='Same conclusion with potentially different support.',kind='analysis',evidence=evidence)]},
        'analysis_metadata':{'organisation_profile':{'used':rid=='B'}}})

def test_blind_rating_has_evidence_without_merging_different_support(tmp_path):
    csv_path=tmp_path/'ratings.csv';key_path=tmp_path/'key.json'
    assert compare.blind_export([report('A',['Fact A']),report('B',['Fact B'])],csv_path,key_path,seed=1) == 2
    rows=list(csv.reader(csv_path.open(encoding='utf-8-sig')))
    assert rows[0][-2:] == ['source excerpts','limitations / assumptions']
    assert {r[-2] for r in rows[1:]} == {'Fact A','Fact B'}
    assert all(r[2] == '' for r in rows[1:])

def test_blind_import_refuses_a_mismatched_row_text(tmp_path):
    cp=tmp_path/'r.csv';kp=tmp_path/'k.json';target=tmp_path/'stored.jsonl'
    compare.blind_export([report('A',['Fact A'])],cp,kp,seed=1)
    rows=list(csv.reader(cp.open(encoding='utf-8-sig')));rows[1][1]='Changed or stale text';rows[1][2]='useful'
    with cp.open('w',encoding='utf-8-sig',newline='') as stream:csv.writer(stream).writerows(rows)
    saved,errors=compare.blind_import(cp,kp,target)
    assert saved == 0 and errors
