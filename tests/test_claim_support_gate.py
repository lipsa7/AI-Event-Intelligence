"""v6.13 claim-support release gate: offline tests with explicit doubles."""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from services import claim_support_gate as gate
from services import research_orchestrator_service as orchestrator_module
from services.hypothesis_evidence_service import claim_fingerprint
from services.research_orchestrator_service import DEEP_PROFILE, ResearchOrchestratorService as Orchestrator


def claim(claim_id, kind, status, text="text", supporting=("EVID-A", "EVID-B"), contradicted=()):
    return {
        "claim_id": claim_id, "epistemic_type": kind, "grounding_status": status,
        "claim_text": text, "supporting_evidence_ids": list(supporting) if status != "unsupported" else [],
        "contradicted_evidence_ids": list(contradicted),
        "unsupported_parts": [] if status in {"supported", "inference_supported"} else [f"{text} is not in the evidence"],
    }


def grounding(*claims):
    return {"review_status": "complete", "status": gate.aggregate(claims), "claims": list(claims),
            "metadata": {"model": "offline"}}


# Shape of report REPORT-3C6F (5 Oct 2026): facts pass, conclusion half and the
# whole mechanism do not. The old aggregate released it as partially_supported.
REPORT_3C6F = grounding(
    claim("P1", "fact", "supported"),
    claim("P2", "fact", "supported"),
    claim("P3", "fact", "partially_supported", "Nvidia vertically integrated Hugging Face"),
    claim("R-MECHANISM-01", "mechanism", "unsupported", "Math skill implies vulnerability analysis"),
    claim("R-MECHANISM-02", "mechanism", "unsupported"),
    claim("R-MECHANISM-03", "mechanism", "unsupported"),
    claim("R-SECOND-ORDER-01", "second_order_effect", "unsupported"),
    claim("R-INSIGHT-01", "hidden_insight", "supported"),
    claim("R-INSIGHT-02", "hidden_insight", "unsupported", "Bottleneck shifts to hardware containment"),
)


class GateRuleTests(unittest.TestCase):
    def test_recent_report_shape_is_blocked_but_repairable(self):
        self.assertEqual(REPORT_3C6F["status"], "partially_supported")
        verdict = gate.evaluate(REPORT_3C6F)
        self.assertFalse(verdict["passed"])
        self.assertTrue(verdict["repairable"])
        self.assertEqual(set(verdict["reasons"]), {
            "premise_not_supported_by_cited_evidence",
            "mechanism_step_not_supported_by_cited_evidence",
            "conclusion_not_supported_by_cited_evidence",
        })
        self.assertEqual((verdict["core_claims_passed"], verdict["core_claims"]), (3, 8))
        self.assertIn("Bottleneck shifts to hardware containment", " ".join(gate.repair_brief(verdict)))

    def test_inference_supported_conclusion_and_mechanism_pass(self):
        verdict = gate.evaluate(grounding(
            claim("P1", "fact", "supported"),
            claim("M1", "mechanism", "inference_supported"),
            claim("I1", "hidden_insight", "inference_supported"),
        ))
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["reasons"], [])

    def test_partially_supported_premise_blocks(self):
        verdict = gate.evaluate(grounding(
            claim("P1", "fact", "partially_supported"),
            claim("M1", "mechanism", "supported"),
            claim("I1", "hidden_insight", "supported"),
        ))
        self.assertEqual(verdict["reasons"], ["premise_not_supported_by_cited_evidence"])

    def test_inference_supported_premise_blocks(self):
        # A premise is a fact: it must be stated by its evidence, not inferred.
        verdict = gate.evaluate(grounding(
            claim("P1", "fact", "inference_supported"),
            claim("I1", "hidden_insight", "supported"),
        ))
        self.assertFalse(verdict["passed"])

    def test_contradicted_core_claim_is_not_repairable(self):
        verdict = gate.evaluate(grounding(
            claim("P1", "fact", "supported"),
            claim("I1", "hidden_insight", "contradicted", contradicted=["EVID-C"]),
        ))
        self.assertFalse(verdict["repairable"])
        self.assertIn("claim_contradicted_by_cited_evidence", verdict["reasons"])

    def test_missing_or_incomplete_grounding_blocks(self):
        for value in (None, {}, {"review_status": "error", "claims": []}, {"review_status": "complete"}):
            verdict = gate.evaluate(value)
            self.assertEqual(verdict["reasons"], ["grounding_unavailable"])
            self.assertFalse(verdict["repairable"])

    def test_no_conclusion_claim_blocks(self):
        verdict = gate.evaluate(grounding(claim("P1", "fact", "supported")))
        self.assertIn("no_conclusion_claim_checked", verdict["reasons"])

    def test_unsupported_second_order_sentence_is_pruned_not_blocking(self):
        record = grounding(
            claim("P1", "fact", "supported"),
            claim("I1", "hidden_insight", "supported"),
            claim("S1", "second_order_effect", "inference_supported", "Insurers price runtime controls."),
            claim("S2", "second_order_effect", "unsupported", "Regulators mandate air gaps."),
        )
        verdict = gate.evaluate(record)
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["pruned_claim_ids"], ["S2"])
        candidate = {"insight": "x", "second_order_effect": "Insurers price runtime controls. Regulators mandate air gaps."}
        out, out_grounding, notes = gate.sanitize_for_release(candidate, record, verdict, {})
        self.assertEqual(out["second_order_effect"], "Insurers price runtime controls.")
        self.assertEqual(notes["pruned_second_order_sentences"], ["Regulators mandate air gaps."])
        self.assertEqual([c["claim_id"] for c in out_grounding["claims"]], ["P1", "I1", "S1"])
        self.assertEqual(out_grounding["status"], "inference_supported")
        self.assertEqual(out_grounding["claim_count"], 3)
        self.assertEqual(candidate["second_order_effect"], "Insurers price runtime controls. Regulators mandate air gaps.")

    def test_sanitize_refuses_a_failed_candidate(self):
        with self.assertRaises(ValueError):
            gate.sanitize_for_release({}, REPORT_3C6F, gate.evaluate(REPORT_3C6F), {})

    def test_alternative_citation_removed_unless_cited_evidence_backs_it(self):
        record = grounding(claim("P1", "fact", "supported"), claim("I1", "hidden_insight", "supported"))
        verdict = gate.evaluate(record)
        candidate = {
            "insight_revision_id": "REV",
            "alternative_explanations": [
                {"alternative_id": "ALT-1", "text": "Misconfigured ports, not novel exploits.", "evidence_ids": ["EVID-D281"]},
                {"alternative_id": "ALT-2", "text": "Backed alternative.", "evidence_ids": ["EVID-B"]},
                {"alternative_id": "ALT-3", "text": "Unsourced alternative.", "evidence_ids": []},
            ],
            "metadata": {"alternative_analysis": [
                {"alternative_id": "ALT-1", "analysis_status": "supported_alternative"},
                {"alternative_id": "ALT-2", "analysis_status": "supported_alternative"},
            ]},
        }
        self.assertEqual([c["alternative_id"] for c in gate.alternative_claims(candidate)], ["ALT-1", "ALT-2"])
        partly, _, _ = gate.sanitize_for_release(
            candidate, record, verdict, {"ALT-1": "unsupported", "ALT-2": "partially_supported"})
        # Only a source that states the alternative keeps the citation.
        self.assertEqual(partly["alternative_explanations"][1]["evidence_ids"], [])
        out, _, notes = gate.sanitize_for_release(
            candidate, record, verdict, {"ALT-1": "unsupported", "ALT-2": "supported"})
        alternatives = {a["alternative_id"]: a for a in out["alternative_explanations"]}
        self.assertEqual(alternatives["ALT-1"]["evidence_ids"], [])
        self.assertEqual(alternatives["ALT-2"]["evidence_ids"], ["EVID-B"])
        self.assertEqual(alternatives["ALT-3"]["evidence_ids"], [])
        self.assertEqual(notes["alternatives_uncited"][0]["removed_evidence_ids"], ["EVID-D281"])
        analysis = {a["alternative_id"]: a["analysis_status"] for a in out["metadata"]["alternative_analysis"]}
        self.assertEqual(analysis, {"ALT-1": "plausible_unverified_alternative", "ALT-2": "supported_alternative"})
        # Check could not run: no citation is kept unverified.
        out, _, _ = gate.sanitize_for_release(candidate, record, verdict, None)
        self.assertTrue(all(not a["evidence_ids"] for a in out["alternative_explanations"]))


