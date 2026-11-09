"""Offline regression tests; no application services or network clients are started."""
import unittest
from services import actor_moves as moves
from services import insight_evaluator as judge
from services import strategic_hypothesis_lab as lab
from services.leverage_validation import result

LEV = dict(holder='Vendor', counterparty='Buyer', dependency='qualified supply',
           control_point='capacity', outside_option='Replacement requires two years of qualification',
           holder_dependence='Buyer accounts for one percent of revenue')
FACTS = [dict(fact_id='F1', text='Buyer needs qualified supply.', evidence_id='E1')]

class LeverageValidationTests(unittest.TestCase):
    def test_unknown_alternatives_never_pass_even_with_supported_checks(self):
        checks = dict.fromkeys(('leverage', 'leverage_alternatives', 'leverage_asymmetry'), 'supported')
        self.assertEqual(result(LEV, checks), 'passed')
        for unknown in ('none named in the facts', 'unknown', 'not established', 'no alternatives identified'):
            with self.subTest(unknown=unknown):
                self.assertEqual(result(dict(LEV, outside_option=unknown), checks), 'not_established')

    def test_populated_actor_fields_are_not_a_passed_test(self):
        self.assertFalse(judge._leverage_contract({'leverage': LEV})['complete'])
        self.assertTrue(judge._leverage_contract({'leverage': dict(LEV, leverage_test='passed')})['complete'])

    def actor(self):
        return dict(actor='Vendor', crux={}, powers=[dict(LEV, power='cornered_resource', benefit='allocation flexibility',
                    barrier='', fact_ids=['F1'])], rules_and_incentives=[], others=[], choice={}, must_be_true=[],
                    moves=[dict(move='Offer priority supply', why_it_benefits='preserves flexibility', fact_ids=['F1'], basis='leverage')])

    def test_actor_source_checks_control_ranking_label(self):
        actor = self.actor()
        claims = moves.claims([actor], FACTS)
        targets = {tuple(c['_target']) for c in claims}
        self.assertIn((0, 'power_alternatives', 0), targets)
        self.assertIn((0, 'power_asymmetry', 0), targets)
        for alternatives_status, expected in [('supported', True), ('unsupported', False), ('contradicted', False)]:
            with self.subTest(status=alternatives_status):
                statuses = {c['claim_id']: ('unsupported' if c['_target'][1] == 'already' else
                             alternatives_status if c['_target'][1] == 'power_alternatives' else 'supported') for c in claims}
                clean = moves.sanitize([actor], claims, statuses, FACTS)['actors'][0]
                self.assertEqual(judge._leverage_contract(clean)['complete'], expected)
                items = judge.candidates({'actor_moves': {'actors': [clean]}})
                self.assertTrue(items)
                self.assertEqual(items[0]['strategic_type'] == 'leverage_decision', expected)

    def test_lab_unknown_alternatives_are_not_checked_as_scarcity(self):
        item = dict(id='H1', thesis='Vendor can influence contract timing.', mechanism='Capacity constrains delivery.',
                    leverage=dict(LEV, outside_option='none named in the facts'), fact_ids=['F1'])
        claims = lab.grounding_claims(item, ['E1'])
        self.assertFalse(any(c['claim_id'].endswith('-ALTERNATIVES') for c in claims))
        checked = [dict(claim_id=c['claim_id'], grounding_status='supported', supporting_evidence_ids=['E1']) for c in claims]
        self.assertEqual(lab.finalize(item, checked, FACTS)['leverage_test'], 'not_established')

    def test_older_lab_reports_without_passed_test_are_not_leverage_decisions(self):
        item = dict(thesis='Vendor can influence delivery timing.', leverage=dict(LEV, complete=True),
                    supporting_evidence_ids=['E1'])
        candidate = judge.candidates({'strategic_hypotheses': {'verified': [item]}})[0]
        self.assertNotEqual(candidate['strategic_type'], 'leverage_decision')

if __name__ == '__main__':
    unittest.main()
