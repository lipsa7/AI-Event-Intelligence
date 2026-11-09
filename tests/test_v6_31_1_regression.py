from services import insight_evaluator as ev
from services import claim_support_gate as gate


def all_yes():
    return {k: True for k, _, _ in ev.RUBRIC}


def test_routine_action_not_leverage():
    report = {"actor_moves": {"actors": [{
        "actor": "OpenAI",
        "crux": {"text": "Regulatory exposure"},
        "leverage": {"text": "EU can fine providers"},
        "choice": {},
        "moves": [{"basis": "incentive", "move": "File an incident notification",
                   "why_it_benefits": "Shows cooperation"}],
    }]}}
    item = ev.candidates(report)[0]
    assert item["strategic_type"] == "strategic_action"
    assert item["leverage_complete"] is False


def test_complete_dependency_chain_can_be_leverage():
    report = {"actor_moves": {"actors": [{
        "actor": "EU Commission",
        "crux": {"text": "Needs incident facts"},
        "leverage": {
            "holder": "EU Commission",
            "counterparty": "OpenAI",
            "dependency": "OpenAI depends on continued EU market access",
            "control_point": "AI Act enforcement authority",
            "outside_option": "litigate or narrow EU exposure",
            "holder_dependence": "the Commission needs OpenAI's incident facts, but can fine without them",
            "text": "enforcement authority",
            "leverage_test": "passed",
        },
        "choice": {},
        "moves": [{"basis": "leverage", "move": "Condition regulatory relief on verified disclosure",
                   "why_it_benefits": "turns enforcement authority into information access"}],
    }]}}
    item = ev.candidates(report)[0]
    assert item["strategic_type"] == "leverage_decision"
    assert item["leverage_complete"] is True


def test_strategic_action_score_is_capped():
    report = {"actor_moves": {"actors": [{
        "actor": "OpenAI",
        "crux": {"text": "Regulatory exposure"},
        "leverage": {},
        "choice": {},
        "moves": [{"basis": "incentive", "move": "File an incident notification",
                   "why_it_benefits": "Shows cooperation"}],
    }]}}
    item = ev.candidates(report)[0]
    from tests.test_substance_audit import audit
    item["evidence"] = ["Source excerpt supplied for this scoring fixture."]
    scored = ev.parse_rubric({"scores": [{"id": item["id"], "answers": all_yes(), "weakest": "none", "substance": audit()}]}, [item])[item["id"]]
    assert scored["score"] <= 9
    assert scored["score_cap"] == 9


def test_repairable_rejection_is_preserved():
    candidate = {
        "insight_id": "I-1",
        "insight_revision_id": "R-1",
        "insight": "Military-agent ambitions plus a months-long discovery cycle may indicate assurance is lagging deployment ambition.",
        "metadata": {"insight_quality_check": {"is_merely_explanatory": False,
                    "new_information_created": "Links sensitive deployment ambition to an observed discovery lag."}},
    }
    verdict = {
        "passed": False,
        "repairable": True,
        "core_claims": 10,
        "core_claims_passed": 5,
        "reasons": ["mechanism_step_not_supported_by_cited_evidence"],
        "failed_claims": [{"epistemic_type": "mechanism", "grounding_status": "unsupported",
                           "claim_text": "telemetry caused the lag", "unsupported_parts": ["cause not established"]}],
    }
    rec = gate.promising_rejected(candidate, verdict)
    assert rec is not None
    assert rec["status"] == "promising_unverified"
    assert rec["research_needed"]


def test_evaluator_exposes_promising_rejected():
    report = {"promising_hypotheses": [{"status": "promising_unverified", "text": "x" * 50}]}
    out = ev.promising_unverified(report)
    assert len(out) == 1
    assert out[0]["status"] == "promising_unverified"
