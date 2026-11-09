"""v6.36.2 - GPT review of v6.36.1:
1. Keyword filter alone was weak ("... for customers" passed; "Ensure compliance ..." slipped through).
   Now a rule-based move must name two DIFFERENT organisations, who gains and who loses position
   (structural, in parse_draft; the shift is also checked against the sources as a step). The
   compliance filter remains, described as a heuristic backstop.
2. The 'organisation addressed' status was computed before the source checks; it is now recomputed
   from the implications actually shown.
3. A hard ceiling on Gemini API attempts (model calls, grounded searches and retries all count),
   used by try_precedents.py --max-calls.
"""

from __future__ import annotations

import json
import os

import pytest

import services.gemini_llm_service as gemini
import services.research_orchestrator_service as ros
from services import editorial_briefing as brief
from tests.test_v6_36_1_enforced_fit import FACTS, POLICY

PARSE_FACTS = [{"fact_id": "F1", "development": "EU AI Act", "text": FACTS[0]["text"], "evidence_id": "E1"}]


def _item(move, gains="OpenAI", loses="Anthropic", actor="OpenAI", counterparty="Services Australia",
          consequence="gets audit access before renewing", displacement=None):
    # v6.36.3 (GPT review of v6.36.2): counterparty + consequence required; a loser only for displacement.
    item = dict(POLICY, basis="incentive", actor=actor, attractive_move=move, text=move, fact_ids=["F1"],
                who_gains=gains, who_loses=loses, counterparty=counterparty, counterparty_consequence=consequence)
    if displacement is not None:
        item["competitive_displacement"] = displacement
    return item


def test_a_rule_based_move_must_name_a_specific_counterparty():
    ok = brief.parse_draft({"implications": [_item("Offer agencies log access to keep contracts")]}, PARSE_FACTS, [])
    assert len(ok["implications"]) == 1
    for bad in (_item("Comply with reporting duties for customers", counterparty="customers"),
                _item("Offer log access", actor="providers"),
                _item("Offer log access", counterparty="OpenAI"),
                _item("Offer log access", counterparty="the public"),
                _item("Offer log access", consequence="")):
        out = brief.parse_draft({"implications": [bad]}, PARSE_FACTS, [])
        assert out["implications"] == [] and out["position_rejected"], bad


def test_compliance_heuristic_catches_reworded_compliance():
    assert brief.is_compliance_only("Ensure compliance with reporting duties", "Anthropic")
    assert brief.is_compliance_only("Comply with reporting duties for customers", "Services Australia", "Anthropic")
    assert not brief.is_compliance_only("Use early compliance to win the Services Australia contract from Anthropic",
                                        "Anthropic")
    _, fit = brief.enforce_policy_fit({"implications": []}, "OpenAI")
    assert "heuristic" in fit["compliance_filter"]


def test_status_reflects_the_implications_actually_shown(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "OpenAI")
    monkeypatch.delenv("EVENT_INTELLIGENCE_PROFILE", raising=False)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")

    class Grounding:   # the OpenAI implication's facts are NOT backed: the check removes it
        def ground_claims(self, *, claims, evidence_records):
            return {"claims": [{"claim_id": c["claim_id"],
                                "grounding_status": "unsupported" if "OpenAI" in c["claim_text"] and c.get("epistemic_type") == "fact"
                                else "supported" if c.get("epistemic_type") == "fact" else "inference_supported",
                                "supporting_evidence_ids": c.get("evidence_ids") or []} for c in claims]}

    obj.grounding_service = Grounding()
    obj._implication_novelty = lambda developments, evidence, items, **k: (list(items), [], {})
    draft = {"headline": "Fines apply.", "paragraphs": [[{"text": "Fines apply.", "fact_ids": ["F1"]}]], "analogies": [],
             "implications": [_item("Offer agencies log access to keep contracts"),
                              {"text": "Agencies will tighten rules.", "basis": "trend", "fact_ids": ["F1"],
                               "conditions": "c", "watch_for": "w", "horizon": "h"}]}
    obj._ask_briefing_model = lambda prompt: json.dumps(draft)
    developments = [{"title": "EU AI Act", "facts": [{"text": FACTS[0]["text"], "evidence_id": "E1", "source_id": "S1",
                                                     "date": "2026-10-01"}]}]
    out = obj._editorial_briefing(topic="ai", developments=developments, evidence=[], state={})
    fit = out["policy_fit"]
    assert fit["assessed_before_checks"] is True                 # the draft named OpenAI ...
    assert fit["advised_organisation_addressed"] is False         # ... but nothing about OpenAI is shown


class _Models:
    def __init__(self):
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise Exception("503 UNAVAILABLE server error")      # transient: retried
        return type("R", (), {"text": json.dumps({"ok": True}), "candidates": [], "usage_metadata": None})()


def test_call_ceiling_counts_every_attempt_including_retries(monkeypatch):
    service = gemini.GeminiLLMService(api_key="test-key", model_name="gemini-3.5-flash", retry_base_seconds=0.0)
    models = _Models()
    service._client = type("Client", (), {"models": models})()
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    gemini.set_call_budget(1)
    try:
        with pytest.raises(gemini.GeminiCallBudgetExceeded):
            service.ask("hi")                                    # attempt 1 fails, the retry is refused
        assert models.calls == 1 and gemini.calls_used() == 1
    finally:
        gemini.set_call_budget(None)
    gemini.consume_call()                                        # no ceiling: never refused


def test_grounded_search_counts_toward_the_ceiling():
    from services.grounded_search import GeminiGroundedSearch
    import inspect
    source = inspect.getsource(GeminiGroundedSearch)
    assert source.index("consume_call()") < source.index("pool.submit(self.client.models.generate_content")
    text = open(os.path.join(os.path.dirname(__file__), "..", "try_precedents.py"), encoding="utf-8").read()
    assert "gemini.set_call_budget(args.max_calls)" in text and '"--max-calls"' in text
