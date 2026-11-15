"""Typed leverage (claim-support-gate v3.0, 8 Nov 2026).

Case: the 25 Oct run (REPORT-316E7FC814727B8CCAA30C52) rejected every candidate because the
leverage and the incentives were checked as facts a source must state ("the evidence does not
state the purpose of the funding"). Control stays a fact; dependency and incentives are reasoning.
"""
from services import claim_support_gate as gate


def grounding(*claims):
    return {"review_status": "complete", "claims": [
        {"claim_id": cid, "epistemic_type": kind, "grounding_status": status, "claim_text": cid,
         "supporting_evidence_ids": ["EVID-A", "EVID-B"], "unsupported_parts": ["not stated"]}
        for cid, kind, status in claims]}


SOURCES = {"EVID-A": "S1", "EVID-B": "S2"}
BASE = [("R-P1", "fact", "supported"), ("R-INSIGHT-01", "hidden_insight", "inference_supported"),
        ("R-CONTROL", "fact", "supported")]


def test_inferred_incentive_passes_and_unstated_incentive_is_a_labelled_assumption():
    verdict = gate.evaluate(grounding(*BASE, ("R-DEPENDENCY", "connection", "inference_supported"),
                                      ("R-HOLDER-INCENTIVE", "connection", "inference_supported"),
                                      ("R-DEPENDENT-INCENTIVE", "connection", "unsupported")), SOURCES)
    assert verdict["passed"], verdict["reasons"]
    [assumption] = verdict["key_assumptions"]
    assert assumption["kind"] == "incentive" and assumption["text"].startswith("Inferred incentive")
    assert assumption["claim_id"] == "R-DEPENDENT-INCENTIVE"


def test_partly_backed_dependency_is_an_assumption_but_unbacked_dependency_blocks():
    partly = gate.evaluate(grounding(*BASE, ("R-DEPENDENCY", "connection", "partially_supported"),
                                     ("R-HOLDER-INCENTIVE", "connection", "supported")), SOURCES)
    assert partly["passed"] and partly["key_assumptions"][0]["kind"] == "dependency"
    unbacked = gate.evaluate(grounding(*BASE, ("R-DEPENDENCY", "connection", "unsupported"),
                                       ("R-HOLDER-INCENTIVE", "connection", "supported")), SOURCES)
    assert not unbacked["passed"]
    assert "leverage_dependency_not_supported_by_cited_evidence" in unbacked["reasons"]


def test_control_is_still_a_fact_and_contradiction_still_blocks():
    no_control = gate.evaluate(grounding(("R-P1", "fact", "supported"),
                                         ("R-INSIGHT-01", "hidden_insight", "inference_supported"),
                                         ("R-CONTROL", "fact", "partially_supported"),
                                         ("R-DEPENDENCY", "connection", "supported")), SOURCES)
    assert not no_control["passed"] and "premise_not_supported_by_cited_evidence" in no_control["reasons"]
    contradicted = gate.evaluate(grounding(*BASE, ("R-HOLDER-INCENTIVE", "connection", "contradicted")), SOURCES)
    assert not contradicted["passed"] and not contradicted["repairable"]


def test_assumption_limit_counts_leverage_assumptions():
    verdict = gate.evaluate(grounding(*BASE, ("R-DEPENDENCY", "connection", "partially_supported"),
                                      ("R-HOLDER-INCENTIVE", "connection", "unsupported"),
                                      ("R-DEPENDENT-INCENTIVE", "connection", "unsupported"),
                                      ("R-MECHANISM-01", "mechanism", "unsupported")),
                            SOURCES, {"EVID-A": "E1", "EVID-B": "E2"}, {"EVID-A": "P1", "EVID-B": "P2"})
    assert not verdict["passed"] and "too_many_unverified_assumptions" in verdict["reasons"]
    assert verdict["key_assumptions"] == []


def test_old_single_leverage_fact_claim_is_judged_as_before():
    verdict = gate.evaluate(grounding(("R-P1", "fact", "supported"),
                                      ("R-INSIGHT-01", "hidden_insight", "inference_supported"),
                                      ("R-LEVERAGE", "fact", "partially_supported")), SOURCES)
    assert not verdict["passed"]