# ---------------------------------------------------------------------------
# Orchestrator flow with doubles
# ---------------------------------------------------------------------------

EVIDENCE = [
    {"evidence_id": "EVID-A", "source_id": "SOURCE-A", "event_id": "EVENT-A", "text": "Fact A."},
    {"evidence_id": "EVID-B", "source_id": "SOURCE-B", "event_id": "EVENT-B", "text": "Fact B."},
]


def candidate(name, *, text=None, mechanism="A mechanism that is long enough to pass the deterministic thin-mechanism rule easily."):
    return {
        "insight_id": f"INSIGHT-{name}", "insight_revision_id": f"INSIGHTREV-{name}",
        "insight_type": "prospective", "finding_tier": "hidden_insight",
        "insight": text or (f"{name}: a conclusion sentence long enough for the deterministic release gate, "
                            "which wants more than one hundred and twenty characters of text."),
        "premises": [{"premise_id": "P1", "text": "Fact A.", "evidence_ids": ["EVID-A"]}],
        "mechanism": {"description": mechanism},
        "supporting_evidence_ids": ["EVID-A", "EVID-B"],
        "second_order_effect": "Supported consequence. Invented consequence.",
        "alternative_explanations": [],
        "metadata": {
            "finding_tier": "hidden_insight",
            # v6.26: every hidden insight states leverage plus incentives.
            "leverage_analysis": {"holder": "Okta", "controls": "agent identity access", "dependent": "fintechs",
                                  "holder_incentive": "it is paid per seat", "predicted_move": "Okta prices per agent",
                                  "leverage_evidence_ids": ["EVID-A"], "holder_incentive_evidence_ids": ["EVID-B"]},
            "scope_validation": {"event_dependency": "necessary", "distinct_event_count": 2},
            "evidence_linkage_diagnostics": {
                "support_cluster_resolved": True,
                "cluster_coherence_reason": "Two independent developments are explicitly linked by the supported mechanism.",
            },
        },
    }


PASSING = lambda: grounding(  # noqa: E731
    claim("P1", "fact", "supported"),
    claim("M1", "mechanism", "inference_supported"),
    claim("S1", "second_order_effect", "supported", "Supported consequence."),
    claim("S2", "second_order_effect", "unsupported", "Invented consequence."),
    claim("I1", "hidden_insight", "inference_supported"),
)
FAILING = lambda: copy.deepcopy(REPORT_3C6F)  # noqa: E731
CONTRADICTED = lambda: grounding(  # noqa: E731
    claim("P1", "fact", "supported"),
    claim("I1", "hidden_insight", "contradicted", contradicted=["EVID-COUNTER"]),
)


class Investigator:
    def __init__(self, records=None):
        self.calls = []
        self.records = records or []

    def investigate(self, *, candidate, evidence, blueprint, as_of=""):
        self.calls.append(candidate)
        return {
            "claim_fingerprint": claim_fingerprint(candidate), "insight_id": candidate.get("insight_id"),
            "assessment": {"status": "completed_found" if self.records else "completed_none_found",
                           "completeness": "complete", "found_evidence_ids": [r["evidence_id"] for r in self.records],
                           "note": "searched"},
            "history": {"status": "searched", "records": []},
            "evidence": copy.deepcopy(self.records),
        }


class Budget:
    def __init__(self, seconds):
        self.seconds = seconds

    def remaining_discretionary_seconds(self):
        return self.seconds


def make_orchestrator(*, verdicts, new_candidates=(), investigator=None, budget=None, alternative_status=None,
                      display_status=None, display_default="supported", forecast_status="unsupported",
                      quality=None, display_error=False, nonobvious=None):
    """verdicts: {revision id: grounding factory}; new_candidates: list of lists, one per analyze call."""
    obj = object.__new__(Orchestrator)
    obj.hypothesis_evidence_service = investigator or Investigator()
    obj.execution_budget_service = budget
    obj.grounding_calls = []
    obj.analyze_calls = []
    # v6.20: the structural non-obviousness test has its own tests
    # (test_v6_20_resolver_memory_nonobvious.py); here it passes unless set.
    obj.nonobvious_calls = []
    obj._non_obviousness = lambda grounding, events, evidence: (
        obj.nonobvious_calls.append(1) or dict(nonobvious or {"passed": True, "reasons": []}))
    queue = [copy.deepcopy(list(batch)) for batch in new_candidates]

    def ground_insights(insights, evidence):
        out = {}
        for insight in insights:
            key = insight["insight_revision_id"]
            obj.grounding_calls.append((key, list(insight.get("counterevidence_ids") or []), len(evidence)))
            factory = verdicts[key]
            out[key] = factory(insight, evidence) if factory.__code__.co_argcount == 2 else factory()
        return out

    def analyze(**kwargs):
        obj.analyze_calls.append(kwargs)
        return {"insights": queue.pop(0) if queue else []}

    obj.claim_batches = []

    def ground_claims(*, claims, evidence_records):
        obj.claim_batches.append([dict(c) for c in claims])
        out = []
        for c in claims:
            if c.get("alternative_id"):
                status = (alternative_status or {}).get(c["alternative_id"], "unsupported")
            elif c["claim_id"].endswith("FORECAST-LEAK"):
                status = forecast_status
            else:
                if display_error:
                    raise RuntimeError("grounding model down")
                status = (display_status or {}).get(c["claim_text"], display_default)
            out.append({"claim_id": c["claim_id"], "grounding_status": status})
        return {"claims": out}

    obj._ground_insights = ground_insights
    obj.strategic_inference_agent = SimpleNamespace(analyze=analyze)
    obj.grounding_service = SimpleNamespace(ground_claims=ground_claims)
    def assess(**kw):
        key = kw["insight"].get("insight_revision_id")
        if quality and key in quality:
            return copy.deepcopy(quality[key])
        return {"quality_status": "pass", "quality_score": 70.0, "dimensions": {}}

    obj.insight_quality_service = SimpleNamespace(assess=assess)
    return obj


