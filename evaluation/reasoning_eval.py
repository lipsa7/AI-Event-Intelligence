"""
Offline evaluation for StrategicInferenceAgent / reasoning stage.

This evaluator measures whether the system's structured research reasoning
matches labelled reasoning units and preserves the epistemic distinctions used
by the product:

    FACT / CONNECTION / MECHANISM / INFERENCE / COUNTERSIGNAL / UNCERTAINTY

It does NOT treat semantic similarity as proof that reasoning is correct.
Lexical similarity is used only as a fallback to align a predicted reasoning
item with a labelled gold item when an explicit gold_item_id is unavailable.

Recommended gold JSONL
----------------------
One report/topic per line:

{
  "case_id": "CASE-001",
  "research_question": "...",

  "available_evidence_ids": [
    "FACT-001",
    "FACT-002",
    "METRIC-001",
    "TRIPLE-001",
    "ENE-001"
  ],

  "reasoning_items": [
    {
      "gold_item_id": "RI-001",
      "item_type": "mechanism",
      "text": "...",
      "epistemic_type": "mechanism",
      "mechanism_type": "CAPACITY_CONSTRAINT",
      "support_level": "strong",
      "acceptable_support_sets": [
        ["FACT-001", "METRIC-001"],
        ["FACT-001", "ENE-001"]
      ],
      "required_counterevidence_ids": [],
      "case_tags": ["multi_evidence"]
    },
    {
      "gold_item_id": "RI-002",
      "item_type": "hidden_insight",
      "text": "...",
      "epistemic_type": "inference",
      "support_level": "moderate",
      "acceptable_support_sets": [
        ["FACT-001", "FACT-002", "ENE-001"]
      ],
      "required_counterevidence_ids": ["FACT-009"],
      "case_tags": ["hidden_insight", "counterevidence_required"]
    }
  ],

  "critical_unknowns": [
    {
      "gold_item_id": "UNK-001",
      "text": "...",
      "case_tags": ["uncertainty"]
    }
  ]
}

Prediction JSONL
----------------
Either provide a normalized `reasoning_items` list, or provide the product's
structured report directly:

{
  "case_id": "CASE-001",
  "research_question": "...",
  "executive_synthesis": {...},
  "event_landscape": [...],
  "connections": [...],
  "underlying_dynamics": {
      "mechanisms": [...],
      "structural_shifts": [...],
      "changing_incentives": [...],
      "constraint_links": [...]
  },
  "hidden_insights": [...],
  "second_order_effects": [...],
  "counterevidence_and_alternatives": [...],
  "what_to_watch": [...],
  "uncertainty": {
      "critical_unknowns": [...],
      "known_evidence_limits": [...]
  }
}

Core metrics
------------
Reasoning-unit matching:
- precision / recall / F1 overall
- precision / recall / F1 by item type
- unmatched predicted item count
- missed gold item count

Epistemic quality:
- epistemic-type accuracy
- mechanism-type accuracy
- qualitative support-level accuracy

Evidence:
- evidence reference validity
- claim-level evidence-reference validity
- acceptable support-set hit rate
- multi-evidence support-set hit rate
- required counterevidence coverage
- required counterevidence full-hit rate

Uncertainty:
- critical-unknown precision / recall / F1
- known-evidence-limit count

Structural report checks:
- required-section completeness
- hidden-insight mechanism presence rate
- hidden-insight counter-signal/unknown presence rate
- what-to-watch conditional-signal presence rate

Diagnostics:
- per-case metrics
- case-tag breakdown
- explicit list of unmatched/missed items

Important interpretation notes
------------------------------
1. An unmatched predicted reasoning item is not automatically a hallucination.
   It means it was not matched to the labelled gold set. Whether it is truly
   unsupported requires GroundingService/RedTeam evaluation.

2. `acceptable_support_sets` may contain alternative sufficient evidence
   combinations. A reasoning item passes when it contains all IDs from at least
   one acceptable set.

3. Qualitative support levels are evaluated as labels:
       strong / moderate / weak / unknown
   They are not probabilities.

4. This evaluator does not decide release. PolicyEnforcementService applies
   quality thresholds separately.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


ITEM_TYPES: tuple[str, ...] = (
    "event",
    "connection",
    "mechanism",
    "structural_shift",
    "changing_incentive",
    "constraint_link",
    "hidden_insight",
    "second_order_effect",
    "counterevidence",
    "alternative_explanation",
    "watch_item",
)

EPISTEMIC_TYPES: tuple[str, ...] = (
    "fact",
    "connection",
    "mechanism",
    "inference",
    "countersignal",
    "uncertainty",
)

SUPPORT_LEVELS: tuple[str, ...] = (
    "strong",
    "moderate",
    "weak",
    "unknown",
)

MECHANISM_TYPES: tuple[str, ...] = (
    "CAPACITY_CONSTRAINT",
    "REGULATORY_SHIFT",
    "PRICING_POWER_SHIFT",
    "DEMAND_SHIFT",
    "CAPITAL_REALLOCATION",
    "BUSINESS_MODEL_CHANGE",
    "TECHNOLOGY_TRANSITION",
    "LABOUR_OR_TALENT_SHIFT",
    "DISTRIBUTION_SHIFT",
    "GEOPOLITICAL_SHIFT",
    "SUPPLY_CHAIN_RECONFIGURATION",
    "REPUTATION_OR_TRUST_SHIFT",
    "MARKET_CONSOLIDATION",
    "CUSTOMER_LOCK_IN",
    "INCENTIVE_CHANGE",
    "OTHER",
)

REQUIRED_REPORT_SECTIONS: tuple[str, ...] = (
    "event_landscape",
    "connections",
    "underlying_dynamics",
    "hidden_insights",
    "second_order_effects",
    "counterevidence_and_alternatives",
    "what_to_watch",
    "uncertainty",
)

ITEM_TYPE_ALIASES: Dict[str, str] = {
    "event": "event",
    "event_landscape": "event",
    "development": "event",

    "connection": "connection",
    "connections": "connection",

    "mechanism": "mechanism",
    "mechanisms": "mechanism",

    "structural_shift": "structural_shift",
    "structural_shifts": "structural_shift",

    "changing_incentive": "changing_incentive",
    "changing_incentives": "changing_incentive",
    "incentive_change": "changing_incentive",

    "constraint_link": "constraint_link",
    "constraint_links": "constraint_link",

    "hidden_insight": "hidden_insight",
    "hidden_insights": "hidden_insight",
    "insight": "hidden_insight",

    "second_order_effect": "second_order_effect",
    "second_order_effects": "second_order_effect",
    "second_order": "second_order_effect",

    "counterevidence": "counterevidence",
    "counter_signal": "counterevidence",
    "countersignal": "counterevidence",

    "alternative_explanation": "alternative_explanation",
    "alternative": "alternative_explanation",

    "watch_item": "watch_item",
    "what_to_watch": "watch_item",
    "watch": "watch_item",
}

EPISTEMIC_ALIASES: Dict[str, str] = {
    "fact": "fact",
    "factual": "fact",
    "evidence": "fact",

    "connection": "connection",
    "relationship": "connection",

    "mechanism": "mechanism",
    "causal_mechanism": "mechanism",

    "inference": "inference",
    "insight": "inference",
    "hypothesis": "inference",
    "scenario": "inference",

    "countersignal": "countersignal",
    "counter_signal": "countersignal",
    "counterevidence": "countersignal",

    "uncertainty": "uncertainty",
    "unknown": "uncertainty",
}

SUPPORT_LEVEL_ALIASES: Dict[str, str] = {
    "strong": "strong",
    "high": "strong",

    "moderate": "moderate",
    "medium": "moderate",

    "weak": "weak",
    "low": "weak",

    "unknown": "unknown",
    "insufficient": "unknown",
    "insufficient_evidence": "unknown",
}

DEFAULT_EPISTEMIC_BY_ITEM_TYPE: Dict[str, str] = {
    "event": "fact",
    "connection": "connection",
    "mechanism": "mechanism",
    "structural_shift": "inference",
    "changing_incentive": "inference",
    "constraint_link": "connection",
    "hidden_insight": "inference",
    "second_order_effect": "inference",
    "counterevidence": "countersignal",
    "alternative_explanation": "inference",
    "watch_item": "inference",
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


def _canonical_text(value: Any) -> str:
    text = _clean(value).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _token_set(value: Any) -> Set[str]:
    return {
        token
        for token in _canonical_text(value).split()
        if len(token) > 1
    }


def _jaccard(
    left: Iterable[str],
    right: Iterable[str],
) -> float:
    left_set = set(left)
    right_set = set(right)

    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0

    return len(left_set & right_set) / len(
        left_set | right_set
    )


def _normalize_item_type(value: Any) -> str:
    return ITEM_TYPE_ALIASES.get(
        _normalize_key(value),
        "",
    )


def _normalize_epistemic_type(value: Any) -> str:
    return EPISTEMIC_ALIASES.get(
        _normalize_key(value),
        "",
    )


def _normalize_support_level(value: Any) -> str:
    return SUPPORT_LEVEL_ALIASES.get(
        _normalize_key(value),
        "",
    )


def _normalize_mechanism_type(value: Any) -> str:
    normalized = _clean(value).upper().replace(
        "-",
        "_",
    ).replace(
        " ",
        "_",
    )

    return (
        normalized
        if normalized in MECHANISM_TYPES
        else ""
    )


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
                or item.get("constraint_id")
                or ""
            )

        normalized = _normalize_id(item)

        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result


def _normalize_support_sets(
    value: Any,
) -> List[Set[str]]:
    if not isinstance(value, list):
        return []

    result: List[Set[str]] = []
    seen: Set[Tuple[str, ...]] = set()

    for raw_set in value:
        ids = set(
            _normalize_id_list(raw_set)
        )

        if not ids:
            continue

        key = tuple(sorted(ids))
        if key in seen:
            continue

        seen.add(key)
        result.append(ids)

    return result


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
    return None if value is None else round(
        value,
        digits,
    )


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


def load_jsonl(
    path: str | Path,
) -> List[Dict[str, Any]]:
    path = Path(path)
    records: List[Dict[str, Any]] = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line_number, line in enumerate(
            handle,
            start=1,
        ):
            stripped = line.strip()

            if not stripped:
                continue

            try:
                parsed = json.loads(
                    stripped
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} "
                    f"of {path}: {exc}"
                ) from exc

            if not isinstance(
                parsed,
                dict,
            ):
                raise ValueError(
                    f"Line {line_number} of {path} "
                    "must contain a JSON object."
                )

            records.append(parsed)

    return records


def _case_id(
    record: Mapping[str, Any],
) -> str:
    return (
        _clean(record.get("case_id"))
        or _clean(record.get("report_id"))
        or _clean(record.get("id"))
    )


def _index_cases(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}

    for index, record in enumerate(records):
        case_id = _case_id(record)

        if not case_id:
            raise ValueError(
                f"Reasoning record {index} "
                "is missing case_id/report_id."
            )

        if case_id in indexed:
            raise ValueError(
                f"Duplicate case_id in evaluation "
                f"data: {case_id}"
            )

        indexed[case_id] = dict(
            record
        )

    return indexed


def _item_text(
    item: Mapping[str, Any],
) -> str:
    for key in (
        "text",
        "statement",
        "claim",
        "insight",
        "description",
        "summary",
        "mechanism",
        "effect",
        "shift",
        "signal",
        "watch_signal",
        "alternative_explanation",
        "counterevidence",
        "what",
        "title",
    ):
        value = _clean(
            item.get(key)
        )
        if value:
            return value

    return ""


def _item_id(
    item: Mapping[str, Any],
) -> str:
    return (
        _clean(item.get("gold_item_id"))
        or _clean(item.get("reference_item_id"))
        or _clean(item.get("item_id"))
        or _clean(item.get("id"))
    )


def _evidence_ids(
    item: Mapping[str, Any],
) -> List[str]:
    return _normalize_id_list(
        item.get("evidence_ids")
        or item.get("supporting_evidence_ids")
        or item.get("evidence")
        or item.get("references")
        or []
    )


def _counterevidence_ids(
    item: Mapping[str, Any],
) -> List[str]:
    return _normalize_id_list(
        item.get("counterevidence_ids")
        or item.get("counter_signal_ids")
        or item.get("countersignal_ids")
        or item.get("counter_evidence_ids")
        or []
    )


def _critical_unknown_values(
    item: Mapping[str, Any],
) -> List[Any]:
    value = (
        item.get("critical_unknowns")
        or item.get("unknowns")
        or []
    )

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return value

    return []


def _has_nonempty_mechanism(
    item: Mapping[str, Any],
) -> bool:
    mechanism = item.get("mechanism")

    if isinstance(mechanism, Mapping):
        return bool(
            _item_text(mechanism)
            or _clean(
                mechanism.get(
                    "mechanism_type"
                )
            )
        )

    return bool(
        _clean(mechanism)
        or _clean(
            item.get("mechanism_type")
        )
    )


def _has_countersignal_or_unknown(
    item: Mapping[str, Any],
) -> bool:
    return bool(
        _counterevidence_ids(item)
        or _critical_unknown_values(item)
        or item.get("counter_signals")
        or item.get("countersignals")
        or item.get("counterevidence")
        or item.get("main_uncertainty")
    )


def _has_watch_condition(
    item: Mapping[str, Any],
) -> bool:
    return bool(
        _clean(item.get("condition"))
        or _clean(item.get("signal"))
        or _clean(item.get("watch_signal"))
        or _clean(item.get("indicator"))
        or _clean(item.get("trigger"))
        or _clean(item.get("what_to_watch"))
    )


def _append_items(
    target: List[Dict[str, Any]],
    raw_items: Any,
    item_type: str,
) -> None:
    if raw_items is None:
        return

    if isinstance(raw_items, str):
        raw_items = [
            {"text": raw_items}
        ]
    elif isinstance(raw_items, Mapping):
        raw_items = [raw_items]

    if not isinstance(
        raw_items,
        list,
    ):
        return

    canonical_type = _normalize_item_type(
        item_type
    )

    for raw in raw_items:
        if isinstance(raw, str):
            raw = {
                "text": raw
            }

        if not isinstance(
            raw,
            Mapping,
        ):
            continue

        item = dict(raw)
        item.setdefault(
            "item_type",
            canonical_type,
        )

        if not _normalize_epistemic_type(
            item.get("epistemic_type")
        ):
            default = DEFAULT_EPISTEMIC_BY_ITEM_TYPE.get(
                canonical_type,
                "",
            )
            if default:
                item.setdefault(
                    "epistemic_type",
                    default,
                )

        target.append(item)


def _flatten_report_items(
    report: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """
    Convert either normalized reasoning_items or the product report schema
    into one list of evaluable reasoning units.
    """
    normalized = report.get(
        "reasoning_items"
    )

    if isinstance(normalized, list):
        return [
            dict(item)
            for item in normalized
            if isinstance(
                item,
                Mapping,
            )
        ]

    result: List[Dict[str, Any]] = []

    _append_items(
        result,
        report.get("event_landscape"),
        "event",
    )
    _append_items(
        result,
        report.get("connections"),
        "connection",
    )

    underlying = report.get(
        "underlying_dynamics"
    )
    if isinstance(
        underlying,
        Mapping,
    ):
        _append_items(
            result,
            underlying.get("mechanisms"),
            "mechanism",
        )
        _append_items(
            result,
            underlying.get(
                "structural_shifts"
            ),
            "structural_shift",
        )
        _append_items(
            result,
            underlying.get(
                "changing_incentives"
            ),
            "changing_incentive",
        )
        _append_items(
            result,
            underlying.get(
                "constraint_links"
            ),
            "constraint_link",
        )

    _append_items(
        result,
        report.get("hidden_insights"),
        "hidden_insight",
    )
    _append_items(
        result,
        report.get(
            "second_order_effects"
        ),
        "second_order_effect",
    )

    counter_section = report.get(
        "counterevidence_and_alternatives"
    )

    if isinstance(
        counter_section,
        Mapping,
    ):
        _append_items(
            result,
            counter_section.get(
                "counterevidence"
            ),
            "counterevidence",
        )
        _append_items(
            result,
            counter_section.get(
                "alternative_explanations"
            ),
            "alternative_explanation",
        )
    else:
        # Some versions may use one mixed list.
        _append_items(
            result,
            counter_section,
            "counterevidence",
        )

    _append_items(
        result,
        report.get("what_to_watch"),
        "watch_item",
    )

    return result


def _critical_unknown_items(
    record: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    direct = record.get(
        "critical_unknowns"
    )

    if direct is None:
        uncertainty = record.get(
            "uncertainty"
        )
        if isinstance(
            uncertainty,
            Mapping,
        ):
            direct = uncertainty.get(
                "critical_unknowns"
            )

    if isinstance(direct, str):
        direct = [direct]

    if not isinstance(
        direct,
        list,
    ):
        return []

    result: List[Dict[str, Any]] = []

    for item in direct:
        if isinstance(item, str):
            result.append(
                {"text": item}
            )
        elif isinstance(
            item,
            Mapping,
        ):
            result.append(
                dict(item)
            )

    return result


def _known_evidence_limits(
    report: Mapping[str, Any],
) -> List[Any]:
    uncertainty = report.get(
        "uncertainty"
    )

    if not isinstance(
        uncertainty,
        Mapping,
    ):
        return []

    value = uncertainty.get(
        "known_evidence_limits",
        [],
    )

    if isinstance(value, str):
        return [value]

    return (
        value
        if isinstance(value, list)
        else []
    )


def _available_evidence_ids(
    gold_case: Mapping[str, Any],
) -> Set[str]:
    return set(
        _normalize_id_list(
            gold_case.get(
                "available_evidence_ids"
            )
            or gold_case.get(
                "valid_evidence_ids"
            )
            or []
        )
    )


def _support_sets(
    item: Mapping[str, Any],
) -> List[Set[str]]:
    support_sets = _normalize_support_sets(
        item.get(
            "acceptable_support_sets"
        )
        or item.get(
            "gold_support_sets"
        )
        or []
    )

    if support_sets:
        return support_sets

    required = set(
        _normalize_id_list(
            item.get(
                "required_evidence_ids"
            )
            or item.get(
                "gold_evidence_ids"
            )
            or []
        )
    )

    return (
        [required]
        if required
        else []
    )


def _support_set_hit(
    predicted_ids: Set[str],
    support_sets: Sequence[Set[str]],
) -> Optional[bool]:
    if not support_sets:
        return None

    return any(
        support_set.issubset(
            predicted_ids
        )
        for support_set in support_sets
    )


def _is_multi_evidence(
    support_sets: Sequence[Set[str]],
) -> bool:
    return bool(
        support_sets
        and all(
            len(support_set) > 1
            for support_set in support_sets
        )
    )


def _fallback_item_similarity(
    gold_item: Mapping[str, Any],
    predicted_item: Mapping[str, Any],
) -> float:
    gold_type = _normalize_item_type(
        gold_item.get("item_type")
    )
    predicted_type = _normalize_item_type(
        predicted_item.get("item_type")
    )

    if (
        gold_type
        and predicted_type
        and gold_type != predicted_type
    ):
        return 0.0

    text_score = _jaccard(
        _token_set(
            _item_text(gold_item)
        ),
        _token_set(
            _item_text(predicted_item)
        ),
    )

    gold_mechanism = _normalize_mechanism_type(
        gold_item.get("mechanism_type")
    )
    pred_mechanism = _normalize_mechanism_type(
        predicted_item.get(
            "mechanism_type"
        )
    )

    mechanism_score = 0.0
    if gold_mechanism:
        mechanism_score = float(
            gold_mechanism == pred_mechanism
        )

    gold_evidence = set(
        _evidence_ids(gold_item)
    )
    if not gold_evidence:
        gold_evidence = set().union(
            *_support_sets(gold_item)
        ) if _support_sets(gold_item) else set()

    pred_evidence = set(
        _evidence_ids(predicted_item)
    )

    evidence_score = _jaccard(
        gold_evidence,
        pred_evidence,
    )

    if gold_mechanism:
        return (
            0.65 * text_score
            + 0.20 * mechanism_score
            + 0.15 * evidence_score
        )

    return (
        0.80 * text_score
        + 0.20 * evidence_score
    )


def _best_assignment(
    score_matrix: Sequence[Sequence[float]],
) -> List[Tuple[int, int, float]]:
    """
    Exact maximum-weight assignment for small reasoning sets using DP.
    Deterministic greedy fallback for larger sets.
    """
    gold_count = len(score_matrix)
    pred_count = (
        len(score_matrix[0])
        if gold_count
        else 0
    )

    if not gold_count or not pred_count:
        return []

    if (
        gold_count > 18
        or pred_count > 18
    ):
        candidates = [
            (
                score_matrix[g][p],
                g,
                p,
            )
            for g in range(
                gold_count
            )
            for p in range(
                pred_count
            )
        ]
        candidates.sort(
            key=lambda value: (
                -value[0],
                value[1],
                value[2],
            )
        )

        used_gold: Set[int] = set()
        used_pred: Set[int] = set()
        result: List[
            Tuple[int, int, float]
        ] = []

        for score, g, p in candidates:
            if (
                g in used_gold
                or p in used_pred
            ):
                continue

            used_gold.add(g)
            used_pred.add(p)
            result.append(
                (g, p, score)
            )

        return result

    states: Dict[
        int,
        Tuple[
            float,
            List[
                Tuple[int, int, float]
            ],
        ],
    ] = {
        0: (0.0, [])
    }

    for g in range(gold_count):
        next_states = dict(states)

        for mask, (
            total,
            assignments,
        ) in states.items():
            for p in range(
                pred_count
            ):
                bit = 1 << p

                if mask & bit:
                    continue

                score = score_matrix[g][p]
                new_mask = mask | bit
                candidate = (
                    total + score,
                    assignments
                    + [(g, p, score)],
                )

                existing = next_states.get(
                    new_mask
                )

                if (
                    existing is None
                    or candidate[0]
                    > existing[0]
                ):
                    next_states[
                        new_mask
                    ] = candidate

        states = next_states

    return max(
        states.values(),
        key=lambda value: (
            value[0],
            len(value[1]),
        ),
    )[1]


def match_reasoning_items(
    gold_items: Sequence[Mapping[str, Any]],
    predicted_items: Sequence[Mapping[str, Any]],
    *,
    minimum_similarity: float = 0.40,
) -> List[Dict[str, Any]]:
    """
    Match reasoning units one-to-one.

    Explicit gold_item_id/reference_item_id is preferred. Lexical similarity is
    only an alignment fallback; it is not used as a correctness judgement.
    """
    matches: List[Dict[str, Any]] = []
    used_gold: Set[int] = set()
    used_pred: Set[int] = set()

    gold_id_index: Dict[str, int] = {}

    for index, item in enumerate(
        gold_items
    ):
        item_id = _item_id(item)
        if item_id:
            gold_id_index[
                item_id
            ] = index

    for p, predicted in enumerate(
        predicted_items
    ):
        reference_id = (
            _clean(
                predicted.get(
                    "gold_item_id"
                )
            )
            or _clean(
                predicted.get(
                    "reference_item_id"
                )
            )
        )

        if (
            not reference_id
            or reference_id
            not in gold_id_index
        ):
            continue

        g = gold_id_index[
            reference_id
        ]

        if g in used_gold:
            continue

        used_gold.add(g)
        used_pred.add(p)

        matches.append(
            {
                "gold_index": g,
                "predicted_index": p,
                "score": 1.0,
                "method": (
                    "explicit_gold_item_id"
                ),
            }
        )

    remaining_gold = [
        g
        for g in range(
            len(gold_items)
        )
        if g not in used_gold
    ]
    remaining_pred = [
        p
        for p in range(
            len(predicted_items)
        )
        if p not in used_pred
    ]

    if (
        not remaining_gold
        or not remaining_pred
    ):
        return matches

    score_matrix = [
        [
            _fallback_item_similarity(
                gold_items[g],
                predicted_items[p],
            )
            for p in remaining_pred
        ]
        for g in remaining_gold
    ]

    for local_g, local_p, score in _best_assignment(
        score_matrix
    ):
        if score < minimum_similarity:
            continue

        matches.append(
            {
                "gold_index": (
                    remaining_gold[
                        local_g
                    ]
                ),
                "predicted_index": (
                    remaining_pred[
                        local_p
                    ]
                ),
                "score": score,
                "method": (
                    "lexical_alignment"
                ),
            }
        )

    return matches


def _structural_completeness(
    report: Mapping[str, Any],
) -> Dict[str, Any]:
    present: Dict[str, bool] = {}

    for section in REQUIRED_REPORT_SECTIONS:
        value = report.get(
            section
        )

        if section == "underlying_dynamics":
            present[section] = isinstance(
                value,
                Mapping,
            )
        elif section == "uncertainty":
            present[section] = isinstance(
                value,
                Mapping,
            )
        else:
            present[section] = (
                isinstance(value, list)
            )

    return {
        "required_section_count": len(
            REQUIRED_REPORT_SECTIONS
        ),
        "present_required_section_count": sum(
            int(value)
            for value in present.values()
        ),
        "required_section_completeness": _round_metric(
            _safe_divide(
                sum(
                    int(value)
                    for value in present.values()
                ),
                len(
                    REQUIRED_REPORT_SECTIONS
                ),
            )
        ),
        "section_presence": present,
    }


def _evaluate_unknowns(
    gold_unknowns: Sequence[Mapping[str, Any]],
    predicted_unknowns: Sequence[Mapping[str, Any]],
    *,
    minimum_similarity: float,
) -> Dict[str, Any]:
    matches = match_reasoning_items(
        gold_unknowns,
        predicted_unknowns,
        minimum_similarity=(
            minimum_similarity
        ),
    )

    return {
        **_prf(
            len(matches),
            len(predicted_unknowns)
            - len(matches),
            len(gold_unknowns)
            - len(matches),
        ),
        "matches": matches,
    }


def evaluate_reasoning_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    *,
    minimum_item_similarity: float = 0.40,
) -> Dict[str, Any]:
    gold_by_case = _index_cases(
        gold_records
    )
    pred_by_case = _index_cases(
        prediction_records
    )

    gold_case_ids = set(
        gold_by_case
    )
    pred_case_ids = set(
        pred_by_case
    )

    missing_case_ids = sorted(
        gold_case_ids - pred_case_ids
    )
    extra_case_ids = sorted(
        pred_case_ids - gold_case_ids
    )

    total_gold_items = 0
    total_pred_items = 0
    total_matches = 0

    overall_type_counts: Dict[
        str,
        Dict[str, int],
    ] = {
        item_type: {
            "tp": 0,
            "fp": 0,
            "fn": 0,
        }
        for item_type in ITEM_TYPES
    }

    epistemic_correct = 0
    epistemic_total = 0

    mechanism_correct = 0
    mechanism_total = 0

    support_correct = 0
    support_total = 0

    valid_evidence_refs = 0
    total_evidence_refs = 0
    claims_with_refs = 0
    claims_all_refs_valid = 0

    support_set_hit = 0
    support_set_total = 0

    multi_support_hit = 0
    multi_support_total = 0

    counterevidence_covered = 0
    counterevidence_required_total = 0
    counterevidence_full_hit = 0
    counterevidence_item_total = 0

    hidden_insight_count = 0
    hidden_insight_with_mechanism = 0
    hidden_insight_with_counter_or_unknown = 0

    watch_count = 0
    watch_with_condition = 0

    unknown_tp = unknown_fp = unknown_fn = 0

    structural_completeness_values: List[
        float
    ] = []

    known_evidence_limit_count = 0

    tag_totals: Counter[str] = Counter()
    tag_matched: Counter[str] = Counter()
    tag_support_hit: Counter[str] = Counter()
    tag_support_evaluated: Counter[str] = Counter()

    per_case: List[Dict[str, Any]] = []

    for case_id in sorted(
        gold_case_ids
    ):
        gold_case = gold_by_case[
            case_id
        ]
        predicted_case = pred_by_case.get(
            case_id,
            {},
        )

        gold_items = _flatten_report_items(
            gold_case
        )
        predicted_items = _flatten_report_items(
            predicted_case
        )

        total_gold_items += len(
            gold_items
        )
        total_pred_items += len(
            predicted_items
        )

        matches = match_reasoning_items(
            gold_items,
            predicted_items,
            minimum_similarity=(
                minimum_item_similarity
            ),
        )
        total_matches += len(
            matches
        )

        matched_gold_indices = {
            match["gold_index"]
            for match in matches
        }
        matched_pred_indices = {
            match["predicted_index"]
            for match in matches
        }

        available_ids = _available_evidence_ids(
            gold_case
        )

        case_errors: List[
            Dict[str, Any]
        ] = []

        # Per-type matching counts.
        for item_type in ITEM_TYPES:
            gold_indices = {
                i
                for i, item in enumerate(
                    gold_items
                )
                if _normalize_item_type(
                    item.get(
                        "item_type"
                    )
                ) == item_type
            }
            pred_indices = {
                i
                for i, item in enumerate(
                    predicted_items
                )
                if _normalize_item_type(
                    item.get(
                        "item_type"
                    )
                ) == item_type
            }

            same_type_matches = sum(
                1
                for match in matches
                if (
                    match["gold_index"]
                    in gold_indices
                    and match[
                        "predicted_index"
                    ]
                    in pred_indices
                )
            )

            overall_type_counts[
                item_type
            ]["tp"] += same_type_matches
            overall_type_counts[
                item_type
            ]["fp"] += (
                len(pred_indices)
                - same_type_matches
            )
            overall_type_counts[
                item_type
            ]["fn"] += (
                len(gold_indices)
                - same_type_matches
            )

        for match in matches:
            gold_item = gold_items[
                match["gold_index"]
            ]
            pred_item = predicted_items[
                match[
                    "predicted_index"
                ]
            ]

            gold_type = _normalize_item_type(
                gold_item.get(
                    "item_type"
                )
            )

            gold_epistemic = _normalize_epistemic_type(
                gold_item.get(
                    "epistemic_type"
                )
            )
            pred_epistemic = _normalize_epistemic_type(
                pred_item.get(
                    "epistemic_type"
                )
            )

            if gold_epistemic:
                epistemic_total += 1
                epistemic_correct += int(
                    gold_epistemic
                    == pred_epistemic
                )

            gold_mechanism = _normalize_mechanism_type(
                gold_item.get(
                    "mechanism_type"
                )
            )

            if gold_mechanism:
                mechanism_total += 1
                mechanism_correct += int(
                    gold_mechanism
                    == _normalize_mechanism_type(
                        pred_item.get(
                            "mechanism_type"
                        )
                    )
                )

            gold_support = _normalize_support_level(
                gold_item.get(
                    "support_level"
                )
            )

            if gold_support:
                support_total += 1
                support_correct += int(
                    gold_support
                    == _normalize_support_level(
                        pred_item.get(
                            "support_level"
                        )
                    )
                )

            predicted_ids = set(
                _evidence_ids(
                    pred_item
                )
            )

            if predicted_ids:
                claims_with_refs += 1

                invalid_ids = (
                    predicted_ids
                    - available_ids
                    if available_ids
                    else set()
                )

                total_evidence_refs += len(
                    predicted_ids
                )
                valid_evidence_refs += (
                    len(predicted_ids)
                    - len(invalid_ids)
                )

                if not invalid_ids:
                    claims_all_refs_valid += 1

                if invalid_ids:
                    case_errors.append(
                        {
                            "gold_item_id": (
                                _item_id(
                                    gold_item
                                )
                            ),
                            "error": (
                                "invalid_evidence_reference"
                            ),
                            "invalid_ids": sorted(
                                invalid_ids
                            ),
                        }
                    )

            support_sets = _support_sets(
                gold_item
            )
            hit = _support_set_hit(
                predicted_ids,
                support_sets,
            )

            if hit is not None:
                support_set_total += 1
                support_set_hit += int(
                    hit
                )

                if _is_multi_evidence(
                    support_sets
                ):
                    multi_support_total += 1
                    multi_support_hit += int(
                        hit
                    )

            required_counter = set(
                _normalize_id_list(
                    gold_item.get(
                        "required_counterevidence_ids"
                    )
                    or gold_item.get(
                        "required_counter_signal_ids"
                    )
                    or []
                )
            )

            if required_counter:
                counterevidence_item_total += 1
                predicted_counter = set(
                    _counterevidence_ids(
                        pred_item
                    )
                )

                covered = len(
                    required_counter
                    & predicted_counter
                )

                counterevidence_covered += covered
                counterevidence_required_total += len(
                    required_counter
                )

                if required_counter.issubset(
                    predicted_counter
                ):
                    counterevidence_full_hit += 1

            tags = _normalize_tags(
                gold_item.get(
                    "case_tags"
                )
                or gold_item.get(
                    "tags"
                )
                or []
            )

            for tag in tags:
                tag_totals[tag] += 1
                tag_matched[tag] += 1

                if hit is not None:
                    tag_support_evaluated[
                        tag
                    ] += 1
                    tag_support_hit[
                        tag
                    ] += int(
                        hit
                    )

        # Count tags for missed gold items too.
        for gold_index, gold_item in enumerate(
            gold_items
        ):
            if gold_index in matched_gold_indices:
                continue

            for tag in _normalize_tags(
                gold_item.get(
                    "case_tags"
                )
                or gold_item.get(
                    "tags"
                )
                or []
            ):
                tag_totals[tag] += 1

        # Structural quality on predicted report.
        if predicted_case:
            structural = _structural_completeness(
                predicted_case
            )
            completeness = structural[
                "required_section_completeness"
            ]
            if isinstance(
                completeness,
                (int, float),
            ):
                structural_completeness_values.append(
                    float(
                        completeness
                    )
                )
        else:
            structural = {
                "required_section_count": len(
                    REQUIRED_REPORT_SECTIONS
                ),
                "present_required_section_count": 0,
                "required_section_completeness": 0.0,
                "section_presence": {
                    section: False
                    for section in REQUIRED_REPORT_SECTIONS
                },
            }
            structural_completeness_values.append(
                0.0
            )

        for item in predicted_items:
            item_type = _normalize_item_type(
                item.get(
                    "item_type"
                )
            )

            if item_type == "hidden_insight":
                hidden_insight_count += 1
                hidden_insight_with_mechanism += int(
                    _has_nonempty_mechanism(
                        item
                    )
                )
                hidden_insight_with_counter_or_unknown += int(
                    _has_countersignal_or_unknown(
                        item
                    )
                )

            if item_type == "watch_item":
                watch_count += 1
                watch_with_condition += int(
                    _has_watch_condition(
                        item
                    )
                )

        known_evidence_limit_count += len(
            _known_evidence_limits(
                predicted_case
            )
        )

        gold_unknowns = _critical_unknown_items(
            gold_case
        )
        pred_unknowns = _critical_unknown_items(
            predicted_case
        )

        unknown_metrics = _evaluate_unknowns(
            gold_unknowns,
            pred_unknowns,
            minimum_similarity=(
                minimum_item_similarity
            ),
        )

        unknown_tp += int(
            unknown_metrics[
                "true_positive"
            ]
        )
        unknown_fp += int(
            unknown_metrics[
                "false_positive"
            ]
        )
        unknown_fn += int(
            unknown_metrics[
                "false_negative"
            ]
        )

        missed_items = [
            {
                "gold_item_id": _item_id(
                    gold_items[index]
                ),
                "item_type": _normalize_item_type(
                    gold_items[index].get(
                        "item_type"
                    )
                ),
                "text": _item_text(
                    gold_items[index]
                ),
            }
            for index in range(
                len(gold_items)
            )
            if index not in matched_gold_indices
        ]

        unmatched_predicted = [
            {
                "item_type": _normalize_item_type(
                    predicted_items[index].get(
                        "item_type"
                    )
                ),
                "text": _item_text(
                    predicted_items[index]
                ),
                "evidence_ids": _evidence_ids(
                    predicted_items[index]
                ),
            }
            for index in range(
                len(predicted_items)
            )
            if index not in matched_pred_indices
        ]

        per_case.append(
            {
                "case_id": case_id,
                "gold_reasoning_item_count": len(
                    gold_items
                ),
                "predicted_reasoning_item_count": len(
                    predicted_items
                ),
                "matched_reasoning_item_count": len(
                    matches
                ),
                "reasoning_item_recall": _round_metric(
                    _safe_divide(
                        len(matches),
                        len(gold_items),
                    )
                ),
                "reasoning_item_precision": _round_metric(
                    _safe_divide(
                        len(matches),
                        len(predicted_items),
                    )
                ),
                "required_section_completeness": (
                    structural[
                        "required_section_completeness"
                    ]
                ),
                "critical_unknown_metrics": (
                    unknown_metrics
                ),
                "missed_gold_items": (
                    missed_items
                ),
                "unmatched_predicted_items": (
                    unmatched_predicted
                ),
                "errors": case_errors,
            }
        )

    overall_item_metrics = _prf(
        total_matches,
        total_pred_items
        - total_matches,
        total_gold_items
        - total_matches,
    )

    per_type_metrics: Dict[
        str,
        Any,
    ] = {}

    for item_type, counts in overall_type_counts.items():
        if (
            counts["tp"]
            or counts["fp"]
            or counts["fn"]
        ):
            per_type_metrics[
                item_type
            ] = _prf(
                counts["tp"],
                counts["fp"],
                counts["fn"],
            )

    tag_breakdown = {
        tag: {
            "gold_item_count": tag_totals[
                tag
            ],
            "matched_item_count": (
                tag_matched[
                    tag
                ]
            ),
            "item_recall": _round_metric(
                _safe_divide(
                    tag_matched[tag],
                    tag_totals[tag],
                )
            ),
            "support_set_hit_rate": _round_metric(
                _safe_divide(
                    tag_support_hit[tag],
                    tag_support_evaluated[
                        tag
                    ],
                )
            ),
        }
        for tag in sorted(
            tag_totals
        )
    }

    unknown_metrics = _prf(
        unknown_tp,
        unknown_fp,
        unknown_fn,
    )

    return {
        "gold_case_count": len(
            gold_case_ids
        ),
        "prediction_case_count": len(
            pred_case_ids
        ),
        "missing_case_count": len(
            missing_case_ids
        ),
        "extra_case_count": len(
            extra_case_ids
        ),
        "missing_case_ids": (
            missing_case_ids
        ),
        "extra_case_ids": (
            extra_case_ids
        ),

        "gold_reasoning_item_count": (
            total_gold_items
        ),
        "predicted_reasoning_item_count": (
            total_pred_items
        ),
        "matched_reasoning_item_count": (
            total_matches
        ),

        "reasoning_item_micro": (
            overall_item_metrics
        ),
        "reasoning_item_per_type": (
            per_type_metrics
        ),

        "epistemic_type_accuracy": _round_metric(
            _safe_divide(
                epistemic_correct,
                epistemic_total,
            )
        ),
        "epistemic_type_evaluated_count": (
            epistemic_total
        ),

        "mechanism_type_accuracy": _round_metric(
            _safe_divide(
                mechanism_correct,
                mechanism_total,
            )
        ),
        "mechanism_type_evaluated_count": (
            mechanism_total
        ),

        "support_level_accuracy": _round_metric(
            _safe_divide(
                support_correct,
                support_total,
            )
        ),
        "support_level_evaluated_count": (
            support_total
        ),

        "evidence_reference_validity_rate": _round_metric(
            _safe_divide(
                valid_evidence_refs,
                total_evidence_refs,
            )
        ),
        "evidence_reference_count": (
            total_evidence_refs
        ),
        "claim_level_evidence_reference_validity_rate": (
            _round_metric(
                _safe_divide(
                    claims_all_refs_valid,
                    claims_with_refs,
                )
            )
        ),

        "support_set_hit_rate": _round_metric(
            _safe_divide(
                support_set_hit,
                support_set_total,
            )
        ),
        "support_set_evaluated_count": (
            support_set_total
        ),

        "multi_evidence_support_set_hit_rate": (
            _round_metric(
                _safe_divide(
                    multi_support_hit,
                    multi_support_total,
                )
            )
        ),
        "multi_evidence_support_set_evaluated_count": (
            multi_support_total
        ),

        "required_counterevidence_coverage_rate": (
            _round_metric(
                _safe_divide(
                    counterevidence_covered,
                    counterevidence_required_total,
                )
            )
        ),
        "required_counterevidence_full_hit_rate": (
            _round_metric(
                _safe_divide(
                    counterevidence_full_hit,
                    counterevidence_item_total,
                )
            )
        ),
        "counterevidence_required_item_count": (
            counterevidence_item_total
        ),

        "critical_unknown_micro": (
            unknown_metrics
        ),

        "known_evidence_limit_count": (
            known_evidence_limit_count
        ),

        "required_section_completeness_mean": (
            _round_metric(
                (
                    sum(
                        structural_completeness_values
                    )
                    / len(
                        structural_completeness_values
                    )
                )
                if structural_completeness_values
                else None
            )
        ),

        "hidden_insight_count": (
            hidden_insight_count
        ),
        "hidden_insight_mechanism_presence_rate": (
            _round_metric(
                _safe_divide(
                    hidden_insight_with_mechanism,
                    hidden_insight_count,
                )
            )
        ),
        "hidden_insight_counter_or_unknown_presence_rate": (
            _round_metric(
                _safe_divide(
                    hidden_insight_with_counter_or_unknown,
                    hidden_insight_count,
                )
            )
        ),

        "watch_item_count": (
            watch_count
        ),
        "watch_item_conditional_signal_presence_rate": (
            _round_metric(
                _safe_divide(
                    watch_with_condition,
                    watch_count,
                )
            )
        ),

        "case_tag_breakdown": (
            tag_breakdown
        ),

        "per_case": per_case,

        "interpretation_notes": {
            "unmatched_prediction": (
                "An unmatched predicted reasoning item is not "
                "automatically a hallucination. It was not aligned "
                "to the labelled gold set and should be checked by "
                "grounding/red-team evaluation."
            ),
            "support_level": (
                "Support levels are qualitative labels, not "
                "calibrated probabilities."
            ),
            "evidence": (
                "Evidence-ID validity is distinct from whether the "
                "evidence actually entails the reasoning item."
            ),
            "matching": (
                "Explicit gold_item_id mapping is preferred. "
                "Lexical similarity is only a fallback alignment "
                "mechanism and is not treated as proof of correctness."
            ),
            "release": (
                "These are offline reasoning-quality metrics. "
                "PolicyEnforcementService makes release decisions."
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
            "predictions_path is required for reasoning evaluation."
        )

    minimum_item_similarity = float(
        kwargs.pop(
            "minimum_item_similarity",
            0.40,
        )
    )

    if kwargs:
        raise TypeError(
            "Unknown reasoning evaluation option(s): "
            + ", ".join(
                sorted(kwargs)
            )
        )

    return evaluate_reasoning_records(
        gold_records=load_jsonl(
            gold_path
        ),
        prediction_records=load_jsonl(
            predictions_path
        ),
        minimum_item_similarity=(
            minimum_item_similarity
        ),
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
        description=(
            "Evaluate StrategicInferenceAgent reasoning outputs."
        )
    )
    parser.add_argument(
        "--gold",
        required=True,
        help="Path to reasoning gold JSONL.",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to reasoning prediction JSONL.",
    )
    parser.add_argument(
        "--minimum-item-similarity",
        type=float,
        default=0.40,
        help=(
            "Minimum lexical fallback alignment score. "
            "Explicit gold_item_id matching is preferred."
        ),
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output metrics JSON path.",
    )

    args = parser.parse_args()

    metrics = run_evaluation(
        gold_path=args.gold,
        predictions_path=(
            args.predictions
        ),
        minimum_item_similarity=(
            args.minimum_item_similarity
        ),
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