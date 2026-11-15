"""
Offline evaluation for retrieval quality.

This evaluator is designed for the Event Intelligence research product and
measures whether retrieval surfaces the right evidence for a research claim,
topic, or reasoning step.

It supports two retrieval intents:
- supporting evidence
- counterevidence

Recommended gold JSONL
----------------------
One retrieval query/case per line:

{
  "query_id": "RET-001",
  "query_text": "What evidence supports the claim that grid access is becoming a bottleneck?",
  "retrieval_intent": "support",

  "relevant_evidence_ids": [
    "FACT-001",
    "METRIC-002",
    "ENE-001"
  ],

  "graded_relevance": {
    "FACT-001": 3,
    "METRIC-002": 2,
    "ENE-001": 1
  },

  "available_evidence_ids": [
    "FACT-001",
    "FACT-002",
    "METRIC-002",
    "ENE-001",
    "FACT-009"
  ],

  "case_tags": [
    "multi_evidence",
    "infrastructure"
  ]
}

Prediction JSONL
----------------
One ranked retrieval result list per query:

{
  "query_id": "RET-001",
  "retrieved": [
    {"evidence_id": "FACT-001", "score": 0.91},
    {"evidence_id": "METRIC-002", "score": 0.82},
    {"evidence_id": "FACT-009", "score": 0.74}
  ]
}

Alternative compact prediction form:
{
  "query_id": "RET-001",
  "retrieved_evidence_ids": [
    "FACT-001",
    "METRIC-002",
    "FACT-009"
  ]
}

Core metrics
------------
At each requested k:
- Precision@k
- Recall@k
- HitRate@k
- nDCG@k

Across queries:
- MRR
- MAP
- first relevant rank
- invalid evidence-reference rate
- duplicate retrieved-ID rate

Breakdowns:
- retrieval_intent breakdown (support vs counterevidence)
- case_tag breakdown

Important distinctions
----------------------
1. Retrieval relevance is not grounding. A retrieved record may be relevant to a
   claim but still fail to entail it.

2. `graded_relevance` is optional. If absent, all relevant items receive binary
   relevance = 1.

3. Counterevidence should be evaluated separately from support retrieval. A
   system that retrieves supporting evidence well but misses contradictory
   evidence is not sufficient for this product.

4. Metrics are calculated over the labelled gold set only. They do not prove
   that the gold set contains every potentially useful evidence record.

5. These are offline quality measurements, not release thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


DEFAULT_K_VALUES: tuple[int, ...] = (1, 3, 5, 10)

INTENT_ALIASES: Dict[str, str] = {
    "support": "support",
    "supporting": "support",
    "supporting_evidence": "support",
    "evidence": "support",

    "counterevidence": "counterevidence",
    "counter_evidence": "counterevidence",
    "counter": "counterevidence",
    "contradictory": "counterevidence",
    "contradiction": "counterevidence",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_key(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _normalize_id(value: Any) -> str:
    return _clean(value).upper()


def _normalize_intent(value: Any) -> str:
    normalized = _normalize_key(value)
    return INTENT_ALIASES.get(normalized, normalized or "unspecified")


def _normalize_tags(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    result: List[str] = []
    seen: Set[str] = set()

    for item in value:
        normalized = _normalize_key(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result


def _normalize_id_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    result: List[str] = []
    seen: Set[str] = set()

    for item in value:
        if isinstance(item, Mapping):
            item = (
                item.get("evidence_id")
                or item.get("id")
                or item.get("source_id")
                or item.get("document_id")
                or ""
            )

        normalized = _normalize_id(item)

        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result


def _raw_retrieved_ids(record: Mapping[str, Any]) -> List[str]:
    """
    Preserve duplicates here so duplicate-rate diagnostics remain possible.
    """
    value = (
        record.get("retrieved")
        or record.get("results")
        or record.get("retrieved_evidence_ids")
        or record.get("evidence_ids")
        or []
    )

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    result: List[str] = []

    for item in value:
        if isinstance(item, Mapping):
            item = (
                item.get("evidence_id")
                or item.get("id")
                or item.get("source_id")
                or item.get("document_id")
                or ""
            )

        normalized = _normalize_id(item)
        if normalized:
            result.append(normalized)

    return result


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()

    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)

    return result


def _safe_divide(
    numerator: int | float,
    denominator: int | float,
) -> Optional[float]:
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _round_metric(
    value: Optional[float],
    digits: int = 4,
) -> Optional[float]:
    return None if value is None else round(value, digits)


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


def _query_id(record: Mapping[str, Any]) -> str:
    return (
        _clean(record.get("query_id"))
        or _clean(record.get("case_id"))
        or _clean(record.get("id"))
    )


def _index_queries(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}

    for index, record in enumerate(records):
        query_id = _query_id(record)

        if not query_id:
            raise ValueError(
                f"Retrieval record {index} is missing query_id/case_id."
            )

        if query_id in indexed:
            raise ValueError(
                f"Duplicate query_id in retrieval evaluation data: {query_id}"
            )

        indexed[query_id] = dict(record)

    return indexed


def _relevant_ids(record: Mapping[str, Any]) -> Set[str]:
    return set(
        _normalize_id_list(
            record.get("relevant_evidence_ids")
            or record.get("gold_evidence_ids")
            or record.get("relevant_ids")
            or []
        )
    )


def _available_ids(record: Mapping[str, Any]) -> Set[str]:
    return set(
        _normalize_id_list(
            record.get("available_evidence_ids")
            or record.get("valid_evidence_ids")
            or []
        )
    )


def _graded_relevance(
    record: Mapping[str, Any],
    relevant_ids: Set[str],
) -> Dict[str, float]:
    raw = record.get("graded_relevance")

    if not isinstance(raw, Mapping):
        return {
            evidence_id: 1.0
            for evidence_id in relevant_ids
        }

    result: Dict[str, float] = {}

    for evidence_id, grade in raw.items():
        normalized_id = _normalize_id(evidence_id)

        if not normalized_id:
            continue

        try:
            numeric_grade = float(grade)
        except (TypeError, ValueError):
            continue

        if numeric_grade < 0:
            continue

        result[normalized_id] = numeric_grade

    for evidence_id in relevant_ids:
        result.setdefault(evidence_id, 1.0)

    return result


def precision_at_k(
    retrieved: Sequence[str],
    relevant: Set[str],
    k: int,
) -> Optional[float]:
    if k <= 0:
        raise ValueError("k must be > 0.")

    top_k = list(retrieved[:k])

    if not top_k:
        return 0.0

    hits = sum(
        1
        for evidence_id in top_k
        if evidence_id in relevant
    )

    # Standard P@k denominator is k, even if fewer than k are returned.
    return hits / k


def recall_at_k(
    retrieved: Sequence[str],
    relevant: Set[str],
    k: int,
) -> Optional[float]:
    if k <= 0:
        raise ValueError("k must be > 0.")

    if not relevant:
        return None

    top_k = set(retrieved[:k])
    return len(top_k & relevant) / len(relevant)


def hit_rate_at_k(
    retrieved: Sequence[str],
    relevant: Set[str],
    k: int,
) -> Optional[float]:
    if not relevant:
        return None

    return float(
        any(
            evidence_id in relevant
            for evidence_id in retrieved[:k]
        )
    )


def reciprocal_rank(
    retrieved: Sequence[str],
    relevant: Set[str],
) -> float:
    for rank, evidence_id in enumerate(
        retrieved,
        start=1,
    ):
        if evidence_id in relevant:
            return 1.0 / rank

    return 0.0


def average_precision(
    retrieved: Sequence[str],
    relevant: Set[str],
) -> Optional[float]:
    if not relevant:
        return None

    hits = 0
    precision_sum = 0.0

    for rank, evidence_id in enumerate(
        retrieved,
        start=1,
    ):
        if evidence_id in relevant:
            hits += 1
            precision_sum += hits / rank

    return precision_sum / len(relevant)


def dcg_at_k(
    retrieved: Sequence[str],
    graded_relevance: Mapping[str, float],
    k: int,
) -> float:
    score = 0.0

    for index, evidence_id in enumerate(
        retrieved[:k],
        start=1,
    ):
        relevance = float(
            graded_relevance.get(
                evidence_id,
                0.0,
            )
        )

        # Standard graded DCG gain.
        gain = (2.0 ** relevance) - 1.0
        discount = math.log2(index + 1.0)

        score += gain / discount

    return score


def ndcg_at_k(
    retrieved: Sequence[str],
    graded_relevance: Mapping[str, float],
    k: int,
) -> Optional[float]:
    if not graded_relevance:
        return None

    actual = dcg_at_k(
        retrieved,
        graded_relevance,
        k,
    )

    ideal_grades = sorted(
        graded_relevance.values(),
        reverse=True,
    )[:k]

    ideal = 0.0

    for index, relevance in enumerate(
        ideal_grades,
        start=1,
    ):
        gain = (2.0 ** float(relevance)) - 1.0
        discount = math.log2(index + 1.0)
        ideal += gain / discount

    if ideal == 0:
        return None

    return actual / ideal


def first_relevant_rank(
    retrieved: Sequence[str],
    relevant: Set[str],
) -> Optional[int]:
    for rank, evidence_id in enumerate(
        retrieved,
        start=1,
    ):
        if evidence_id in relevant:
            return rank

    return None


def _aggregate_mean(
    values: Sequence[Optional[float]],
) -> Optional[float]:
    filtered = [
        float(value)
        for value in values
        if isinstance(value, (int, float))
    ]

    if not filtered:
        return None

    return sum(filtered) / len(filtered)


def evaluate_retrieval_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> Dict[str, Any]:
    gold_by_query = _index_queries(
        gold_records
    )
    pred_by_query = _index_queries(
        prediction_records
    )

    gold_query_ids = set(
        gold_by_query
    )
    pred_query_ids = set(
        pred_by_query
    )

    missing_query_ids = sorted(
        gold_query_ids - pred_query_ids
    )
    extra_query_ids = sorted(
        pred_query_ids - gold_query_ids
    )

    normalized_k_values = sorted(
        {
            int(k)
            for k in k_values
            if int(k) > 0
        }
    )

    if not normalized_k_values:
        raise ValueError(
            "At least one positive k value is required."
        )

    per_query: List[Dict[str, Any]] = []

    precision_values: Dict[
        int,
        List[Optional[float]],
    ] = defaultdict(list)

    recall_values: Dict[
        int,
        List[Optional[float]],
    ] = defaultdict(list)

    hit_values: Dict[
        int,
        List[Optional[float]],
    ] = defaultdict(list)

    ndcg_values: Dict[
        int,
        List[Optional[float]],
    ] = defaultdict(list)

    rr_values: List[float] = []
    ap_values: List[Optional[float]] = []
    first_rank_values: List[int] = []

    total_raw_results = 0
    total_duplicate_results = 0

    total_retrieved_unique = 0
    total_invalid_unique = 0

    intent_query_counts: Counter[str] = Counter()
    intent_metric_accumulators: Dict[
        str,
        Dict[str, Any],
    ] = defaultdict(
        lambda: {
            "rr": [],
            "ap": [],
            "precision": defaultdict(list),
            "recall": defaultdict(list),
            "hit": defaultdict(list),
            "ndcg": defaultdict(list),
        }
    )

    tag_query_counts: Counter[str] = Counter()
    tag_recall_accumulators: Dict[
        str,
        Dict[int, List[Optional[float]]],
    ] = defaultdict(
        lambda: defaultdict(list)
    )

    tag_hit_accumulators: Dict[
        str,
        Dict[int, List[Optional[float]]],
    ] = defaultdict(
        lambda: defaultdict(list)
    )

    for query_id in sorted(
        gold_query_ids
    ):
        gold = gold_by_query[
            query_id
        ]
        prediction = pred_by_query.get(
            query_id,
            {},
        )

        relevant = _relevant_ids(
            gold
        )
        graded = _graded_relevance(
            gold,
            relevant,
        )
        available = _available_ids(
            gold
        )

        raw_retrieved = _raw_retrieved_ids(
            prediction
        )
        retrieved = _dedupe_preserve_order(
            raw_retrieved
        )

        duplicate_count = (
            len(raw_retrieved)
            - len(retrieved)
        )

        invalid_ids = (
            sorted(
                set(retrieved)
                - available
            )
            if available
            else []
        )

        total_raw_results += len(
            raw_retrieved
        )
        total_duplicate_results += (
            duplicate_count
        )

        total_retrieved_unique += len(
            retrieved
        )
        total_invalid_unique += len(
            invalid_ids
        )

        rr = reciprocal_rank(
            retrieved,
            relevant,
        )
        ap = average_precision(
            retrieved,
            relevant,
        )
        first_rank = first_relevant_rank(
            retrieved,
            relevant,
        )

        rr_values.append(rr)
        ap_values.append(ap)

        if first_rank is not None:
            first_rank_values.append(
                first_rank
            )

        intent = _normalize_intent(
            gold.get(
                "retrieval_intent"
            )
        )
        intent_query_counts[
            intent
        ] += 1
        intent_metric_accumulators[
            intent
        ]["rr"].append(
            rr
        )
        intent_metric_accumulators[
            intent
        ]["ap"].append(
            ap
        )

        tags = _normalize_tags(
            gold.get("case_tags")
            or gold.get("tags")
            or []
        )

        query_metrics: Dict[
            str,
            Any,
        ] = {}

        for k in normalized_k_values:
            precision = precision_at_k(
                retrieved,
                relevant,
                k,
            )
            recall = recall_at_k(
                retrieved,
                relevant,
                k,
            )
            hit_rate = hit_rate_at_k(
                retrieved,
                relevant,
                k,
            )
            ndcg = ndcg_at_k(
                retrieved,
                graded,
                k,
            )

            precision_values[
                k
            ].append(
                precision
            )
            recall_values[
                k
            ].append(
                recall
            )
            hit_values[
                k
            ].append(
                hit_rate
            )
            ndcg_values[
                k
            ].append(
                ndcg
            )

            intent_metric_accumulators[
                intent
            ]["precision"][k].append(
                precision
            )
            intent_metric_accumulators[
                intent
            ]["recall"][k].append(
                recall
            )
            intent_metric_accumulators[
                intent
            ]["hit"][k].append(
                hit_rate
            )
            intent_metric_accumulators[
                intent
            ]["ndcg"][k].append(
                ndcg
            )

            for tag in tags:
                tag_recall_accumulators[
                    tag
                ][k].append(
                    recall
                )
                tag_hit_accumulators[
                    tag
                ][k].append(
                    hit_rate
                )

            query_metrics[
                f"precision_at_{k}"
            ] = _round_metric(
                precision
            )
            query_metrics[
                f"recall_at_{k}"
            ] = _round_metric(
                recall
            )
            query_metrics[
                f"hit_rate_at_{k}"
            ] = _round_metric(
                hit_rate
            )
            query_metrics[
                f"ndcg_at_{k}"
            ] = _round_metric(
                ndcg
            )

        for tag in tags:
            tag_query_counts[
                tag
            ] += 1

        per_query.append(
            {
                "query_id": query_id,
                "retrieval_intent": intent,
                "relevant_count": len(
                    relevant
                ),
                "retrieved_count": len(
                    retrieved
                ),
                "raw_retrieved_count": len(
                    raw_retrieved
                ),
                "duplicate_retrieved_count": (
                    duplicate_count
                ),
                "invalid_evidence_ids": (
                    invalid_ids
                ),
                "reciprocal_rank": _round_metric(
                    rr
                ),
                "average_precision": _round_metric(
                    ap
                ),
                "first_relevant_rank": (
                    first_rank
                ),
                "metrics": (
                    query_metrics
                ),
                "case_tags": (
                    tags
                ),
            }
        )

    aggregate_at_k = {}

    for k in normalized_k_values:
        aggregate_at_k[
            str(k)
        ] = {
            "precision_at_k": _round_metric(
                _aggregate_mean(
                    precision_values[k]
                )
            ),
            "recall_at_k": _round_metric(
                _aggregate_mean(
                    recall_values[k]
                )
            ),
            "hit_rate_at_k": _round_metric(
                _aggregate_mean(
                    hit_values[k]
                )
            ),
            "ndcg_at_k": _round_metric(
                _aggregate_mean(
                    ndcg_values[k]
                )
            ),
        }

    intent_breakdown: Dict[
        str,
        Any,
    ] = {}

    for intent in sorted(
        intent_query_counts
    ):
        bucket = (
            intent_metric_accumulators[
                intent
            ]
        )

        intent_breakdown[
            intent
        ] = {
            "query_count": (
                intent_query_counts[
                    intent
                ]
            ),
            "mrr": _round_metric(
                _aggregate_mean(
                    bucket["rr"]
                )
            ),
            "map": _round_metric(
                _aggregate_mean(
                    bucket["ap"]
                )
            ),
            "at_k": {
                str(k): {
                    "precision_at_k": _round_metric(
                        _aggregate_mean(
                            bucket[
                                "precision"
                            ][k]
                        )
                    ),
                    "recall_at_k": _round_metric(
                        _aggregate_mean(
                            bucket[
                                "recall"
                            ][k]
                        )
                    ),
                    "hit_rate_at_k": _round_metric(
                        _aggregate_mean(
                            bucket[
                                "hit"
                            ][k]
                        )
                    ),
                    "ndcg_at_k": _round_metric(
                        _aggregate_mean(
                            bucket[
                                "ndcg"
                            ][k]
                        )
                    ),
                }
                for k in normalized_k_values
            },
        }

    tag_breakdown: Dict[
        str,
        Any,
    ] = {}

    for tag in sorted(
        tag_query_counts
    ):
        tag_breakdown[
            tag
        ] = {
            "query_count": (
                tag_query_counts[
                    tag
                ]
            ),
            "at_k": {
                str(k): {
                    "recall_at_k": _round_metric(
                        _aggregate_mean(
                            tag_recall_accumulators[
                                tag
                            ][k]
                        )
                    ),
                    "hit_rate_at_k": _round_metric(
                        _aggregate_mean(
                            tag_hit_accumulators[
                                tag
                            ][k]
                        )
                    ),
                }
                for k in normalized_k_values
            },
        }

    return {
        "gold_query_count": len(
            gold_query_ids
        ),
        "prediction_query_count": len(
            pred_query_ids
        ),
        "missing_query_count": len(
            missing_query_ids
        ),
        "extra_query_count": len(
            extra_query_ids
        ),
        "missing_query_ids": (
            missing_query_ids
        ),
        "extra_query_ids": (
            extra_query_ids
        ),

        "k_values": (
            normalized_k_values
        ),
        "at_k": (
            aggregate_at_k
        ),

        "mrr": _round_metric(
            _aggregate_mean(
                rr_values
            )
        ),
        "map": _round_metric(
            _aggregate_mean(
                ap_values
            )
        ),

        "mean_first_relevant_rank": (
            _round_metric(
                (
                    sum(
                        first_rank_values
                    )
                    / len(
                        first_rank_values
                    )
                )
                if first_rank_values
                else None
            )
        ),

        "duplicate_retrieved_id_rate": (
            _round_metric(
                _safe_divide(
                    total_duplicate_results,
                    total_raw_results,
                )
            )
        ),

        "invalid_evidence_reference_rate": (
            _round_metric(
                _safe_divide(
                    total_invalid_unique,
                    total_retrieved_unique,
                )
            )
        ),

        "retrieval_intent_breakdown": (
            intent_breakdown
        ),
        "case_tag_breakdown": (
            tag_breakdown
        ),

        "per_query": (
            per_query
        ),

        "interpretation_notes": {
            "relevance_vs_grounding": (
                "Retrieval relevance is not factual entailment; use grounding "
                "evaluation for support verification."
            ),
            "counterevidence": (
                "Support and counterevidence retrieval are broken out separately "
                "because the product must deliberately surface evidence that may "
                "weaken an interpretation."
            ),
            "graded_relevance": (
                "nDCG uses graded_relevance when supplied; otherwise all labelled "
                "relevant evidence receives relevance grade 1."
            ),
            "gold_completeness": (
                "Metrics are bounded by the labelled gold set and do not prove "
                "that the gold set contains every useful evidence record."
            ),
        },
    }


def run_evaluation(
    gold_path: str | Path,
    predictions_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Standard interface expected by evaluation_runner.py.
    """
    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for retrieval evaluation."
        )

    k_values = kwargs.pop(
        "k_values",
        DEFAULT_K_VALUES,
    )

    if isinstance(
        k_values,
        str,
    ):
        k_values = [
            int(part.strip())
            for part in k_values.split(",")
            if part.strip()
        ]

    if not isinstance(
        k_values,
        (list, tuple),
    ):
        raise TypeError(
            "k_values must be a list/tuple of positive integers "
            "or a comma-separated string."
        )

    if kwargs:
        raise TypeError(
            "Unknown retrieval evaluation option(s): "
            + ", ".join(
                sorted(kwargs)
            )
        )

    return evaluate_retrieval_records(
        gold_records=load_jsonl(
            gold_path
        ),
        prediction_records=load_jsonl(
            predictions_path
        ),
        k_values=[
            int(k)
            for k in k_values
        ],
    )


def _save_json(
    payload: Mapping[str, Any],
    path: str | Path,
) -> None:
    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            dict(payload),
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval predictions."
    )
    parser.add_argument(
        "--gold",
        required=True,
        help="Path to retrieval gold JSONL.",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to retrieval predictions JSONL.",
    )
    parser.add_argument(
        "--k",
        default="1,3,5,10",
        help="Comma-separated k values, e.g. 1,3,5,10.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output metrics JSON path.",
    )

    args = parser.parse_args()

    k_values = [
        int(part.strip())
        for part in args.k.split(",")
        if part.strip()
    ]

    metrics = run_evaluation(
        gold_path=args.gold,
        predictions_path=args.predictions,
        k_values=k_values,
    )

    print(
        json.dumps(
            metrics,
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.output:
        _save_json(
            metrics,
            args.output,
        )


if __name__ == "__main__":
    main()