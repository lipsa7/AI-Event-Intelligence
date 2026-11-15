from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


STORE_SCHEMA_VERSION = "1.0"
STAGE_STATUSES = {"succeeded", "partial", "failed", "skipped"}
SEVERITIES = {"", "info", "low", "medium", "high", "critical"}


class EvaluationStoreError(RuntimeError):
    pass


class EvaluationStoreValidationError(EvaluationStoreError):
    pass


class EvaluationStoreConflictError(EvaluationStoreError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_payload(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _loads(value: str, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _string_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise EvaluationStoreValidationError(
            "Expected string or sequence of strings."
        )
    out, seen = [], set()
    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


class EvaluationStore:
    """
    SQLite store for offline evaluation runs.

    Tables:
      evaluation_runs  -> experiment provenance
      stage_results    -> aggregate evaluator metrics
      case_results     -> case-level failures/measurements

    Append-oriented: existing run/stage/case keys are rejected rather than
    silently overwritten.

    This service stores evaluation results. It does not compute metrics,
    statistical significance, policy conformance, or release decisions.
    """

    def __init__(
        self,
        db_path: str | Path = "evaluation/results/evaluation_store.db",
        *,
        auto_initialize: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        if auto_initialize:
            self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    dataset_fingerprint TEXT NOT NULL DEFAULT '',
                    blueprint_id TEXT NOT NULL DEFAULT '',
                    policy_versions_json TEXT NOT NULL DEFAULT '{}',
                    model_versions_json TEXT NOT NULL DEFAULT '{}',
                    prompt_versions_json TEXT NOT NULL DEFAULT '{}',
                    git_commit TEXT NOT NULL DEFAULT '',
                    python_version TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS stage_results (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    evaluator_version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    runtime_ms REAL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage),
                    FOREIGN KEY (run_id)
                      REFERENCES evaluation_runs(run_id)
                      ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS case_results (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    passed INTEGER,
                    severity TEXT NOT NULL DEFAULT '',
                    failure_types_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    measurements_json TEXT NOT NULL DEFAULT '{}',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage, case_id),
                    FOREIGN KEY (run_id, stage)
                      REFERENCES stage_results(run_id, stage)
                      ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_eval_runs_variant
                    ON evaluation_runs(variant);
                CREATE INDEX IF NOT EXISTS idx_stage_results_stage
                    ON stage_results(stage);
                CREATE INDEX IF NOT EXISTS idx_case_results_passed
                    ON case_results(passed);
                CREATE INDEX IF NOT EXISTS idx_case_results_severity
                    ON case_results(severity);
                """
            )

    def create_run(
        self,
        *,
        run_id: str,
        variant: str,
        dataset_name: str,
        dataset_fingerprint: str = "",
        dataset_manifest: Any = None,
        blueprint_id: str = "",
        policy_versions: Optional[Mapping[str, Any]] = None,
        model_versions: Optional[Mapping[str, Any]] = None,
        prompt_versions: Optional[Mapping[str, Any]] = None,
        git_commit: str = "",
        python_version: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        created_at: str = "",
    ) -> Dict[str, Any]:
        run_id = _clean(run_id)
        variant = _clean(variant)
        dataset_name = _clean(dataset_name)

        if not run_id or not variant or not dataset_name:
            raise EvaluationStoreValidationError(
                "run_id, variant, and dataset_name are required."
            )

        dataset_fingerprint = _clean(dataset_fingerprint)
        if not dataset_fingerprint and dataset_manifest is not None:
            dataset_fingerprint = fingerprint_payload(dataset_manifest)

        record = {
            "run_id": run_id,
            "schema_version": STORE_SCHEMA_VERSION,
            "created_at": _clean(created_at) or _now(),
            "variant": variant,
            "dataset_name": dataset_name,
            "dataset_fingerprint": dataset_fingerprint,
            "blueprint_id": _clean(blueprint_id),
            "policy_versions": dict(policy_versions or {}),
            "model_versions": dict(model_versions or {}),
            "prompt_versions": dict(prompt_versions or {}),
            "git_commit": _clean(git_commit),
            "python_version": _clean(python_version),
            "metadata": dict(metadata or {}),
        }

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO evaluation_runs VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["run_id"],
                        record["schema_version"],
                        record["created_at"],
                        record["variant"],
                        record["dataset_name"],
                        record["dataset_fingerprint"],
                        record["blueprint_id"],
                        canonical_json(record["policy_versions"]),
                        canonical_json(record["model_versions"]),
                        canonical_json(record["prompt_versions"]),
                        record["git_commit"],
                        record["python_version"],
                        canonical_json(record["metadata"]),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise EvaluationStoreConflictError(
                f"run_id={run_id!r} already exists."
            ) from exc

        return record

    def record_stage_result(
        self,
        *,
        run_id: str,
        stage: str,
        status: str,
        metrics: Optional[Mapping[str, Any]] = None,
        evaluator_version: str = "",
        runtime_ms: Optional[float] = None,
        error_type: str = "",
        error_message: str = "",
        created_at: str = "",
    ) -> Dict[str, Any]:
        run_id = _clean(run_id)
        stage = _clean(stage)
        status = _clean(status).casefold()

        if not run_id or not stage:
            raise EvaluationStoreValidationError(
                "run_id and stage are required."
            )
        if status not in STAGE_STATUSES:
            raise EvaluationStoreValidationError(
                f"Unsupported stage status={status!r}."
            )
        if self.get_run(run_id) is None:
            raise EvaluationStoreValidationError(
                f"Unknown run_id={run_id!r}."
            )
        if runtime_ms is not None and float(runtime_ms) < 0:
            raise EvaluationStoreValidationError(
                "runtime_ms cannot be negative."
            )

        record = {
            "run_id": run_id,
            "stage": stage,
            "evaluator_version": _clean(evaluator_version),
            "status": status,
            "runtime_ms": None if runtime_ms is None else float(runtime_ms),
            "metrics": dict(metrics or {}),
            "error_type": _clean(error_type),
            "error_message": _clean(error_message),
            "created_at": _clean(created_at) or _now(),
        }

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO stage_results
                    (run_id, stage, evaluator_version, status, runtime_ms,
                     metrics_json, error_type, error_message, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["run_id"],
                        record["stage"],
                        record["evaluator_version"],
                        record["status"],
                        record["runtime_ms"],
                        canonical_json(record["metrics"]),
                        record["error_type"],
                        record["error_message"],
                        record["created_at"],
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise EvaluationStoreConflictError(
                f"Stage {stage!r} already exists for run {run_id!r}."
            ) from exc

        return record

    def record_case_result(
        self,
        *,
        run_id: str,
        stage: str,
        case_id: str,
        passed: Optional[bool],
        severity: str = "",
        failure_types: Any = None,
        tags: Any = None,
        measurements: Optional[Mapping[str, Any]] = None,
        details: Optional[Mapping[str, Any]] = None,
        created_at: str = "",
    ) -> Dict[str, Any]:
        run_id = _clean(run_id)
        stage = _clean(stage)
        case_id = _clean(case_id)
        severity = _clean(severity).casefold()

        if not run_id or not stage or not case_id:
            raise EvaluationStoreValidationError(
                "run_id, stage, and case_id are required."
            )
        if passed is not None and not isinstance(passed, bool):
            raise EvaluationStoreValidationError(
                "passed must be bool or None."
            )
        if severity not in SEVERITIES:
            raise EvaluationStoreValidationError(
                f"Unsupported severity={severity!r}."
            )
        if not self._stage_exists(run_id, stage):
            raise EvaluationStoreValidationError(
                f"Unknown stage result {run_id!r}/{stage!r}."
            )

        record = {
            "run_id": run_id,
            "stage": stage,
            "case_id": case_id,
            "passed": passed,
            "severity": severity,
            "failure_types": _string_list(failure_types),
            "tags": _string_list(tags),
            "measurements": dict(measurements or {}),
            "details": dict(details or {}),
            "created_at": _clean(created_at) or _now(),
        }

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO case_results
                    (run_id, stage, case_id, passed, severity,
                     failure_types_json, tags_json, measurements_json,
                     details_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        stage,
                        case_id,
                        None if passed is None else int(passed),
                        severity,
                        canonical_json(record["failure_types"]),
                        canonical_json(record["tags"]),
                        canonical_json(record["measurements"]),
                        canonical_json(record["details"]),
                        record["created_at"],
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise EvaluationStoreConflictError(
                f"Case {case_id!r} already exists for {run_id!r}/{stage!r}."
            ) from exc

        return record

    def record_case_results(
        self,
        *,
        run_id: str,
        stage: str,
        cases: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        results = []
        for case in cases:
            results.append(
                self.record_case_result(
                    run_id=run_id,
                    stage=stage,
                    case_id=case.get("case_id") or case.get("id") or "",
                    passed=case.get("passed"),
                    severity=case.get("severity") or "",
                    failure_types=case.get("failure_types"),
                    tags=case.get("tags") or case.get("case_tags"),
                    measurements=case.get("measurements")
                    if isinstance(case.get("measurements"), Mapping)
                    else {},
                    details=case.get("details")
                    if isinstance(case.get("details"), Mapping)
                    else {},
                    created_at=case.get("created_at") or "",
                )
            )
        return results

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evaluation_runs WHERE run_id=?",
                (_clean(run_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "schema_version": row["schema_version"],
            "created_at": row["created_at"],
            "variant": row["variant"],
            "dataset_name": row["dataset_name"],
            "dataset_fingerprint": row["dataset_fingerprint"],
            "blueprint_id": row["blueprint_id"],
            "policy_versions": _loads(row["policy_versions_json"], {}),
            "model_versions": _loads(row["model_versions_json"], {}),
            "prompt_versions": _loads(row["prompt_versions_json"], {}),
            "git_commit": row["git_commit"],
            "python_version": row["python_version"],
            "metadata": _loads(row["metadata_json"], {}),
        }

    def list_stage_results(self, *, run_id: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM stage_results"
        params = ()
        if run_id:
            query += " WHERE run_id=?"
            params = (_clean(run_id),)
        query += " ORDER BY created_at DESC, stage ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [{
            "run_id": r["run_id"],
            "stage": r["stage"],
            "evaluator_version": r["evaluator_version"],
            "status": r["status"],
            "runtime_ms": r["runtime_ms"],
            "metrics": _loads(r["metrics_json"], {}),
            "error_type": r["error_type"],
            "error_message": r["error_message"],
            "created_at": r["created_at"],
        } for r in rows]

    def list_case_results(
        self,
        *,
        run_id: str = "",
        stage: str = "",
        passed: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        conditions, params = [], []
        if run_id:
            conditions.append("run_id=?")
            params.append(_clean(run_id))
        if stage:
            conditions.append("stage=?")
            params.append(_clean(stage))
        if passed is not None:
            conditions.append("passed=?")
            params.append(int(passed))

        query = "SELECT * FROM case_results"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC, case_id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [{
            "run_id": r["run_id"],
            "stage": r["stage"],
            "case_id": r["case_id"],
            "passed": None if r["passed"] is None else bool(r["passed"]),
            "severity": r["severity"],
            "failure_types": _loads(r["failure_types_json"], []),
            "tags": _loads(r["tags_json"], []),
            "measurements": _loads(r["measurements_json"], {}),
            "details": _loads(r["details_json"], {}),
            "created_at": r["created_at"],
        } for r in rows]

    def failure_summary(
        self,
        *,
        run_id: str = "",
        stage: str = "",
    ) -> Dict[str, Any]:
        failures = self.list_case_results(
            run_id=run_id,
            stage=stage,
            passed=False,
        )

        by_stage, by_severity, by_type, by_tag = {}, {}, {}, {}

        for case in failures:
            by_stage[case["stage"]] = by_stage.get(case["stage"], 0) + 1

            severity = case["severity"] or "unspecified"
            by_severity[severity] = by_severity.get(severity, 0) + 1

            for item in case["failure_types"]:
                by_type[item] = by_type.get(item, 0) + 1

            for item in case["tags"]:
                by_tag[item] = by_tag.get(item, 0) + 1

        return {
            "failed_case_count": len(failures),
            "by_stage": dict(sorted(by_stage.items())),
            "by_severity": dict(sorted(by_severity.items())),
            "by_failure_type": dict(sorted(by_type.items())),
            "by_tag": dict(sorted(by_tag.items())),
        }

    def export_run_bundle(self, run_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise EvaluationStoreValidationError(
                f"Unknown run_id={run_id!r}."
            )
        return {
            "run": run,
            "stage_results": self.list_stage_results(run_id=run_id),
            "case_results": self.list_case_results(run_id=run_id),
            "failure_summary": self.failure_summary(run_id=run_id),
        }

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                ok = conn.execute("SELECT 1").fetchone()[0] == 1
            return {
                "healthy": ok,
                "schema_version": STORE_SCHEMA_VERSION,
                "db_path": str(self.db_path),
            }
        except Exception as exc:
            return {
                "healthy": False,
                "schema_version": STORE_SCHEMA_VERSION,
                "db_path": str(self.db_path),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

    def _stage_exists(self, run_id: str, stage: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM stage_results WHERE run_id=? AND stage=?",
                (_clean(run_id), _clean(stage)),
            ).fetchone()
        return row is not None