from services.user_feedback_service import UserFeedbackService


def test_surface_level_feedback_maps_to_structured_refinement_request():
    result = UserFeedbackService().interpret(
        (
            "This is too surface-level. "
            "Explain the mechanism and "
            "second-order implications."
        ),
        feedback_scope="report",
        report_id="REPORT-1",
    )

    assert (
        "too_surface_level"
        in result["feedback_types"]
    )

    assert (
        result["refinement_request"][
            "requested"
        ]
        is True
    )

    assert (
        result["refinement_request"][
            "execution_authorized_here"
        ]
        is False
    )

    assert (
        result["metadata"][
            "user_feedback_is_grounding_evidence"
        ]
        is False
    )


def test_positive_examples_remain_quality_examples_not_evidence():
    result = UserFeedbackService().interpret(
        "More like this.",
        feedback_scope="insight",
        target_insight_ids=[
            "INSIGHT-1"
        ],
        positive_examples=[
            {
                "text": (
                    "Mechanism-rich insight"
                ),
                "reason": "useful",
            }
        ],
    )

    examples = result[
        "structured_preferences"
    ][
        "positive_examples"
    ]

    assert examples

    assert (
        result["metadata"][
            "examples_are_quality_guidance_not_evidence"
        ]
        is True
    )

    assert (
        result["metadata"][
            "user_feedback_is_grounding_evidence"
        ]
        is False
    )