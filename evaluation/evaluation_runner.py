"""
Unified offline-evaluation runner for the Event Intelligence research product.

Default conventions
-------------------
Gold datasets:
    evaluation/datasets/<stage>_gold.jsonl

Predictions:
    evaluation/predictions/<variant>/<stage>_predictions.jsonl

Results:
    evaluation/results/<run_id>/
        <stage>.json
        input_manifest.json
        summary.json

Supported stages
----------------
- extraction
- clustering
- retrieval
- grounding
- reasoning
- red_team
- report
- selection

Preferred evaluator contract
----------------------------
Each evaluator module should expose:

    run_evaluation(
        gold_path: str | Path,
        predictions_path: str | Path | None = None,
        **kwargs,
    ) -> dict

The current clustering evaluator is supported through an adapter because it
exposes `load_jsonl()` and `evaluate_clustering()`.

Important
---------
This runner performs measurement only. It does not make release-policy or
product-governance decisions. Numeric deltas between variants are descriptive;
statistical significance belongs in the statistics/evaluation layer.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import logging
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


logger = logging.getLogger(__name__)


SUPPORTED_STAGES: tuple[str, ...] = (
    "extraction",
    "clustering",
    "retrieval",
    "grounding",
    "reasoning",
    "red_team",
    "report",
    "selection",
)

STAGE_MODULES: Dict[str, str] = {
    "extraction": "extraction_eval",
    "clustering": "clustering_eval",
    "retrieval": "retrieval_eval",
    "grounding": "grounding_eval",
    "reasoning": "reasoning_eval",
    "red_team": "red_team_eval",
    "report": "report_eval",
    "selection": "selection_eval",
}


@dataclass
class StageResult:
    stage: str
    status: str
    gold_path: str = ""
    predictions_path: str = ""
    evaluator_module: str = ""
    duration_ms: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationRunSummary:
    run_id: str
    variant: str
    started_at_utc: str
    completed_at_utc: str
    duration_ms: float
    requested_stages: List[str]
    completed_stages: List[str]
    skipped_stages: List[str]
    failed_stages: List[str]
    strict: bool
    environment: Dict[str, Any]
    dataset_manifest_fingerprint: str
    stage_results: Dict[str, Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_dump(payload: Any, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )


def _load_json(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return parsed


def _file_fingerprint(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def _manifest_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(
        canonical.encode("utf-8", errors="ignore")
    ).hexdigest()[:16]


def _resolve_git_commit(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except Exception:
        return ""

    return result.stdout.strip()[:40] if result.returncode == 0 else ""


def _environment_metadata(project_root: Path) -> Dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": _resolve_git_commit(project_root),
    }


def _normalize_requested_stages(values: Sequence[str]) -> List[str]:
    stages: List[str] = []

    for raw in values:
        for part in str(raw).split(","):
            stage = part.strip().lower()
            if not stage:
                continue

            if stage == "all":
                for supported in SUPPORTED_STAGES:
                    if supported not in stages:
                        stages.append(supported)
                continue

            if stage not in SUPPORTED_STAGES:
                raise ValueError(
                    f"Unsupported stage {stage!r}. Expected one of: "
                    f"{', '.join(SUPPORTED_STAGES)}"
                )

            if stage not in stages:
                stages.append(stage)

    if not stages:
        raise ValueError("At least one evaluation stage is required.")

    return stages


def _load_evaluator_module(stage: str, evaluation_dir: Path):
    module_basename = STAGE_MODULES[stage]
    package_name = f"evaluation.{module_basename}"

    try:
        return importlib.import_module(package_name)
    except ModuleNotFoundError as package_error:
        local_path = evaluation_dir / f"{module_basename}.py"

        if not local_path.exists():
            raise ModuleNotFoundError(
                f"Evaluator for {stage!r} was not found as {package_name!r} "
                f"or at {local_path}."
            ) from package_error

        spec = importlib.util.spec_from_file_location(
            f"_local_{module_basename}",
            local_path,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load evaluator: {local_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


def _load_jsonl_records(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    path = Path(path)

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc

            if not isinstance(parsed, dict):
                raise ValueError(
                    f"Line {line_number} of {path} must contain a JSON object."
                )

            records.append(parsed)

    return records


def _prediction_mapping_from_jsonl(
    path: str | Path,
    *,
    item_id_key: str = "item_id",
    predicted_cluster_key: str = "predicted_cluster_id",
) -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    for index, record in enumerate(_load_jsonl_records(path)):
        item_id = _safe_text(record.get(item_id_key))
        predicted_cluster = _safe_text(record.get(predicted_cluster_key))

        if not item_id:
            raise ValueError(
                f"Prediction record {index} is missing {item_id_key!r}."
            )
        if item_id in mapping:
            raise ValueError(f"Duplicate prediction for item_id={item_id!r}.")

        mapping[item_id] = predicted_cluster

    return mapping


def _run_clustering_adapter(
    module: Any,
    gold_path: Path,
    predictions_path: Optional[Path],
    kwargs: Mapping[str, Any],
) -> Dict[str, Any]:
    if not hasattr(module, "evaluate_clustering"):
        raise AttributeError(
            "clustering_eval.py must expose evaluate_clustering()."
        )

    gold_records = (
        module.load_jsonl(gold_path)
        if hasattr(module, "load_jsonl")
        else _load_jsonl_records(gold_path)
    )

    predictions = None
    if predictions_path is not None:
        predictions = _prediction_mapping_from_jsonl(
            predictions_path,
            item_id_key=_safe_text(kwargs.get("item_id_key")) or "item_id",
            predicted_cluster_key=(
                _safe_text(kwargs.get("predicted_cluster_key"))
                or "predicted_cluster_id"
            ),
        )

    return module.evaluate_clustering(
        records=gold_records,
        predicted_assignments=predictions,
        **dict(kwargs),
    )


def _run_generic_evaluator(
    module: Any,
    gold_path: Path,
    predictions_path: Optional[Path],
    kwargs: Mapping[str, Any],
) -> Dict[str, Any]:
    evaluator: Optional[Callable[..., Any]] = None

    if hasattr(module, "run_evaluation"):
        evaluator = module.run_evaluation
    elif hasattr(module, "evaluate_from_files"):
        evaluator = module.evaluate_from_files

    if evaluator is None:
        raise AttributeError(
            f"{module.__name__} must expose run_evaluation(...) or "
            "evaluate_from_files(...)."
        )

    result = evaluator(
        gold_path=gold_path,
        predictions_path=predictions_path,
        **dict(kwargs),
    )

    if not isinstance(result, dict):
        raise TypeError(
            f"{module.__name__} returned {type(result).__name__}; expected dict."
        )

    return result


def _resolve_stage_paths(
    *,
    stage: str,
    variant: str,
    evaluation_dir: Path,
    manifest: Mapping[str, Any],
) -> tuple[Path, Optional[Path], Dict[str, Any]]:
    """
    Resolve stage files from an optional manifest or repository convention.

    Manifest example:
    {
      "clustering": {
        "gold": "evaluation/datasets/clustering_gold.jsonl",
        "predictions": "evaluation/predictions/v2/clustering_predictions.jsonl",
        "kwargs": {}
      }
    }
    """
    stage_config = manifest.get(stage, {})
    if not isinstance(stage_config, dict):
        raise ValueError(f"Manifest entry for {stage!r} must be an object.")

    gold_override = _safe_text(stage_config.get("gold"))
    prediction_override = _safe_text(stage_config.get("predictions"))
    kwargs = stage_config.get("kwargs", {})

    if not isinstance(kwargs, dict):
        raise ValueError(f"Manifest kwargs for {stage!r} must be an object.")

    gold_path = (
        Path(gold_override)
        if gold_override
        else evaluation_dir / "datasets" / f"{stage}_gold.jsonl"
    )

    if prediction_override:
        predictions_path: Optional[Path] = Path(prediction_override)
    else:
        candidate = (
            evaluation_dir
            / "predictions"
            / variant
            / f"{stage}_predictions.jsonl"
        )
        predictions_path = candidate if candidate.exists() else None

    return gold_path, predictions_path, dict(kwargs)


def _stage_input_manifest(
    *,
    stage: str,
    gold_path: Path,
    predictions_path: Optional[Path],
    kwargs: Mapping[str, Any],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "stage": stage,
        "gold_path": str(gold_path),
        "gold_exists": gold_path.exists(),
        "predictions_path": str(predictions_path or ""),
        "predictions_exist": bool(
            predictions_path and predictions_path.exists()
        ),
        "kwargs": dict(kwargs),
    }

    if gold_path.exists():
        payload["gold_fingerprint"] = _file_fingerprint(gold_path)

    if predictions_path and predictions_path.exists():
        payload["predictions_fingerprint"] = _file_fingerprint(
            predictions_path
        )

    return payload


def run_stage(
    *,
    stage: str,
    variant: str,
    evaluation_dir: Path,
    manifest: Mapping[str, Any],
    strict: bool,
) -> StageResult:
    started = time.perf_counter()

    gold_path, predictions_path, kwargs = _resolve_stage_paths(
        stage=stage,
        variant=variant,
        evaluation_dir=evaluation_dir,
        manifest=manifest,
    )

    module_basename = STAGE_MODULES[stage]

    if not gold_path.exists():
        status = "failed" if strict else "skipped"
        return StageResult(
            stage=stage,
            status=status,
            gold_path=str(gold_path),
            predictions_path=str(predictions_path or ""),
            evaluator_module=module_basename,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            error_type="FileNotFoundError",
            error_message=f"Gold dataset not found: {gold_path}",
        )

    try:
        module = _load_evaluator_module(stage, evaluation_dir)

        if stage == "clustering":
            metrics = _run_clustering_adapter(
                module,
                gold_path,
                predictions_path,
                kwargs,
            )
        else:
            metrics = _run_generic_evaluator(
                module,
                gold_path,
                predictions_path,
                kwargs,
            )

        return StageResult(
            stage=stage,
            status="completed",
            gold_path=str(gold_path),
            predictions_path=str(predictions_path or ""),
            evaluator_module=module_basename,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            metrics=metrics,
        )

    except Exception as exc:
        logger.exception("Evaluation stage %s failed.", stage)
        return StageResult(
            stage=stage,
            status="failed",
            gold_path=str(gold_path),
            predictions_path=str(predictions_path or ""),
            evaluator_module=module_basename,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            error_type=type(exc).__name__,
            error_message=str(exc)[:1500],
        )


def run_evaluations(
    *,
    stages: Sequence[str],
    variant: str,
    project_root: str | Path = ".",
    manifest_path: str | Path | None = None,
    results_root: str | Path | None = None,
    run_id: str = "",
    strict: bool = False,
) -> Dict[str, Any]:
    project_root = Path(project_root).resolve()
    evaluation_dir = project_root / "evaluation"
    requested_stages = _normalize_requested_stages(stages)

    manifest: Dict[str, Any] = (
        _load_json(manifest_path) if manifest_path else {}
    )

    results_root_path = (
        Path(results_root)
        if results_root
        else evaluation_dir / "results"
    )

    variant = _safe_text(variant) or "default"

    if not run_id:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_variant = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-"
            for ch in variant
        ).strip("-") or "default"
        run_id = f"{timestamp}_{safe_variant}"

    run_dir = results_root_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    started_perf = time.perf_counter()
    started_at = _utc_now_iso()

    input_entries: Dict[str, Any] = {}
    for stage in requested_stages:
        gold_path, predictions_path, kwargs = _resolve_stage_paths(
            stage=stage,
            variant=variant,
            evaluation_dir=evaluation_dir,
            manifest=manifest,
        )
        input_entries[stage] = _stage_input_manifest(
            stage=stage,
            gold_path=gold_path,
            predictions_path=predictions_path,
            kwargs=kwargs,
        )

    manifest_fingerprint = _manifest_fingerprint(input_entries)

    _json_dump(
        {
            "run_id": run_id,
            "variant": variant,
            "requested_stages": requested_stages,
            "dataset_manifest_fingerprint": manifest_fingerprint,
            "inputs": input_entries,
        },
        run_dir / "input_manifest.json",
    )

    stage_results: Dict[str, StageResult] = {}

    for stage in requested_stages:
        logger.info("Running evaluation stage=%s", stage)
        result = run_stage(
            stage=stage,
            variant=variant,
            evaluation_dir=evaluation_dir,
            manifest=manifest,
            strict=strict,
        )
        stage_results[stage] = result
        _json_dump(result.to_dict(), run_dir / f"{stage}.json")

    completed_stages = [
        stage for stage, result in stage_results.items()
        if result.status == "completed"
    ]
    skipped_stages = [
        stage for stage, result in stage_results.items()
        if result.status == "skipped"
    ]
    failed_stages = [
        stage for stage, result in stage_results.items()
        if result.status == "failed"
    ]

    summary = EvaluationRunSummary(
        run_id=run_id,
        variant=variant,
        started_at_utc=started_at,
        completed_at_utc=_utc_now_iso(),
        duration_ms=round((time.perf_counter() - started_perf) * 1000, 2),
        requested_stages=list(requested_stages),
        completed_stages=completed_stages,
        skipped_stages=skipped_stages,
        failed_stages=failed_stages,
        strict=bool(strict),
        environment=_environment_metadata(project_root),
        dataset_manifest_fingerprint=manifest_fingerprint,
        stage_results={
            stage: result.to_dict()
            for stage, result in stage_results.items()
        },
    )

    summary_dict = summary.to_dict()
    _json_dump(summary_dict, run_dir / "summary.json")
    return summary_dict


def compare_result_runs(
    baseline_summary_path: str | Path,
    candidate_summary_path: str | Path,
) -> Dict[str, Any]:
    """
    Compare descriptive numeric deltas between two evaluation runs.

    No statistical-significance claim is made here.
    """
    baseline = _load_json(baseline_summary_path)
    candidate = _load_json(candidate_summary_path)

    baseline_stages = baseline.get("stage_results", {})
    candidate_stages = candidate.get("stage_results", {})

    if not isinstance(baseline_stages, dict):
        baseline_stages = {}
    if not isinstance(candidate_stages, dict):
        candidate_stages = {}

    comparisons: Dict[str, Any] = {}

    for stage in sorted(set(baseline_stages) & set(candidate_stages)):
        baseline_metrics = baseline_stages.get(stage, {}).get("metrics", {})
        candidate_metrics = candidate_stages.get(stage, {}).get("metrics", {})

        if not isinstance(baseline_metrics, dict):
            baseline_metrics = {}
        if not isinstance(candidate_metrics, dict):
            candidate_metrics = {}

        deltas: Dict[str, Any] = {}

        for metric in sorted(set(baseline_metrics) & set(candidate_metrics)):
            baseline_value = baseline_metrics[metric]
            candidate_value = candidate_metrics[metric]

            if (
                isinstance(baseline_value, (int, float))
                and not isinstance(baseline_value, bool)
                and isinstance(candidate_value, (int, float))
                and not isinstance(candidate_value, bool)
            ):
                deltas[metric] = {
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                    "delta": round(
                        float(candidate_value) - float(baseline_value),
                        6,
                    ),
                }

        comparisons[stage] = {
            "numeric_metric_deltas": deltas
        }

    return {
        "baseline_run_id": baseline.get("run_id", ""),
        "candidate_run_id": candidate.get("run_id", ""),
        "baseline_variant": baseline.get("variant", ""),
        "candidate_variant": candidate.get("variant", ""),
        "stage_comparisons": comparisons,
        "interpretation_note": (
            "Metric deltas are descriptive only. Use paired confidence "
            "intervals/statistical tests where appropriate before claiming "
            "a meaningful improvement."
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or compare Event Intelligence offline evaluations."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run one or more evaluation stages.",
    )
    run_parser.add_argument(
        "--stage",
        action="append",
        default=[],
        help="Stage name, comma-separated stages, or 'all'.",
    )
    run_parser.add_argument(
        "--variant",
        default="default",
        help="Model/prompt/experiment variant name.",
    )
    run_parser.add_argument(
        "--project-root",
        default=".",
        help="Project root containing evaluation/.",
    )
    run_parser.add_argument(
        "--manifest",
        default="",
        help="Optional JSON file overriding per-stage paths/kwargs.",
    )
    run_parser.add_argument(
        "--results-root",
        default="",
        help="Optional results directory; defaults to evaluation/results.",
    )
    run_parser.add_argument(
        "--run-id",
        default="",
        help="Optional explicit run ID.",
    )
    run_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat missing datasets/modules as failures rather than "
            "incremental-development skips."
        ),
    )

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare descriptive numeric metric deltas between runs.",
    )
    compare_parser.add_argument(
        "--baseline",
        required=True,
        help="Path to baseline summary.json.",
    )
    compare_parser.add_argument(
        "--candidate",
        required=True,
        help="Path to candidate summary.json.",
    )
    compare_parser.add_argument(
        "--output",
        default="",
        help="Optional JSON path for comparison output.",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.command == "run":
        summary = run_evaluations(
            stages=args.stage or ["all"],
            variant=args.variant,
            project_root=args.project_root,
            manifest_path=args.manifest or None,
            results_root=args.results_root or None,
            run_id=args.run_id,
            strict=args.strict,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        if args.strict and summary["failed_stages"]:
            raise SystemExit(1)
        return

    comparison = compare_result_runs(
        baseline_summary_path=args.baseline,
        candidate_summary_path=args.candidate,
    )
    print(json.dumps(comparison, indent=2, ensure_ascii=False))

    if args.output:
        _json_dump(comparison, args.output)


if __name__ == "__main__":
    main()