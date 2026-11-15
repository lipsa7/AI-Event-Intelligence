import pytest

from services.user_feedback_service import UserFeedbackService
from services.user_preference_service import (
    UserPreferenceService,
    UserPreferenceValidationError,
)


def test_feedback_updates_preference_projection_and_persists(
    tmp_path,
):
    db = (
        tmp_path
        / "preferences.sqlite3"
    )

    feedback = UserFeedbackService().interpret(
        (
            "Too obvious. I want more "
            "mechanism and second-order effects."
        ),
        feedback_scope="report",
        report_id="REPORT-1",
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

    assert (
        "research_depth"
        not in second.compiler_preferences()
    )

    second.close()


def test_explicit_preference_cannot_inject_execution_research_depth(
    tmp_path,
):
    service = UserPreferenceService(
        profile_key="default",
        db_path=str(
            tmp_path
            / "preferences.sqlite3"
        ),
    )

    with pytest.raises(
        UserPreferenceValidationError
    ):
        service.set_explicit_preferences(
            {
                "research_depth": "deep"
            },
            reason="not allowed",
        )

    service.close()