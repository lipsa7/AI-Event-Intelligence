"""
Offline evaluation for GroundingService.

This evaluator measures whether generated report claims are correctly classified
as supported, partially supported, inference-supported, unsupported, or
contradicted, and whether their cited evidence is valid and appropriate.

It deliberately separates:
    citation validity
        !=
    factual / inferential support

A citation can point to a real evidence ID and still fail to support the claim.

Recommended gold JSONL
----------------------
One claim per line:

{
  "claim_id": "CLAIM-001",
  "claim_text": "Grid access may become a larger infrastructure constraint.",
  "claim_type": "inference",

  "gold_grounding_status": "inference_supported",

  "available_evidence_ids": [
    "FACT-001",
    "FACT-006",
    "METRIC-002",
    "ENE-001"
  ],

  "gold_support_sets": [
    ["FACT-001", "FACT-006"],
    ["FACT-006", "METRIC-002", "ENE-001"]
  ],

  "gold_attribution_status": "correct",
  "gold_temporal_status": "correct",
  "gold_numeric_status": "not_applicable",

  "case_tags": [
    "multi_evidence",
    "hidden_insight"
  ]
}

Prediction JSONL
----------------
One prediction per claim:

{
  "claim_id": "CLAIM-001",
  "predicted_grounding_status": "inference_supported",
  "evidence_ids": ["FACT-001", "FACT-006"],
  "attribution_status": "correct",
  "temporal_status": "correct",
  "numeric_status": "not_applicable"
}

Grounding statuses
------------------
Canonical:
- supported
- partially_supported
- inference_supported
- unsupported
- contradicted

Common aliases such as "grounded", "partial", "inference-supported" are accepted
and normalized.

Core metrics
------------
Classification:
- grounding-status accuracy
- macro F1
- per-class precision / recall / F1
- supported-family accuracy
- unsupported-or-contradicted detection precision / recall / F1
- contradiction precision / recall / F1

Evidence:
- citation validity rate
- claim-level citation-validity rate
- invalid citation count
- acceptable support-set hit rate
- single-evidence support-set hit rate
- multi-evidence support-set hit rate
- evidence-selection precision against gold support evidence
- evidence-coverage recall against the best-matching acceptable support set

Other grounding checks:
- attribution accuracy
- temporal accuracy
- numeric grounding accuracy

Diagnostics:
- missing / extra predictions
- per-claim errors
- per-case-tag grounding accuracy

Important interpretation notes
------------------------------
1. "Citation validity" means the cited ID exists in the evidence inventory.
   It does not mean the evidence supports the claim.

2. `gold_support_sets` contains alternative acceptable evidence combinations.
   A prediction passes the support-set check when it contains every ID in at
   least one acceptable set.

3. Multi-evidence cases are those where every acceptable support set requires
   more than one evidence record.

4. This evaluator does not use embedding similarity as a truth signal.
   Semantic similarity belongs to candidate retrieval; support labels should
   come from labelled evaluation data.

5. These are offline quality measurements. They do not themselves authorize
   release. Product policy / PolicyEnforcementService applies thresholds later.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


CANONICAL_STATUSES: tuple[str, ...] = (
    "supported",
    "partially_supported",
    "inference_supported",
    "unsupported",
    "contradicted",
)

SUPPORTED_FAMILY = {
    "supported",
    "partially_supported",
    "inference_supported",
}

UNSUPPORTED_FAMILY = {
    "unsupported",
    "contradicted",
}

STATUS_ALIASES: Dict[str, str] = {
    "supported": "supported",
    "grounded": "supported",
    "fully_supported": "supported",
    "fully_grounded": "supported",

    "partial": "partially_supported",
    "partially_supported": "partially_supported",
    "partially_grounded": "partially_supported",
    "partial_support": "partially_supported",

    "inference_supported": "inference_supported",
    "inference_support": "inference_supported",
    "supported_inference": "inference_supported",
    "inference_grounded": "inference_supported",

    "unsupported": "unsupported",
    "not_supported": "unsupported",
    "ungrounded": "unsupported",

    "contradicted": "contradicted",
    "contradiction": "contradicted",
    "refuted": "contradicted",
}

OPTIONAL_CHECK_ALIASES: Dict[str, str] = {
    "correct": "correct",
    "pass": "correct",
    "valid": "correct",

    "incorrect": "incorrect",
    "fail": "incorrect",
    "invalid": "incorrect",

    "partial": "partial",
    "partially_correct": "partial",

    "not_applicable": "not_applicable",
    "n/a": "not_applicable",
    "na": "not_applicable",
    "unknown": "unknown",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_key(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _normalize_status(value: Any) -> str:
    return STATUS_ALIASES.get(_normalize_key(value), "")


def _normalize_optional_check(value: Any) -> str:
    normalized = _normalize_key(value)
    return OPTIONAL_CHECK_ALIASES.get(normalized, normalized)


def _normalize_claim_type(value: Any) -> str:
    normalized = _normalize_key(value)
    return normalized or "unknown"


def _normalize_id(value: Any) -> str:
    return _clean(value).upper()


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
                item.get("id")
                or item.get("evidence_id")
                or item.get("source_id")
                or ""
            )

        normalized = _normalize_id(item)
        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def _normalize_support_sets(value: Any) -> List[Set[str]]:
    """
    Normalize alternative acceptable evidence combinations.

    Examples:
        [["FACT-001"], ["FACT-002", "METRIC-001"]]
    """
    if not isinstance(value, list):
        return []

    normalized: List[Set[str]] = []
    seen_keys: Set[Tuple[str, ...]] = set()

    for item in value:
        ids = set(_normalize_id_list(item))

        if not ids:
            continue

        key = tuple(sorted(ids))
        if key in seen_keys:
            continue

        seen_keys.add(key)
        normalized.append(ids)

    return normalized


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


def _prf(
    true_positive: int,
    false_positive: int,
    false_negative: int,
) -> Dict[str, Any]:
    precision = _safe_divide(
        true_positive,
        true_positive + false_positive,
    )
    recall = _safe_divide(
        true_positive,
        true_positive + false_negative,
    )

    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = (
            2
            * precision
            * recall
            / (precision + recall)
        )

    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": _round_metric(precision),
        "recall": _round_metric(recall),
        "f1": _round_metric(f1),
    }


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


def _claim_id(record: Mapping[str, Any]) -> str:
    return (
        _clean(record.get("claim_id"))
        or _clean(record.get("id"))
    )


def _index_claims(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}

    for index, record in enumerate(records):
        claim_id = _claim_id(record)

        if not claim_id:
            raise ValueError(
                f"Grounding record {index} is missing claim_id."
            )

        if claim_id in indexed:
            raise ValueError(
                f"Duplicate claim_id in evaluation data: {claim_id}"
            )

        indexed[claim_id] = dict(record)

    return indexed


def _gold_status(record: Mapping[str, Any]) -> str:
    return _normalize_status(
        record.get("gold_grounding_status")
        or record.get("grounding_status")
        or record.get("gold_status")
    )


def _predicted_status(record: Mapping[str, Any]) -> str:
    return _normalize_status(
        record.get("predicted_grounding_status")
        or record.get("grounding_status")
        or record.get("status")
    )


def _predicted_evidence_ids(
    record: Mapping[str, Any],
) -> List[str]:
    return _normalize_id_list(
        record.get("evidence_ids")
        or record.get("supporting_evidence_ids")
        or record.get("citations")
        or []
    )


def _available_evidence_ids(
    gold_record: Mapping[str, Any],
    support_sets: Sequence[Set[str]],
) -> Set[str]:
    explicit = set(
        _normalize_id_list(
            gold_record.get("available_evidence_ids")
            or gold_record.get("valid_evidence_ids")
            or []
        )
    )

    if explicit:
        return explicit

    # Conservative fallback for early datasets:
    # only evidence that appears in an acceptable support set is known-valid.
    return set().union(*support_sets) if support_sets else set()


def _gold_support_sets(
    record: Mapping[str, Any],
) -> List[Set[str]]:
    support_sets = _normalize_support_sets(
        record.get("gold_support_sets")
        or record.get("acceptable_support_sets")
        or []
    )

    if support_sets:
        return support_sets

    # Convenience fallback for simple cases.
    required = set(
        _normalize_id_list(
            record.get("gold_evidence_ids")
            or record.get("required_evidence_ids")
            or []
        )
    )

    return [required] if required else []


def _support_set_hit(
    predicted_ids: Set[str],
    support_sets: Sequence[Set[str]],
) -> Optional[bool]:
    if not support_sets:
        return None

    return any(
        support_set.issubset(predicted_ids)
        for support_set in support_sets
    )


def _best_support_set_coverage(
    predicted_ids: Set[str],
    support_sets: Sequence[Set[str]],
) -> Optional[float]:
    """
    Return recall against the best-matching acceptable support set.

    Example:
        acceptable sets = [{A, B}, {C, D, E}]
        prediction = {A}
        => max(1/2, 0/3) = 0.5
    """
    if not support_sets:
        return None

    coverage_values = []

    for support_set in support_sets:
        if not support_set:
            continue

        coverage_values.append(
            len(predicted_ids & support_set)
            / len(support_set)
        )

    if not coverage_values:
        return None

    return max(coverage_values)


def _support_union(
    support_sets: Sequence[Set[str]],
) -> Set[str]:
    if not support_sets:
        return set()
    return set().union(*support_sets)


def _is_multi_evidence_case(
    support_sets: Sequence[Set[str]],
) -> bool:
    """
    A case is multi-evidence when every acceptable sufficient support set
    requires more than one evidence record.
    """
    return bool(
        support_sets
        and all(len(support_set) > 1 for support_set in support_sets)
    )


def _is_single_evidence_case(
    support_sets: Sequence[Set[str]],
) -> bool:
    return bool(
        support_sets
        and any(len(support_set) == 1 for support_set in support_sets)
    )


def _optional_field_accuracy(
    gold_value: Any,
    predicted_value: Any,
) -> Optional[bool]:
    gold = _normalize_optional_check(gold_value)

    if not gold or gold == "not_applicable":
        return None

    predicted = _normalize_optional_check(predicted_value)

    if not predicted:
        return False

    return gold == predicted


def _status_per_class(
    gold_statuses: Sequence[str],
    predicted_statuses: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}

    for status in CANONICAL_STATUSES:
        tp = sum(
            1
            for gold, predicted in zip(
                gold_statuses,
                predicted_statuses,
            )
            if gold == status and predicted == status
        )
        fp = sum(
            1
            for gold, predicted in zip(
                gold_statuses,
                predicted_statuses,
            )
            if gold != status and predicted == status
        )
        fn = sum(
            1
            for gold, predicted in zip(
                gold_statuses,
                predicted_statuses,
            )
            if gold == status and predicted != status
        )

        result[status] = _prf(tp, fp, fn)

    return result


def _macro_f1(
    per_class: Mapping[str, Mapping[str, Any]],
) -> Optional[float]:
    values: List[float] = []

    for metrics in per_class.values():
        f1 = metrics.get("f1")
        if isinstance(f1, (int, float)):
            values.append(float(f1))

    if not values:
        return None

    return sum(values) / len(values)


def _binary_family_prf(
    gold_statuses: Sequence[str],
    predicted_statuses: Sequence[str],
    positive_family: Set[str],
) -> Dict[str, Any]:
    tp = fp = fn = 0

    for gold, predicted in zip(
        gold_statuses,
        predicted_statuses,
    ):
        gold_positive = gold in positive_family
        pred_positive = predicted in positive_family

        if gold_positive and pred_positive:
            tp += 1
        elif not gold_positive and pred_positive:
            fp += 1
        elif gold_positive and not pred_positive:
            fn += 1

    return _prf(tp, fp, fn)


def evaluate_grounding_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    gold_by_claim = _index_claims(gold_records)
    pred_by_claim = _index_claims(prediction_records)

    gold_claim_ids = set(gold_by_claim)
    pred_claim_ids = set(pred_by_claim)

    missing_prediction_ids = sorted(
        gold_claim_ids - pred_claim_ids
    )
    extra_prediction_ids = sorted(
        pred_claim_ids - gold_claim_ids
    )

    gold_statuses: List[str] = []
    predicted_statuses: List[str] = []

    invalid_citation_count = 0
    total_citation_count = 0
    claims_with_citations = 0
    claims_with_all_valid_citations = 0

    support_set_evaluated = 0
    support_set_hit_count = 0

    single_support_evaluated = 0
    single_support_hit_count = 0

    multi_support_evaluated = 0
    multi_support_hit_count = 0

    evidence_precision_numer = 0
    evidence_precision_denom = 0

    support_coverage_values: List[float] = []

    attribution_correct = attribution_total = 0
    temporal_correct = temporal_total = 0
    numeric_correct = numeric_total = 0

    tag_totals: Counter[str] = Counter()
    tag_correct: Counter[str] = Counter()

    claim_type_totals: Counter[str] = Counter()
    claim_type_correct: Counter[str] = Counter()

    claim_errors: List[Dict[str, Any]] = []

    for claim_id in sorted(gold_claim_ids):
        gold = gold_by_claim[claim_id]
        predicted = pred_by_claim.get(claim_id, {})

        gold_status = _gold_status(gold)
        predicted_status = _predicted_status(predicted)

        if not gold_status:
            raise ValueError(
                f"Gold claim {claim_id!r} is missing a valid "
                "gold_grounding_status."
            )

        # Missing predictions are treated as incorrect, not silently excluded.
        if not predicted:
            predicted_status = ""

        gold_statuses.append(gold_status)
        predicted_statuses.append(predicted_status)

        status_correct = (
            bool(predicted_status)
            and predicted_status == gold_status
        )

        claim_type = _normalize_claim_type(
            gold.get("claim_type")
        )
        claim_type_totals[claim_type] += 1
        claim_type_correct[claim_type] += int(
            status_correct
        )

        tags = _normalize_tags(
            gold.get("case_tags")
            or gold.get("tags")
            or []
        )

        for tag in tags:
            tag_totals[tag] += 1
            tag_correct[tag] += int(
                status_correct
            )

        support_sets = _gold_support_sets(gold)
        available_ids = _available_evidence_ids(
            gold,
            support_sets,
        )

        predicted_ids_list = _predicted_evidence_ids(
            predicted
        )
        predicted_ids = set(predicted_ids_list)

        invalid_ids = sorted(
            predicted_ids - available_ids
        )

        if predicted_ids:
            claims_with_citations += 1
            if not invalid_ids:
                claims_with_all_valid_citations += 1

        invalid_citation_count += len(invalid_ids)
        total_citation_count += len(predicted_ids)

        support_hit = _support_set_hit(
            predicted_ids,
            support_sets,
        )

        if support_hit is not None:
            support_set_evaluated += 1
            support_set_hit_count += int(support_hit)

            if _is_multi_evidence_case(support_sets):
                multi_support_evaluated += 1
                multi_support_hit_count += int(
                    support_hit
                )

            if _is_single_evidence_case(support_sets):
                single_support_evaluated += 1
                single_support_hit_count += int(
                    support_hit
                )

            coverage = _best_support_set_coverage(
                predicted_ids,
                support_sets,
            )
            if coverage is not None:
                support_coverage_values.append(
                    coverage
                )

            acceptable_evidence = _support_union(
                support_sets
            )

            valid_predicted = (
                predicted_ids & available_ids
                if available_ids
                else predicted_ids
            )

            evidence_precision_numer += len(
                valid_predicted & acceptable_evidence
            )
            evidence_precision_denom += len(
                valid_predicted
            )

        attribution_result = _optional_field_accuracy(
            gold.get("gold_attribution_status"),
            predicted.get("attribution_status"),
        )
        if attribution_result is not None:
            attribution_total += 1
            attribution_correct += int(
                attribution_result
            )

        temporal_result = _optional_field_accuracy(
            gold.get("gold_temporal_status"),
            predicted.get("temporal_status"),
        )
        if temporal_result is not None:
            temporal_total += 1
            temporal_correct += int(
                temporal_result
            )

        numeric_result = _optional_field_accuracy(
            gold.get("gold_numeric_status"),
            predicted.get("numeric_status"),
        )
        if numeric_result is not None:
            numeric_total += 1
            numeric_correct += int(
                numeric_result
            )

        errors: List[str] = []

        if not predicted:
            errors.append("missing_prediction")
        elif not predicted_status:
            errors.append(
                "invalid_or_missing_predicted_status"
            )
        elif not status_correct:
            errors.append(
                "grounding_status_mismatch"
            )

        if invalid_ids:
            errors.append(
                "invalid_evidence_reference"
            )

        if support_hit is False:
            errors.append(
                "acceptable_support_set_not_covered"
            )

        if attribution_result is False:
            errors.append("attribution_mismatch")

        if temporal_result is False:
            errors.append("temporal_mismatch")

        if numeric_result is False:
            errors.append("numeric_grounding_mismatch")

        if errors:
            claim_errors.append(
                {
                    "claim_id": claim_id,
                    "claim_text": _clean(
                        gold.get("claim_text")
                    ),
                    "claim_type": claim_type,
                    "gold_grounding_status": (
                        gold_status
                    ),
                    "predicted_grounding_status": (
                        predicted_status
                    ),
                    "predicted_evidence_ids": (
                        predicted_ids_list
                    ),
                    "invalid_evidence_ids": (
                        invalid_ids
                    ),
                    "support_set_hit": (
                        support_hit
                    ),
                    "errors": errors,
                    "case_tags": tags,
                }
            )

    per_class = _status_per_class(
        gold_statuses,
        predicted_statuses,
    )

    exact_status_correct = sum(
        1
        for gold, predicted in zip(
            gold_statuses,
            predicted_statuses,
        )
        if gold == predicted
    )

    supported_family_correct = sum(
        1
        for gold, predicted in zip(
            gold_statuses,
            predicted_statuses,
        )
        if (
            (gold in SUPPORTED_FAMILY)
            == (predicted in SUPPORTED_FAMILY)
        )
    )

    unsupported_detection = _binary_family_prf(
        gold_statuses,
        predicted_statuses,
        UNSUPPORTED_FAMILY,
    )

    contradiction_detection = _binary_family_prf(
        gold_statuses,
        predicted_statuses,
        {"contradicted"},
    )

    tag_accuracy = {
        tag: {
            "case_count": tag_totals[tag],
            "grounding_status_accuracy": _round_metric(
                _safe_divide(
                    tag_correct[tag],
                    tag_totals[tag],
                )
            ),
        }
        for tag in sorted(tag_totals)
    }

    claim_type_accuracy = {
        claim_type: {
            "case_count": (
                claim_type_totals[claim_type]
            ),
            "grounding_status_accuracy": _round_metric(
                _safe_divide(
                    claim_type_correct[claim_type],
                    claim_type_totals[claim_type],
                )
            ),
        }
        for claim_type in sorted(
            claim_type_totals
        )
    }

    citation_validity_rate = _safe_divide(
        total_citation_count
        - invalid_citation_count,
        total_citation_count,
    )

    claim_level_citation_validity_rate = (
        _safe_divide(
            claims_with_all_valid_citations,
            claims_with_citations,
        )
    )

    return {
        "gold_claim_count": len(gold_claim_ids),
        "prediction_claim_count": len(pred_claim_ids),
        "matched_claim_count": len(
            gold_claim_ids & pred_claim_ids
        ),
        "missing_prediction_count": len(
            missing_prediction_ids
        ),
        "extra_prediction_count": len(
            extra_prediction_ids
        ),
        "missing_prediction_ids": (
            missing_prediction_ids
        ),
        "extra_prediction_ids": (
            extra_prediction_ids
        ),

        "grounding_status_accuracy": _round_metric(
            _safe_divide(
                exact_status_correct,
                len(gold_statuses),
            )
        ),
        "grounding_status_macro_f1": _round_metric(
            _macro_f1(per_class)
        ),
        "grounding_status_per_class": per_class,

        "supported_family_accuracy": _round_metric(
            _safe_divide(
                supported_family_correct,
                len(gold_statuses),
            )
        ),

        "unsupported_or_contradicted_detection": (
            unsupported_detection
        ),
        "contradiction_detection": (
            contradiction_detection
        ),

        "citation_count": total_citation_count,
        "invalid_citation_count": (
            invalid_citation_count
        ),
        "citation_validity_rate": _round_metric(
            citation_validity_rate
        ),
        "claim_level_citation_validity_rate": (
            _round_metric(
                claim_level_citation_validity_rate
            )
        ),

        "support_set_evaluated_count": (
            support_set_evaluated
        ),
        "support_set_hit_rate": _round_metric(
            _safe_divide(
                support_set_hit_count,
                support_set_evaluated,
            )
        ),

        "single_evidence_case_count": (
            single_support_evaluated
        ),
        "single_evidence_support_set_hit_rate": (
            _round_metric(
                _safe_divide(
                    single_support_hit_count,
                    single_support_evaluated,
                )
            )
        ),

        "multi_evidence_case_count": (
            multi_support_evaluated
        ),
        "multi_evidence_support_set_hit_rate": (
            _round_metric(
                _safe_divide(
                    multi_support_hit_count,
                    multi_support_evaluated,
                )
            )
        ),

        "evidence_selection_precision": _round_metric(
            _safe_divide(
                evidence_precision_numer,
                evidence_precision_denom,
            )
        ),

        "best_support_set_coverage_recall": (
            _round_metric(
                (
                    sum(support_coverage_values)
                    / len(support_coverage_values)
                )
                if support_coverage_values
                else None
            )
        ),

        "attribution_accuracy": _round_metric(
            _safe_divide(
                attribution_correct,
                attribution_total,
            )
        ),
        "attribution_evaluated_count": (
            attribution_total
        ),

        "temporal_accuracy": _round_metric(
            _safe_divide(
                temporal_correct,
                temporal_total,
            )
        ),
        "temporal_evaluated_count": (
            temporal_total
        ),

        "numeric_grounding_accuracy": _round_metric(
            _safe_divide(
                numeric_correct,
                numeric_total,
            )
        ),
        "numeric_evaluated_count": (
            numeric_total
        ),

        "claim_type_breakdown": (
            claim_type_accuracy
        ),
        "case_tag_breakdown": (
            tag_accuracy
        ),

        "claim_errors": claim_errors,

        "interpretation_notes": {
            "citation_validity": (
                "Checks whether cited evidence IDs exist in the "
                "gold evidence inventory; it does not establish support."
            ),
            "support_set_hit": (
                "Checks whether the prediction contains every ID "
                "from at least one acceptable gold evidence combination."
            ),
            "multi_evidence": (
                "A multi-evidence case requires more than one evidence "
                "record in every acceptable sufficient support set."
            ),
            "semantic_similarity": (
                "No embedding-similarity score is treated as factual support."
            ),
            "governance": (
                "These are offline quality metrics, not release decisions."
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
            "predictions_path is required for grounding evaluation."
        )

    if kwargs:
        raise TypeError(
            "Unknown grounding evaluation option(s): "
            + ", ".join(sorted(kwargs))
        )

    gold_records = load_jsonl(gold_path)
    prediction_records = load_jsonl(
        predictions_path
    )

    return evaluate_grounding_records(
        gold_records=gold_records,
        prediction_records=prediction_records,
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
        description="Evaluate GroundingService predictions."
    )
    parser.add_argument(
        "--gold",
        required=True,
        help="Path to grounding gold JSONL.",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to grounding predictions JSONL.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional metrics JSON output path.",
    )

    args = parser.parse_args()

    metrics = run_evaluation(
        gold_path=args.gold,
        predictions_path=args.predictions,
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