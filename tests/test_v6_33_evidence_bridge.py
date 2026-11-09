from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


lab = load("strategic_hypothesis_lab_v633", "services/strategic_hypothesis_lab.py")
evaluator = load("insight_evaluator_v633", "services/insight_evaluator.py")


def test_fact_to_evidence_bridge_accepts_direct_evidence_ids():
    facts = [{
        "fact_id": "F1",
        "text": "OpenAI discovered the incident in August.",
        "evidence_ids": ["EVID-AAA11111"],
    }]
    assert lab.evidence_ids_for_fact_ids(facts, ["F1"]) == ["EVID-AAA11111"]


def test_fact_to_evidence_bridge_can_resolve_same_source_text():
    facts = [{
        "fact_id": "F2",
        "text": "The regulator required a review of legacy government systems after the incident.",
        "sources": [{"source_id": "SOURCE-ONE"}],
    }]
    records = [{
        "evidence_id": "EVID-BBB22222",
        "source_id": "SOURCE-ONE",
        "text": "After the incident, the regulator required a review of legacy government systems.",
    }]
    assert lab.evidence_ids_for_fact_ids(facts, ["F2"], records) == ["EVID-BBB22222"]


def test_research_candidate_supplies_required_premises_contract():
    item = {
        "id": "EVOLVED_H1",
        "thesis": "A structural bottleneck shifts bargaining power toward the provider.",
        "mechanism": "Scarcity -> dependency -> bargaining power.",
        "fact_ids": ["F1"],
        "research_question": "Is the bottleneck durable?",
        "falsifier": "Capacity expands immediately.",
        "alternative_explanation": "Temporary outage.",
        "lens": "dependency and control-point shift",
    }
    facts = [{"fact_id": "F1", "text": "Capacity is scarce.", "evidence_ids": ["EVID-AAA11111"]}]
    candidate = lab.to_research_candidate(
        item, ["EVID-AAA11111"], facts=facts, evidence_records=[]
    )
    assert candidate["insight_type"] == "prospective"
    assert candidate["premises"]
    assert candidate["premises"][0]["premise_id"] == "LAB-P-F1"
    assert candidate["premises"][0]["text"] == "Capacity is scarce."


def test_intent_claim_cannot_be_promoted_on_inference_support_alone():
    item = {
        "id": "EVOLVED_H1",
        "thesis": "The renaming was a calculated semantic poison pill designed to nullify pending regulation.",
        "mechanism": "Rename -> statutory confusion -> enforcement delay.",
        "fact_ids": ["F1", "F2"],
        "critic_total": 9,
    }
    facts = [
        {"fact_id": "F1", "text": "The administration renamed the technology.", "evidence_ids": ["EVID-A1111111"]},
        {"fact_id": "F2", "text": "A bill used related terminology.", "evidence_ids": ["EVID-B2222222"]},
    ]
    grounded = [
        {"claim_id": "LAB-EVOLVED_H1-THESIS", "grounding_status": "inference_supported", "supporting_evidence_ids": ["EVID-A1111111", "EVID-B2222222"]},
        {"claim_id": "LAB-EVOLVED_H1-MECH", "grounding_status": "inference_supported", "supporting_evidence_ids": ["EVID-A1111111", "EVID-B2222222"]},
    ]
    result = lab.finalize(item, grounded, facts)
    assert result["status"] == "promising_unverified"
    assert "direct_evidence_required_for_intent_attribution" in result["promotion_blockers"]


def test_zero_informational_lift_is_capped_at_seven():
    item = {
        "id": "I1", "text": "A routine action", "detail": "", "strategic_type": "strategic_action",
        "leverage_complete": False, "widely_expected": False,
    }
    answers = {key: True for key, _, _ in evaluator.RUBRIC}
    answers["E1"] = False
    answers["E2"] = False
    parsed = evaluator.parse_rubric({"scores": [{"id": "I1", "answers": answers, "weakest": "lift"}]}, [item])
    assert parsed["I1"]["dimensions"]["informational_lift"] == 0
    assert parsed["I1"]["score_cap"] == 7
    assert parsed["I1"]["score"] <= 7


