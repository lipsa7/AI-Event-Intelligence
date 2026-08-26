"""
Clustering evaluation utilities for the Event Intelligence research product.

Evaluates whether event clustering groups records that belong to the same
underlying development while keeping materially different developments apart.

Core metrics:
- pairwise precision / recall / F1
- adjusted Rand index (ARI)
- cluster count error

Diagnostics:
- mixed predicted-cluster rate
- item-weighted mixed-cluster rate
- fragmented gold-cluster rate
- item-weighted fragmentation rate
- singleton rates
- unassigned prediction rate
- optional coherence-status accuracy / macro-F1
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ALLOWED_COHERENCE_STATUSES = {
    "coherent",
    "mixed",
    "insufficient_evidence",
}


@dataclass(frozen=True)
class PairwiseCounts:
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int


@dataclass(frozen=True)
class ClusteringEvaluationResult:
    item_count: int
    assigned_item_count: int
    unassigned_item_count: int
    gold_cluster_count: int
    predicted_cluster_count: int

    pairwise_true_positive: int
    pairwise_false_positive: int
    pairwise_false_negative: int
    pairwise_true_negative: int

    pairwise_precision: Optional[float]
    pairwise_recall: Optional[float]
    pairwise_f1: Optional[float]
    adjusted_rand_index: Optional[float]

    cluster_count_error: int
    absolute_cluster_count_error: int

    mixed_predicted_cluster_count: int
    mixed_predicted_cluster_rate: Optional[float]
    mixed_item_count: int
    mixed_item_rate: Optional[float]

    fragmented_gold_cluster_count: int
    fragmented_gold_cluster_rate: Optional[float]
    fragmented_item_count: int
    fragmented_item_rate: Optional[float]

    gold_singleton_cluster_rate: Optional[float]
    predicted_singleton_cluster_rate: Optional[float]
    unassigned_prediction_rate: Optional[float]

    coherence_accuracy: Optional[float]
    coherence_macro_f1: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _safe_divide(numerator: int | float, denominator: int | float) -> Optional[float]:
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _round_metric(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def _normalize_label(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_coherence_status(value: Any) -> str:
    normalized = _normalize_label(value).lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in ALLOWED_COHERENCE_STATUSES else ""


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    path = Path(path)
    records: List[Dict[str, Any]] = []

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


def save_metrics(metrics: Mapping[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(
            dict(metrics),
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def _validate_records(
    records: Sequence[Mapping[str, Any]],
    *,
    item_id_key: str,
    gold_cluster_key: str,
) -> None:
    seen_item_ids: set[str] = set()

    for index, record in enumerate(records):
        item_id = _normalize_label(record.get(item_id_key))
        gold_cluster_id = _normalize_label(record.get(gold_cluster_key))

        if not item_id:
            raise ValueError(
                f"Record {index} is missing a non-empty '{item_id_key}'."
            )
        if item_id in seen_item_ids:
            raise ValueError(f"Duplicate item_id detected: {item_id}")
        if not gold_cluster_id:
            raise ValueError(
                f"Record {item_id!r} is missing a non-empty '{gold_cluster_key}'."
            )

        seen_item_ids.add(item_id)


def _build_assignments(
    records: Sequence[Mapping[str, Any]],
    predicted_assignments: Optional[Mapping[str, Any]],
    *,
    item_id_key: str,
    gold_cluster_key: str,
    predicted_cluster_key: str,
) -> Tuple[List[str], List[str], List[str]]:
    item_ids: List[str] = []
    gold_labels: List[str] = []
    predicted_labels: List[str] = []

    for record in records:
        item_id = _normalize_label(record.get(item_id_key))
        gold_label = _normalize_label(record.get(gold_cluster_key))

        if predicted_assignments is not None:
            raw_prediction = predicted_assignments.get(item_id)
        else:
            raw_prediction = record.get(predicted_cluster_key)

        predicted_label = _normalize_label(raw_prediction)

        # Every missing prediction gets its own synthetic label so missing items
        # are not incorrectly grouped together.
        if not predicted_label:
            predicted_label = f"__UNASSIGNED__::{item_id}"

        item_ids.append(item_id)
        gold_labels.append(gold_label)
        predicted_labels.append(predicted_label)

    return item_ids, gold_labels, predicted_labels


def _pairwise_counts(
    gold_labels: Sequence[str],
    predicted_labels: Sequence[str],
) -> PairwiseCounts:
    if len(gold_labels) != len(predicted_labels):
        raise ValueError("Gold and predicted labels must have equal length.")

    tp = fp = fn = tn = 0

    for left in range(len(gold_labels)):
        for right in range(left + 1, len(gold_labels)):
            gold_same = gold_labels[left] == gold_labels[right]
            predicted_same = predicted_labels[left] == predicted_labels[right]

            if gold_same and predicted_same:
                tp += 1
            elif not gold_same and predicted_same:
                fp += 1
            elif gold_same and not predicted_same:
                fn += 1
            else:
                tn += 1

    return PairwiseCounts(tp, fp, fn, tn)


def _comb2(value: int) -> int:
    return value * (value - 1) // 2


def adjusted_rand_index(
    gold_labels: Sequence[str],
    predicted_labels: Sequence[str],
) -> Optional[float]:
    if len(gold_labels) != len(predicted_labels):
        raise ValueError("Gold and predicted labels must have equal length.")

    n = len(gold_labels)
    if n < 2:
        return None

    gold_counts = Counter(gold_labels)
    predicted_counts = Counter(predicted_labels)
    contingency = Counter(zip(gold_labels, predicted_labels))

    sum_comb_contingency = sum(_comb2(count) for count in contingency.values())
    sum_comb_gold = sum(_comb2(count) for count in gold_counts.values())
    sum_comb_pred = sum(_comb2(count) for count in predicted_counts.values())
    total_pairs = _comb2(n)

    if total_pairs == 0:
        return None

    expected_index = sum_comb_gold * sum_comb_pred / total_pairs
    max_index = 0.5 * (sum_comb_gold + sum_comb_pred)
    denominator = max_index - expected_index

    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        # Degenerate case: return perfect only when partitions match exactly.
        return 1.0 if list(gold_labels) == list(predicted_labels) else 0.0

    return (sum_comb_contingency - expected_index) / denominator


def _cluster_members(
    item_ids: Sequence[str],
    labels: Sequence[str],
) -> Dict[str, List[str]]:
    clusters: Dict[str, List[str]] = defaultdict(list)
    for item_id, label in zip(item_ids, labels):
        clusters[label].append(item_id)
    return dict(clusters)


def _mixed_cluster_diagnostics(
    gold_labels: Sequence[str],
    predicted_labels: Sequence[str],
) -> Tuple[int, Optional[float], int, Optional[float]]:
    predicted_to_gold: Dict[str, Counter[str]] = defaultdict(Counter)

    for gold_label, predicted_label in zip(gold_labels, predicted_labels):
        predicted_to_gold[predicted_label][gold_label] += 1

    real_predicted_clusters = {
        label: dist
        for label, dist in predicted_to_gold.items()
        if not label.startswith("__UNASSIGNED__::")
    }

    mixed_cluster_count = 0
    mixed_item_count = 0

    for distribution in real_predicted_clusters.values():
        if len(distribution) > 1:
            mixed_cluster_count += 1
            mixed_item_count += sum(distribution.values())

    assigned_items = sum(
        1
        for label in predicted_labels
        if not label.startswith("__UNASSIGNED__::")
    )

    return (
        mixed_cluster_count,
        _safe_divide(mixed_cluster_count, len(real_predicted_clusters)),
        mixed_item_count,
        _safe_divide(mixed_item_count, assigned_items),
    )


def _fragmentation_diagnostics(
    gold_labels: Sequence[str],
    predicted_labels: Sequence[str],
) -> Tuple[int, Optional[float], int, Optional[float]]:
    gold_to_predicted: Dict[str, Counter[str]] = defaultdict(Counter)

    for gold_label, predicted_label in zip(gold_labels, predicted_labels):
        gold_to_predicted[gold_label][predicted_label] += 1

    fragmented_cluster_count = 0
    fragmented_item_count = 0

    for distribution in gold_to_predicted.values():
        if len(distribution) > 1:
            fragmented_cluster_count += 1
            fragmented_item_count += sum(distribution.values())

    return (
        fragmented_cluster_count,
        _safe_divide(fragmented_cluster_count, len(gold_to_predicted)),
        fragmented_item_count,
        _safe_divide(fragmented_item_count, len(gold_labels)),
    )


def _singleton_cluster_rate(
    labels: Sequence[str],
    *,
    ignore_unassigned: bool,
) -> Optional[float]:
    counts = Counter(
        label
        for label in labels
        if not (
            ignore_unassigned
            and label.startswith("__UNASSIGNED__::")
        )
    )

    if not counts:
        return None

    singleton_count = sum(1 for size in counts.values() if size == 1)
    return singleton_count / len(counts)


def _macro_f1(
    gold_values: Sequence[str],
    predicted_values: Sequence[str],
    labels: Sequence[str],
) -> Optional[float]:
    per_class_f1: List[float] = []

    for label in labels:
        tp = sum(
            1
            for gold, pred in zip(gold_values, predicted_values)
            if gold == label and pred == label
        )
        fp = sum(
            1
            for gold, pred in zip(gold_values, predicted_values)
            if gold != label and pred == label
        )
        fn = sum(
            1
            for gold, pred in zip(gold_values, predicted_values)
            if gold == label and pred != label
        )

        precision = _safe_divide(tp, tp + fp)
        recall = _safe_divide(tp, tp + fn)

        if precision is None and recall is None:
            continue

        precision = precision or 0.0
        recall = recall or 0.0

        f1 = (
            0.0
            if precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )
        per_class_f1.append(f1)

    if not per_class_f1:
        return None

    return sum(per_class_f1) / len(per_class_f1)


def _coherence_metrics(
    records: Sequence[Mapping[str, Any]],
    *,
    gold_key: str,
    predicted_key: str,
) -> Tuple[Optional[float], Optional[float]]:
    gold_values: List[str] = []
    predicted_values: List[str] = []

    for record in records:
        gold = _normalize_coherence_status(record.get(gold_key))
        predicted = _normalize_coherence_status(record.get(predicted_key))

        if not gold or not predicted:
            continue

        gold_values.append(gold)
        predicted_values.append(predicted)

    if not gold_values:
        return None, None

    accuracy = sum(
        1
        for gold, pred in zip(gold_values, predicted_values)
        if gold == pred
    ) / len(gold_values)

    macro_f1 = _macro_f1(
        gold_values,
        predicted_values,
        sorted(ALLOWED_COHERENCE_STATUSES),
    )

    return accuracy, macro_f1


def evaluate_clustering(
    records: Sequence[Mapping[str, Any]],
    predicted_assignments: Optional[Mapping[str, Any]] = None,
    *,
    item_id_key: str = "item_id",
    gold_cluster_key: str = "gold_cluster_id",
    predicted_cluster_key: str = "predicted_cluster_id",
    gold_coherence_key: str = "gold_coherence_status",
    predicted_coherence_key: str = "predicted_coherence_status",
) -> Dict[str, Any]:
    """
    Evaluate predicted event-cluster assignments against labelled ground truth.

    Missing predictions are treated as unassigned, not silently dropped.
    """
    records = list(records)

    if not records:
        raise ValueError("At least one evaluation record is required.")

    _validate_records(
        records,
        item_id_key=item_id_key,
        gold_cluster_key=gold_cluster_key,
    )

    item_ids, gold_labels, predicted_labels = _build_assignments(
        records,
        predicted_assignments,
        item_id_key=item_id_key,
        gold_cluster_key=gold_cluster_key,
        predicted_cluster_key=predicted_cluster_key,
    )

    pairwise = _pairwise_counts(gold_labels, predicted_labels)

    pairwise_precision = _safe_divide(
        pairwise.true_positive,
        pairwise.true_positive + pairwise.false_positive,
    )
    pairwise_recall = _safe_divide(
        pairwise.true_positive,
        pairwise.true_positive + pairwise.false_negative,
    )

    if pairwise_precision is None or pairwise_recall is None:
        pairwise_f1 = None
    elif pairwise_precision + pairwise_recall == 0:
        pairwise_f1 = 0.0
    else:
        pairwise_f1 = (
            2
            * pairwise_precision
            * pairwise_recall
            / (pairwise_precision + pairwise_recall)
        )

    gold_clusters = _cluster_members(item_ids, gold_labels)
    predicted_clusters = {
        label: members
        for label, members in _cluster_members(item_ids, predicted_labels).items()
        if not label.startswith("__UNASSIGNED__::")
    }

    unassigned_count = sum(
        1
        for label in predicted_labels
        if label.startswith("__UNASSIGNED__::")
    )

    (
        mixed_cluster_count,
        mixed_cluster_rate,
        mixed_item_count,
        mixed_item_rate,
    ) = _mixed_cluster_diagnostics(gold_labels, predicted_labels)

    (
        fragmented_cluster_count,
        fragmented_cluster_rate,
        fragmented_item_count,
        fragmented_item_rate,
    ) = _fragmentation_diagnostics(gold_labels, predicted_labels)

    coherence_accuracy, coherence_macro_f1 = _coherence_metrics(
        records,
        gold_key=gold_coherence_key,
        predicted_key=predicted_coherence_key,
    )

    result = ClusteringEvaluationResult(
        item_count=len(item_ids),
        assigned_item_count=len(item_ids) - unassigned_count,
        unassigned_item_count=unassigned_count,
        gold_cluster_count=len(gold_clusters),
        predicted_cluster_count=len(predicted_clusters),

        pairwise_true_positive=pairwise.true_positive,
        pairwise_false_positive=pairwise.false_positive,
        pairwise_false_negative=pairwise.false_negative,
        pairwise_true_negative=pairwise.true_negative,

        pairwise_precision=_round_metric(pairwise_precision),
        pairwise_recall=_round_metric(pairwise_recall),
        pairwise_f1=_round_metric(pairwise_f1),
        adjusted_rand_index=_round_metric(
            adjusted_rand_index(gold_labels, predicted_labels)
        ),

        cluster_count_error=len(predicted_clusters) - len(gold_clusters),
        absolute_cluster_count_error=abs(
            len(predicted_clusters) - len(gold_clusters)
        ),

        mixed_predicted_cluster_count=mixed_cluster_count,
        mixed_predicted_cluster_rate=_round_metric(mixed_cluster_rate),
        mixed_item_count=mixed_item_count,
        mixed_item_rate=_round_metric(mixed_item_rate),

        fragmented_gold_cluster_count=fragmented_cluster_count,
        fragmented_gold_cluster_rate=_round_metric(fragmented_cluster_rate),
        fragmented_item_count=fragmented_item_count,
        fragmented_item_rate=_round_metric(fragmented_item_rate),

        gold_singleton_cluster_rate=_round_metric(
            _singleton_cluster_rate(gold_labels, ignore_unassigned=False)
        ),
        predicted_singleton_cluster_rate=_round_metric(
            _singleton_cluster_rate(predicted_labels, ignore_unassigned=True)
        ),
        unassigned_prediction_rate=_round_metric(
            _safe_divide(unassigned_count, len(item_ids))
        ),

        coherence_accuracy=_round_metric(coherence_accuracy),
        coherence_macro_f1=_round_metric(coherence_macro_f1),
    )

    return result.to_dict()


def build_cluster_error_report(
    records: Sequence[Mapping[str, Any]],
    predicted_assignments: Optional[Mapping[str, Any]] = None,
    *,
    item_id_key: str = "item_id",
    gold_cluster_key: str = "gold_cluster_id",
    predicted_cluster_key: str = "predicted_cluster_id",
) -> Dict[str, Any]:
    """
    Return interpretable error diagnostics for dashboards/manual inspection.
    """
    records = list(records)

    _validate_records(
        records,
        item_id_key=item_id_key,
        gold_cluster_key=gold_cluster_key,
    )

    item_ids, gold_labels, predicted_labels = _build_assignments(
        records,
        predicted_assignments,
        item_id_key=item_id_key,
        gold_cluster_key=gold_cluster_key,
        predicted_cluster_key=predicted_cluster_key,
    )

    predicted_to_members: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    gold_to_members: Dict[str, List[Tuple[str, str]]] = defaultdict(list)

    for item_id, gold_label, predicted_label in zip(
        item_ids,
        gold_labels,
        predicted_labels,
    ):
        predicted_to_members[predicted_label].append((item_id, gold_label))
        gold_to_members[gold_label].append((item_id, predicted_label))

    mixed_predicted_clusters: List[Dict[str, Any]] = []
    for predicted_label, members in sorted(predicted_to_members.items()):
        if predicted_label.startswith("__UNASSIGNED__::"):
            continue

        gold_distribution = Counter(gold for _, gold in members)
        if len(gold_distribution) <= 1:
            continue

        mixed_predicted_clusters.append(
            {
                "predicted_cluster_id": predicted_label,
                "item_count": len(members),
                "gold_cluster_distribution": dict(gold_distribution),
                "item_ids": [item_id for item_id, _ in members],
            }
        )

    fragmented_gold_clusters: List[Dict[str, Any]] = []
    for gold_label, members in sorted(gold_to_members.items()):
        predicted_distribution = Counter(pred for _, pred in members)
        if len(predicted_distribution) <= 1:
            continue

        fragmented_gold_clusters.append(
            {
                "gold_cluster_id": gold_label,
                "item_count": len(members),
                "predicted_cluster_distribution": dict(predicted_distribution),
                "item_ids": [item_id for item_id, _ in members],
            }
        )

    unassigned_item_ids = [
        item_id
        for item_id, predicted_label in zip(item_ids, predicted_labels)
        if predicted_label.startswith("__UNASSIGNED__::")
    ]

    return {
        "mixed_predicted_clusters": mixed_predicted_clusters,
        "fragmented_gold_clusters": fragmented_gold_clusters,
        "unassigned_item_ids": unassigned_item_ids,
    }


def _load_prediction_mapping(
    path: str | Path,
    *,
    item_id_key: str,
    predicted_cluster_key: str,
) -> Dict[str, str]:
    records = load_jsonl(path)
    mapping: Dict[str, str] = {}

    for index, record in enumerate(records):
        item_id = _normalize_label(record.get(item_id_key))
        cluster_id = _normalize_label(record.get(predicted_cluster_key))

        if not item_id:
            raise ValueError(
                f"Prediction record {index} is missing '{item_id_key}'."
            )
        if item_id in mapping:
            raise ValueError(f"Duplicate prediction for item_id: {item_id}")

        mapping[item_id] = cluster_id

    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate event-clustering predictions."
    )
    parser.add_argument("--gold", required=True, help="Path to gold JSONL.")
    parser.add_argument(
        "--predictions",
        default="",
        help=(
            "Optional prediction JSONL. If omitted, predicted_cluster_id is "
            "read from the gold records."
        ),
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON file for aggregate metrics.",
    )
    parser.add_argument(
        "--error-output",
        default="",
        help="Optional JSON file for detailed cluster errors.",
    )

    args = parser.parse_args()

    gold_records = load_jsonl(args.gold)

    predictions = None
    if args.predictions:
        predictions = _load_prediction_mapping(
            args.predictions,
            item_id_key="item_id",
            predicted_cluster_key="predicted_cluster_id",
        )

    metrics = evaluate_clustering(
        records=gold_records,
        predicted_assignments=predictions,
    )

    print(json.dumps(metrics, indent=2, ensure_ascii=False))

    if args.output:
        save_metrics(metrics, args.output)

    if args.error_output:
        save_metrics(
            build_cluster_error_report(
                gold_records,
                predicted_assignments=predictions,
            ),
            args.error_output,
        )


if __name__ == "__main__":
    main()