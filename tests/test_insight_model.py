from __future__ import annotations

from copy import deepcopy

import pytest

from models.insight import (
    Insight,
    InsightModelError,
    build_insight_revision,
    stable_id,
)


def _prospective_payload() -> dict:
    return {
        "insight_id": (
            "INSIGHT-001"
        ),
        "insight_type": (
            "prospective"
        ),
        "insight": (
            "Power availability may become "
            "a more important constraint on "
            "AI infrastructure expansion."
        ),
        "premises": [
            {
                "premise_id": (
                    "PREMISE-001"
                ),
                "text": (
                    "AI data-center "
                    "construction is increasing."
                ),
                "evidence_ids": [
                    "EVID-SHARED",
                    "EVID-DIRECT",
                ],
                "status": (
                    "supported"
                ),
            }
        ],
        "mechanism": {
            "description": (
                "Additional compute capacity "
                "increases power and grid-"
                "interconnection requirements."
            ),
            "analogical_evidence_ids": [
                "EVID-SHARED",
                "EVID-MECHANISM",
            ],
            "status": (
                "supported"
            ),
        },
        "feasibility": {
            "constraint_ids": [
                "CONSTRAINT-GRID"
            ],
            "feasibility_evidence_ids": [
                "EVID-FEASIBILITY"
            ],
            "magnitude_check": (
                "Grid connection delays are "
                "material enough to affect "
                "site selection."
            ),
            "blocking_factors": [
                (
                    "New generation capacity "
                    "could reduce the constraint."
                )
            ],
            "status": (
                "partially_supported"
            ),
        },
        "counterevidence_ids": [
            "EVID-COUNTER"
        ],
        "counter_incentives": [
            (
                "Operators may prefer existing "
                "locations despite grid delays."
            )
        ],
        "counter_incentive_evidence_ids": [
            "EVID-COUNTER-INCENTIVE"
        ],
        "alternative_explanations": [
            {
                "alternative_id": (
                    "ALT-001"
                ),
                "text": (
                    "Chip availability may "
                    "remain the dominant "
                    "constraint instead."
                ),
                "evidence_ids": [
                    "EVID-ALT"
                ],
                "status": (
                    "partially_supported"
                ),
            }
        ],
        "event_ids": [
            "EVENT-001",
            "EVENT-002",
        ],
        "analytical_lenses": [
            "infrastructure",
            "supply_constraints",
        ],
        "reasoning_patterns": [
            "cross_event_synthesis",
            "bottleneck_shift",
        ],
        "structural_change": (
            "AI infrastructure competition "
            "may shift toward physical-"
            "resource access."
        ),
        "second_order_effect": (
            "Regions with faster grid "
            "interconnection may attract "
            "more data-center investment."
        ),
        "critical_unknowns": [
            (
                "How quickly new generation "
                "and transmission can be added."
            )
        ],
        "direct_precedent_found": False,
    }


def test_insight_type_must_be_explicit() -> None:
    payload = (
        _prospective_payload()
    )

    payload.pop(
        "insight_type"
    )

    with pytest.raises(
        InsightModelError,
        match=(
            "insight_type "
            "must be explicit"
        ),
    ):
        build_insight_revision(
            payload
        )


def test_prospective_missing_status_defaults_to_unresolved() -> None:
    revision = (
        build_insight_revision(
            _prospective_payload()
        )
    )

    assert (
        revision.prospective_status
        == "unresolved"
    )


@pytest.mark.parametrize(
    "extra",
    [
        {
            "prospective_status": (
                "plausible_unprecedented"
            ),
        },
        {
            "direct_precedent_found": (
                False
            ),
        },
    ],
)
def test_descriptive_insight_rejects_prospective_only_fields(
    extra: dict,
) -> None:
    payload = {
        "insight_id": (
            "INSIGHT-DESC"
        ),
        "insight_type": (
            "descriptive"
        ),
        "insight": (
            "The two events reflect "
            "the same structural shift."
        ),
        **extra,
    }

    with pytest.raises(
        InsightModelError
    ):
        build_insight_revision(
            payload
        )


def test_string_false_is_parsed_as_false() -> None:
    payload = (
        _prospective_payload()
    )

    payload[
        "direct_precedent_found"
    ] = "false"

    revision = (
        build_insight_revision(
            payload
        )
    )

    assert (
        revision.direct_precedent_found
        is False
    )


def test_evidence_roles_are_explicit_and_many_to_many() -> None:
    revision = (
        build_insight_revision(
            _prospective_payload()
        )
    )

    roles = (
        revision.evidence_roles
    )

    assert roles[
        "direct_premise_evidence"
    ] == (
        "EVID-SHARED",
        "EVID-DIRECT",
    )

    assert roles[
        "analogical_mechanism_evidence"
    ] == (
        "EVID-SHARED",
        "EVID-MECHANISM",
    )

    assert roles[
        "feasibility_evidence"
    ] == (
        "EVID-FEASIBILITY",
    )

    assert roles[
        "counterevidence"
    ] == (
        "EVID-COUNTER",
    )

    assert roles[
        "counter_incentive_evidence"
    ] == (
        "EVID-COUNTER-INCENTIVE",
    )

    assert roles[
        "alternative_explanation_evidence"
    ] == (
        "EVID-ALT",
    )

    # Many-to-many:
    # the same evidence record may legitimately
    # support more than one evidence role.
    assert (
        "EVID-SHARED"
        in roles[
            "direct_premise_evidence"
        ]
    )

    assert (
        "EVID-SHARED"
        in roles[
            "analogical_mechanism_evidence"
        ]
    )

    # The old generic bucket must not return.
    assert not hasattr(
        revision,
        "supporting_evidence_ids",
    )