def test_zero_lift_action_cannot_outrank_positive_lift_hypothesis():
    report = {
        "strategic_hypotheses": {"verified": [{
            "id": "H1",
            "thesis": "A hidden capacity constraint shifts the control point from price to qualified allocation.",
            "novel_proposition": "Qualified allocation, not nominal price, becomes scarce.",
            "why_not_obvious": "Sources discuss price and supply separately.",
            "mechanism": "Capacity scarcity -> qualification delay -> allocation leverage.",
            "alternative_explanation": "Temporary outage.",
            "watch_signal": "Dual qualification spending rises.",
            "falsifier": "Capacity normalizes without term changes.",
            "supporting_evidence": ["Capacity is scarce", "Qualification takes a year"],
            "leverage": {"complete": True, "holder": "Supplier", "counterparty": "Buyer", "dependency": "qualified supply", "control_point": "allocation"},
        }]},
        "hidden_insights": [],
        "actor_moves": {"actors": [{
            "actor": "OpenAI",
            "crux": {"text": "Security incident", "sources": [{"text": "Security incident occurred"}]},
            "choice": {},
            "moves": [{"move": "Suspend agent web access globally", "basis": "incentive", "why_it_benefits": "reduces risk"}],
        }]},
        "editorial_briefing": {"implications": [], "expected_implications": []},
    }

    def judge(prompt: str):
        if "CHECKS" in prompt:
            scores = []
            # Candidate order is H1 then actor action => I1, I2.
            for item_id in ("I1", "I2"):
                answers = {key: True for key, _, _ in evaluator.RUBRIC}
                if item_id == "I2":
                    answers["E1"] = False
                    answers["E2"] = False
                scores.append({"id": item_id, "answers": answers, "weakest": ""})
            return {"scores": scores}
        return {"winner": "A", "deciding_dimension": "lift", "reason": ""}

    result = evaluator.evaluate(report, "test", judge)
    assert result["items"][0]["kind"] == "strategic_hypothesis"
    assert result["items"][0]["dimensions"]["informational_lift"] > 0
    action = next(x for x in result["items"] if x["kind"] == "strategic_decision")
    assert action["dimensions"]["informational_lift"] == 0
    assert action["rank"] > result["items"][0]["rank"]


def test_orchestrator_passes_facts_and_seed_evidence_to_research():
    text = (ROOT / "services/research_orchestrator_service.py").read_text(encoding="utf-8")
    assert "facts=facts, evidence_records=records" in text
    assert 'receipt.setdefault("seed_evidence_ids", list(seed_ids))' in text
    assert '"Targeted research failed; existing seed evidence remains available for grounding."' in text



def test_mechanism_intent_language_needs_direct_mechanism_support_too():
    item = {
        "id": "EVOLVED_H9",
        "thesis": "The policy change and bill use overlapping terminology.",
        "mechanism": "The administration deliberately uses the overlap to delay enforcement.",
        "fact_ids": ["F1", "F2"],
        "critic_total": 9,
    }
    facts = [
        {"fact_id": "F1", "text": "The policy changed terminology.", "evidence_id": "EVID-A1111111"},
        {"fact_id": "F2", "text": "The bill used overlapping terminology.", "evidence_id": "EVID-B2222222"},
    ]
    grounded = [
        {"claim_id": "LAB-EVOLVED_H9-THESIS", "grounding_status": "supported", "supporting_evidence_ids": ["EVID-A1111111", "EVID-B2222222"]},
        {"claim_id": "LAB-EVOLVED_H9-MECH", "grounding_status": "inference_supported", "supporting_evidence_ids": ["EVID-A1111111", "EVID-B2222222"]},
    ]
    result = lab.finalize(item, grounded, facts)
    assert result["status"] == "promising_unverified"
    assert "direct_evidence_required_for_intent_attribution" in result["promotion_blockers"]


def test_report_like_fact_schema_resolves_top_level_and_nested_evidence_ids():
    facts = [
        {
            "fact_id": "F7",
            "text": "OpenAI notified Australia after a delay.",
            "evidence_id": "EVID-TOP77777",
            "sources": [{"source_id": "SOURCE-A", "evidence_id": "EVID-NEST8888"}],
        }
    ]
    resolved = lab.evidence_ids_for_fact_ids(facts, ["F7"])
    assert resolved == ["EVID-NEST8888", "EVID-TOP77777"]
