"""
Local aggregate telemetry service.

Recommended location:
    services/telemetry_service.py

Role
----
every pipeline component
        ↓
TelemetryService
        ↓
run/stage/action metrics
        ↓
failure dashboard + EvaluationStore + experiments

Telemetry is aggregate operational/product observability.

It is deliberately separate from InsightTraceService:

TelemetryService:
    latency, counts, statuses, errors, budgets, model routes, quality metrics

InsightTraceService:
    detailed auditable research path for one insight/revision

Privacy boundary:
- raw prompts are not accepted
- raw model completions are not accepted
- private chain-of-thought is not accepted
- article/source full bodies are not accepted
- secrets/API keys are not accepted

SQLite is the default durable backend. No paid service is required.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "telemetry-service-v2.0"

PROHIBITED_FIELDS = {
    "chain_of_thought", "chainofthought", "cot", "scratchpad",
    "hidden_reasoning", "hiddenreasoning", "raw_reasoning", "rawreasoning",
    "private_reasoning", "privatereasoning", "raw_prompt", "rawprompt",
    "prompt", "system_prompt", "raw_completion", "rawcompletion",
    "raw_response", "rawresponse", "api_key", "apikey", "authorization",
    "password", "secret", "source_body", "article_body", "full_text",
}


class TelemetryServiceError(RuntimeError):
    pass


class TelemetryValidationError(TelemetryServiceError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    text = _clean(value).casefold().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _private_path(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if _enum(key) in PROHIBITED_FIELDS:
                return current
            nested = _private_path(item, current)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            nested = _private_path(item, f"{path}[{i}]")
            if nested:
                return nested
    return None


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise TelemetryValidationError(f"{name} must be numeric.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TelemetryValidationError(f"{name} must be numeric.") from exc
    if not math.isfinite(result):
        raise TelemetryValidationError(f"{name} must be finite.")
    return result


class TelemetryService:
    def __init__(
        self,
        *,
        db_path: str = "data/telemetry.sqlite3",
        clock: Any = _now,
    ) -> None:
        if not callable(clock):
            raise TelemetryValidationError("clock must be callable.")

        self.clock = clock
        self.db_path = _clean(db_path) or ":memory:"
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row

        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_events(
                event_no INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL,
                counters_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                dimensions_json TEXT NOT NULL,
                error_type TEXT NOT NULL,
                error_code TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_telemetry_run_stage
            ON telemetry_events(run_id, stage, event_no)
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def record_event(
        self,
        *,
        run_id: str,
        stage: str,
        event_type: str,
        status: str = "ok",
        duration_ms: Optional[float] = None,
        counters: Optional[Mapping[str, Any]] = None,
        metrics: Optional[Mapping[str, Any]] = None,
        dimensions: Optional[Mapping[str, Any]] = None,
        error_type: str = "",
        error_code: str = "",
        event_id: str = "",
    ) -> Dict[str, Any]:
        run_id = _clean(run_id)
        stage = _enum(stage)
        event_type = _enum(event_type)
        status = _enum(status)

        if not run_id or not stage or not event_type:
            raise TelemetryValidationError(
                "run_id, stage and event_type are required."
            )

        counters = dict(counters or {})
        metrics = dict(metrics or {})
        dimensions = dict(dimensions or {})

        for name, value in (
            ("counters", counters),
            ("metrics", metrics),
            ("dimensions", dimensions),
        ):
            private = _private_path(value)
            if private:
                raise TelemetryValidationError(
                    f"{name} contains prohibited sensitive/raw field at "
                    f"{private!r}."
                )

        normalized_counters = {}
        for key, value in counters.items():
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise TelemetryValidationError(
                    f"counter {key!r} must be nonnegative integer."
                )
            normalized_counters[_enum(key)] = int(value)

        normalized_metrics = {
            _enum(key): _number(value, name=f"metrics.{key}")
            for key, value in metrics.items()
        }

        duration = (
            None
            if duration_ms is None
            else _number(duration_ms, name="duration_ms")
        )
        if duration is not None and duration < 0:
            raise TelemetryValidationError("duration_ms must be >= 0.")

        semantic = {
            "run_id": run_id,
            "stage": stage,
            "event_type": event_type,
            "status": status,
            "duration_ms": duration,
            "counters": normalized_counters,
            "metrics": normalized_metrics,
            "dimensions": _json_safe(dimensions),
            "error_type": _clean(error_type),
            "error_code": _clean(error_code),
            "created_at": self.clock(),
        }

        if not event_id:
            event_id = "TELEM-" + hashlib.sha256(
                json.dumps(
                    semantic,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24].upper()

        existing = self._db.execute(
            "SELECT * FROM telemetry_events WHERE event_id=?",
            (event_id,),
        ).fetchone()

        if existing is not None:
            return self._row(existing)

        self._db.execute(
            """
            INSERT INTO telemetry_events(
                event_id, run_id, stage, event_type, status, duration_ms,
                counters_json, metrics_json, dimensions_json,
                error_type, error_code, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                stage,
                event_type,
                status,
                duration,
                json.dumps(normalized_counters, sort_keys=True),
                json.dumps(normalized_metrics, sort_keys=True),
                json.dumps(_json_safe(dimensions), sort_keys=True),
                _clean(error_type),
                _clean(error_code),
                semantic["created_at"],
            ),
        )
        self._db.commit()
        return self.get_event(event_id)

    def record_stage(
        self,
        *,
        run_id: str,
        stage: str,
        duration_ms: float,
        status: str = "ok",
        input_count: Optional[int] = None,
        output_count: Optional[int] = None,
        error_type: str = "",
        error_code: str = "",
        metrics: Optional[Mapping[str, Any]] = None,
        dimensions: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        counters = {}
        if input_count is not None:
            counters["input_count"] = input_count
        if output_count is not None:
            counters["output_count"] = output_count

        return self.record_event(
            run_id=run_id,
            stage=stage,
            event_type="stage_complete",
            status=status,
            duration_ms=duration_ms,
            counters=counters,
            metrics=metrics,
            dimensions=dimensions,
            error_type=error_type,
            error_code=error_code,
        )

    def get_event(self, event_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM telemetry_events WHERE event_id=?",
            (_clean(event_id),),
        ).fetchone()
        if row is None:
            raise TelemetryServiceError("Telemetry event not found.")
        return self._row(row)

    def list_events(
        self,
        *,
        run_id: str = "",
        stage: str = "",
        status: str = "",
        limit: Optional[int] = None,
    ) -> list[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM telemetry_events ORDER BY event_no ASC"
        ).fetchall()
        output = []
        for row in rows:
            record = self._row(row)
            if run_id and record["run_id"] != _clean(run_id):
                continue
            if stage and record["stage"] != _enum(stage):
                continue
            if status and record["status"] != _enum(status):
                continue
            output.append(record)

        if limit is not None:
            if isinstance(limit, bool) or int(limit) <= 0:
                raise TelemetryValidationError("limit must be positive integer.")
            output = output[: int(limit)]

        return output

    def run_summary(self, run_id: str) -> Dict[str, Any]:
        events = self.list_events(run_id=run_id)

        total_duration_ms = sum(
            item["duration_ms"] or 0.0
            for item in events
        )
        errors = [
            item
            for item in events
            if item["status"] not in {"ok", "success", "complete", "completed"}
        ]

        stage_counts = {}
        counters = {}
        for item in events:
            stage_counts[item["stage"]] = stage_counts.get(item["stage"], 0) + 1
            for key, value in item["counters"].items():
                counters[key] = counters.get(key, 0) + int(value)

        return {
            "run_id": _clean(run_id),
            "event_count": len(events),
            "error_event_count": len(errors),
            "total_recorded_duration_ms": round(total_duration_ms, 3),
            "stage_event_counts": stage_counts,
            "aggregate_counters": counters,
            "metadata": {
                "service_version": SERVICE_VERSION,
                "raw_prompts_stored": False,
                "raw_model_outputs_stored": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def export_for_evaluation(
        self,
        *,
        run_id: str,
    ) -> Dict[str, Any]:
        events = self.list_events(run_id=run_id)
        return {
            "run_id": _clean(run_id),
            "events": events,
            "summary": self.run_summary(run_id),
            "telemetry_is_grounding_evidence": False,
        }

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "event_no": int(row["event_no"]),
            "event_id": _clean(row["event_id"]),
            "run_id": _clean(row["run_id"]),
            "stage": _clean(row["stage"]),
            "event_type": _clean(row["event_type"]),
            "status": _clean(row["status"]),
            "duration_ms": (
                None if row["duration_ms"] is None else float(row["duration_ms"])
            ),
            "counters": json.loads(row["counters_json"]),
            "metrics": json.loads(row["metrics_json"]),
            "dimensions": json.loads(row["dimensions_json"]),
            "error_type": _clean(row["error_type"]),
            "error_code": _clean(row["error_code"]),
            "created_at": _clean(row["created_at"]),
        }


LocalTelemetryService = TelemetryService