"""v6.21.4: search coverage for past cases (section 3) and hidden insights (section 4).

Replays showed both sections depended on one guess: one precedent search per
development, and one insight candidate (all three attempts in one replay told
the same story; in another the only candidate failed final quality before the
claim gate, so nothing else was tried).

1. Past cases: two named-case searches per development; if no case WITH an
   observed outcome was found, one follow-up search (for what followed the
   case found, or for a different named case).
2. Hidden insight: when no candidate passes, one candidate per untried
   combination of developments goes through the same claim gate.
3. The log states which historical cases were unusable and why.
"""

from __future__ import annotations

import copy
import inspect
import json
from datetime import datetime, timezone

import pytest

import services.research_orchestrator_service as ros
from services import editorial_briefing as brief
from services.hypothesis_evidence_service import HypothesisEvidenceService
from tests.test_claim_support_gate import DEEP_PROFILE, PASSING, FAILING, candidate, make_orchestrator


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


class FakeEngine:
    def __init__(self, planner, found):
        self.planner, self.found, self.queries = planner, found, []

    def discover(self, **kwargs):
        self.queries += [q["query"] for q in self.planner("x")["queries"]]
        return {"discovered_evidence": copy.deepcopy(self.found)}


def _followup(records, gap_of_query, found, ask=None):
    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=None)
    if ask:
        service._ask = ask
    engines = []

    def make_engine(planner):
        engines.append(FakeEngine(planner, found))
        return engines[-1]

    out = service._precedent_followup(records=records, gap_of_query=gap_of_query, title="dev", now=NOW,
                                      compiled={"evidence_discovery_policy": {}}, make_engine=make_engine,
                                      candidate={"insight_id": "G", "insight": "i", "premises": []},
                                      extend_deadline=lambda seconds: None)
    return out, engines


FULL = {"evidence_id": "E-FULL", "historical_case": {"year": 2017, "date_quote": "USDA staff were told",
                                                      "outcome_quote": "The guidance was withdrawn."}}
PARTIAL = {"evidence_id": "E-PART", "historical_case": {"year": 2017, "date_quote": "USDA staff were told to avoid "
                                                         "the term climate change", "outcome_quote": ""}}


def test_case_without_outcome_triggers_a_search_for_what_followed():
    out, engines = _followup([PARTIAL], {"usda climate change 2017": "precedent"}, [FULL])
    assert out["reason"] == "case_found_without_outcome" and out["cases_found"] == 1
    assert "2017 outcome what happened next" in engines[0].queries[0]
    assert out["records"][0]["evidence_id"] == "E-FULL"


def test_no_case_asks_for_a_different_named_case():
    asked = []

    def ask(prompt, *, timeout, task):
        asked.append((task, prompt))
        return {"query": "Facebook Cambridge Analytica 2018 FTC settlement"}

    out, engines = _followup([], {"usda climate change 2017": "precedent"}, [FULL], ask=ask)
    assert asked[0][0] == "precedent_retry_plan" and "usda climate change 2017" in asked[0][1]
    assert engines[0].queries == ["Facebook Cambridge Analytica 2018 FTC settlement"]
    assert out["reason"] == "no_case_found"


def test_no_followup_when_a_complete_case_exists_or_no_precedent_was_searched():
    assert _followup([FULL], {"q": "precedent"}, [])[0]["status"] == "not_needed"
    assert _followup([], {"q": "policy"}, [])[0]["status"] == "not_needed"


def test_two_precedent_searches_fit_in_the_budget():
    assert HypothesisEvidenceService.GAP_MAX_QUERIES == 7     # v6.22: + expert commentary; v11: + primary document
    assert HypothesisEvidenceService.GAP_MAX_SOURCES >= HypothesisEvidenceService.GAP_MAX_QUERIES * 2
    assert "TWO different specific earlier cases" in inspect.getsource(HypothesisEvidenceService.investigate_gaps)


# 2 hidden-insight exploration ------------------------------------------

def _events_and_evidence():
    events, evidence = [], []
    for cluster, count in (("dev_eu", 3), ("dev_breach", 3), ("dev_rename", 2), ("dev_small", 1)):
        for i in range(count):
            event_id = f"EVENT-{cluster}-{i}".upper()
            # v10.3: developments are combined only when they share a named actor; these share OpenAI.
            events.append({"event_id": event_id, "underlying_event_id": f"U-{cluster}-{i}",
                           "strategic_cluster_id": cluster, "strategic_cluster_label": cluster.replace("dev_", ""),
                           "strategic_cluster_status": "verified",  # v13: only verified clusters group events
                           "actors": ["OpenAI"]})
            evidence.append({"evidence_id": f"EVID-{cluster}-{i}".upper(), "event_id": event_id,
                             "source_id": f"S-{cluster}-{i}", "text": "fact"})
    evidence.append({"evidence_id": "EVID-RULE", "source_id": "S-RULE", "analytical_context_only": True, "text": "rule"})
    # The records the gate-test fixtures cite (two different sources).
    evidence += [{"evidence_id": "EVID-A", "source_id": "SOURCE-A", "event_id": "EVENT-DEV_EU-0", "text": "Fact A."},
                 {"evidence_id": "EVID-B", "source_id": "SOURCE-B", "event_id": "EVENT-DEV_BREACH-0", "text": "Fact B."}]
    return events, evidence


