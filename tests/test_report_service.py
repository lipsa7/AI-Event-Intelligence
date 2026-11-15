import pytest

from services.report_service import (
    ReportService,
    ReportValidationError,
)


def _sections():
    return {
        "event_landscape": [
            {
                "text": "Event",
                "evidence_ids": [
                    "FACT-1"
                ],
            }
        ],
        "connections": [
            {
                "text": "Connection",
                "evidence_ids": [
                    "FACT-1",
                    "FACT-2",
                ],
            }
        ],
        "what_is_happening_underneath": [
            "Structural change"
        ],
        "hidden_insights": [
            {
                "insight_id": "INSIGHT-1",
                "evidence_ids": [
                    "FACT-2"
                ],
            }
        ],
        "second_order_effects": [],
        "counterevidence_and_alternative_explanations": [
            "Alternative"
        ],
        "what_to_watch": [
            "Signal"
        ],
        "evidence_trail": [
            {
                "evidence_id": "FACT-1"
            },
            {
                "evidence_id": "FACT-2"
            },
        ],
        "critical_unknowns": [
            "Unknown"
        ],
    }


def test_report_revision_is_immutable_and_logical_id_stable(
    tmp_path,
):
    service = ReportService(
        db_path=str(
            tmp_path
            / "reports.sqlite3"
        )
    )

    first = service.create_report(
        research_question="What changed?",
        sections=_sections(),
        report_id="REPORT-1",
    )

    second = service.revise_report(
        report_id="REPORT-1",
        sections={
            **_sections(),
            "what_to_watch": [
                "New signal"
            ],
        },
    )

    assert (
        first["report_id"]
        == second["report_id"]
        == "REPORT-1"
    )

    assert (
        first["report_revision_id"]
        != second["report_revision_id"]
    )

    assert (
        service.get_revision(
            first["report_revision_id"]
        )[
            "revision_no"
        ]
        == 1
    )

    assert (
        service.get_revision(
            second["report_revision_id"]
        )[
            "revision_no"
        ]
        == 2
    )

    service.close()


def test_released_report_cannot_be_silently_revised(
    tmp_path,
):
    service = ReportService(
        db_path=str(
            tmp_path
            / "reports.sqlite3"
        )
    )

    report = service.create_report(
        research_question="What changed?",
        sections=_sections(),
    )

    service.apply_release_decision(
        report_id=report[
            "report_id"
        ],
        release_decision={
            "decision_id": "DECISION-1",
            "release_allowed": True,
            "authorizes_release": True,
        },
    )

    with pytest.raises(
        ReportValidationError
    ):
        service.revise_report(
            report_id=report[
                "report_id"
            ],
            sections=_sections(),
        )

    service.close()