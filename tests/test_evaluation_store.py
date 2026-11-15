from services.evaluation_store import EvaluationStore


def test_evaluation_store_run_and_failure_summary(tmp_path):
    store = EvaluationStore(
        db_path=str(
            tmp_path
            / "eval.sqlite3"
        )
    )

    store.initialize()

    store.create_run(
        run_id="EVAL-1",
        variant="baseline",
        dataset_name="test",
        dataset_fingerprint="fp",
        dataset_manifest={},
        blueprint_id="BLUEPRINT-1",
        policy_versions={},
        model_versions={},
        prompt_versions={},
        git_commit="",
        python_version="",
        metadata={},
    )

    store.record_stage_result(
        run_id="EVAL-1",
        stage="grounding",
        status="succeeded",
        metrics={
            "accuracy": 0.9
        },
        evaluator_version="v1",
        runtime_ms=10.0,
    )

    store.record_case_result(
        run_id="EVAL-1",
        stage="grounding",
        case_id="CASE-1",
        passed=False,
        severity="high",
        failure_types=[
            "unsupported_claim"
        ],
        tags=[
            "prospective"
        ],
        measurements={},
        details={},
    )

    summary = store.failure_summary(
        run_id="EVAL-1",
        stage="grounding",
    )

    assert summary["failed_case_count"] == 1
    assert (
        summary["by_failure_type"][
            "unsupported_claim"
        ]
        == 1
    )