def release(obj, candidates, grounded=None, state=None):
    state = state if state is not None else {"warnings": []}
    grounded = grounded if grounded is not None else {
        c["insight_revision_id"]: obj._ground_insights([c], EVIDENCE)[c["insight_revision_id"]] for c in candidates
    }
    out = obj._claim_supported_release(
        topic="artificial intelligence", candidates=candidates, grounded=grounded, events=[],
        evidence=list(EVIDENCE), graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state,
    )
    return out, state


class ReleaseFlowTests(unittest.TestCase):
    def test_passing_candidate_is_researched_on_final_wording_and_sanitized(self):
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING})
        (released, evidence, grounded), state = release(obj, [candidate("ONE")])
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-ONE"])
        self.assertEqual(released[0]["second_order_effect"], "Supported consequence.")
        self.assertEqual(grounded["INSIGHTREV-ONE"]["status"], "inference_supported")
        self.assertEqual(len(obj.hypothesis_evidence_service.calls), 1)
        self.assertEqual(len(state["final_claim_research"]), 1)
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["final_claim_research"]["status"], "completed_none_found")
        self.assertEqual(obj.analyze_calls, [])
        self.assertEqual(released[0]["quality_assessment"]["quality_status"], "pass")

    def test_wording_already_researched_is_not_searched_again(self):
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING})
        item = candidate("ONE")
        state = {"warnings": [], "hypothesis_research": [
            {"claim_fingerprint": claim_fingerprint(item), "assessment": {"status": "completed_none_found"}}]}
        (released, _, _), state = release(obj, [item], state=state)
        self.assertEqual(len(released), 1)
        self.assertEqual(obj.hypothesis_evidence_service.calls, [])
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["final_claim_research"]["status"], "already_researched")

    def test_failed_candidate_is_revised_once_and_revision_released(self):
        revised = candidate("ONE")
        revised["insight_revision_id"] = "INSIGHTREV-ONE-R"
        revised["insight"] = revised["insight"].replace("ONE:", "ONE (narrowed):")
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-ONE-R": PASSING},
                                new_candidates=[[revised]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual([r["insight_revision_id"] for r in released], ["INSIGHTREV-ONE-R"])
        self.assertEqual(len(obj.analyze_calls), 1)
        context = obj.analyze_calls[0]["refinement_context"]
        self.assertTrue(context["instruction"].startswith(orchestrator_module.CLAIM_REPAIR_INSTRUCTION))  # v6.21: + convergence guidance
        self.assertEqual(context["target_insight_ids"], ["INSIGHT-ONE"])
        self.assertTrue(any("hardware containment" in line for line in context["quality_weaknesses"]))
        attempts = state["claim_support_gate"]["attempts"]
        self.assertEqual([a["origin"] for a in attempts], ["pipeline", "repair"])
        self.assertEqual(attempts[0]["next"], "repair")
        self.assertEqual(state["claim_support_gate"]["extra_syntheses"], 1)

    def test_failed_revision_leads_to_a_different_candidate(self):
        revised = candidate("ONE")
        revised["insight_revision_id"] = "INSIGHTREV-ONE-R"
        revised["insight"] = revised["insight"].replace("ONE:", "ONE (narrowed):")
        other = candidate("TWO")
        obj = make_orchestrator(
            verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-ONE-R": FAILING, "INSIGHTREV-TWO": PASSING},
            new_candidates=[[revised], [other]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-TWO"])
        second = obj.analyze_calls[1]["refinement_context"]
        self.assertTrue(second["instruction"].startswith("DIFFERENT CANDIDATE"))
        self.assertIn("ONE: a conclusion", second["instruction"])
        self.assertIn("ONE (narrowed)", second["instruction"])
        self.assertNotIn("target_insight_ids", second)
        self.assertEqual([a["origin"] for a in state["claim_support_gate"]["attempts"]], ["pipeline", "repair", "alternative"])

    def test_retry_limit_then_abstain_with_reasons(self):
        revised = candidate("ONE")
        revised["insight_revision_id"] = "INSIGHTREV-ONE-R"
        revised["insight"] = revised["insight"].replace("ONE:", "ONE (narrowed):")
        obj = make_orchestrator(
            verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-ONE-R": FAILING, "INSIGHTREV-TWO": FAILING},
            new_candidates=[[revised], [candidate("TWO")], [candidate("THREE")]])
        (released, _, grounded), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertEqual(grounded, {})
        self.assertEqual(len(obj.analyze_calls), orchestrator_module.DEEP_CLAIM_GATE_EXTRA_SYNTHESES)
        log = state["claim_support_gate"]
        self.assertEqual(log["stopped"], "retry_limit")
        self.assertIn("claim_support_gate:conclusion_not_supported_by_cited_evidence", log["abstention_reasons"])
        self.assertEqual(obj.hypothesis_evidence_service.calls, [])

    def test_contradicted_candidate_gets_a_different_candidate_not_a_revision(self):
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": CONTRADICTED, "INSIGHTREV-TWO": PASSING},
                                new_candidates=[[candidate("TWO")]])
        (released, _, _), _ = release(obj, [candidate("ONE")])
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-TWO"])
        self.assertTrue(obj.analyze_calls[0]["refinement_context"]["instruction"].startswith("DIFFERENT CANDIDATE"))

    def test_identical_regeneration_is_not_rechecked(self):
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": CONTRADICTED}, new_candidates=[[candidate("ONE")], []])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertEqual(len(state["claim_support_gate"]["attempts"]), 1)

    def test_no_extra_synthesis_when_time_is_short(self):
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING}, budget=Budget(60))
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertEqual(obj.analyze_calls, [])
        self.assertEqual(state["claim_support_gate"]["stopped"], "time_budget")

    def test_contrary_evidence_from_final_research_is_attached_and_rechecked(self):
        counter = {"evidence_id": "EVID-COUNTER", "evidence_role": "counterevidence", "text": "Contrary report."}

        def verdict_with_counter(insight, evidence):
            return CONTRADICTED() if "EVID-COUNTER" in (insight.get("counterevidence_ids") or []) else PASSING()

        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": verdict_with_counter, "INSIGHTREV-TWO": PASSING},
                                investigator=Investigator([counter]), new_candidates=[[candidate("TWO")]])
        (released, evidence, _), state = release(obj, [candidate("ONE")])
        self.assertIn("EVID-COUNTER", [e["evidence_id"] for e in evidence])
        first = state["claim_support_gate"]["attempts"][0]
        self.assertFalse(first["passed"])
        self.assertTrue(first["final_claim_research"]["regrounded_with_research_evidence"])
        self.assertEqual(obj.grounding_calls[1][1], ["EVID-COUNTER"])
        # The different candidate passes; its research found the same contrary
        # evidence, which is attached to it and checked again.
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-TWO"])
        self.assertIn("EVID-COUNTER", released[0]["counterevidence_ids"])

    def test_research_limit_releases_with_disclosure(self):
        # v6.25 (agreed with the user; was fail-closed since v6.14): a wording that
        # could not be searched is released, labelled, confidence capped at moderate.
        state = {"warnings": [], "final_claim_research": [{"claim_fingerprint": x} for x in "abc"]}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, new_candidates=[[candidate("TWO")]])
        (released, _, _), state = release(obj, [candidate("ONE")], state=state)
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-ONE"])
        self.assertEqual(obj.hypothesis_evidence_service.calls, [])
        attempt = state["claim_support_gate"]["attempts"][0]
        self.assertEqual(attempt["final_claim_research"]["status"], "not_run_limit_reached")
        self.assertEqual(attempt["counterevidence_check"], "not_run_limit_reached")
        self.assertEqual(released[0]["metadata"]["counterevidence_check"]["status"], "not_run_limit_reached")
        self.assertEqual(obj.analyze_calls, [])

    def test_misattributed_alternative_loses_its_citation(self):
        item = candidate("ONE")
        item["alternative_explanations"] = [
            {"alternative_id": "ALT-1", "text": "Docker misconfiguration.", "evidence_ids": ["EVID-A"], "status": "unresolved"}]
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, alternative_status={"ALT-1": "unsupported"})
        (released, _, _), state = release(obj, [item])
        self.assertEqual(released[0]["alternative_explanations"][0]["evidence_ids"], [])
        notes = state["claim_support_gate"]["attempts"][0]["release_notes"]
        self.assertEqual(notes["alternatives_uncited"][0]["alternative_id"], "ALT-1")

    def test_grounding_failure_on_new_candidate_is_a_rejection_not_a_crash(self):
        def broken(insight, evidence):
            raise RuntimeError("classifier down")
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-TWO": broken},
                                new_candidates=[[], [candidate("TWO")]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        reasons = [a["reasons"] for a in state["claim_support_gate"]["attempts"]]
        self.assertEqual(reasons[-1], ["grounding_unavailable"])

    def test_thin_new_candidate_rejected_by_existing_deterministic_gate_without_grounding(self):
        thin = candidate("TWO", mechanism="Too thin.")
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": CONTRADICTED}, new_candidates=[[thin], []])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertEqual(state["claim_support_gate"]["attempts"][1]["reasons"], ["mechanism_too_thin_for_release"])
        self.assertNotIn("INSIGHTREV-TWO", [call[0] for call in obj.grounding_calls])

    def test_repair_that_keeps_the_conclusion_wording_is_checked(self):
        # Only a premise failed: the expected repair narrows the premise and
        # keeps the conclusion sentence unchanged.
        failing_premise = lambda: grounding(  # noqa: E731
            claim("P1", "fact", "partially_supported"),
            claim("M1", "mechanism", "inference_supported"),
            claim("I1", "hidden_insight", "supported"),
        )
        repaired = candidate("ONE")
        repaired["insight_revision_id"] = "INSIGHTREV-ONE-R"
        repaired["premises"] = [{"premise_id": "P1", "text": "Fact A, narrowed.", "evidence_ids": ["EVID-A"]}]
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": failing_premise, "INSIGHTREV-ONE-R": PASSING},
                                new_candidates=[[repaired]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual([r["insight_revision_id"] for r in released], ["INSIGHTREV-ONE-R"])
        self.assertEqual(released[0]["insight"], candidate("ONE")["insight"])
        self.assertEqual([a["origin"] for a in state["claim_support_gate"]["attempts"]], ["pipeline", "repair"])

    def test_unchanged_repair_is_not_rechecked(self):
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING}, new_candidates=[[candidate("ONE")], []])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertEqual(len(state["claim_support_gate"]["attempts"]), 1)
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["next"], "repair_then_alternative")

    def test_contrary_evidence_from_earlier_research_of_same_wording_is_weighed(self):
        counter = {"evidence_id": "EVID-COUNTER", "evidence_role": "counterevidence", "text": "Contrary report."}
        item = candidate("ONE")

        def verdict_with_counter(insight, evidence):
            return CONTRADICTED() if "EVID-COUNTER" in (insight.get("counterevidence_ids") or []) else PASSING()

        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": verdict_with_counter})
        state = {"warnings": [], "hypothesis_research": [
            {"claim_fingerprint": claim_fingerprint(item), "assessment": {"status": "completed_found"},
             "evidence": [counter]}]}
        (released, evidence, _), state = release(obj, [item], state=state)
        self.assertEqual(released, [])
        self.assertEqual(obj.hypothesis_evidence_service.calls, [])
        attempt = state["claim_support_gate"]["attempts"][0]
        self.assertEqual(attempt["final_claim_research"]["status"], "already_researched")
        self.assertTrue(attempt["final_claim_research"]["regrounded_with_research_evidence"])
        self.assertIn("claim_contradicted_by_cited_evidence", attempt["reasons"])
        self.assertIn("EVID-COUNTER", [e["evidence_id"] for e in evidence])

    def test_already_cited_contrary_evidence_is_not_regrounded(self):
        counter = {"evidence_id": "EVID-COUNTER", "evidence_role": "counterevidence", "text": "Contrary report."}
        item = candidate("ONE")
        item["counterevidence_ids"] = ["EVID-COUNTER"]
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING})
        state = {"warnings": [], "hypothesis_research": [
            {"claim_fingerprint": claim_fingerprint(item), "assessment": {"status": "completed_found"},
             "evidence": [counter]}]}
        (released, _, _), _ = release(obj, [item], state=state)
        self.assertEqual(len(released), 1)
        self.assertEqual(len(obj.grounding_calls), 1)

    def test_incomplete_earlier_search_is_not_reused(self):
        item = candidate("ONE")
        state = {"warnings": [], "hypothesis_research": [
            {"claim_fingerprint": claim_fingerprint(item), "assessment": {"status": "incomplete"}}]}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING})
        (released, _, _), state = release(obj, [item], state=state)
        self.assertEqual(len(obj.hypothesis_evidence_service.calls), 1)
        self.assertEqual(len(released), 1)

    def test_incomplete_final_search_releases_with_disclosure(self):
        # v6.25: shown with "counter-evidence check incomplete", not withheld.
        class Failing(Investigator):
            def investigate(self, **kw):
                receipt = super().investigate(**kw)
                receipt["assessment"]["status"] = "incomplete"
                return receipt
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, investigator=Failing(),
                                new_candidates=[[candidate("TWO")]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-ONE"])
        note = released[0]["metadata"]["counterevidence_check"]
        self.assertEqual(note["status"], "incomplete")
        self.assertIn("could not be completed", note["note"])
        self.assertEqual(obj.analyze_calls, [])

    def test_research_support_is_attached_and_rechecked(self):
        support = {"evidence_id": "EVID-FEAS", "source_id": "SOURCE-F", "evidence_role": "feasibility_evidence",
                   "text": "Supporting feasibility record."}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, investigator=Investigator([support]))
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertIn("EVID-FEAS", released[0]["supporting_evidence_ids"])
        self.assertEqual(released[0]["feasibility"]["feasibility_evidence_ids"], ["EVID-FEAS"])
        self.assertEqual(len(obj.grounding_calls), 2)

    def test_two_records_from_one_source_do_not_back_a_conclusion(self):
        same_source = lambda: grounding(  # noqa: E731
            claim("P1", "fact", "supported"),
            claim("M1", "mechanism", "inference_supported", supporting=("EVID-A", "EVID-A2")),
            claim("I1", "hidden_insight", "supported", supporting=("EVID-A", "EVID-A2")),
        )
        evidence = EVIDENCE + [{"evidence_id": "EVID-A2", "source_id": "SOURCE-A", "text": "More of article A."}]
        verdict = gate.evaluate(same_source(), {e["evidence_id"]: e["source_id"] for e in evidence})
        self.assertEqual(set(verdict["reasons"]), {
            "conclusion_not_backed_by_two_independent_sources",
            "mechanism_step_not_backed_by_two_independent_sources"})
        self.assertTrue(verdict["repairable"])
        self.assertTrue(gate.evaluate(same_source())["passed"])  # no source map: rule not applied

    def test_low_feasibility_blocks_and_the_repair_is_told_why(self):
        weak = {"quality_status": "strong", "dimensions": {"prospective_feasibility": 1, "non_obviousness": 2},
                "defects": [{"code": "prospective_feasibility_unchecked", "severity": "high",
                             "description": "Magnitude/feasibility/blockers are not represented."}]}
        repaired = candidate("ONE")
        repaired["insight_revision_id"] = "INSIGHTREV-ONE-R"
        repaired["feasibility"] = {"magnitude_check": "added", "feasibility_evidence_ids": ["EVID-B"]}
        repaired["premises"] = [{"premise_id": "P1", "text": "Fact A and B.", "evidence_ids": ["EVID-A"]}]
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING, "INSIGHTREV-ONE-R": PASSING},
                                quality={"INSIGHTREV-ONE": weak}, new_candidates=[[repaired]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        first = state["claim_support_gate"]["attempts"][0]
        self.assertEqual(first["reasons"], ["final_quality_prospective_feasibility_1_of_4"])
        brief = obj.analyze_calls[0]["refinement_context"]["quality_weaknesses"]
        self.assertTrue(any("prospective_feasibility_unchecked" in line for line in brief))
        self.assertEqual([r["insight_revision_id"] for r in released], ["INSIGHTREV-ONE-R"])

    def test_forecast_already_answered_by_evidence_is_rejected_and_never_shown(self):
        item = candidate("ONE", text=("Okta will introduce runtime kill switches for AI agents before 2027, which the "
                                      "release gate needs to be longer than one hundred and twenty characters."))
        item["metadata"]["forecast_contract"] = {"question": "Will Okta launch agent kill switches by 2027?"}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, forecast_status="supported",
                                new_candidates=[[]])
        state = {"warnings": [], "strategic_connections": []}
        (released, _, _), state = release(obj, [item], state=state)
        # An already-answered forecast is never released, not even relabelled as a connection.
        self.assertEqual(released, [])
        self.assertNotIn("released_as", state["claim_support_gate"])
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["reasons"],
                         ["forecast_already_answered_by_evidence"])
        self.assertTrue(any("[forecast]" in line for line in obj.analyze_calls[0]["refinement_context"]["quality_weaknesses"]))

    def test_everything_shown_is_grounded_and_failing_material_removed(self):
        item = candidate("ONE")
        item["why_it_matters"] = "Labs now own the containment problem. Hardware air gaps become mandatory."
        item["metadata"].update({
            "novel_proposition": "Evaluation infrastructure is part of the threat surface. Math skill caused the July intrusion.",
            "triggering_anomaly": "Why did a walled-off model reach the internet?",
            "insight_derivation": {
                "observations": [{"text": "Fact A.", "evidence_ids": ["EVID-A"]}],
                "historical_or_analogical_support": [{"case": "Classical RL reward hacking", "evidence_ids": []}],
                "counter_cases": [{"case": "Nvidia CEO called the warnings odd", "evidence_ids": ["EVID-B"]}],
                "consequence_chain": [
                    {"order": 1, "text": "Audits face delays.", "epistemic_level": "supported_inference", "evidence_ids": ["EVID-B"]},
                    {"order": 2, "text": "Insurers price runtime controls.", "epistemic_level": "hypothesis", "evidence_ids": []}],
                "falsifiers": ["A forensic disclosure."], "watch_signals": ["Cloud sandbox launches."]},
            "likelihood_assessment": {"label": "moderate", "basis": "Grounded in confirmed escapes."},
        })
        thesis = item["insight"]
        unsupported = {
            "Math skill caused the July intrusion.": "unsupported",
            "Hardware air gaps become mandatory.": "unsupported",
            "Classical RL reward hacking": "unsupported",
            f"This evidence weakens or cuts against the following thesis: {thesis}": "unsupported",
            "Audits face delays.": "unsupported",
            "Connection the sources do not state.": "partially_supported",
        }
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, display_status=unsupported)
        state = {"warnings": [], "strategic_connections": [
            {"statement": "OpenAI restricted internet access after the breach.", "evidence_ids": ["EVID-A", "EVID-B"]},
            {"statement": "Connection the sources do not state.", "evidence_ids": ["EVID-A"]}]}
        (released, _, _), state = release(obj, [item], state=state)
        out = released[0]
        meta = out["metadata"]
        self.assertEqual(meta["novel_proposition"], "Evaluation infrastructure is part of the threat surface.")
        self.assertEqual(meta["triggering_anomaly"], "Why did a walled-off model reach the internet?")
        self.assertEqual(out["why_it_matters"], "Labs now own the containment problem.")
        derivation = meta["insight_derivation"]
        self.assertEqual(derivation["historical_or_analogical_support"], [])
        self.assertEqual(derivation["counter_cases"], [])
        self.assertEqual(derivation["observations"][0]["text"], "Fact A.")
        self.assertEqual([c["epistemic_level"] for c in derivation["consequence_chain"]], ["hypothesis", "hypothesis"])
        self.assertEqual(derivation["falsifiers"], ["A forensic disclosure."])
        self.assertEqual(meta["likelihood_assessment"]["basis"], "Grounded in confirmed escapes.")
        self.assertEqual([c["statement"] for c in state["strategic_connections"]],
                         ["OpenAI restricted internet access after the breach."])
        shown = [c["claim_text"] for batch in obj.claim_batches for c in batch]
        self.assertIn("The facts this question takes for granted are stated in, or follow directly from, the "
                      "evidence: Why did a walled-off model reach the internet?", shown)
        self.assertNotIn("Insurers price runtime controls.", shown)  # already labelled hypothesis
        self.assertTrue(all(not k.startswith("_") for batch in obj.claim_batches for c in batch for k in c))

    def test_loaded_question_and_unsupported_subfields_are_removed(self):
        item = candidate("ONE")
        question = "Why did the August mathematics model cause the July sandbox intrusion?"
        item["metadata"].update({
            "triggering_anomaly": question,
            "insight_derivation": {
                "historical_or_analogical_support": [{
                    "case": "Reward hacking in benchmark suites", "mechanism_match": "Agents exploit scoring bugs.",
                    "important_difference": "Hardware vendors hide the real danger.", "evidence_ids": ["EVID-A"]}],
                "counter_cases": [{
                    "case": "Nvidia launched a safety platform", "evidence_ids": ["EVID-B"],
                    "why_it_matters": "This proves vendors are deliberately hiding the danger."}],
            },
        })
        statuses = {
            "The facts this question takes for granted are stated in, or follow directly from, the evidence: "
            + question: "contradicted",
            "Hardware vendors hide the real danger.": "unsupported",
            "This proves vendors are deliberately hiding the danger.": "unsupported",
        }
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, display_status=statuses)
        (released, _, _), _ = release(obj, [item])
        meta = released[0]["metadata"]
        self.assertEqual(meta["triggering_anomaly"], "")
        self.assertEqual(meta["insight_derivation"]["historical_or_analogical_support"], [])
        self.assertEqual(meta["insight_derivation"]["counter_cases"], [])

    def test_conclusion_the_sources_already_state_is_shown_and_the_search_goes_on(self):
        # v6.30.2 (settled, user 2026-10-10): backed by its sources -> shown, labelled, with
        # citations; the search for an original insight continues.
        restating = lambda: grounding(  # noqa: E731
            claim("P1", "fact", "supported"),
            claim("M1", "mechanism", "inference_supported"),
            claim("I1", "hidden_insight", "supported"),
        )
        other = candidate("TWO")
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": restating, "INSIGHTREV-TWO": PASSING},
                                new_candidates=[[other]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        first = state["claim_support_gate"]["attempts"][0]
        self.assertEqual(first["released_as"], "source_backed")
        self.assertEqual(first["reasons"], ["source_backed_not_original:conclusion_restates_sources"])
        context = obj.analyze_calls[0]["refinement_context"]
        self.assertTrue(context["instruction"].startswith("DIFFERENT CANDIDATE"))
        self.assertTrue(any("[non-obvious]" in line for line in context["quality_weaknesses"]))
        self.assertEqual([r["insight_id"] for r in released], ["INSIGHT-ONE", "INSIGHT-TWO"])
        self.assertEqual([r["finding_tier"] for r in released], ["hidden_insight", "hidden_insight"])
        self.assertEqual(released[0]["metadata"]["source_backed"]["reasons"], ["conclusion_restates_sources"])
        self.assertNotIn("source_backed", released[1]["metadata"])

    def test_backed_candidate_is_shown_as_an_insight_when_no_original_one_passes(self):
        restating = lambda: grounding(  # noqa: E731
            claim("P1", "fact", "supported"),
            claim("M1", "mechanism", "inference_supported"),
            claim("S1", "second_order_effect", "supported", "Supported consequence."),
            claim("S2", "second_order_effect", "unsupported", "Invented consequence."),
            claim("I1", "hidden_insight", "supported"),
        )
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": restating, "INSIGHTREV-TWO": FAILING,
                                          "INSIGHTREV-THREE": FAILING},
                                new_candidates=[[candidate("TWO")], [candidate("THREE")]])
        state = {"warnings": [], "strategic_connections": [{"statement": "A connection.", "evidence_ids": ["EVID-A", "EVID-B"]}]}
        (released, _, grounded), state = release(obj, [candidate("ONE")], state=state)
        self.assertEqual(len(released), 1)
        out = released[0]
        self.assertEqual(out["insight_id"], "INSIGHT-ONE")
        self.assertEqual(out["finding_tier"], "hidden_insight")
        self.assertTrue(out["metadata"]["source_backed"])
        self.assertEqual(out["second_order_effect"], "Supported consequence.")
        self.assertIn("evidence_table", out["metadata"])          # shown with its citations
        self.assertIn("INSIGHTREV-ONE", grounded)
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["released_as"], "source_backed")
        self.assertTrue(obj.analyze_calls)                         # kept looking for an original one

    def test_quality_failure_never_becomes_a_verified_connection(self):
        weak = {"quality_status": "strong", "dimensions": {"prospective_feasibility": 1}, "defects": []}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING, "INSIGHTREV-TWO": FAILING},
                                quality={"INSIGHTREV-ONE": weak}, new_candidates=[[], [candidate("TWO")]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertNotIn("released_as", state["claim_support_gate"])
        self.assertIn("claim_support_gate:final_quality_prospective_feasibility_1_of_4",
                      state["claim_support_gate"]["abstention_reasons"])

    def test_counterevidence_quality_is_ignored_only_after_a_search_that_found_nothing(self):
        quality = {"quality_status": "strong", "dimensions": {"counterevidence_quality": 1}}
        self.assertEqual(gate.quality_reasons(quality), ["final_quality_counterevidence_quality_1_of_4"])
        self.assertEqual(gate.quality_reasons(quality, gate.counterevidence_exemption("completed_none_found", {})), [])
        self.assertEqual(gate.counterevidence_exemption("completed_found", {}), ())
        self.assertEqual(gate.counterevidence_exemption("completed_none_found", {"counterevidence_ids": ["EVID-C"]}), ())

    def test_no_counterevidence_after_a_completed_search_does_not_block_release(self):
        no_counter = {"quality_status": "strong", "dimensions": {"counterevidence_quality": 1}, "defects": []}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, quality={"INSIGHTREV-ONE": no_counter})
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["final_claim_research"]["research_status"],
                         "completed_none_found")
        self.assertEqual([r["finding_tier"] for r in released], ["hidden_insight"])

    def test_counterevidence_quality_still_blocks_when_contrary_evidence_exists(self):
        counter = {"evidence_id": "EVID-COUNTER", "source_id": "SOURCE-C", "evidence_role": "counterevidence",
                   "text": "Contrary report."}
        weak_counter = {"quality_status": "strong", "dimensions": {"counterevidence_quality": 1}, "defects": []}
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, investigator=Investigator([counter]),
                                quality={"INSIGHTREV-ONE": weak_counter}, new_candidates=[[]])
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual(released, [])
        self.assertEqual(state["claim_support_gate"]["attempts"][0]["reasons"],
                         ["final_quality_counterevidence_quality_1_of_4"])

    def test_dashboard_shows_a_plain_no_finding_result(self):
        import contextlib
        import importlib
        import sys
        import types
        calls = []

        class Ctx(contextlib.nullcontext):
            def __call__(self, fn=None, *a, **k):
                return fn

        class St(types.ModuleType):
            def __getattr__(self, name):
                return lambda *a, **k: calls.append((name, a[0] if a else k)) or Ctx()

        saved = {name: sys.modules.get(name) for name in ("streamlit", "frontend.dashboard")}
        sys.modules["streamlit"] = St("streamlit")
        sys.modules.pop("frontend.dashboard", None)
        try:
            dashboard = importlib.import_module("frontend.dashboard")
            calls.clear()
            dashboard.render_main_finding({}, {"hidden_insights": [], "strategic_findings": [], "connections": [],
                                               "editorial_briefing": {}}, "abstained")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        # v6.17.1: four-section contract. Section 2 is visibly present even
        # when empty, and section 4 says "None this run".
        self.assertEqual(calls[0], ("subheader", "Briefing"))
        self.assertIn(("subheader", "Hidden cross-actor insight"), calls)
        self.assertIn(("markdown", "**None this run.**"), calls)

    def test_withheld_forecast_is_not_registered(self):
        from unittest.mock import patch
        registered = []

        class Store:
            def register(self, *, run_id, candidate):
                registered.append(candidate["insight_id"])
                return {"forecast_id": "F"}

        withheld = gate.as_verified_connection(candidate("ONE"), "restates")
        kept = candidate("TWO")
        report, state = {}, {"warnings": []}
        with patch("services.forecast_store.ForecastStore", Store):
            Orchestrator._record_forecasts(run_id="RUN-T", insights=[withheld, kept], report=report, state=state)
        self.assertEqual(registered, ["INSIGHT-TWO"])

    def test_run_with_no_finding_still_shows_verified_connections(self):
        connection_ok = "OpenAI restricted internet access after the breach."
        obj = make_orchestrator(verdicts={}, display_status={"Unbacked link.": "unsupported"})
        state = {"strategic_connections": [
            {"statement": connection_ok, "evidence_ids": ["EVID-A", "EVID-B"], "connection_type": "causal"},
            {"statement": "Unbacked link.", "evidence_ids": ["EVID-A"]}]}
        shown = obj._verified_connections_for_report(EVIDENCE, state)
        self.assertEqual([c["text"] for c in shown], [connection_ok])
        self.assertEqual(shown[0]["connection_type"], "causal")
        self.assertEqual(shown[0]["evidence_ids"], ["EVID-A", "EVID-B"])

    def test_partially_supported_does_not_keep_shown_material(self):
        item = candidate("ONE")
        item["counterevidence_ids"] = ["EVID-A"]
        item["metadata"]["insight_derivation"] = {
            "historical_or_analogical_support": [{
                "case": "Reward hacking in benchmark suites", "mechanism_match": "Agents exploit scoring bugs.",
                "important_difference": "Scale differs.", "evidence_ids": ["EVID-A"]}],
            "counter_cases": [{"case": "Nvidia launched a safety platform", "evidence_ids": ["EVID-B"],
                               "why_it_matters": "Vendors frame safety as a product."}],
        }
        thesis = item["insight"]
        weakens = f"This evidence weakens or cuts against the following thesis: {thesis}"
        cases = {
            "historical case": ("Reward hacking in benchmark suites", "historical_or_analogical_support"),
            "mechanism match": ("Agents exploit scoring bugs.", "historical_or_analogical_support"),
            "important difference": ("Scale differs.", "historical_or_analogical_support"),
            "counter-case": ("Nvidia launched a safety platform", "counter_cases"),
            "why it matters": ("Vendors frame safety as a product.", "counter_cases"),
        }
        for label, (text, field) in cases.items():
            with self.subTest(label):
                obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING},
                                        display_status={text: "partially_supported"})
                (released, _, _), _ = release(obj, [copy.deepcopy(item)])
                derivation = released[0]["metadata"]["insight_derivation"]
                self.assertEqual(derivation[field], [])
                other = "counter_cases" if field != "counter_cases" else "historical_or_analogical_support"
                self.assertEqual(len(derivation[other]), 1)
        # The weakens-the-thesis role check: partly supported is not enough either.
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, display_status={weakens: "partially_supported"})
        (released, _, _), _ = release(obj, [copy.deepcopy(item)])
        self.assertEqual(released[0]["metadata"]["insight_derivation"]["counter_cases"], [])
        self.assertEqual(released[0]["counterevidence_ids"], [])
        # inference_supported analysis passes; a fact needs "supported".
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING},
                                display_status={"Scale differs.": "inference_supported",
                                                "Nvidia launched a safety platform": "inference_supported"})
        (released, _, _), _ = release(obj, [copy.deepcopy(item)])
        derivation = released[0]["metadata"]["insight_derivation"]
        self.assertEqual(len(derivation["historical_or_analogical_support"]), 1)
        self.assertEqual(derivation["counter_cases"], [])

    def test_quality_is_enforced_again_after_display_pruning(self):
        repaired = candidate("ONE")
        repaired["insight_revision_id"] = "INSIGHTREV-ONE-R"
        repaired["premises"] = [{"premise_id": "P1", "text": "Fact A, restated.", "evidence_ids": ["EVID-A"]}]
        item = candidate("ONE")
        item["metadata"]["novel_proposition"] = "An unsupported sentence."
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING, "INSIGHTREV-ONE-R": PASSING},
                                display_status={"An unsupported sentence.": "unsupported"},
                                new_candidates=[[repaired]])
        calls = {"INSIGHTREV-ONE": 0}

        def assess(**kw):
            key = kw["insight"].get("insight_revision_id")
            if key == "INSIGHTREV-ONE":
                calls[key] += 1
                if calls[key] >= 2:  # the pruned copy
                    return {"quality_status": "needs_refinement", "dimensions": {"mechanism_quality": 1},
                            "defects": [{"code": "thin_mechanism", "severity": "high", "description": "Mechanism too thin."}]}
            return {"quality_status": "pass", "dimensions": {}}

        obj.insight_quality_service = SimpleNamespace(assess=assess)
        state = {"warnings": [], "strategic_connections": [{"statement": "A connection.", "evidence_ids": ["EVID-A", "EVID-B"]}]}
        (released, _, _), state = release(obj, [item], state=state)
        first = state["claim_support_gate"]["attempts"][0]
        self.assertFalse(first["passed"])
        self.assertEqual(first["reasons"], ["after_display_check:final_quality_needs_refinement",
                                            "after_display_check:final_quality_mechanism_quality_1_of_4"])
        brief = obj.analyze_calls[0]["refinement_context"]["quality_weaknesses"]
        self.assertTrue(any("An unsupported sentence." in line for line in brief))
        self.assertEqual([r["insight_revision_id"] for r in released], ["INSIGHTREV-ONE-R"])
        self.assertEqual(len(state["strategic_connections"]), 1)

    def test_model_chosen_counterevidence_must_cut_against_the_thesis(self):
        item = candidate("ONE")
        item["counterevidence_ids"] = ["EVID-A", "EVID-COUNTER"]
        thesis = item["insight"]
        evidence_counter = {"evidence_id": "EVID-COUNTER", "source_id": "SOURCE-C",
                            "evidence_role": "counterevidence", "text": "Research-reviewed contrary record."}
        obj = make_orchestrator(
            verdicts={"INSIGHTREV-ONE": PASSING},
            display_status={f"This evidence weakens or cuts against the following thesis: {thesis}": "unsupported"})
        state = {"warnings": []}
        grounded = {"INSIGHTREV-ONE": PASSING()}
        out = obj._claim_supported_release(
            topic="ai", candidates=[item], grounded=grounded, events=[], evidence=EVIDENCE + [evidence_counter],
            graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
        released = out[0]
        # EVID-A (a byline-style record the model called counterevidence) is checked and dropped;
        # EVID-COUNTER was already reviewed as contrary by the research step and is kept unchecked.
        self.assertEqual(released[0]["counterevidence_ids"], ["EVID-COUNTER"])

    def test_display_grounding_failure_removes_unchecked_material(self):
        item = candidate("ONE")
        item["metadata"]["novel_proposition"] = "Evaluation infrastructure is part of the threat surface."
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING}, display_error=True)
        state = {"warnings": [], "strategic_connections": [
            {"statement": "A connection.", "evidence_ids": ["EVID-A", "EVID-B"]}]}
        (released, _, _), state = release(obj, [item], state=state)
        self.assertEqual(released[0]["metadata"]["novel_proposition"], "")
        self.assertEqual(state["strategic_connections"], [])
        self.assertFalse(released[0]["metadata"]["claim_support_gate"]["display_grounding_ran"])