def test_all_referenced_evidence_ids_are_deduplicated() -> None:
    revision = (
        build_insight_revision(
            _prospective_payload()
        )
    )

    referenced = (
        revision
        .all_referenced_evidence_ids
    )

    assert (
        referenced.count(
            "EVID-SHARED"
        )
        == 1
    )

    assert set(
        referenced
    ) == {
        "EVID-SHARED",
        "EVID-DIRECT",
        "EVID-MECHANISM",
        "EVID-FEASIBILITY",
        "EVID-COUNTER",
        "EVID-COUNTER-INCENTIVE",
        "EVID-ALT",
    }


def test_revision_id_changes_when_research_content_changes() -> None:
    first_payload = (
        _prospective_payload()
    )

    second_payload = deepcopy(
        first_payload
    )

    second_payload[
        "premises"
    ][0][
        "evidence_ids"
    ].append(
        "EVID-NEW"
    )

    first = (
        build_insight_revision(
            first_payload
        )
    )

    second = (
        build_insight_revision(
            second_payload
        )
    )

    assert (
        first.insight_revision_id
        != second.insight_revision_id
    )

    assert (
        first.revision_fingerprint
        != second.revision_fingerprint
    )


def test_assessment_state_and_revision_id_do_not_change_semantic_fingerprint() -> None:
    first_payload = (
        _prospective_payload()
    )

    second_payload = deepcopy(
        first_payload
    )

    first_payload[
        "insight_revision_id"
    ] = "INSIGHTREV-A"

    second_payload[
        "insight_revision_id"
    ] = "INSIGHTREV-B"

    first_payload[
        "quality_status"
    ] = "needs_refinement"

    first_payload[
        "epistemic_status"
    ] = "unresolved"

    second_payload[
        "quality_status"
    ] = "strong"

    second_payload[
        "epistemic_status"
    ] = "inference_supported"

    first = (
        build_insight_revision(
            first_payload
        )
    )

    second = (
        build_insight_revision(
            second_payload
        )
    )

    # Revision IDs and downstream assessment state
    # are not part of the thesis fingerprint.
    assert (
        first.semantic_fingerprint
        == second.semantic_fingerprint
    )

    # They are also excluded from research-content
    # fingerprinting when the underlying revision
    # content is identical.
    assert (
        first.revision_fingerprint
        == second.revision_fingerprint
    )


def test_set_serialization_is_deterministic() -> None:
    first = stable_id(
        "TEST",
        {
            "letters": {
                "b",
                "a",
                "c",
            },
            "numbers": {
                3,
                1,
                2,
            },
        },
    )

    second = stable_id(
        "TEST",
        {
            "numbers": set(
                [
                    2,
                    3,
                    1,
                ]
            ),
            "letters": set(
                [
                    "c",
                    "a",
                    "b",
                ]
            ),
        },
    )

    assert (
        first
        == second
    )


@pytest.mark.parametrize(
    "private_payload",
    [
        {
            "raw_prompt": (
                "private prompt"
            ),
        },
        {
            "metadata": {
                "chain_of_thought": (
                    "private reasoning"
                ),
            },
        },
        {
            "metadata": {
                "nested": {
                    "raw_response": (
                        "private output"
                    ),
                }
            },
        },
    ],
)
def test_private_reasoning_fields_are_rejected(
    private_payload: dict,
) -> None:
    payload = (
        _prospective_payload()
    )

    for key, value in (
        private_payload.items()
    ):
        payload[
            key
        ] = value

    with pytest.raises(
        InsightModelError,
        match=(
            "prohibited "
            "private/raw reasoning"
        ),
    ):
        build_insight_revision(
            payload
        )


def test_logical_insight_requires_current_revision_to_belong_to_history() -> None:
    with pytest.raises(
        InsightModelError,
        match=(
            "current_revision_id"
        ),
    ):
        Insight(
            insight_id=(
                "INSIGHT-001"
            ),
            insight_type=(
                "prospective"
            ),
            lifecycle_status=(
                "active"
            ),
            current_revision_id=(
                "INSIGHTREV-MISSING"
            ),
            revision_ids=(
                "INSIGHTREV-001",
            ),
        )


def test_superseded_logical_insight_requires_successor() -> None:
    with pytest.raises(
        InsightModelError,
        match=(
            "superseded_by_insight_id"
        ),
    ):
        Insight(
            insight_id=(
                "INSIGHT-001"
            ),
            insight_type=(
                "prospective"
            ),
            lifecycle_status=(
                "superseded"
            ),
            current_revision_id=(
                "INSIGHTREV-001"
            ),
            revision_ids=(
                "INSIGHTREV-001",
            ),
        )