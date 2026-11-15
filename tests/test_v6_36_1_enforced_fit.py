"""v6.36.1 - GPT review of v6.36: enforce in code what v6.36 only asked for in prompts.
1. Policy fit: an incentive implication whose move is only to comply with the rule is dropped (code,
   not prompt); if no leverage/incentive implication names the organisation advised, one targeted
   top-up call asks for it, and the report says plainly when none passed.
2. An analogy without 'why it may mislead' is not shown.
3. try_precedents.py runs precedent searches ONLY, with hard caps (2 searches, 4 pages).
"""

from __future__ import annotations

import json

import services.research_orchestrator_service as ros
import tests.test_v6_17_report_sections as base
from services import editorial_briefing as brief

FACTS = [{"fact_id": "F1", "development": "EU AI Act", "text": "Providers face fines of 3% of turnover.",
          "evidence_id": "E1", "date": "2026-10-01"}]
POLICY = {"policy_or_regulation": "EU AI Act", "company_relevance": "OpenAI is a provider",
          "economic_or_strategic_effect": "raises the cost of slow disclosure", "conditions": "fines enforced"}


def _incentive(actor, move, gains="OpenAI", loses="Anthropic"):
    # v6.36.3: a named counterparty and its consequence; a loser only for displacement claims.
    return dict(POLICY, basis="incentive", actor=actor, attractive_move=move, text=f"{actor}: {move}", fact_ids=["F1"],
                counterparty="Services Australia", counterparty_consequence="gets audit access before renewing",
                who_gains=gains, who_loses=loses)


def test_compliance_only_moves_are_dropped_in_code():
    assert brief.is_compliance_only("Submit incident reports to the EU AI Office within the deadline")
    assert brief.is_compliance_only("Comply with SOCI notification duties")
    assert not brief.is_compliance_only("Offer agencies a 72-hour notice guarantee before its rivals do")
    # v6.36.2: naming the party that loses position is what lets a compliance-flavoured move through
    assert not brief.is_compliance_only("Submit early incident reports to win contracts from Anthropic", "Anthropic")
    draft = {"implications": [_incentive("OpenAI", "Comply with the EU AI Act reporting rules"),
                              _incentive("OpenAI", "Offer government customers log access to keep contracts")]}
    out, fit = brief.enforce_policy_fit(draft, "OpenAI")
    assert [i["attractive_move"] for i in out["implications"]] == ["Offer government customers log access to keep contracts"]
    assert fit["compliance_moves_rejected"] == ["Comply with the EU AI Act reporting rules"]
    assert fit["advised_organisation_addressed"] is True
    _, generic = brief.enforce_policy_fit({"implications": [_incentive("Model providers", "Raise prices for agents", gains="Microsoft")]},
                                          "OpenAI")
    assert generic["advised_organisation_addressed"] is False
    assert brief.enforce_policy_fit({"implications": []}, "")[1]["advised_organisation_addressed"] is None


def test_briefing_asks_once_for_the_advised_organisation_and_reports_the_result(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "OpenAI")
    monkeypatch.delenv("EVENT_INTELLIGENCE_PROFILE", raising=False)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")
    class Grounding:   # every claim backed, so the briefing is not replaced by the plain fallback
        def ground_claims(self, *, claims, evidence_records):
            return {"claims": [{"claim_id": c["claim_id"], "grounding_status":
                                "supported" if c.get("epistemic_type") == "fact" else "inference_supported",
                                "supporting_evidence_ids": c.get("evidence_ids") or []} for c in claims]}

    obj.grounding_service = Grounding()
    obj._implication_novelty = lambda developments, evidence, items, **k: (list(items), [], {})
    prompts = []
    first = {"headline": "h", "paragraphs": [[{"text": "Fines apply.", "fact_ids": ["F1"]}]], "analogies": [],
             "implications": [_incentive("Model providers", "Comply with reporting rules"),
                              dict(text="Agencies will tighten rules.", basis="trend", fact_ids=["F1"],
                                   conditions="c", watch_for="w", horizon="h")] * 1}
    second = {"implications": [_incentive("OpenAI", "Offer regulators early incident reports to win public-sector contracts")]}

    def ask(prompt):
        prompts.append(prompt)
        return json.dumps(second if "with OpenAI as the holder or actor" in prompt else first)

    obj._ask_briefing_model = ask
    developments = [{"title": "EU AI Act", "facts": [{"text": FACTS[0]["text"], "evidence_id": "E1", "source_id": "S1",
                                                     "date": "2026-10-01"}]}]
    out = obj._editorial_briefing(topic="ai", developments=developments, evidence=[], state={})
    fit = out.get("policy_fit") or {}
    assert any("with OpenAI as the holder or actor" in p for p in prompts)        # one targeted top-up
    assert fit.get("focus_topup") is True and fit["advised_organisation_addressed"] is True
    assert "Comply with reporting rules" in fit["compliance_moves_rejected"]


def test_analogy_without_its_limits_is_not_shown():
    cases = [{"case_id": "H1"}]
    base_item = {"case_id": "H1", "fact_ids": ["F1"], "comparison": "c", "difference": "d"}
    assert brief.parse_draft({"analogies": [base_item]}, FACTS, cases)["analogies"] == []
    kept = brief.parse_draft({"analogies": [dict(base_item, why_it_may_mislead="different regulator")]}, FACTS, cases)
    assert kept["analogies"][0]["why_it_may_mislead"] == "different regulator"


def test_stage_test_searches_precedents_only_within_hard_caps():
    service, search, grounded = base._gap_setup(results_per_query=3)
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search, only_gaps=("precedent",), max_sources=4)
    assert {q["gap"] for q in receipt["queries"]} == {"precedent"}                # no policy/commentary searches
    assert len(receipt["queries"]) <= 2
    assert all(r.get("gap_type") == "precedent" for r in receipt["records"])
    assert grounded.calls <= 3