class SupportingChangeTests(unittest.TestCase):
    def test_quality_reasons(self):
        self.assertEqual(gate.quality_reasons({"quality_status": "strong", "dimensions": {"non_obviousness": 2}}), [])
        self.assertEqual(gate.quality_reasons({"quality_status": "needs_refinement", "dimensions": {}}),
                         ["final_quality_needs_refinement"])
        self.assertEqual(gate.quality_reasons({"quality_status": "strong", "dimensions": {"mechanism_quality": 0}}),
                         ["final_quality_mechanism_quality_0_of_4"])
        self.assertEqual(gate.quality_reasons(None), ["final_quality_unavailable"])

    def test_dropped_connection_is_a_failover_error(self):
        from agents.strategic_inference_agent import StrategicInferenceAgent as Agent

        class ReadError(Exception):
            pass

        error = RuntimeError("Gemini request failed")
        error.__cause__ = ReadError("[WinError 10054] An existing connection was forcibly closed by the remote host")
        self.assertTrue(Agent._is_failover_eligible_llm_error(error))
        self.assertTrue(Agent._is_transient_llm_error(error))
        self.assertFalse(Agent._is_failover_eligible_llm_error(RuntimeError("API key not valid")))

    def test_synthesis_prompt_states_the_release_contract(self):
        from agents.strategic_inference_agent import StrategicInferenceAgent
        agent = object.__new__(StrategicInferenceAgent)
        agent._strategic_model_used_this_run = "offline-model"
        captured = {}
        agent._call_json = lambda **kwargs: captured.update(kwargs) or {}
        records = [{"id": f"EVID-{i}", "evidence_id": f"EVID-{i}", "event_id": f"EVENT-{i}",
                    "source_id": f"SOURCE-{i}", "text": f"Fact {i}.", "evidence_type": "source_excerpt"} for i in range(3)]
        agent._generate_synthesis(
            research_question="artificial intelligence", evidence_records=records,
            evidence_review={"annotations": [{"evidence_id": r["id"], "priority_score": 50} for r in records]},
            auxiliary_context={}, reasoning_guidance={"desired_depth": "deep", "max_candidate_insights": 1},
            normalized_refinement={}, previous_report={}, event_ids=[], constraint_ids=[],
            active_budget=None, execution_stage="initial")
        prompt = captured["prompt"]
        self.assertIn("RELEASE CONTRACT", prompt)
        self.assertIn("at least two different sources", prompt)
        self.assertIn("Never attribute a motive", prompt)
        self.assertIn("combining at least two sources supports but none of them states", prompt)

    def test_grounding_prompt_checks_chronology(self):
        from services.grounding_service import GROUNDING_PROMPT_VERSION, GroundingService
        service = object.__new__(GroundingService)
        prompt = service._build_prompt(claim_id="C1", claim_text="X caused Y.", epistemic_type="mechanism",
                                       evidence_records=[], multi_evidence_required=True)
        self.assertIn("cannot be its cause", prompt)
        self.assertEqual(GROUNDING_PROMPT_VERSION, "grounding-v1.2-company-claims")