def test_leverage_parties_must_be_named():
    lev = {"holder": "TypeSafe Inc. and ASI Inc.", "controls": "proprietary models",
           "dependent": "Venture investors seeking direct financial exposure to AI model infrastructure",
           "holder_incentive": "x", "predicted_move": "y"}
    assert gate.leverage_reasons({"leverage_analysis": lev}) == ["leverage_party_not_named:dependent"]
    assert gate.leverage_reasons({"leverage_analysis": dict(lev, dependent="the White House")}) == []
    assert not gate.named_party("Institutional capital providers seeking AI exposure")
    assert gate.named_party("EU regulators") and gate.named_party("Microsoft")
    assert any("NAMED" in line for line in gate.so_what_brief(["leverage_party_not_named:dependent"]))


def test_mechanism_steps_are_checked_against_the_premises_they_cite():
    """Claude-proxy replay of the 25 Oct run: a mechanism with only analogical (past-case) IDs was
    checked against the past cases alone, so every step failed."""
    from services.research_orchestrator_service import ResearchOrchestratorService as ROS
    obj = ROS.__new__(ROS)
    insight = {"revision_id": "INSIGHTREV-M", "insight": "X will happen.",
               "premises": [{"premise_id": "P1", "text": "a", "evidence_ids": ["EVID-1"]},
                            {"premise_id": "P2", "text": "b", "evidence_ids": ["EVID-2"]}],
               "supporting_evidence_ids": ["EVID-1", "EVID-2"],
               "mechanism": {"description": "Observed: A holds B (P2). Then C follows.",
                             "analogical_evidence_ids": ["EVID-PAST"]}}
    steps = [c for c in obj._claims_from_insight(insight) if "-MECHANISM-" in c["claim_id"]]
    assert steps[0]["evidence_ids"][0] == "EVID-2" and "EVID-1" not in steps[0]["evidence_ids"]
    assert "EVID-PAST" in steps[0]["evidence_ids"]            # past cases added, never alone
    assert {"EVID-1", "EVID-2"} <= set(steps[1]["evidence_ids"])


def test_watch_sentences_are_not_checked_as_conclusions():
    from services.research_orchestrator_service import ResearchOrchestratorService as ROS
    obj = ROS.__new__(ROS)
    insight = {"revision_id": "INSIGHTREV-W", "supporting_evidence_ids": ["EVID-1"],
               "insight": "X is exposed to the probe. Watch whether the FTC issues civil investigative demands."}
    texts = [c["claim_text"] for c in obj._claims_from_insight(insight) if "-INSIGHT-" in c["claim_id"]]
    assert texts == ["X is exposed to the probe."]


def _g(*rows):
    return {"review_status": "complete", "claims": [
        {"claim_id": cid, "epistemic_type": kind, "grounding_status": status, "claim_text": text,
         "supporting_evidence_ids": ["EVID-A", "EVID-B"], "unsupported_parts": []}
        for cid, kind, status, text in rows]}


def test_labelled_forecast_in_the_conclusion_is_a_labelled_judgment():
    base = [("R-P1", "fact", "supported", "p"), ("R-CONTROL", "fact", "supported", "c"),
            ("R-INSIGHT-01", "hidden_insight", "inference_supported", "The accord adds no penalty.")]
    forecast = ("R-INSIGHT-02", "hidden_insight", "unsupported",
                "Hypothesis: when issued, the FTC's demands will ask about the incident.")
    verdict = gate.evaluate(_g(*base, forecast), SOURCES)
    assert verdict["passed"], verdict["reasons"]
    [item] = verdict["key_assumptions"]
    assert item["kind"] == "forecast" and item["text"].startswith("Forecast - the app's judgment")
    contradicted = gate.evaluate(_g(*base, forecast[:2] + ("contradicted", forecast[3])), SOURCES)
    assert not contradicted["passed"]
    only_forecast = gate.evaluate(_g(("R-P1", "fact", "supported", "p"), forecast), SOURCES)
    assert "no_conclusion_claim_checked" in only_forecast["reasons"]


