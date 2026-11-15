"""Offline behavioral tests; model/provider replies are explicit doubles."""
from __future__ import annotations
import copy
import json
import math
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.evidence_discovery_agent import EvidenceDiscoveryAgent, EvidenceDiscoveryValidationError
from agents.strategic_inference_agent import StrategicInferenceAgent
from services.hypothesis_evidence_service import HypothesisEvidenceService, attach_research, claim_fingerprint
from services.research_orchestrator_service import ResearchOrchestratorService as Orchestrator
from services.forecast_store import ForecastStore

ASOF = '2026-10-04T12:00:00+00:00'
CANDIDATE = {
    'insight_id': 'INSIGHT-TEST', 'insight_revision_id': 'INSIGHTREV-TEST',
    'insight_type': 'prospective',
    'insight': 'Grid access may shift bargaining power toward existing connected data centers.',
    'premises': [{'premise_id': 'P1', 'text': 'New compute investment needs power.', 'evidence_ids': ['EVID-INITIAL']}],
    'mechanism': {'description': 'Interconnection delays constrain new supply despite strong demand.'},
    'supporting_evidence_ids': ['EVID-INITIAL'], 'alternative_explanations': [],
    'metadata': {'finding_tier': 'hidden_insight'},
}
INITIAL = {'evidence_id': 'EVID-INITIAL', 'source_id': 'SOURCE-INITIAL', 'event_id': 'EVENT-INITIAL',
           'text': 'Acme deployed a compute facility.', 'evidence_type': 'source_excerpt'}
PLAN = [
    {'mode': 'counterevidence', 'query': 'grid interconnection delays resolved capacity surplus'},
    {'mode': 'alternative_explanation', 'query': 'data center location decisions competing cost drivers'},
    {'mode': 'feasibility', 'query': 'electricity grid connection queue capacity'},
    {'mode': 'analogical_mechanism', 'query': 'historical power bottleneck factory expansion outcome'},
]
BODY = 'Grid operator Delta manages interconnections.\n\n' + ('Background paragraph. ' * 120) + '\n\nNew grid connections require four years of upgrades.'
HISTORY = 'In 2020, the transmission bottleneck delayed factory expansion.\n\nSeveral factories opened two years later after grid upgrades.'

