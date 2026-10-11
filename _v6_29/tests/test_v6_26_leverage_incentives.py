"""v6.26: the product's bar - leverage plus incentives - and the plain-meaning check.

The user's bar (2026-10-10): "you see the leverage for a thing and add incentives".
Objective (spec): forward reasoning in three forms - precedent, leverage, policy/incentive fit.
The candidates so far were "X while Y" juxtapositions and a strongly worded restatement
("renaming AI 'Super Intelligence' neutralises the Ban Artificial Superintelligence Act")
whose plain meaning one of its own sources already stated.
"""

from __future__ import annotations

import json
import pathlib

import services.research_orchestrator_service as ros
from services import claim_support_gate as gate
from services import non_obviousness as nob
from tests.test_claim_support_gate import DEEP_PROFILE, PASSING, candidate, make_orchestrator
from tests.test_v6_21_4_search_coverage import _events_and_evidence

LEVERAGE = {"holder": "OpenAI", "controls": "the logs of what its agents accessed",
            "dependent": "Services Australia", "leverage_evidence_ids": ["EVID-A"],
            "holder_incentive": "EU AI Act fines up to 3% of turnover for late incident reports",
            "holder_incentive_evidence_ids": ["EVID-B"],
            "dependent_incentive": "the SOCI Act puts the reporting duty on the operator",
            "dependent_incentive_evidence_ids": ["EVID-A"],
            "predicted_move": "OpenAI discloses faster where it carries the penalty than where its customers do"}


# 1 the bar -----------------------------------------------------------------

def test_missing_leverage_or_incentive_fails_the_bar():
    assert gate.leverage_reasons({"metadata": {"leverage_analysis": LEVERAGE}}) == []
    partial = dict(LEVERAGE, holder_incentive="", predicted_move="")
    assert gate.leverage_reasons({"metadata": {"leverage_analysis": partial}}) == [
        "no_leverage_incentive_structure:holder_incentive,predicted_move"]
    assert gate.leverage_reasons({}) == [
        "no_leverage_incentive_structure:holder,controls,dependent,holder_incentive,predicted_move"]


def test_leverage_and_incentives_are_checked_as_facts():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    item = candidate("L")
    item["metadata"]["leverage_analysis"] = LEVERAGE
    claims = {c["claim_id"]: c for c in obj._claims_from_insight(item)}
    lev = claims["INSIGHTREV-L-LEVERAGE"]
    assert lev["epistemic_type"] == "fact" and lev["evidence_ids"] == ["EVID-A"]
    assert lev["claim_text"] == ("OpenAI controls or holds the logs of what its agents accessed, which Services "
                                 "Australia depends on or needs.")
    assert claims["INSIGHTREV-L-HOLDER-INCENTIVE"]["claim_text"].startswith("For OpenAI: EU AI Act")
    assert claims["INSIGHTREV-L-DEPENDENT-INCENTIVE"]["epistemic_type"] == "fact"


def test_juxtaposition_without_leverage_is_sent_back_before_any_check(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 1)
    monkeypatch.setattr(ros, "INSIGHT_EXPLORATION_COMBINATIONS", 0)
    jux = candidate("JUX")
    jux["metadata"].pop("leverage_analysis")
    obj = make_orchestrator(verdicts={"INSIGHTREV-JUX": PASSING}, new_candidates=[[]])
    events, evidence = _events_and_evidence()
    state = {"warnings": [], "run_id": "RUN-L"}
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[jux], grounded={"INSIGHTREV-JUX": PASSING()}, events=events, evidence=evidence,
        graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
    assert released == []
    attempt = state["claim_support_gate"]["attempts"][0]
    assert attempt["reasons"][0].startswith("no_leverage_incentive_structure")
    brief = obj.analyze_calls[0]["refinement_context"]
    assert "leverage" in json.dumps(brief).casefold()


