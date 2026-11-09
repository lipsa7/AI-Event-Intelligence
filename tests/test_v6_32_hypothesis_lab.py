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


lab = load("strategic_hypothesis_lab_v632", "services/strategic_hypothesis_lab.py")
judge = load("insight_evaluator_v632", "services/insight_evaluator.py")


FACTS = [
    {"fact_id": "F1", "text": "Buyer A depends on supplier B for a scarce component.",
     "citations": [{"evidence_id": "E1", "source_id": "S1"}]},
    {"fact_id": "F2", "text": "Supplier B has allocated most new capacity to a higher-margin market.",
     "citations": [{"evidence_id": "E2", "source_id": "S2"}]},
    {"fact_id": "F3", "text": "Switching to supplier C requires a twelve-month qualification process.",
     "citations": [{"evidence_id": "E3", "source_id": "S3"}]},
]


def rich_row():
    return {
        "id": "B1H1",
        "lens": "dependency and control-point shift",
        "thesis": "B's capacity allocation has shifted bargaining power because A cannot qualify an alternative before the next contracting cycle, making allocation rather than nominal price the operative control point.",
        "novel_proposition": "The scarce variable is qualified allocation, not component price, so bargaining power moves before posted prices do.",
        "why_not_obvious": "Each source separately describes supply, allocation and qualification time; none states that the qualification clock converts allocation into bargaining leverage.",
        "anomaly_or_trigger": "A remains exposed despite an apparent alternative supplier.",
        "mechanism": "B reallocates scarce capacity -> A cannot switch within twelve months -> B controls near-term qualified supply -> contract terms can move before market-wide prices.",
        "fact_ids": ["F1", "F2", "F3"],
        "leverage": {"holder": "B", "counterparty": "A", "dependency": "qualified component supply", "control_point": "near-term capacity allocation", "outside_option": "C after qualification", "holder_dependence": "B sells to many buyers besides A"},
        "second_order_effect": "A may redesign procurement around qualification optionality rather than spot price.",
        "likely_actor_move": "B prioritizes contracts that preserve allocation flexibility.",
        "likely_countermove": "A accelerates dual qualification even at higher short-term cost.",
        "alternative_explanation": "The allocation is temporary and reflects a one-off production issue rather than durable bargaining power.",
        "falsifier": "B restores capacity to A before C can be qualified without demanding better terms.",
        "watch_signal": "Qualification spending or multi-sourcing announcements by A.",
        "research_question": "Is B's reallocation contractual/structural or temporary operational disruption?",
    }


def test_generation_parser_keeps_structural_hypothesis():
    reply = {"hypotheses": [rich_row()]}
    rows = lab.parse_generation(reply, FACTS)
    assert len(rows) == 1
    assert rows[0]["leverage"]["complete"] is True
    assert rows[0]["fact_ids"] == ["F1", "F2", "F3"]


def test_generation_parser_rejects_bland_recommendation():
    row = rich_row()
    row.update({
        "thesis": "Company A should focus on improving partnerships and monitoring supply risks.",
        "novel_proposition": "It should monitor suppliers.",
        "why_not_obvious": "This is useful advice.",
    })
    assert lab.parse_generation({"hypotheses": [row]}, FACTS) == []


def test_evolution_requires_high_critic_score():
    row = rich_row()
    row["critic_scores"] = {
        "mental_model_change": 2,
        "mechanism_depth": 2,
        "surprise_nonobviousness": 2,
        "strategic_consequence": 2,
        "falsifiability": 1,
    }
    rows = lab.parse_evolution({"hypotheses": [row]}, FACTS)
    assert rows[0]["critic_total"] == 9
    row["critic_scores"] = {k: 1 for k in row["critic_scores"]}
    assert lab.parse_evolution({"hypotheses": [row]}, FACTS) == []


def test_grounding_must_support_thesis_and_mechanism():
    item = rich_row()
    item["critic_total"] = 9
    grounded = [
        {"claim_id": "LAB-B1H1-THESIS", "grounding_status": "inference_supported", "supporting_evidence_ids": ["E1", "E2", "E3"]},
        {"claim_id": "LAB-B1H1-MECH", "grounding_status": "inference_supported", "supporting_evidence_ids": ["E1", "E2", "E3"]},
    ]
    assert lab.finalize(item, grounded, FACTS)["status"] == "verified"
    grounded[1]["grounding_status"] = "partially_supported"
    assert lab.finalize(item, grounded, FACTS)["status"] == "promising_unverified"


def test_evaluator_puts_verified_lab_hypothesis_in_pool_first():
    item = rich_row()
    item.update({"critic_total": 9, "status": "verified", "supporting_evidence_ids": ["E1", "E2", "E3"]})
    report = {
        "strategic_hypotheses": {"verified": [item]},
        "actor_moves": {"actors": []},
        "editorial_briefing": {"implications": []},
        "hidden_insights": [],
    }
    rows = judge.candidates(report)
    assert rows[0]["kind"] == "strategic_hypothesis"
    assert rows[0]["strategic_type"] == "leverage_decision"
    assert rows[0]["leverage_complete"] is True


def test_orchestrator_integrates_and_persists_lab_section():
    text = (ROOT / "services/research_orchestrator_service.py").read_text(encoding="utf-8")
    assert "def _strategic_hypothesis_lab(" in text
    assert 'report["strategic_hypotheses"]' in text
    assert '"strategic_hypotheses": _json_safe(report.get("strategic_hypotheses", {}))' in text
