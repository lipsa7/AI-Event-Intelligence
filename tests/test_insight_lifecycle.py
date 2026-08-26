from services.insight import InsightRevision
from services.insight_registry_service import InsightRegistryService
from services.insight_trace_service import InsightTraceService


def test_insight_revision_preserves_descriptive_vs_prospective_contract():
    revision = InsightRevision.from_mapping(
        {
            "insight_id": "INSIGHT-1",
            "insight_revision_id": "INSIGHTREV-1",
            "insight_type": "prospective",
            "insight": (
                "Grid access may become "
                "differentiating."
            ),
            "premises": [
                {
                    "premise_id": "PREMISE-1",
                    "text": (
                        "Queue times are increasing."
                    ),
                    "evidence_ids": [
                        "FACT-1"
                    ],
                }
            ],
            "mechanism": {
                "description": (
                    "Scarce access constrains "
                    "deployment."
                ),
                "analogical_evidence_ids": [
                    "FACT-2"
                ],
            },
            "feasibility": {
                "constraint_ids": [
                    "CONSTRAINT-1"
                ],
                "feasibility_evidence_ids": [
                    "FACT-3"
                ],
                "blocking_factors": [],
            },
            "supporting_evidence_ids": [
                "FACT-1"
            ],
            "counterevidence_ids": [],
            "counter_incentive_evidence_ids": [],
            "alternative_explanations": [],
            "critical_unknowns": [
                "Future queue policy"
            ],
            "quality_status": "unassessed",
            "epistemic_status": "unresolved",
            "prospective_status": "unresolved",
        }
    )

    roles = revision.evidence_roles

    assert "FACT-1" in {
        evidence_id
        for evidence_ids
        in roles.values()
        for evidence_id
        in evidence_ids
    }

    assert revision.is_prospective is True


def test_registry_persists_revision_lifecycle(tmp_path):
    db = (
        tmp_path
        / "registry.sqlite3"
    )

    service = InsightRegistryService(
        db_path=str(db)
    )

    created = service.create_insight(
        insight_id="INSIGHT-1",
        insight_revision_id="INSIGHTREV-1",
        insight_type="prospective",
        text="Initial insight.",
        supporting_evidence_ids=[
            "FACT-1"
        ],
    )

    revised = service.revise_insight(
        insight_id="INSIGHT-1",
        insight_revision_id="INSIGHTREV-2",
        text="Revised insight.",
        reason="new evidence",
        supporting_evidence_ids=[
            "FACT-1",
            "FACT-2",
        ],
    )

    assert (
        revised[
            "current_revision_id"
        ]
        == "INSIGHTREV-2"
    )

    assert (
        service.get_revision(
            "INSIGHTREV-1"
        )[
            "revision_status"
        ]
        == "superseded"
    )

    service.close()

    restored = InsightRegistryService(
        db_path=str(db)
    )

    assert (
        restored.get_insight(
            "INSIGHT-1"
        )[
            "current_revision_id"
        ]
        == "INSIGHTREV-2"
    )

    restored.close()


def test_trace_requires_complete_evidence_manifest(tmp_path):
    service = InsightTraceService(
        db_path=str(
            tmp_path
            / "trace.sqlite3"
        )
    )

    trace = service.start_trace(
        insight_id="INSIGHT-1",
        insight_revision_id="INSIGHTREV-1",
        insight_type="prospective",
        research_question="What changed?",
        run_id="RUN-1",
        blueprint_id="BLUEPRINT-1",
        research_profile="fast",
    )

    trace_id = trace[
        "trace_id"
    ]

    service.register_accepted_evidence(
        trace_id=trace_id,
        evidence_ids=[
            "FACT-1",
            "FACT-2",
        ],
    )

    service.mark_evidence_examined(
        trace_id=trace_id,
        evidence_ids=[
            "FACT-1",
            "FACT-2",
        ],
        relevant_evidence_ids=[
            "FACT-1"
        ],
    )

    service.set_final_reasoning_context(
        trace_id=trace_id,
        evidence_ids=[
            "FACT-1"
        ],
    )

    current = service.get_trace(
        trace_id
    )

    assert (
        current["evidence_manifest"][
            "unexamined_evidence_ids"
        ]
        == []
    )

    assert (
        current["evidence_manifest"][
            "examined_evidence_ids"
        ]
        == [
            "FACT-1",
            "FACT-2",
        ]
    )

    service.close()