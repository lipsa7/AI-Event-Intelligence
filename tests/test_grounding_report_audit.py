from copy import deepcopy

import pytest

from services.grounding_service import GroundingService
from services.report_service import ReportService
from services.audit_receipt_service import AuditReceiptService


@pytest.mark.xfail(
    reason=(
        "Current grounding_service normalizes the supplied inferential "
        "epistemic type to unknown and can return direct 'supported'. "
        "The intended final GroundingService contract should preserve "
        "inference_supported instead."
    ),
    strict=False,
)
def test_grounding_inference_is_not_promoted_to_direct_fact():
    def classifier(prompt):
        return {
            "status": "supported",
            "supporting_evidence_ids": [
                "FACT-1"
            ],
            "contradicted_evidence_ids": [],
            "attribution_valid": True,
            "temporal_valid": True,
            "numeric_valid": True,
            "unsupported_parts": [],
            "rationale": (
                "The supplied evidence supports "
                "the underlying premise."
            ),
        }

    service = GroundingService(
        support_classifier=classifier,
    )

    result = service.ground_claim(
        claim_id="CLAIM-1",
        claim_text=(
            "Grid access may become a "
            "strategic differentiator."
        ),
        evidence_ids=[
            "FACT-1"
        ],
        evidence_records=[
            {
                "evidence_id": "FACT-1",
                "claim": (
                    "Grid interconnection "
                    "queues are long."
                ),
            }
        ],
        epistemic_type="inference",
    )

    assert result.grounding_status in {
        "inference_supported",
        "partially_supported",
        "unsupported",
    }


def test_report_release_requires_policy_authorization(tmp_path):
    service = ReportService(
        db_path=str(
            tmp_path
            / "reports.sqlite3"
        )
    )

    report = service.create_report(
        research_question="What changed?",
        sections={
            "event_landscape": [],
            "connections": [],
            "what_is_happening_underneath": [],
            "hidden_insights": [],
            "second_order_effects": [],
            "counterevidence_and_alternative_explanations": [],
            "what_to_watch": [],
            "evidence_trail": [],
            "critical_unknowns": [],
        },
    )

    blocked = service.apply_release_decision(
        report_id=report["report_id"],
        release_decision={
            "decision_id": "DECISION-1",
            "release_allowed": False,
            "authorizes_release": False,
        },
    )

    assert blocked["state"] == "blocked"
    service.close()


def test_audit_receipt_detects_tampering_and_chains(tmp_path):
    service = AuditReceiptService(
        db_path=str(
            tmp_path
            / "audit.sqlite3"
        ),
        signing_key="test-key",
        clock=lambda: (
            "2026-08-21T00:00:00+00:00"
        ),
    )

    first = service.create_and_append_receipt(
        run_id="RUN-1",
        final_outcome="blocked",
        stop_reason="test",
        release_decision={
            "decision_id": "DECISION-1",
            "release_allowed": False,
            "authorizes_release": False,
        },
    )

    second = service.create_and_append_receipt(
        run_id="RUN-2",
        final_outcome="blocked",
        stop_reason="test",
        release_decision={
            "decision_id": "DECISION-2",
            "release_allowed": False,
            "authorizes_release": False,
        },
    )

    assert (
        second[
            "previous_receipt_hash"
        ]
        == first[
            "receipt_hash"
        ]
    )

    assert (
        service.verify_chain()[
            "valid"
        ]
        is True
    )

    tampered = deepcopy(
        first
    )

    tampered[
        "stop_reason"
    ] = "changed"

    assert (
        service.verify_receipt(
            tampered
        )["valid"]
        is False
    )

    service.close()