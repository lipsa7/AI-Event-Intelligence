"""v6.31.1 - three fixes from the GPT review of v6.31:
1. Provenance has separate dimensions: a leverage-based decision stays a leverage decision when an
   expert also argues it (expert-corroborated), instead of becoming "expert-backed" (the backup).
2. The judge sees a compact evidence context (what the sources state), so "adds beyond the
   sources" and "mechanism backed" are real checks.
3. Only pairs the user decided count as agreement with the user; reference pairs are separate.
"""

from __future__ import annotations

import inspect
import json
import pathlib

import services.research_orchestrator_service as ros
from services import insight_evaluator as ev

MOVE_REPORT = {"actor_moves": {"actors": [{
    "actor": "OpenAI", "leverage": {"benefit": "sole access to agent logs",
                                    "sources": [{"evidence_id": "E1", "text": "Only OpenAI can see agent logs."}]},
    "moves": [{"move": "Publish a detection SLA for government agents", "why_it_benefits": "trust",
               "sources": [{"evidence_id": "E2", "text": "Australia was told three months after the breach."}],
               "expert_sources": [{"publisher": "csis.org", "credibility": "research institution"}]}]}]}}


def test_expert_agreement_corroborates_a_leverage_decision():
    item = ev.candidates(MOVE_REPORT)[0]
    assert item["type"] == "leverage_decision" and item["provenance"] == "leverage_decision"
    assert item["corroboration"] == ["csis.org, research institution"]
    assert item["provenance_label"].startswith("Strategic decision from leverage · Expert-corroborated")


def test_judge_gets_the_evidence_context():
    item = ev.candidates(MOVE_REPORT)[0]
    assert item["evidence"][0] == "[source states] Australia was told three months after the breach."
    prompt = ev.rubric_prompt("ai", [item])
    assert "Evidence (what the sources state):" in prompt and "Only OpenAI can see agent logs." in prompt
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert "evidence_text=getattr(self, \"_evaluation_evidence\", None) or {}" in source


def test_reference_pairs_never_count_as_agreement_with_the_user():
    gold = json.loads((pathlib.Path(__file__).resolve().parent / "data" / "insight_gold.json").read_text(encoding="utf-8"))
    assert "pairs" not in gold and gold["human_pairs"] and gold["reference_pairs"]
    assert not any("REF" in (p["a_id"], p["b_id"]) for p in gold["human_pairs"])
    result = ev.agreement(gold, lambda prompt: {"winner": "A"})
    assert result["agreement_with_you"]["pairs"] == len(gold["human_pairs"])
    assert result["reference_sanity"]["pairs"] == len(gold["reference_pairs"])
