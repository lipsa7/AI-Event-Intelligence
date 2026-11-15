"""
Release-path tests for a Deep report that releases a verified cross-event
connection (strategic finding) instead of a hidden insight.

Live run RUN-0E1F73B5F0604BACAA457009 was blocked at the final policy check
with REQUIRED_REPORT_SECTION_MISSING for hidden_insights, although the
claim-level gate had released a fully supported verified connection.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from services.blueprint_compiler import BlueprintCompiler
from services.policy_enforcement_service import PolicyEnforcementService
from services.policy_registry import PolicyRegistryService
from services.research_orchestrator_service import (
    ResearchOrchestratorService as Orchestrator,
)


REVISION = "INSIGHTREV-0E67C635F529D504707B7AD5"


@pytest.fixture(scope="module")
def blueprint(tmp_path_factory):
    db = tmp_path_factory.mktemp("policy") / "policy_registry.sqlite3"
    registry = PolicyRegistryService(db_path=str(db))
    snapshot = registry.get_policy_snapshot(
        analysis_mode="research",
        research_depth="deep",
    )
    intent = {
        "intent_id": "INTENT-TEST",
        "intent_type": "research",
        "research_question": "artificial intelligence",
        "analysis_mode": "research",
        "research_depth": "deep",
        "requested_output": "research_report",
        "constraints": {},
        "source_preferences": {},
        "insight_preferences": {},
    }
    return BlueprintCompiler().compile(
        validated_intent=intent,
        policy_snapshot=dict(snapshot),
    )


def _claim(claim_id, status="supported"):
    return {
        "claim_id": claim_id,
        "claim_text": "Claim " + claim_id,
        "epistemic_type": "fact",
        "grounding_status": status,
        "supporting_evidence_ids": ["EVID-A", "EVID-B"],
        "valid_evidence_ids": ["EVID-A", "EVID-B"],
        "invalid_evidence_ids": [],
        "contradicted_evidence_ids": [],
    }


def _grounding(statuses=("supported", "supported", "supported", "supported")):
    return {
        REVISION: {
            "claims": [
                _claim("P" + str(index), status)
                for index, status in enumerate(statuses, start=1)
            ]
        }
    }


def _report():
    finding = {
        "text": "Agent deployments are producing cross-domain security incidents.",
        "insight_id": "INSIGHT-2C876C6A8D5F500E7AF37A99",
        "insight_revision_id": REVISION,
        "finding_tier": "strategic_connection",
        "evidence_ids": ["EVID-A", "EVID-B"],
    }
    return {
        "run_id": "RUN-TEST",
        "outcome": "strategic_finding_released",
        "event_landscape": [{"event_id": "EVENT-1"}],
        "connections": [{"text": "connection"}],
        "what_is_happening_underneath": [{"text": "underneath"}],
        "hidden_insights": [],
        "strategic_findings": [finding],
        "second_order_effects": [{"text": "effect"}],
        "counterevidence_and_alternative_explanations": [
            {"note": "searched; none established", "status": "completed_none_found"}
        ],
        "what_to_watch": [{"text": "watch"}],
        "evidence_trail": [{"evidence_id": "EVID-A"}, {"evidence_id": "EVID-B"}],
        "critical_unknowns": ["unknown"],
        "analysis_metadata": {
            "claim_support_gate": {
                "released": 1,
                "released_as": "verified_connection",
                "version": "claim-support-gate-v1.5",
            }
        },
    }


def _decide(blueprint, report, grounding=None, red_team=None):
    grounding_assessments = Orchestrator._policy_grounding_assessments(
        _grounding() if grounding is None else grounding
    )
    red_team_assessments = Orchestrator._policy_red_team_assessments(
        {"review_status": "complete", "issues": []}
        if red_team is None
        else red_team,
        report=report,
    )
    return PolicyEnforcementService().validate_release(
        blueprint=blueprint,
        report=report,
        grounding_assessments=grounding_assessments,
        red_team_assessments=red_team_assessments,
    )


def _codes(decision):
    return [violation["code"] for violation in decision["violations"]]


def test_verified_connection_report_is_released(blueprint):
    decision = _decide(blueprint, _report())
    assert decision["release_allowed"] is True, _codes(decision)
    assert decision["authorizes_release"] is True
    assert "VERIFIED_CONNECTION_RELEASE" in [
        warning["code"] for warning in decision["warnings"]
    ]


def test_without_claim_gate_marker_hidden_insights_still_required(blueprint):
    report = _report()
    report["analysis_metadata"] = {}
    decision = _decide(blueprint, report)
    assert decision["release_allowed"] is False
    assert "REQUIRED_REPORT_SECTION_MISSING" in _codes(decision)


def test_gate_marker_must_say_verified_connection(blueprint):
    report = _report()
    report["analysis_metadata"]["claim_support_gate"]["released_as"] = "hidden_insight"
    assert _decide(blueprint, report)["release_allowed"] is False


def test_gate_marker_must_say_released(blueprint):
    report = _report()
    report["analysis_metadata"]["claim_support_gate"]["released"] = 0
    assert _decide(blueprint, report)["release_allowed"] is False


def test_outcome_must_be_strategic_finding_release(blueprint):
    report = _report()
    report["outcome"] = "hidden_insight_released"
    assert _decide(blueprint, report)["release_allowed"] is False


@pytest.mark.parametrize(
    "statuses",
    [
        ("supported", "inference_supported"),
        ("supported", "partially_supported"),
        ("supported", "unsupported"),
    ],
)
def test_verified_connection_needs_every_claim_supported(blueprint, statuses):
    decision = _decide(blueprint, _report(), grounding=_grounding(statuses))
    assert decision["release_allowed"] is False


def test_finding_must_match_a_grounded_revision(blueprint):
    report = _report()
    report["strategic_findings"][0]["insight_revision_id"] = "INSIGHTREV-OTHER"
    assert _decide(blueprint, report)["release_allowed"] is False


def test_finding_needs_evidence_ids(blueprint):
    report = _report()
    report["strategic_findings"][0]["evidence_ids"] = []
    assert _decide(blueprint, report)["release_allowed"] is False


def test_every_finding_must_be_grounded(blueprint):
    report = _report()
    extra = deepcopy(report["strategic_findings"][0])
    extra["insight_revision_id"] = "INSIGHTREV-UNGROUNDED"
    report["strategic_findings"].append(extra)
    assert _decide(blueprint, report)["release_allowed"] is False


def test_strategic_findings_get_stable_id_checks(blueprint):
    report = _report()
    report["strategic_findings"][0]["insight_id"] = ""
    decision = _decide(blueprint, report)
    assert decision["release_allowed"] is False


def test_incomplete_red_team_still_blocks(blueprint):
    decision = _decide(
        blueprint,
        _report(),
        red_team={"review_status": "partial", "issues": []},
    )
    assert decision["release_allowed"] is False


def test_hidden_insight_reports_unchanged(blueprint):
    report = _report()
    report["outcome"] = "hidden_insight_released"
    report["hidden_insights"] = deepcopy(report["strategic_findings"])
    report["analysis_metadata"] = {}
    decision = _decide(blueprint, report)
    assert decision["release_allowed"] is True, _codes(decision)
    assert "VERIFIED_CONNECTION_RELEASE" not in [
        warning["code"] for warning in decision["warnings"]
    ]


def test_release_block_summary_names_the_violations():
    summary = Orchestrator._release_block_summary(
        {
            "violations": [
                {
                    "code": "REQUIRED_REPORT_SECTION_MISSING",
                    "message": "Required report section 'hidden_insights' is missing/empty.",
                },
                {
                    "code": "HARD_QUALITY_GATE_FAILED",
                    "message": "Hard quality gate 'RED-TEAM-REQUIRED' failed.",
                },
            ]
        }
    )
    assert summary.startswith("Release blocked by the final policy check: ")
    assert "REQUIRED_REPORT_SECTION_MISSING" in summary
    assert "RED-TEAM-REQUIRED" in summary


def test_release_block_summary_falls_back_to_reasons_and_authority():
    assert "service down" in Orchestrator._release_block_summary(
        {"reasons": ["service down"]}
    )
    assert "authority=policy_validation_error" in Orchestrator._release_block_summary(
        {"authority": "policy_validation_error"}
    )


def test_release_block_summary_is_bounded():
    summary = Orchestrator._release_block_summary(
        {
            "violations": [
                {"code": "CODE_" + str(index), "message": "m"}
                for index in range(7)
            ]
        }
    )
    assert "and 3 more" in summary