class Model:
    model_name = 'offline-model'
    timeout_seconds = 30
    def __init__(self, mode='valid'):
        self.mode = mode
        self.prompts = []
    def ask(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        if kwargs['metadata']['task'] == 'hypothesis_query_plan':
            return json.dumps({'queries': copy.deepcopy(PLAN)})
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        if self.mode == 'malformed':
            return '{}'
        if self.mode == 'irrelevant':
            return json.dumps({'review_complete': True, 'records': []})
        if payload['source_text'] == HISTORY:
            row = {'quote': HISTORY.split('\n\n')[0], 'claim_kind': 'reported_claim', 'relation': 'supports',
                   'connection': 'Power constraints delayed capacity.', 'historical_case': {
                       'year': 2020, 'date_quote': HISTORY.split('\n\n')[0],
                       'outcome_quote': HISTORY.split('\n\n')[1], 'similarity': 'Power-dependent expansion',
                       'difference': 'Factories are not compute facilities'}}
        else:
            row = {'quote': 'New grid connections require four years of upgrades.',
                   'claim_kind': 'reported_claim', 'relation': 'weakens' if self.mode == 'counter' else 'supports',
                   'connection': 'Power connection is a dependency.', 'historical_case': None}
        if self.mode == 'fabricated':
            row['quote'] = 'Invented evidence absent from the source.'
        return json.dumps({'review_complete': True, 'records': [row]})

class Search:
    def __init__(self, mode='empty'):
        self.mode = mode
        self.calls = []
    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.mode == 'error':
            raise TimeoutError('offline failure')
        results = []
        if self.mode in ('context', 'partial') and query == PLAN[2]['query']:
            results = [{'url': 'https://grid.example/report', 'title': 'Grid constraints', 'published_at': '2026-10-01T00:00:00Z'}]
        if self.mode == 'context' and query == PLAN[3]['query']:
            results = [{'url': 'https://history.example/report', 'title': 'Capacity history', 'published_at': '2022-01-01T00:00:00Z'}]
        return {'results': results, 'provider_stats': {'fixture': {'status': 'failed' if self.mode == 'partial' else 'ok'}}}

class Fetch:
    def fetch(self, url, **kwargs):
        return {'canonical_url': url, 'text_content': HISTORY if 'history' in url else BODY,
                'metadata': {'content_was_truncated': False}}

def service(mode='empty', model_mode='valid'):
    search, model = Search(mode), Model(model_mode)
    return HypothesisEvidenceService(web_search_service=search, source_fetch_service=Fetch(), llm_service=model), search, model

def investigate(svc):
    return svc.investigate(candidate=CANDIDATE, evidence=[INITIAL], blueprint={}, as_of=ASOF)

class ResearchTests(unittest.TestCase):
    def test_completed_no_alternative_is_valid(self):
        svc, search, _ = service()
        receipt = investigate(svc)
        self.assertEqual(receipt['assessment']['status'], 'completed_none_found')
        result = attach_research(CANDIDATE, [receipt])
        self.assertEqual(result['alternative_explanations'], [])
        self.assertEqual(result['metadata']['hypothesis_research']['status'], 'completed_none_found')
        self.assertEqual(len(search.calls), 4)

    def test_failure_is_not_absence(self):
        for mode in ('error', 'partial'):
            svc, _, _ = service(mode)
            receipt = investigate(svc)
            self.assertEqual(receipt['assessment']['status'], 'incomplete')
            self.assertNotIn('none were established', receipt['assessment']['note'])

    def test_dependency_query_and_history_are_not_anchored_to_ai(self):
        svc, search, model = service('context')
        receipt = investigate(svc)
        self.assertEqual(len(receipt['evidence']), 2)
        self.assertEqual(len(receipt['history']['records']), 1)
        self.assertTrue(any(q == PLAN[2]['query'] for q, _ in search.calls))
        self.assertTrue(all('artificial intelligence' not in q for q, _ in search.calls))
        historical = next(kw for q, kw in search.calls if q == PLAN[3]['query'])
        self.assertIn('from_date', historical)
        self.assertLess(historical['to_date'], ASOF)
        self.assertTrue(all(e['event_id'] == '' and e['analytical_context_only'] for e in receipt['evidence']))
        self.assertIn(BODY, [json.loads(p.rsplit('\n',1)[1])['source_text'] for p,k in model.prompts if k['metadata']['task'] == 'hypothesis_document_review'])
        self.assertEqual(len(receipt['source_documents']), 2)

    def test_counterevidence_is_determined_by_document_not_query_intent(self):
        svc, _, _ = service('context', 'counter')
        result = investigate(svc)
        self.assertEqual(result['assessment']['status'], 'completed_found')
        self.assertEqual(len(result['assessment']['found_evidence_ids']), 1)

    def test_bad_quotes_and_malformed_review_are_incomplete(self):
        for mode in ('fabricated', 'malformed'):
            svc, _, _ = service('context', mode)
            result = investigate(svc)
            self.assertEqual(result['assessment']['status'], 'incomplete')
            self.assertEqual(result['evidence'], [])

    def test_irrelevance_is_reviewed_not_a_transport_failure(self):
        svc, _, _ = service('context', 'irrelevant')
        result = investigate(svc)
        self.assertEqual(result['assessment']['status'], 'completed_none_found')
        self.assertEqual(result['evidence'], [])

    def test_modified_claim_cannot_reuse_old_success(self):
        svc, _, _ = service()
        receipt = investigate(svc)
        changed = copy.deepcopy(CANDIDATE)
        changed['insight'] = 'An unrelated consequence will happen.'
        out = attach_research(changed, [receipt])
        self.assertEqual(out['metadata']['hypothesis_research']['status'], 'incomplete')

    def test_full_source_required_and_date_enforced(self):
        svc, _, _ = service()
        source = {'canonical_url': 'https://grid.example/report', 'text_content': BODY}
        now = datetime.fromisoformat(ASOF)
        context = {'search_result': {'published_at': ASOF}, 'query': 'grid'}
        for extra in ({'metadata': {'limited_context': True}}, {'metadata': {'content_was_truncated': True}},
                      {'text_content': 'x' * 48001}, {'published_at': '2027-01-01T00:00:00Z'}):
            with self.assertRaises(ValueError):
                svc._process_source(source={**source, **extra}, context=context, candidate=CANDIDATE, now=now, timeout=20)
        with self.assertRaises(ValueError):
            svc._process_source(source=source, context={'search_result': {}}, candidate=CANDIDATE, now=now, timeout=20)
        with self.assertRaises(ValueError):
            svc._process_source(source=source, context={**context, 'to_date':'2025-01-01'}, candidate=CANDIDATE, now=now, timeout=20)

    def test_budget_timeout_does_not_expire_at_first_network_timeout(self):
        authorizations=[]
        budget_service=SimpleNamespace(remaining_discretionary_seconds=lambda:70,
            authorize=lambda **kw: authorizations.append(kw) or {'allowed':True})
        agent = EvidenceDiscoveryAgent(web_search_service=Search(), source_fetch_service=Fetch(), evidence_processor=lambda **kw: {}, budget_service=budget_service)
        with patch('agents.evidence_discovery_agent.time.monotonic', return_value=20):
            budget = agent._operation_budget(action='source_fetch', local_timeout=12, total_timeout=90, started_at=0, metadata={})
        self.assertTrue(budget.allowed)
        self.assertEqual(budget.timeout_seconds, 12)
        self.assertEqual(authorizations[0]['expected_seconds'],12)

    def test_external_search_disabled_no_model_or_provider_call(self):
        svc, search, model = service()
        out = svc.investigate(candidate=CANDIDATE, evidence=[], blueprint={'data_policy': {'allow_external_search': False}}, as_of=ASOF)
        self.assertEqual(out['assessment']['status'], 'incomplete')
        self.assertFalse(search.calls or model.prompts)

    def test_search_payload_failure_not_empty_success(self):
        agent = EvidenceDiscoveryAgent(web_search_service=lambda **kw: {'error':'provider unavailable'}, source_fetch_service=Fetch(), evidence_processor=lambda **kw: {})
        with self.assertRaises(EvidenceDiscoveryValidationError):
            agent._invoke_search(query='grid', max_results=2, timeout_seconds=5)

class IntegrationTests(unittest.TestCase):
    def make_orchestrator(self, svc, new_candidates=None, fail=False):
        obj = object.__new__(Orchestrator)
        obj.hypothesis_evidence_service = svc
        calls = []
        def analyze(**kwargs):
            calls.append(kwargs)
            if fail:
                raise TimeoutError()
            return {'insights': copy.deepcopy(new_candidates if new_candidates is not None else [CANDIDATE])}
        obj.strategic_inference_agent = SimpleNamespace(analyze=analyze)
        obj.execution_budget_service = None
        return obj, calls

    def run_research(self, obj):
        state = {'warnings': []}
        out = obj._research_hypotheses(topic='artificial intelligence', candidates=[copy.deepcopy(CANDIDATE)],
            inference_result={'insights':[CANDIDATE]}, events=[], evidence=[INITIAL], graph_context={}, constraints={},
            blueprint={}, state=state)
        return out, state

    def test_actual_discovery_through_orchestrator_feeds_history_and_reassesses(self):
        svc, _, _ = service('context')
        obj, calls = self.make_orchestrator(svc)
        (candidates, evidence, _), state = self.run_research(obj)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(evidence), 3)
        self.assertEqual(len(calls[0]['historical_events']['records']), 1)
        self.assertEqual(len(calls[0]['evidence']), 3)
        self.assertEqual(candidates[0]['alternative_explanations'], [])
        self.assertEqual(state['historical_context']['records'][0]['historical_case']['year'], 2020)
        self.assertEqual(len(calls[0]['current_events']), 0)

    def test_none_found_does_not_need_rewrite_or_drop_candidate(self):
        svc, _, _ = service()
        obj, calls = self.make_orchestrator(svc)
        (candidates, _, _), _ = self.run_research(obj)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(calls)
        report = obj._build_report(topic='artificial intelligence', run_id='RUN-TEST', depth='deep', events=[],
                                   evidence=[INITIAL], constraints={}, graph_context={}, insights=candidates, grounding={})
        self.assertEqual(len(report['hidden_insights']), 1)
        self.assertEqual(report['counterevidence_and_alternative_explanations'][0]['status'], 'completed_none_found')

    def test_new_counterevidence_cannot_fall_back_to_unreviewed_thesis(self):
        svc, _, _ = service('context', 'counter')
        obj, _ = self.make_orchestrator(svc, fail=True)
        (candidates, evidence, _), state = self.run_research(obj)
        self.assertEqual(candidates, [])
        self.assertEqual(len(evidence), 3)
        self.assertEqual(state['warnings'][-1]['type'], 'hypothesis_reassessment_failed')

    def test_valid_reassessment_abstention_is_preserved(self):
        svc, _, _ = service('context', 'counter')
        obj, _ = self.make_orchestrator(svc, new_candidates=[])
        (candidates, _, _), _ = self.run_research(obj)
        self.assertEqual(candidates, [])

    def test_no_generic_alternatives_are_manufactured(self):
        self.assertEqual(Orchestrator._strategic_candidate_alternative_texts({}), [])

    def test_context_flags_and_long_citations_survive_synthesis_compaction(self):
        agent = object.__new__(StrategicInferenceAgent)
        record = {**INITIAL, 'id': 'EVID-CONTEXT', 'event_id':'', 'text':'X'*1200,
                  'source_excerpt':'X'*1200, 'analytical_context_only':True, 'evidence_role':'counterevidence',
                  'historical_case': {'year':2020}, 'structured_payload': {'connection_is_inference':True}}
        compact = agent._compact_evidence_for_synthesis(record=record, priority_score=0)
        self.assertTrue(compact['analytical_context_only'])
        self.assertEqual(compact['evidence_role'], 'counterevidence')
        self.assertEqual(len(compact['claim']), 1200)
        self.assertEqual(compact['historical_case']['year'], 2020)

    def test_refinement_retains_research_instructions(self):
        agent = object.__new__(StrategicInferenceAgent)
        payload = {'target_insight_ids':['INSIGHT-TEST'], 'target_insights':[CANDIDATE],
                   'instruction':'weigh the new history', 'hypothesis_research':[{'assessment':{'status':'completed_none_found'}}]}
        result = agent._normalize_refinement_context(payload, {})
        self.assertEqual(result['instruction'], payload['instruction'])
        self.assertEqual(result['hypothesis_research'], payload['hypothesis_research'])

    def test_retrieved_counterevidence_survives_top_k_in_actual_prompt(self):
        agent = object.__new__(StrategicInferenceAgent)
        agent._strategic_model_used_this_run = 'offline-model'
        captured = {}
        def call_json(**kwargs):
            captured.update(kwargs)
            return {}
        agent._call_json = call_json
        records = [{**INITIAL, 'id':f'EVID-HIGH{i}', 'event_id':f'EVENT-{i}', 'source_id':f'SOURCE-{i}'} for i in range(30)]
        contrary = {'id':'EVID-COUNTER-LAST', 'evidence_id':'EVID-COUNTER-LAST', 'event_id':'',
                    'source_id':'SOURCE-GRID', 'text':'The grid bottleneck was resolved.',
                    'analytical_context_only':True, 'evidence_role':'counterevidence',
                    'discovery_metadata':{'query_id':'QUERY-X'}, 'historical_case':{'year':2020}}
        records.append(contrary)
        agent._generate_synthesis(research_question='artificial intelligence', evidence_records=records,
            evidence_review={'annotations':[{'evidence_id':r['id'], 'priority_score':100-i} for i,r in enumerate(records)]},
            auxiliary_context={}, reasoning_guidance={'desired_depth':'deep','max_candidate_insights':1},
            normalized_refinement={'hypothesis_research':[{'status':'completed_found'}]}, previous_report={},
            event_ids=[], constraint_ids=[], active_budget=None, execution_stage='refinement')
        prompt=captured['prompt']
        self.assertIn('EVID-COUNTER-LAST',prompt)
        self.assertIn('"evidence_role":"counterevidence"',prompt)
        self.assertIn('"historical_case":{"year":2020}',prompt)
        self.assertIn('completed_found',prompt)

    def test_data_policy_denial_overrides_discovery_default(self):
        svc, search, model = service()
        result=svc.investigate(candidate=CANDIDATE,evidence=[],blueprint={
            'data_policy':{'allow_external_search':False},
            'evidence_discovery_policy':{'external_search_enabled':True}},as_of=ASOF)
        self.assertFalse(search.calls or model.prompts)
        self.assertEqual(result['assessment']['status'],'incomplete')

