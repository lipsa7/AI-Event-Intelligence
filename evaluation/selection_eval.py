"""
Offline evaluation for strategic cluster/topic selection.

Designed for experiments such as:
    A. top-k relevance
    B. MMR
    C. determinant / DPP-inspired quality-diversity selection

Gold JSONL example
------------------
{
  "query_id": "SEL-001",
  "target_k": 4,
  "important_theme_ids": ["AI_CHIPS", "AI_POWER", "AI_COPYRIGHT"],
  "candidates": [
    {
      "cluster_id": "CLUSTER-001",
      "theme_id": "AI_CHIPS",
      "relevant": true,
      "importance_grade": 3,
      "material_update": true,
      "human_report_usefulness": 4.5,
      "embedding": [0.1, 0.2, 0.3]
    }
  ],
  "case_tags": ["broad_topic", "dominant_story_risk"]
}

Prediction JSONL example
------------------------
{
  "query_id": "SEL-001",
  "selected_cluster_ids": ["CLUSTER-001", "CLUSTER-004"],
  "selection_runs": [
    ["CLUSTER-001", "CLUSTER-004"],
    ["CLUSTER-001", "CLUSTER-005"]
  ]
}

Metrics
-------
Selection quality:
- relevant selection rate
- mean selected importance grade
- important-theme coverage
- material-update rate
- human-rated downstream report usefulness (only if labelled)

Diversity:
- distinct selected theme count/rate
- repeated-theme rate
- dominant-theme share
- entropy effective theme count
- optional semantic effective rank from selected embeddings

Robustness:
- invalid selected-cluster rate
- duplicate selected-ID rate
- target-k exact-match rate
- run-to-run Jaccard stability

Important:
- semantic/theme diversity != source independence;
- high diversity is not automatically good: preserve relevance and coverage;
- human usefulness is only reported when explicitly labelled;
- these are offline evaluation metrics, not release thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set


def _clean(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split()).strip()


def _key(value: Any) -> str:
    text = _clean(value).casefold().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _id(value: Any) -> str:
    return _clean(value).upper()


def _safe_div(n: int | float, d: int | float) -> Optional[float]:
    return None if d == 0 else float(n) / float(d)


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return None if not vals else sum(vals) / len(vals)


def _tags(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out, seen = [], set()
    for item in value:
        normalized = _key(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _id_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out, seen = [], set()
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("cluster_id") or item.get("id") or ""
        normalized = _id(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    path = Path(path)
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} of {path}: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"Line {line_number} of {path} must contain a JSON object.")
            rows.append(parsed)
    return rows


def _query_id(record: Mapping[str, Any]) -> str:
    return _clean(record.get("query_id") or record.get("case_id") or record.get("id"))


def _index_cases(records: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        query_id = _query_id(record)
        if not query_id:
            raise ValueError(f"Selection record {index} is missing query_id/case_id.")
        if query_id in indexed:
            raise ValueError(f"Duplicate query_id: {query_id}")
        indexed[query_id] = dict(record)
    return indexed


def _candidate_index(gold_case: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = gold_case.get("candidates", [])
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return {}

    indexed: Dict[str, Dict[str, Any]] = {}
    for candidate in raw:
        if not isinstance(candidate, Mapping):
            continue
        item = dict(candidate)
        cluster_id = _id(item.get("cluster_id") or item.get("id"))
        if not cluster_id:
            continue
        if cluster_id in indexed:
            raise ValueError(f"Duplicate candidate {cluster_id} in {_query_id(gold_case)}")
        item["cluster_id"] = cluster_id
        indexed[cluster_id] = item
    return indexed


def _theme(candidate: Mapping[str, Any]) -> str:
    return _key(
        candidate.get("theme_id")
        or candidate.get("theme")
        or candidate.get("cluster_development")
    ) or "unknown_theme"


def _relevant(candidate: Mapping[str, Any]) -> bool:
    value = candidate.get("relevant")
    if isinstance(value, bool):
        return value
    try:
        return float(candidate.get("importance_grade", 0)) > 0
    except (TypeError, ValueError):
        return False


def _grade(candidate: Mapping[str, Any]) -> Optional[float]:
    value = candidate.get("importance_grade", candidate.get("relevance_grade"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _material_update(candidate: Mapping[str, Any]) -> Optional[bool]:
    value = candidate.get("material_update")
    if isinstance(value, bool):
        return value
    normalized = _key(value)
    if normalized in {"true", "yes", "material", "material_update", "new"}:
        return True
    if normalized in {"false", "no", "repetition", "stale", "not_material"}:
        return False
    return None


def _human_usefulness(candidate: Mapping[str, Any]) -> Optional[float]:
    value = candidate.get("human_report_usefulness", candidate.get("report_usefulness"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _embedding(candidate: Mapping[str, Any]) -> Optional[List[float]]:
    value = candidate.get("embedding")
    if not isinstance(value, list) or not value:
        return None
    out: List[float] = []
    for item in value:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            return None
    return out


def _raw_selected(prediction: Mapping[str, Any]) -> List[str]:
    value = (
        prediction.get("selected_cluster_ids")
        or prediction.get("selected_ids")
        or prediction.get("selected")
        or []
    )
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    out: List[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("cluster_id") or item.get("id") or ""
        cluster_id = _id(item)
        if cluster_id:
            out.append(cluster_id)
    return out


def _dedupe(values: Sequence[str]) -> List[str]:
    out, seen = [], set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _important_themes(
    gold_case: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> Set[str]:
    explicit = gold_case.get("important_theme_ids")
    if isinstance(explicit, str):
        explicit = [explicit]
    if isinstance(explicit, list):
        themes = {_key(x) for x in explicit if _key(x)}
        if themes:
            return themes

    grades = [g for c in candidates.values() if (g := _grade(c)) is not None]
    if not grades:
        return {_theme(c) for c in candidates.values() if _relevant(c)}

    max_grade = max(grades)
    return {_theme(c) for c in candidates.values() if _grade(c) == max_grade}


def _effective_theme_count(themes: Sequence[str]) -> Optional[float]:
    if not themes:
        return None
    counts = Counter(themes)
    total = len(themes)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log(p)
    return math.exp(entropy)


def _semantic_effective_rank(embeddings: Sequence[Sequence[float]]) -> Optional[float]:
    """Entropy effective rank of the Gram matrix of normalized embeddings."""
    if len(embeddings) < 2:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    dimensions = {len(v) for v in embeddings}
    if len(dimensions) != 1:
        return None

    matrix = np.asarray(embeddings, dtype=float)
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms == 0):
        return None

    matrix = matrix / norms[:, None]
    gram = matrix @ matrix.T
    eigenvalues = np.clip(np.linalg.eigvalsh(gram), 0.0, None)
    total = float(eigenvalues.sum())
    if total <= 0:
        return None

    probs = eigenvalues / total
    entropy = -sum(float(p) * math.log(float(p)) for p in probs if p > 0)
    return math.exp(entropy)


def _selection_runs(prediction: Mapping[str, Any]) -> List[List[str]]:
    raw = prediction.get("selection_runs")
    if not isinstance(raw, list):
        return []

    runs: List[List[str]] = []
    for run in raw:
        if isinstance(run, Mapping):
            ids = _id_list(
                run.get("selected_cluster_ids")
                or run.get("selected_ids")
                or run.get("selected")
                or []
            )
        else:
            ids = _id_list(run)
        runs.append(ids)
    return runs


def _jaccard_ids(left: Sequence[str], right: Sequence[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _stability(runs: Sequence[Sequence[str]]) -> Optional[float]:
    if len(runs) < 2:
        return None
    scores = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            scores.append(_jaccard_ids(runs[i], runs[j]))
    return None if not scores else sum(scores) / len(scores)


def _evaluate_case(
    gold_case: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> Dict[str, Any]:
    candidates = _candidate_index(gold_case)

    raw_selected = _raw_selected(prediction)
    selected = _dedupe(raw_selected)
    duplicate_count = len(raw_selected) - len(selected)

    valid_selected = [x for x in selected if x in candidates]
    invalid_selected = [x for x in selected if x not in candidates]
    selected_candidates = [candidates[x] for x in valid_selected]

    try:
        target_k = int(gold_case.get("target_k"))
    except (TypeError, ValueError):
        target_k = None

    relevant_count = sum(int(_relevant(c)) for c in selected_candidates)

    grades = [g for c in selected_candidates if (g := _grade(c)) is not None]

    selected_themes = [_theme(c) for c in selected_candidates]
    theme_counts = Counter(selected_themes)
    distinct_count = len(theme_counts)
    repeated_count = sum(max(count - 1, 0) for count in theme_counts.values())
    dominant_share = (
        max(theme_counts.values()) / len(selected_themes)
        if selected_themes
        else None
    )

    important_themes = _important_themes(gold_case, candidates)
    covered_important = set(selected_themes) & important_themes

    material_values = [v for c in selected_candidates if isinstance((v := _material_update(c)), bool)]
    usefulness_values = [v for c in selected_candidates if (v := _human_usefulness(c)) is not None]
    embeddings = [e for c in selected_candidates if (e := _embedding(c)) is not None]

    effective_theme_count = _effective_theme_count(selected_themes)

    return {
        "query_id": _query_id(gold_case),
        "candidate_count": len(candidates),
        "target_k": target_k,
        "raw_selected_count": len(raw_selected),
        "selected_count": len(selected),
        "valid_selected_count": len(valid_selected),
        "invalid_selected_count": len(invalid_selected),
        "invalid_selected_cluster_ids": invalid_selected,
        "duplicate_selected_id_count": duplicate_count,
        "target_k_exact_match": (
            len(selected) == target_k if target_k is not None else None
        ),
        "relevant_selection_rate": _round(_safe_div(relevant_count, len(selected_candidates))),
        "mean_selected_importance_grade": _round(
            sum(grades) / len(grades) if grades else None
        ),
        "important_theme_count": len(important_themes),
        "selected_important_theme_count": len(covered_important),
        "important_theme_coverage": _round(
            _safe_div(len(covered_important), len(important_themes))
        ),
        "distinct_selected_theme_count": distinct_count,
        "distinct_selected_theme_rate": _round(
            _safe_div(distinct_count, len(selected_themes))
        ),
        "repeated_theme_rate": _round(
            _safe_div(repeated_count, len(selected_themes))
        ),
        "dominant_theme_share": _round(dominant_share),
        "effective_theme_count": _round(effective_theme_count),
        "effective_theme_concentration_ratio": _round(
            _safe_div(effective_theme_count, len(selected_themes))
            if effective_theme_count is not None
            else None
        ),
        "semantic_effective_rank": _round(_semantic_effective_rank(embeddings)),
        "semantic_effective_rank_embedding_count": len(embeddings),
        "material_update_evaluated_count": len(material_values),
        "material_update_rate": _round(
            _safe_div(sum(int(v) for v in material_values), len(material_values))
        ),
        "human_report_usefulness_evaluated_count": len(usefulness_values),
        "mean_human_report_usefulness": _round(
            sum(usefulness_values) / len(usefulness_values)
            if usefulness_values
            else None
        ),
        "selection_run_count": len(_selection_runs(prediction)),
        "run_to_run_jaccard_stability": _round(
            _stability(_selection_runs(prediction))
        ),
        "selected_theme_distribution": dict(sorted(theme_counts.items())),
        "case_tags": _tags(gold_case.get("case_tags") or gold_case.get("tags") or []),
    }


def evaluate_selection_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    gold_by_query = _index_cases(gold_records)
    pred_by_query = _index_cases(prediction_records)

    gold_ids = set(gold_by_query)
    pred_ids = set(pred_by_query)

    missing_ids = sorted(gold_ids - pred_ids)
    extra_ids = sorted(pred_ids - gold_ids)

    per_case: List[Dict[str, Any]] = []

    metric_names = (
        "relevant_selection_rate",
        "mean_selected_importance_grade",
        "important_theme_coverage",
        "distinct_selected_theme_rate",
        "repeated_theme_rate",
        "dominant_theme_share",
        "effective_theme_count",
        "effective_theme_concentration_ratio",
        "semantic_effective_rank",
        "material_update_rate",
        "mean_human_report_usefulness",
        "run_to_run_jaccard_stability",
    )

    metric_values: Dict[str, List[Optional[float]]] = {
        name: [] for name in metric_names
    }

    total_selected = total_invalid = total_duplicates = 0
    target_k_total = target_k_exact = 0

    tag_values: Dict[str, Dict[str, List[Optional[float]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for query_id in sorted(gold_ids):
        metrics = _evaluate_case(
            gold_by_query[query_id],
            pred_by_query.get(query_id, {}),
        )
        per_case.append(metrics)

        total_selected += int(metrics["selected_count"])
        total_invalid += int(metrics["invalid_selected_count"])
        total_duplicates += int(metrics["duplicate_selected_id_count"])

        if metrics["target_k_exact_match"] is not None:
            target_k_total += 1
            target_k_exact += int(metrics["target_k_exact_match"])

        for name in metric_names:
            metric_values[name].append(metrics[name])

        for tag in metrics["case_tags"]:
            tag_values[tag]["relevance"].append(metrics["relevant_selection_rate"])
            tag_values[tag]["coverage"].append(metrics["important_theme_coverage"])
            tag_values[tag]["repetition"].append(metrics["repeated_theme_rate"])
            tag_values[tag]["material_update"].append(metrics["material_update_rate"])

    tag_breakdown = {
        tag: {
            "case_count": len(bucket["relevance"]),
            "mean_relevant_selection_rate": _round(_mean(bucket["relevance"])),
            "mean_important_theme_coverage": _round(_mean(bucket["coverage"])),
            "mean_repeated_theme_rate": _round(_mean(bucket["repetition"])),
            "mean_material_update_rate": _round(_mean(bucket["material_update"])),
        }
        for tag, bucket in sorted(tag_values.items())
    }

    return {
        "gold_query_count": len(gold_ids),
        "prediction_query_count": len(pred_ids),
        "missing_query_count": len(missing_ids),
        "extra_query_count": len(extra_ids),
        "missing_query_ids": missing_ids,
        "extra_query_ids": extra_ids,
        "selected_cluster_count": total_selected,
        "invalid_selected_cluster_count": total_invalid,
        "duplicate_selected_id_count": total_duplicates,
        "invalid_selected_cluster_rate": _round(
            _safe_div(total_invalid, total_selected)
        ),
        "duplicate_selected_id_rate": _round(
            _safe_div(total_duplicates, total_selected + total_duplicates)
        ),
        "target_k_exact_match_rate": _round(
            _safe_div(target_k_exact, target_k_total)
        ),
        "target_k_evaluated_count": target_k_total,
        "mean_relevant_selection_rate": _round(
            _mean(metric_values["relevant_selection_rate"])
        ),
        "mean_selected_importance_grade": _round(
            _mean(metric_values["mean_selected_importance_grade"])
        ),
        "mean_important_theme_coverage": _round(
            _mean(metric_values["important_theme_coverage"])
        ),
        "mean_distinct_selected_theme_rate": _round(
            _mean(metric_values["distinct_selected_theme_rate"])
        ),
        "mean_repeated_theme_rate": _round(
            _mean(metric_values["repeated_theme_rate"])
        ),
        "mean_dominant_theme_share": _round(
            _mean(metric_values["dominant_theme_share"])
        ),
        "mean_effective_theme_count": _round(
            _mean(metric_values["effective_theme_count"])
        ),
        "mean_effective_theme_concentration_ratio": _round(
            _mean(metric_values["effective_theme_concentration_ratio"])
        ),
        "mean_semantic_effective_rank": _round(
            _mean(metric_values["semantic_effective_rank"])
        ),
        "mean_material_update_rate": _round(
            _mean(metric_values["material_update_rate"])
        ),
        "mean_human_report_usefulness": _round(
            _mean(metric_values["mean_human_report_usefulness"])
        ),
        "mean_run_to_run_jaccard_stability": _round(
            _mean(metric_values["run_to_run_jaccard_stability"])
        ),
        "case_tag_breakdown": tag_breakdown,
        "per_case": per_case,
        "interpretation_notes": {
            "diversity": (
                "High diversity is not automatically good; interpret it with "
                "relevance and important-theme coverage."
            ),
            "source_independence": (
                "Semantic/theme diversity is not source or syndication independence."
            ),
            "effective_theme_count": (
                "Label-based effective theme count is an entropy concentration measure."
            ),
            "semantic_effective_rank": (
                "Optional geometric metric from normalized selected embeddings; "
                "returns null when embeddings or numpy are unavailable."
            ),
            "usefulness": (
                "Human report usefulness is only reported when explicitly labelled."
            ),
        },
    }


def run_evaluation(
    gold_path: str | Path,
    predictions_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Standard interface expected by evaluation_runner.py."""
    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for selection evaluation."
        )

    if kwargs:
        raise TypeError(
            "Unknown selection evaluation option(s): "
            + ", ".join(sorted(kwargs))
        )

    return evaluate_selection_records(
        gold_records=load_jsonl(gold_path),
        prediction_records=load_jsonl(predictions_path),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate strategic cluster/topic selection."
    )
    parser.add_argument("--gold", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    metrics = run_evaluation(
        gold_path=args.gold,
        predictions_path=args.predictions,
    )

    print(json.dumps(metrics, indent=2, ensure_ascii=False))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()