def test_combinations_cover_different_developments_and_skip_used_ones():
    obj = make_orchestrator(verdicts={})
    events, evidence = _events_and_evidence()
    combos = obj._development_combinations(events, evidence, limit=6)
    assert combos[0]["keys"] == ["dev_breach", "dev_eu", "dev_rename"]          # the top triple first
    # v6.21.8: one single-development combination (read against the context records) is allowed.
    assert sum(len(set(c["keys"])) == 1 for c in combos) <= 1
    used = {"supporting_evidence_ids": ["EVID-DEV_EU-0", "EVID-DEV_BREACH-0"]}
    after = obj._development_combinations(events, evidence, [used], limit=6)
    assert ["dev_breach", "dev_eu"] not in [c["keys"] for c in after]


def test_exploration_runs_when_no_candidate_passes_and_its_candidate_is_gated(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    events, evidence = _events_and_evidence()
    explored = candidate("EXPLORED")
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-EXPLORED": PASSING},
                            new_candidates=[[explored]])
    state = {"warnings": [], "run_id": "RUN-X"}
    grounded = {"INSIGHTREV-ONE": FAILING()}
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[candidate("ONE")], grounded=grounded, events=events, evidence=evidence,
        graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
    assert [r["insight_revision_id"] for r in released] == ["INSIGHTREV-EXPLORED"]
    # v13 (18 Nov 2026): exploration goes on to other stories until the run's insight limit (it used
    # to stop at the first release); the stub returns nothing more, so only one candidate is released.
    assert len(obj.analyze_calls) >= 1
    call = obj.analyze_calls[0]
    assert "SYSTEMATIC EXPLORATION" in call["refinement_context"]["instruction"]
    assert len(call["current_events"]) < len(events)                       # only that combination's events
    # v13.31.3: a context record stays in a story's slot only if it is about that story; the placeholder
    # "rule" shares no words with the story's records, so it is left out and counted.
    assert not any(r["evidence_id"] == "EVID-RULE" for r in call["evidence"])
    assert any(c.get("context_off_story", 0) >= 1 for c in state["claim_support_gate"]["exploration"]["combinations"])
    attempts = state["claim_support_gate"]["attempts"]
    assert [a["origin"] for a in attempts] == ["pipeline", "exploration"]
    assert state["claim_support_gate"]["exploration"]["combinations"]


def test_an_explored_candidate_gets_its_own_repair_after_the_run_wide_retries_are_used(monkeypatch):
    """v13.14 (20 Nov 2026, RUN-FF8F59D2...): the run-wide limit of extra attempts was used up by the
    first candidate, so the Forum AI election candidate - failed on one premise - was never repaired.
    Each explored story's candidate now gets one repair of its own when its failure is repairable."""
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    events, evidence = _events_and_evidence()
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING, "INSIGHTREV-EXPLORED": FAILING,
                                      "INSIGHTREV-FIXED": PASSING},
                            new_candidates=[[candidate("EXPLORED")], [candidate("FIXED")]])
    state = {"warnings": [], "run_id": "RUN-R"}
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[candidate("ONE")], grounded={"INSIGHTREV-ONE": FAILING()}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
    origins = [a["origin"] for a in state["claim_support_gate"]["attempts"]]
    assert origins[:3] == ["pipeline", "exploration", "repair"], origins
    assert [r["insight_revision_id"] for r in released] == ["INSIGHTREV-FIXED"]
    assert state["claim_support_gate"]["exploration_repairs"] == 1


def test_exploration_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    monkeypatch.setattr(ros, "INSIGHT_EXPLORATION_COMBINATIONS", 0)
    events, evidence = _events_and_evidence()
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": FAILING}, new_candidates=[[candidate("X")]])
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[candidate("ONE")], grounded={"INSIGHTREV-ONE": FAILING()}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE,
        state={"warnings": [], "run_id": "RUN-Y"})
    assert released == [] and obj.analyze_calls == []


def test_a_candidate_dropped_by_final_quality_still_leads_to_exploration():
    source = inspect.getsource(ros.ResearchOrchestratorService)
    branch = source.index("the only candidate failed final quality before the")
    abstain = source.index('reasons=["no_insight_remained_after_final_quality_and_grounding"]')
    assert branch < abstain


# 3 historical-case log --------------------------------------------------

def test_unusable_historical_cases_are_explained():
    rows = brief.case_rejections([
        {"evidence_id": "E1", "historical_case": {"year": 2017, "similarity": "s", "difference": "d", "outcome_quote": ""}},
        {"evidence_id": "E2", "historical_case": {"year": 2014, "similarity": "s", "difference": "d", "outcome_quote": "o"}},
    ])
    assert rows[0]["reasons"] == ["no_observed_outcome"] and rows[1]["usable"] is True


import pytest as _pytest_legacy


@_pytest_legacy.fixture(autouse=True)
def _legacy_gate_mode(monkeypatch):
    """These tests pin claim-support-gate v1-v3 rules, kept as EVENT_INTELLIGENCE_GATE_MODE=legacy
    since v4.0 (two-lane gate, 12 Nov 2026); tests/test_two_lane_gate.py covers v4.0."""
    monkeypatch.setenv("EVENT_INTELLIGENCE_GATE_MODE", "legacy")