class ForecastTests(unittest.TestCase):
    def candidate(self):
        candidate = copy.deepcopy(CANDIDATE)
        deadline = (datetime.now(timezone.utc)+timedelta(days=10)).isoformat()
        candidate['metadata'].update({'forecast_contract': {'question':'Will an upgrade open by deadline?',
             'deadline':deadline, 'resolution_criteria':'Official notice confirming the named upgrade is operational.'},
             'likelihood_assessment': {'numeric_probability':0.7, 'numeric_basis':'Explicit quantified support', 'probability_evidence_ids':['EVID-INITIAL']}})
        return candidate

    def test_forecast_is_frozen_resolved_and_brier_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ForecastStore(Path(tmp)/'forecasts.sqlite3')
            candidate = self.candidate()
            saved = store.register(run_id='RUN-TEST', candidate=candidate)
            same = store.register(run_id='RUN-TEST', candidate=candidate)
            self.assertEqual(saved['forecast_id'], same['forecast_id'])
            self.assertEqual(len(store.list()),1)
            score = store.resolve(saved['forecast_id'], outcome=1, evidence_url='https://grid.example/notice', evidence_note='The official notice confirms the defined outcome.')
            self.assertAlmostEqual(score, .09)
            with self.assertRaises(sqlite3.IntegrityError):
                store.resolve(saved['forecast_id'], outcome=1, evidence_url='https://grid.example/notice', evidence_note='duplicate')

    def test_no_negative_resolution_before_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ForecastStore(Path(tmp)/'f.db')
            saved = store.register(run_id='RUN-TEST', candidate=self.candidate())
            with self.assertRaises(ValueError):
                store.resolve(saved['forecast_id'], outcome=0, evidence_url='https://grid.example/notice', evidence_note='Not yet observed')

    def test_qualitative_forecast_not_given_a_fake_probability(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ForecastStore(Path(tmp)/'f.db')
            candidate=self.candidate()
            candidate['metadata']['likelihood_assessment']={'label':'moderate'}
            saved=store.register(run_id='RUN-TEST',candidate=candidate)
            self.assertFalse(saved['scorable'])
            self.assertIsNone(saved['probability'])
            with self.assertRaises(ValueError):
                store.resolve(saved['forecast_id'],outcome=1,evidence_url='https://example.com/proof',evidence_note='Proof')

    def test_invalid_numeric_probability_is_never_scored(self):
        for probability in (float('nan'), float('inf'), True, 1.1, -0.1, '0.7'):
            candidate=self.candidate()
            candidate['metadata']['likelihood_assessment']['numeric_probability']=probability
            self.assertIsNone(ForecastStore.contract(candidate)['probability'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
