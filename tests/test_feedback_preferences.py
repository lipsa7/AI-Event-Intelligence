from services.user_feedback_service import UserFeedbackService
from services.user_preference_service import UserPreferenceService
from services.preference_data_service import PreferenceDataService


def test_feedback_interpretation_is_quality_signal_not_evidence():
    service = UserFeedbackService()

    feedback = service.interpret(
        "This is too surface-level. "
        "Give me more mechanism and implications.",
        feedback_scope="report",
        report_id="REPORT-1",
    )

    assert (
        "too_surface_level"
        in feedback["feedback_types"]
    )

    assert (
        "want_more_mechanism"
        in feedback["feedback_types"]
        or
        "want_more_implications"
        in feedback["feedback_types"]
    )

    assert (
        feedback["metadata"][
            "user_feedback_is_grounding_evidence"
        ]
        is False
    )


def test_preference_profile_updates_semantic_depth_not_research_depth(
    tmp_path,
):
    feedback = UserFeedbackService().interpret(
        "This is too surface-level. "
        "I want more mechanism.",
        feedback_scope="report",
        report_id="REPORT-1",
    )

    service = UserPreferenceService(
        profile_key="default",
        db_path=str(
            tmp_path
            / "prefs.sqlite3"
        ),
    )

    service.apply_feedback(
        feedback
    )

    profile = service.get_profile()
    compiler_preferences = (
        service.compiler_preferences()
    )

    assert profile["desired_depth"] in {
        "high",
        "very_high",
    }

    assert (
        "research_depth"
        not in compiler_preferences
    )

    service.close()


def test_preference_profile_persists(tmp_path):
    db = (
        tmp_path
        / "prefs.sqlite3"
    )

    feedback = UserFeedbackService().interpret(
        "Good insight. More like this.",
        feedback_scope="insight",
        target_insight_ids=[
            "INSIGHT-1"
        ],
        positive_examples=[
            {
                "text": "Strong mechanism",
                "reason": "useful",
            }
        ],
    )

    first = UserPreferenceService(
        profile_key="default",
        db_path=str(db),
    )

    first.apply_feedback(
        feedback
    )

    before = first.get_profile()
    first.close()

    second = UserPreferenceService(
        profile_key="default",
        db_path=str(db),
    )

    assert (
        second.get_profile()
        == before
    )

    second.close()


def test_preference_data_records_are_not_grounding_evidence():
    feedback = UserFeedbackService().interpret(
        "Prefer insight 1 over insight 2.",
        feedback_scope="report",
        preferred_insight_ids=[
            "INSIGHT-1"
        ],
        rejected_insight_ids=[
            "INSIGHT-2"
        ],
    )

    data = PreferenceDataService()

    ingest = data.ingest_feedback(
        feedback_event=feedback,
        available_insights=[
            {
                "insight_id": "INSIGHT-1",
                "insight": "A",
            },
            {
                "insight_id": "INSIGHT-2",
                "insight": "B",
            },
        ],
        context={},
    )

    record_ids = (
        ingest["created_record_ids"]
        + ingest["existing_record_ids"]
    )

    records = [
        data.get_record(
            record_id
        )
        for record_id
        in record_ids
    ]

    assert records

    assert all(
        record[
            "is_grounding_evidence"
        ]
        is False
        for record
        in records
    )