class AbstentionReceiptTests(unittest.TestCase):
    def test_final_claim_receipts_are_kept_when_the_run_abstains(self):
        import os
        import tempfile
        import time
        obj = object.__new__(Orchestrator)
        obj.report_service = None
        obj.policy_enforcement_service = None
        for name in ("telemetry_service", "insight_registry_service", "audit_receipt_service",
                     "execution_budget_service", "insight_trace_service"):
            setattr(obj, name, None)
        state = {"warnings": [], "claim_support_gate": {"attempts": [{"origin": "pipeline", "passed": False}]},
                 "final_claim_research": [{"claim_fingerprint": "abc", "insight_id": "INSIGHT-ONE",
                                           "assessment": {"status": "completed_found"},
                                           "history": {"status": "searched"}, "evidence": [],
                                           "source_documents": {}}]}
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as folder:
            os.chdir(folder)
            try:
                obj._complete_abstention(
                    state=state, topic="artificial intelligence", run_id="RUN-TEST", depth="deep", events=[],
                    evidence=[], constraints={}, graph_context={}, reasons=["claim_support_gate:x"],
                    started_at=time.monotonic(), stage="claim_support_gate")
                saved = os.path.exists(os.path.join("data", "research_receipts", "RUN-TEST-final.json"))
            finally:
                os.chdir(previous)
        metadata = state["report"]["analysis_metadata"]
        self.assertTrue(saved)
        self.assertEqual(metadata["final_claim_research"][0]["insight_id"], "INSIGHT-ONE")
        self.assertEqual(metadata["claim_support_gate"]["attempts"][0]["origin"], "pipeline")


if __name__ == "__main__":
    unittest.main()
