"""v13.28.2: (1) the slot's FIRST explored candidate gets its own repair (it used to wait behind the
second one in the pool and was never repaired); (2) a candidate whose claims are backed but whose
conclusion only restates its sources gets one "deepen" repair instead of being dropped."""
import inspect

import services.research_orchestrator_service as ros
from tests.test_claim_support_gate import DEEP_PROFILE, FAILING, PASSING, candidate, make_orchestrator
from tests.test_v6_21_4_search_coverage import _events_and_evidence


def _run(obj, monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    monkeypatch.setattr(ros, "INSIGHT_EXPLORATION_COMBINATIONS", 1)
    events, evidence = _events_and_evidence()
    state = {"warnings": [], "run_id": "RUN-T"}
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[candidate("ONE")], grounded={"INSIGHTREV-ONE": FAILING()}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
    return released, state


def test_the_first_of_two_explored_candidates_is_repaired(monkeypatch):
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-BEST": FAILING,
                                      "INSIGHTREV-SECOND": FAILING, "INSIGHTREV-BEST-FIXED": PASSING,
                                      "INSIGHTREV-SECOND-FIXED": FAILING},
                            new_candidates=[[candidate("BEST"), candidate("SECOND")],
                                            [candidate("BEST-FIXED")], [candidate("SECOND-FIXED")]])
    released, state = _run(obj, monkeypatch)
    targets = [c["refinement_context"]["target_insights"][0]["insight_revision_id"]
               for c in obj.analyze_calls if c["refinement_context"].get("target_insights")]
    assert targets[0] == "INSIGHTREV-BEST", targets          # the best candidate is repaired first
    assert "INSIGHTREV-BEST-FIXED" in [r["insight_revision_id"] for r in released]
    assert state["claim_support_gate"]["exploration_repairs"] >= 1


def test_restating_its_sources_leads_to_a_deepen_repair():
    source = inspect.getsource(ros.ResearchOrchestratorService)
    block = source[source.index('"reasons": ["source_backed_not_original:" + r for r in reasons]') - 400:]
    assert '"repairable": origin == "exploration"' in block[:700] and '"repair_kind": "deepen"' in block[:700]
    obj = make_orchestrator(verdicts={})
    out = obj._claim_gate_synthesis(
        kind="repair", failed=candidate("X"), verdict={"repair_kind": "deepen", "reasons": ["x"], "failed_claims": []},
        tried=[], topic="ai", events=[], evidence=[], graph_context={}, constraints={}, blueprint={},
        state={"warnings": []})
    assert out == []
    context = obj.analyze_calls[-1]["refinement_context"]
    assert context["instruction"].startswith("DEEPEN REPAIR")
    assert context["target_insights"][0]["insight_revision_id"] == "INSIGHTREV-X"
    assert "do_not_add_facts_not_in_the_records" in context["improvement_actions"]


def test_only_explored_candidates_are_deepened():
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert source.count('"repairable": origin == "exploration"') == 2


def test_an_explored_candidate_that_restates_its_sources_gets_a_deepen_repair(monkeypatch):
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-PLAIN": PASSING,
                                      "INSIGHTREV-DEEPER": PASSING},
                            new_candidates=[[candidate("PLAIN")], [candidate("DEEPER")]],
                            nonobvious={"passed": False, "reasons": ["conclusion_restates_sources"]})
    _, state = _run(obj, monkeypatch)
    attempts = state["claim_support_gate"]["attempts"]
    plain = next(a for a in attempts if a["insight_revision_id"] == "INSIGHTREV-PLAIN")
    assert plain["reasons"][0].startswith("source_backed_not_original")
    assert plain.get("next") == "deepen"
    assert any(c["refinement_context"]["instruction"].startswith("DEEPEN REPAIR") for c in obj.analyze_calls)
    deeper = next(a for a in attempts if a["insight_revision_id"] == "INSIGHTREV-DEEPER")
    assert deeper["origin"] == "repair" and deeper.get("next") != "deepen"   # not deepened twice


def test_candidates_with_a_route_past_the_single_story_test_go_first():
    Orch = ros.ResearchOrchestratorService
    records = [{"evidence_id": "EVID-NEWS", "event_id": "EVENT-1", "text": "A report."},
               {"evidence_id": "EVID-RULE", "gap_type": "policy", "analytical_context_only": True,
                "text": "The rule takes effect on 1 March 2027."}]
    plain = {"insight": "plain", "supporting_evidence_ids": ["EVID-NEWS"]}
    ruled = {"insight": "ruled", "supporting_evidence_ids": ["EVID-NEWS", "EVID-RULE"]}
    out = Orch._route_ready_first([plain, ruled], records)
    assert [o["insight"] for o in out] == ["ruled", "plain"]
    assert out[0]["metadata"]["route_ready"] == "constraint" and out[1]["metadata"]["route_ready"] == ""


def test_a_primary_document_counts_as_a_constraint_only_from_the_issuer():
    from services import non_obviousness
    official = {"evidence_id": "E", "gap_type": "primary", "source_credibility": {"tier": "official"}}
    news = {"evidence_id": "E", "gap_type": "primary", "source_credibility": {"tier": "news"}}
    assert non_obviousness.is_constraint(official)
    assert not non_obviousness.is_constraint(news)
    assert not non_obviousness.is_constraint({"evidence_id": "E", "gap_type": "commentary"})
