"""Plan fix B (docs/PLAN_next_two_weeks.md): research on the advised organisation runs before
the briefing, so a past case it finds can reach the analogies (RUN-3BF777 lost Capital One 2019:
found at 12:23, briefing chose analogies at 12:21). One search per run; late fallback kept."""
from __future__ import annotations

import json

import services.research_orchestrator_service as ros
from services import editorial_briefing as brief

DEVELOPMENTS = [{"title": "OpenAI agent breach", "actors": ["OpenAI", "Australia"],
                 "facts": [{"text": "OpenAI notified Australia three months after its agent accessed a health site.",
                            "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]}]
GENERAL = {"evidence_id": "EVID-GENERAL", "source_id": "S8", "gap_type": "policy", "analytical_context_only": True,
           "gap_for": "OpenAI agent breach",
           "text": "Under the Security of Critical Infrastructure Act entities must notify ACSC within 72 hours."}
CASE = {"evidence_id": "EVID-CAPONE", "source_id": "S7", "gap_type": "precedent",
        "text": "In 2019 Capital One disclosed a breach of 100 million records.",
        "source_excerpt": "In 2019 Capital One disclosed a breach of 100 million records.",
        "historical_case": {"year": 2019, "date_quote": "In 2019", "outcome_quote": "It paid an $80 million penalty.",
                            "similarity": "cloud provider breach disclosed late", "difference": "human attacker"}}


def orchestrator(monkeypatch, seconds=float("inf")):
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "OpenAI")
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: seconds
    obj._run_profile = lambda: None
    calls = []

    class Service:
        def investigate_gaps(self, *, development, blueprint, search):
            calls.append(development["title"])
            return {"status": "complete", "queries": [], "records": [
                {"evidence_id": "EVID-ORG", "source_id": "S9", "gap_type": "policy", "analytical_context_only": True,
                 "gap_for": development["title"], "text": "OpenAI must report serious incidents under the EU AI Act."},
                dict(CASE, precedent_for=development["title"])]}

    obj._hypothesis_evidence_service = lambda: Service()
    obj._grounded_search = lambda state: None
    obj._ask_strong_model = lambda prompt: (json.dumps({"actors": []}), "pro")
    return obj, calls


def capture_briefing(monkeypatch, obj, state):
    seen = {}

    def build_prompt(topic, facts, cases, focus=""):
        seen["facts"], seen["cases"] = [f["evidence_id"] for f in facts], [c["evidence_id"] for c in cases]
        return "prompt"

    def unavailable(prompt):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(brief, "build_prompt", build_prompt)
    obj._ask_briefing_model = unavailable
    obj._briefing_focus = lambda: ""
    out = obj._editorial_briefing(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
    return out, seen


def test_case_found_by_focus_research_reaches_the_briefing(monkeypatch):
    obj, calls = orchestrator(monkeypatch)
    state = {"gap_context_records": [GENERAL]}
    obj._focus_research_before_briefing(DEVELOPMENTS, state)
    assert state["focus_research_early"]["attempted"] is True
    assert state["focus_research_early"]["research"]["position"] == "before_briefing"
    out, seen = capture_briefing(monkeypatch, obj, state)
    assert out["case_count"] == 1 and seen["cases"] == ["EVID-CAPONE"]


def test_briefing_context_facts_are_unchanged_by_focus_records(monkeypatch):
    # GPT review: keeping the order does not protect general coverage (the selector sorts by
    # credibility before its cap), so compare the actual selected fact IDs.
    obj, _ = orchestrator(monkeypatch)
    _, without = capture_briefing(monkeypatch, obj, {"gap_context_records": [GENERAL]})
    state = {"gap_context_records": [GENERAL]}
    obj._focus_research_before_briefing(DEVELOPMENTS, state)
    assert any(r["evidence_id"] == "EVID-ORG" for r in state["gap_context_records"])
    _, with_focus = capture_briefing(monkeypatch, obj, state)
    assert with_focus["facts"] == without["facts"] and "EVID-ORG" not in with_focus["facts"]


def test_actor_moves_reuses_the_early_research_and_never_searches_twice(monkeypatch):
    obj, calls = orchestrator(monkeypatch)
    state = {"gap_context_records": [GENERAL]}
    obj._focus_research_before_briefing(DEVELOPMENTS, state)
    obj._focus_research_before_briefing(DEVELOPMENTS, state)   # a second call is a no-op
    out = obj._actor_moves(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
    assert len(calls) == 1
    assert out["focus_organisation"]["research"]["position"] == "before_briefing"
    assert "case_found_after_briefing" not in state


def test_low_time_budget_keeps_late_research_and_records_the_late_case(monkeypatch):
    obj, calls = orchestrator(monkeypatch, seconds=ros.ResearchOrchestratorService.FOCUS_RESEARCH_MIN_SECONDS
                              + ros.ResearchOrchestratorService.ACTOR_MOVES_MIN_SECONDS + 1)
    state = {"gap_context_records": [GENERAL]}
    obj._focus_research_before_briefing(DEVELOPMENTS, state)
    assert state["focus_research_early"] == {"attempted": False, "reason": "time_budget"} and not calls
    out = obj._actor_moves(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
    assert len(calls) == 1 and out["focus_organisation"]["research"]["position"] == "after_briefing"
    assert state["case_found_after_briefing"] == [
        {"evidence_id": "EVID-CAPONE", "year": 2019, "selectable_as_analogy": True}]


def test_without_an_organisation_nothing_is_researched(monkeypatch):
    obj, calls = orchestrator(monkeypatch)
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "")
    state = {}
    obj._focus_research_before_briefing([{"title": "x", "actors": ["tech CEOs"], "facts": []}], state)
    assert not calls and "focus_research_early" not in state