def test_strategic_model_is_asked_for_leverage_and_runs_on_pro_first():
    import inspect
    import agents.strategic_inference_agent as agent
    source = inspect.getsource(agent)
    assert '"leverage_analysis":{"holder"' in source and "LEVERAGE PLUS INCENTIVES" in source
    assert "EXEMPLAR 5" in source
    assert agent.STRATEGIC_MODEL_SPECS[0]["model"] == "gemini-3.1-pro-preview"
    assert agent.STRATEGIC_MODEL_SPECS[0]["timeout_seconds"] >= 120
    normalized = agent.StrategicInferenceAgent._normalize_leverage(dict(LEVERAGE, extra="x"))
    assert normalized["holder"] == "OpenAI" and normalized["leverage_evidence_ids"] == ["EVID-A"]
    assert agent.StrategicInferenceAgent._normalize_leverage({"holder": ""}) == {}


# 2 plain meaning -------------------------------------------------------------

EVENTS = [{"event_id": "EV-R", "underlying_event_id": "U-RENAME", "title": "Trump renames AI Super Intelligence"},
          {"event_id": "EV-B", "underlying_event_id": "U-BILL", "title": "Sanders and Casar introduce AI ban bill"}]
RENAME = {"evidence_id": "E-R", "event_id": "EV-R", "source_id": "S1",
          "text": "Official policy documents will refer only to 'Super Intelligence', under a new executive order."}
BILL = {"evidence_id": "E-B", "event_id": "EV-B", "source_id": "S2",
        "text": "The Ban Artificial Superintelligence Act would outlaw the very kind of system Trump has now made the "
                "official name for all of AI."}
DRESSED = ("The administrative replacement of the statutory definition of AI with 'Super Intelligence' acts as a "
           "structural defense mechanism that neutralizes targeted legislative safety bans.")
PLAIN = "The bill bans the same thing Trump now calls all AI."


def test_strongly_worded_restatement_fails_on_its_plain_meaning():
    def ask(prompt):
        return json.dumps({"plain": PLAIN}) if "ONE plain sentence" in prompt else '{"takeaways": ["x"]}'

    checked = []

    def ground(claims, records):
        checked.append((claims[0]["claim_text"], sorted(r["evidence_id"] for r in records)))
        # The bill article states the plain version outright.
        status = "supported" if claims[0]["claim_text"] == PLAIN and "E-B" in [r["evidence_id"] for r in records] \
            else "unsupported"
        return {"claims": [{"claim_id": c["claim_id"], "grounding_status": status} for c in claims]}

    grounding = {"claims": [{"claim_id": "C", "claim_text": DRESSED, "epistemic_type": "hidden_insight",
                             "grounding_status": "inference_supported", "supporting_evidence_ids": ["E-R", "E-B"]}]}
    out = nob.check(grounding=grounding, evidence=[RENAME, BILL], events=EVENTS, ground=ground, ask=ask)
    assert not out["passed"] and out["reasons"] == ["plain_meaning_restates_sources"]
    assert out["claims"][0]["plain"] == PLAIN
    assert checked[0] == (PLAIN, ["E-B", "E-R"])                      # judged on the plain version
    assert "plain" in nob.brief(out["reasons"])[0]


def test_plain_rewrite_failure_fails_closed():
    def ask(prompt):
        if "ONE plain sentence" in prompt:
            raise TimeoutError("slow")
        return '{"takeaways": ["x"]}'

    grounding = {"claims": [{"claim_id": "C", "claim_text": DRESSED, "epistemic_type": "hidden_insight",
                             "grounding_status": "inference_supported", "supporting_evidence_ids": ["E-R", "E-B"]}]}
    out = nob.check(grounding=grounding, evidence=[RENAME, BILL], events=EVENTS,
                    ground=lambda c, r: {"claims": []}, ask=ask)
    assert out["reasons"] == ["non_obviousness_untested:plain_rewrite_TimeoutError"]


# 3 report, dashboard, spec ---------------------------------------------------

def test_report_dashboard_and_spec():
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert '"leverage_analysis": _json_safe(insight_metadata.get("leverage_analysis", {}))' in source
    root = pathlib.Path(__file__).resolve().parents[1]
    dash = (root / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "**Leverage:**" in dash and "**Therefore:**" in dash
    spec = (root / "docs" / "PRODUCT_SPEC.md").read_text(encoding="utf-8")
    assert "OBJECTIVE AND SETTLED DECISIONS" in spec and "you see the leverage for a thing and add incentives" in spec