def test_revision_narration_is_not_checked_as_a_conclusion():
    from services.research_orchestrator_service import ResearchOrchestratorService as ROS
    obj = ROS.__new__(ROS)
    insight = {"revision_id": "INSIGHTREV-N", "supporting_evidence_ids": ["EVID-1"],
               "insight": "The FTC probe is the binding channel. The earlier version is narrowed accordingly."}
    texts = [c["claim_text"] for c in obj._claims_from_insight(insight) if "-INSIGHT-" in c["claim_id"]]
    assert texts == ["The FTC probe is the binding channel."]


def test_mechanism_step_is_checked_against_the_chain_so_far():
    from services.research_orchestrator_service import ResearchOrchestratorService as ROS
    obj = ROS.__new__(ROS)
    insight = {"revision_id": "INSIGHTREV-C", "insight": "X.",
               "premises": [{"premise_id": f"P{i}", "text": "t", "evidence_ids": [f"EVID-{i}"]} for i in (1, 2, 3)],
               "mechanism": {"description": "Observed: A (P1). Observed: B (P2). Inference: so C (P3)."}}
    steps = [c for c in obj._claims_from_insight(insight) if "-MECHANISM-" in c["claim_id"]]
    assert steps[0]["evidence_ids"] == ["EVID-1"]
    assert set(steps[2]["evidence_ids"]) == {"EVID-1", "EVID-2", "EVID-3"}


def test_single_credible_source_conclusion_is_released_labelled(monkeypatch):
    rows = [("R-P1", "fact", "supported", "p"), ("R-CONTROL", "fact", "supported", "c"),
            ("R-INSIGHT-01", "hidden_insight", "inference_supported", "The FTC probe is the binding channel.")]
    g = _g(*rows)
    g["claims"][2]["supporting_evidence_ids"] = ["EVID-A"]
    one = {"EVID-A": "S1", "EVID-B": "S2"}
    credible = gate.evaluate(g, one, credible_sources={"S1"})
    assert credible["passed"] and credible["confidence"].startswith("moderate")
    assert credible["single_source_claims"][0]["text"].startswith("Single source, not yet corroborated")
    assert not gate.evaluate(g, one, credible_sources=set())["passed"]          # unrated source: held back
    assert not gate.evaluate(g, one)["passed"]                                   # caller gave no ratings
    monkeypatch.setenv("EVENT_INTELLIGENCE_SINGLE_SOURCE_RELEASE", "0")
    assert not gate.evaluate(g, one, credible_sources={"S1"})["passed"]          # switched off
    monkeypatch.delenv("EVENT_INTELLIGENCE_SINGLE_SOURCE_RELEASE")
    two = _g(*rows)
    assert gate.evaluate(two, one, credible_sources={"S1"})["confidence"].startswith("high")


def test_placeholder_text_is_not_checked_as_a_claim():
    from services.research_orchestrator_service import ResearchOrchestratorService as ROS
    obj = ROS.__new__(ROS)
    insight = {"revision_id": "INSIGHTREV-PH", "supporting_evidence_ids": ["EVID-1"], "insight": "X holds Y.",
               "second_order_effect": "If A then B. No date is defensible from the evidence.",
               "leverage_analysis": {"holder": "the FTC", "controls": "an investigation", "dependent": "OpenAI",
                                     "holder_incentive": "Win a consent order.",
                                     "dependent_incentive": "Not stated in the admitted records.",
                                     "predicted_move": "z"}}
    ids = [c["claim_id"] for c in obj._claims_from_insight(insight)]
    assert not any(i.endswith("-DEPENDENT-INCENTIVE") for i in ids) and any(i.endswith("-HOLDER-INCENTIVE") for i in ids)
    assert sum("-SECOND-ORDER-" in i for i in ids) == 1


import pytest as _pytest_legacy


@_pytest_legacy.fixture(autouse=True)
def _legacy_gate_mode(monkeypatch):
    """These tests pin claim-support-gate v1-v3 rules, kept as EVENT_INTELLIGENCE_GATE_MODE=legacy
    since v4.0 (two-lane gate, 12 Nov 2026); tests/test_two_lane_gate.py covers v4.0."""
    monkeypatch.setenv("EVENT_INTELLIGENCE_GATE_MODE", "legacy")
