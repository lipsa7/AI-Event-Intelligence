import pytest

from services.strategic_topic_selector import StrategicTopicSelector
from services.telemetry_service import (
    TelemetryService,
    TelemetryValidationError,
)


def _clusters():
    return [
        {
            "cluster_id": "A",
            "label": "A",
            "event_count": 5,
            "evidence_count": 8,
            "evidence_density": 0.9,
            "event_density": 0.8,
            "connectivity": 0.9,
            "novelty": 0.9,
            "material_update": 0.8,
            "strategic_relevance": 0.9,
            "recency": 0.8,
        },
        {
            "cluster_id": "B",
            "label": "B",
            "event_count": 4,
            "evidence_count": 6,
            "evidence_density": 0.8,
            "event_density": 0.7,
            "connectivity": 0.8,
            "novelty": 0.7,
            "material_update": 0.7,
            "strategic_relevance": 0.8,
            "recency": 0.8,
        },
        {
            "cluster_id": "C",
            "label": "C",
            "event_count": 3,
            "evidence_count": 4,
            "evidence_density": 0.7,
            "event_density": 0.6,
            "connectivity": 0.5,
            "novelty": 0.95,
            "material_update": 0.6,
            "strategic_relevance": 0.7,
            "recency": 0.7,
        },
    ]


def test_topic_selector_uses_explicit_scores_and_diversity():
    selector = StrategicTopicSelector()

    result = selector.select(
        _clusters(),
        selection_policy={
            "max_selected_clusters": 2,
            "diversity_lambda": 0.75,
            "minimum_candidate_score": 0.1,
        },
        similarity_by_cluster_id={
            "A": {
                "B": 0.95,
                "C": 0.1,
            },
            "B": {
                "C": 0.2
            },
        },
    )

    assert (
        result[
            "selected_cluster_ids"
        ][0]
        == "A"
    )

    assert len(
        result[
            "selected_cluster_ids"
        ]
    ) == 2

    assert (
        result["metadata"][
            "domain_specific_priority_hardcoded"
        ]
        is False
    )


def test_telemetry_records_operational_metrics(tmp_path):
    telemetry = TelemetryService(
        db_path=str(
            tmp_path
            / "telemetry.sqlite3"
        )
    )

    telemetry.record_stage(
        run_id="RUN-1",
        stage="extraction",
        duration_ms=123.0,
        input_count=10,
        output_count=9,
        metrics={
            "success_rate": 0.9
        },
        dimensions={
            "research_depth": "fast"
        },
    )

    summary = telemetry.run_summary(
        "RUN-1"
    )

    assert (
        summary[
            "event_count"
        ]
        == 1
    )

    assert (
        summary[
            "aggregate_counters"
        ][
            "input_count"
        ]
        == 10
    )

    telemetry.close()


def test_telemetry_rejects_raw_prompt_storage(tmp_path):
    telemetry = TelemetryService(
        db_path=str(
            tmp_path
            / "telemetry.sqlite3"
        )
    )

    with pytest.raises(
        TelemetryValidationError
    ):
        telemetry.record_event(
            run_id="RUN-1",
            stage="reasoning",
            event_type="model_call",
            dimensions={
                "raw_prompt": "secret"
            },
        )

    telemetry.close()