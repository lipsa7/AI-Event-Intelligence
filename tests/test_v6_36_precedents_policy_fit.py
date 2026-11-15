"""v6.36 - from a no-cost diagnosis of the 2026-10-11 runs' logs:
1. Precedents: gap research found 4-5 dated past cases per run, but the hypothesis-research
   step then REPLACED state["historical_context"], so the briefing logged historical_cases=[]
   and no analogy was ever shown. Cases are now kept.
2. Past cases were matched on wording (another "renaming"), not mechanism: the case-naming
   prompt now asks for the same structure of holder, dependency, rule and response.
3. Each analogy states why it may mislead (GPT review: a past event happening does not prove
   its mechanism transfers).
4. Policy fit: incentive implications named generic actors and compliance steps as "moves";
   with an organisation being advised, one must name it, and complying is not a move.
5. Experiment record: the advised organisation no longer falls back to --org; whether advice
   was produced is recorded, and compare_runs refuses a pair where a run produced none.
6. try_precedents.py tests the precedent stage alone on a checkpoint (dozens of calls, not a replay).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import compare_runs
import services.research_orchestrator_service as ros
from services import editorial_briefing as brief

ROOT = Path(__file__).resolve().parents[1]
CASE = {"evidence_id": "EVID-CASE", "source_url": "https://www.sec.gov/x", "source_excerpt": "In 2020 ...",
        "historical_case": {"year": 2020, "similarity": "vendor held the logs", "difference": "no agent",
                            "outcome_quote": "the SEC charged the company"}}


def test_hypothesis_research_keeps_the_cases_gap_research_found():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._hypothesis_evidence_service = lambda: object()
    state = {"historical_context": {"status": "searched", "records": [CASE]}}
    obj._research_hypotheses(topic="ai", candidates=[], inference_result={}, events=[], evidence=[],
                             graph_context={}, constraints={}, blueprint={}, state=state)
    records = state["historical_context"]["records"]
    assert [r["evidence_id"] for r in records] == ["EVID-CASE"]
    assert state["historical_context"]["status"] == "searched"
    assert [c["evidence_id"] for c in brief.select_cases(records)] == ["EVID-CASE"]   # reaches the briefing


def test_cases_are_named_by_mechanism_not_wording():
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    source = inspect.getsource(HypothesisEvidenceService.investigate_gaps)
    assert "the same MECHANISM as this" in source and "NOT the same wording, topic or headline" in source
    assert "outcome went the other way" in source


def test_analogies_say_why_they_may_mislead():
    prompt = brief.build_prompt("ai", [{"fact_id": "F1", "development": "d", "text": "t"}],
                                [dict(brief.select_cases([CASE])[0])])
    assert "why_it_may_mislead" in prompt and "does not prove its mechanism transfers" in prompt
    draft = brief.parse_draft({"analogies": [{"case_id": "H1", "fact_ids": ["F1"], "comparison": "c", "difference": "d",
                                              "why_it_may_mislead": "the 2020 vendor was a regulated company"}]},
                              [{"fact_id": "F1", "evidence_id": "E1"}], [{"case_id": "H1"}])
    assert draft["analogies"][0]["why_it_may_mislead"] == "the 2020 vendor was a regulated company"
    dash = (ROOT / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert '"Why it may mislead (analysis): "' in dash


def test_policy_fit_names_the_advised_organisation_and_rejects_compliance_as_a_move(monkeypatch):
    prompt = brief.build_prompt("ai", [{"fact_id": "F1", "development": "d", "text": "t"}], [], focus="OpenAI")
    assert "ORGANISATION BEING ADVISED: OpenAI" in prompt and "complying with it is NOT an attractive_move" in prompt
    assert "ORGANISATION BEING ADVISED" not in brief.build_prompt("ai", [], [])
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "OpenAI")
    monkeypatch.delenv("EVENT_INTELLIGENCE_PROFILE", raising=False)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    assert obj._briefing_focus() == "OpenAI"


def test_experiment_record_has_no_org_fallback_and_flags_missing_advice(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "OpenAI")
    monkeypatch.delenv("EVENT_INTELLIGENCE_PROFILE", raising=False)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._developments_to_watch = lambda events, evidence, state: []
    obj._editorial_briefing = lambda **k: {"mode": "model", "implications": [], "analogies": []}
    obj._actor_moves = lambda **k: {"status": "model_unavailable", "actors": []}      # advice step failed
    obj._forecast_followups = lambda **k: {}
    report = {"research_depth": "fast"}
    obj._attach_spec_sections(report=report, topic="ai", events=[], evidence=[], state={"replay_of": "RUN-ED24"})
    exp = report["analysis_metadata"]["experiment"]
    assert exp["focus_organisation"] == "" and exp["requested_organisation"] == "OpenAI"
    assert exp["advice_produced"] is False
    a = {"report_id": "A", "sections": {"analysis_metadata": {"experiment": dict(exp, focus_organisation="OpenAI",
                                                                                   advice_produced=False),
                                                              "organisation_profile": {"used": False}}}}
    b = {"report_id": "B", "sections": {"analysis_metadata": {"experiment": dict(exp, focus_organisation="OpenAI",
                                                                                   advice_produced=True),
                                                              "organisation_profile": {"used": True}}}}
    assert any("produced no advice" in p for p in compare_runs.pair_problems(a, b))


def test_stage_script_exists_and_runs_only_gap_research():
    text = (ROOT / "try_precedents.py").read_text(encoding="utf-8")
    assert "_precedent_research(developments, state" in text and ".run(" not in text
