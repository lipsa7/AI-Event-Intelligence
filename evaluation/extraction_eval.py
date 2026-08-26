"""
Offline evaluation for ExtractionAgent.

Aligned with the final extraction contract:
    source/article -> 0..N events

Recommended JSONL shape
-----------------------
Gold:
{
  "source_id": "SOURCE-001",
  "article_text": "optional source text for snippet validation",
  "events": [
    {
      "gold_event_id": "GOLD-EVENT-001",
      "event_title": "...",
      "event_type": "...",
      "event_summary": "...",
      "claim_status": "directly_stated",
      "temporal_status": "current",
      "entities": {...},
      "metrics": [...],
      "relationships": [...]
    }
  ]
}

Predictions:
one ExtractionAgent output envelope per line.

Metrics
-------
Event:
- precision / recall / F1
- exact per-article event-count match rate
- missed / extra events

Matched events:
- event_type accuracy
- claim_status accuracy
- temporal_status accuracy
- event-date normalized-text accuracy

Entities:
- micro P/R/F1
- per-category P/R/F1

Relationships:
- micro P/R/F1

Metrics:
- micro P/R/F1
- lexical unit-equivalence accuracy
- period accuracy

Evidence:
- source-snippet validity rate when source article text is available

Important:
- lexical equivalence such as GW <-> gigawatt is allowed;
- arithmetic conversion such as 1 TW <-> 1000 GW is NOT performed;
- snippet occurrence establishes traceability, not independent truth.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ENTITY_CATEGORIES = (
    "companies",
    "countries",
    "organizations",
    "people",
    "technologies",
    "products",
    "policies_regulations",
)

ENTITY_ALIASES = {
    "companies": ("companies",),
    "countries": ("countries",),
    "organizations": ("organizations",),
    "people": ("people",),
    "technologies": ("technologies",),
    "products": ("products",),
    "policies_regulations": ("policies_regulations", "regulations"),
}

UNIT_ALIASES = {
    "%": "percent",
    "percent": "percent",
    "percentage": "percent",
    "pp": "percentage_point",
    "percentage point": "percentage_point",
    "percentage points": "percentage_point",
    "bp": "basis_point",
    "bps": "basis_point",
    "basis point": "basis_point",
    "basis points": "basis_point",

    "w": "watt",
    "watt": "watt",
    "watts": "watt",
    "kw": "kilowatt",
    "kilowatt": "kilowatt",
    "kilowatts": "kilowatt",
    "mw": "megawatt",
    "megawatt": "megawatt",
    "megawatts": "megawatt",
    "gw": "gigawatt",
    "gigawatt": "gigawatt",
    "gigawatts": "gigawatt",
    "tw": "terawatt",
    "terawatt": "terawatt",
    "terawatts": "terawatt",

    "wh": "watt_hour",
    "watt hour": "watt_hour",
    "watt hours": "watt_hour",
    "kwh": "kilowatt_hour",
    "kilowatt hour": "kilowatt_hour",
    "kilowatt hours": "kilowatt_hour",
    "mwh": "megawatt_hour",
    "megawatt hour": "megawatt_hour",
    "megawatt hours": "megawatt_hour",
    "gwh": "gigawatt_hour",
    "gigawatt hour": "gigawatt_hour",
    "gigawatt hours": "gigawatt_hour",
    "twh": "terawatt_hour",
    "terawatt hour": "terawatt_hour",
    "terawatt hours": "terawatt_hour",

    "$": "usd",
    "us$": "usd",
    "usd": "usd",
    "₹": "inr",
    "inr": "inr",
    "€": "eur",
    "eur": "eur",
    "£": "gbp",
    "gbp": "gbp",

    "user": "user",
    "users": "user",
    "unit": "unit",
    "units": "unit",
    "facility": "facility",
    "facilities": "facility",
    "employee": "employee",
    "employees": "employee",
    "job": "job",
    "jobs": "job",
    "day": "day",
    "days": "day",
    "month": "month",
    "months": "month",
    "year": "year",
    "years": "year",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _canonical(value: Any) -> str:
    text = _clean(value).casefold()
    text = re.sub(r"[^a-z0-9%₹$€£]+", " ", text)
    return " ".join(text.split())


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _canonical(value).split()
        if len(token) > 1
    }


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left = set(left)
    right = set(right)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _safe_divide(num: int | float, den: int | float) -> Optional[float]:
    return None if den == 0 else float(num) / float(den)


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def _prf(tp: int, fp: int, fn: int) -> Dict[str, Any]:
    precision = _safe_divide(tp, tp + fp)
    recall = _safe_divide(tp, tp + fn)

    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": _round(precision),
        "recall": _round(recall),
        "f1": _round(f1),
    }


def _normalize_enum(value: Any) -> str:
    return _clean(value).casefold().replace("-", "_").replace(" ", "_")


def _normalize_date_text(value: Any) -> str:
    text = _clean(value).casefold()
    text = re.sub(r"[,./-]+", " ", text)
    return " ".join(text.split())


def _canonical_unit(value: Any) -> str:
    raw = _clean(value).casefold()
    raw = raw.replace("‑", "-").replace("–", "-").replace("—", "-")
    if raw in UNIT_ALIASES:
        return UNIT_ALIASES[raw]

    simplified = re.sub(r"[^\w%₹$€£]+", " ", raw)
    simplified = " ".join(simplified.split())
    return UNIT_ALIASES.get(simplified, simplified)


def _normalize_numeric(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return ""

    stripped = raw.replace(",", "").strip()
    if stripped.endswith("%"):
        stripped = stripped[:-1].strip()

    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", stripped):
        try:
            number = Decimal(stripped)
            normalized = format(number.normalize(), "f")
            if "." in normalized:
                normalized = normalized.rstrip("0").rstrip(".")
            return normalized
        except InvalidOperation:
            pass

    return _canonical(raw)


def _metric_value_unit(metric: Mapping[str, Any]) -> Tuple[str, str]:
    value = _clean(metric.get("value"))
    unit = _clean(metric.get("unit"))

    if value.endswith("%") and not unit:
        value = value[:-1].strip()
        unit = "%"

    if value and not unit:
        match = re.fullmatch(
            r"\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%₹$€£][A-Za-z0-9%₹$€£\-\s]*)\s*",
            value,
        )
        if match:
            value = match.group(1)
            unit = match.group(2)

    return _normalize_numeric(value), _canonical_unit(unit)


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    path = Path(path)
    records: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc

            if not isinstance(parsed, dict):
                raise ValueError(
                    f"Line {line_number} of {path} must be a JSON object."
                )

            records.append(parsed)

    return records


def _source_id(record: Mapping[str, Any]) -> str:
    if _clean(record.get("source_id")):
        return _clean(record.get("source_id"))
    if _clean(record.get("article_id")):
        return _clean(record.get("article_id"))

    metadata = record.get("source_metadata")
    if isinstance(metadata, Mapping):
        return _clean(metadata.get("article_id"))

    return ""


def _index_sources(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}

    for index, record in enumerate(records):
        source_id = _source_id(record)
        if not source_id:
            raise ValueError(
                f"Source record {index} is missing source_id/article_id."
            )
        if source_id in indexed:
            raise ValueError(
                f"Duplicate source_id in evaluation data: {source_id}"
            )
        indexed[source_id] = dict(record)

    return indexed


def _events(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    value = record.get("events", [])
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list):
        return []

    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
    ]


def _entity_values(
    event: Mapping[str, Any],
    category: str,
) -> set[str]:
    values: Any = []
    entities = event.get("entities")

    if isinstance(entities, Mapping):
        for alias in ENTITY_ALIASES[category]:
            if alias in entities:
                values = entities.get(alias, [])
                break
    else:
        for alias in ENTITY_ALIASES[category]:
            if alias in event:
                values = event.get(alias, [])
                break

    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()

    result: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            item = (
                item.get("name")
                or item.get("entity")
                or item.get("label")
                or ""
            )
        normalized = _canonical(item)
        if normalized:
            result.add(normalized)

    return result


def _all_entities(event: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for category in ENTITY_CATEGORIES:
        result.update(_entity_values(event, category))
    return result


def _event_score(
    gold: Mapping[str, Any],
    predicted: Mapping[str, Any],
) -> float:
    gold_type = _canonical(gold.get("event_type"))
    pred_type = _canonical(predicted.get("event_type"))

    type_score = (
        1.0
        if gold_type and gold_type == pred_type
        else _jaccard(_tokens(gold_type), _tokens(pred_type))
    )

    title_score = _jaccard(
        _tokens(gold.get("event_title") or gold.get("title")),
        _tokens(predicted.get("event_title") or predicted.get("title")),
    )
    summary_score = _jaccard(
        _tokens(gold.get("event_summary") or gold.get("summary")),
        _tokens(predicted.get("event_summary") or predicted.get("summary")),
    )
    entity_score = _jaccard(
        _all_entities(gold),
        _all_entities(predicted),
    )

    return (
        0.35 * type_score
        + 0.25 * title_score
        + 0.20 * summary_score
        + 0.20 * entity_score
    )


def _gold_event_id(event: Mapping[str, Any]) -> str:
    return (
        _clean(event.get("gold_event_id"))
        or _clean(event.get("reference_event_id"))
    )


def _best_assignment(
    scores: Sequence[Sequence[float]],
) -> List[Tuple[int, int, float]]:
    """
    Exact one-to-one maximum-weight assignment for small article-level event sets.
    Deterministic greedy fallback for unusually large sets.
    """
    gold_count = len(scores)
    pred_count = len(scores[0]) if gold_count else 0

    if not gold_count or not pred_count:
        return []

    if gold_count > 16 or pred_count > 16:
        candidates = [
            (scores[g][p], g, p)
            for g in range(gold_count)
            for p in range(pred_count)
        ]
        candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

        used_g: set[int] = set()
        used_p: set[int] = set()
        result: List[Tuple[int, int, float]] = []

        for score, g, p in candidates:
            if g in used_g or p in used_p:
                continue
            used_g.add(g)
            used_p.add(p)
            result.append((g, p, score))

        return result

    # DP: prediction-mask -> (total score, assignments)
    states: Dict[int, Tuple[float, List[Tuple[int, int, float]]]] = {
        0: (0.0, [])
    }

    for g in range(gold_count):
        next_states = dict(states)

        for mask, (total, assignments) in states.items():
            for p in range(pred_count):
                bit = 1 << p
                if mask & bit:
                    continue

                new_mask = mask | bit
                new_total = total + scores[g][p]
                candidate = (
                    new_total,
                    assignments + [(g, p, scores[g][p])],
                )

                existing = next_states.get(new_mask)
                if existing is None or candidate[0] > existing[0]:
                    next_states[new_mask] = candidate

        states = next_states

    best = max(
        states.values(),
        key=lambda item: (item[0], len(item[1])),
    )
    return best[1]


def match_events(
    gold_events: Sequence[Mapping[str, Any]],
    predicted_events: Sequence[Mapping[str, Any]],
    *,
    minimum_similarity: float = 0.45,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    used_gold: set[int] = set()
    used_pred: set[int] = set()

    gold_id_index = {
        _gold_event_id(event): index
        for index, event in enumerate(gold_events)
        if _gold_event_id(event)
    }

    # Prefer explicit gold-event links when present.
    for p, event in enumerate(predicted_events):
        event_id = _gold_event_id(event)
        if not event_id or event_id not in gold_id_index:
            continue

        g = gold_id_index[event_id]
        if g in used_gold:
            continue

        used_gold.add(g)
        used_pred.add(p)
        matches.append({
            "gold_index": g,
            "predicted_index": p,
            "score": 1.0,
            "method": "explicit_gold_event_id",
        })

    remaining_gold = [
        g for g in range(len(gold_events))
        if g not in used_gold
    ]
    remaining_pred = [
        p for p in range(len(predicted_events))
        if p not in used_pred
    ]

    if not remaining_gold or not remaining_pred:
        return matches

    score_matrix = [
        [
            _event_score(gold_events[g], predicted_events[p])
            for p in remaining_pred
        ]
        for g in remaining_gold
    ]

    for local_g, local_p, score in _best_assignment(score_matrix):
        if score < minimum_similarity:
            continue

        matches.append({
            "gold_index": remaining_gold[local_g],
            "predicted_index": remaining_pred[local_p],
            "score": score,
            "method": "structural_similarity",
        })

    return matches


def _relationships(
    event: Mapping[str, Any],
) -> set[Tuple[str, str, str]]:
    value = event.get("relationships", [])
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list):
        return set()

    result: set[Tuple[str, str, str]] = set()

    for item in value:
        if not isinstance(item, Mapping):
            continue

        signature = (
            _canonical(item.get("subject") or item.get("source")),
            _canonical(item.get("predicate") or item.get("relation")),
            _canonical(item.get("object") or item.get("target")),
        )

        if all(signature):
            result.add(signature)

    return result


def _metrics(event: Mapping[str, Any]) -> List[Dict[str, Any]]:
    value = event.get("metrics")
    if value is None:
        value = event.get("economic_indicators", [])

    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list):
        return []

    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
    ]


def _metric_subject(metric: Mapping[str, Any]) -> str:
    return _canonical(metric.get("subject") or metric.get("name"))


def _metric_period(metric: Mapping[str, Any]) -> str:
    return _canonical(metric.get("period"))


def _match_metrics(
    gold_metrics: Sequence[Mapping[str, Any]],
    predicted_metrics: Sequence[Mapping[str, Any]],
) -> List[Tuple[int, int]]:
    """
    Match by exact normalized magnitude plus best subject/unit agreement.
    This intentionally does not convert scales.
    """
    used_pred: set[int] = set()
    matches: List[Tuple[int, int]] = []

    for g, gold in enumerate(gold_metrics):
        gold_value, gold_unit = _metric_value_unit(gold)
        gold_subject = _metric_subject(gold)

        best_p: Optional[int] = None
        best_score = -1.0

        for p, predicted in enumerate(predicted_metrics):
            if p in used_pred:
                continue

            pred_value, pred_unit = _metric_value_unit(predicted)
            if not gold_value or pred_value != gold_value:
                continue

            subject_score = _jaccard(
                _tokens(gold_subject),
                _tokens(_metric_subject(predicted)),
            )
            unit_score = 1.0 if gold_unit == pred_unit else 0.0
            score = 0.7 * unit_score + 0.3 * subject_score

            if score > best_score:
                best_score = score
                best_p = p

        if best_p is not None:
            used_pred.add(best_p)
            matches.append((g, best_p))

    return matches


def _article_text(record: Mapping[str, Any]) -> str:
    direct = (
        _clean(record.get("article_text"))
        or _clean(record.get("source_text"))
        or _clean(record.get("raw_text"))
    )

    if direct:
        return " ".join(direct.casefold().split())

    combined = "\n\n".join(
        value
        for value in (
            _clean(record.get("article_title") or record.get("title")),
            _clean(record.get("article_summary") or record.get("summary")),
            _clean(
                record.get("article_body")
                or record.get("body")
                or record.get("content")
            ),
        )
        if value
    )

    return " ".join(combined.casefold().split())


def _snippets(event: Mapping[str, Any]) -> List[str]:
    result: List[str] = []

    general = event.get("evidence_snippets", [])
    if isinstance(general, str):
        general = [general]
    if isinstance(general, list):
        result.extend(
            _clean(item)
            for item in general
            if _clean(item)
        )

    date_snippet = _clean(event.get("event_date_evidence_snippet"))
    if date_snippet:
        result.append(date_snippet)

    for metric in _metrics(event):
        snippet = _clean(metric.get("evidence_snippet"))
        if snippet:
            result.append(snippet)

    relationships = event.get("relationships", [])
    if isinstance(relationships, Mapping):
        relationships = [relationships]

    if isinstance(relationships, list):
        for relationship in relationships:
            if isinstance(relationship, Mapping):
                snippet = _clean(
                    relationship.get("evidence_snippet")
                )
                if snippet:
                    result.append(snippet)

    return list(dict.fromkeys(result))


def evaluate_extraction_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    *,
    minimum_event_similarity: float = 0.45,
) -> Dict[str, Any]:
    gold_by_source = _index_sources(gold_records)
    pred_by_source = _index_sources(prediction_records)

    source_ids = sorted(
        set(gold_by_source) | set(pred_by_source)
    )

    gold_event_count = 0
    predicted_event_count = 0
    matched_event_count = 0
    exact_count_sources = 0

    match_scores: List[float] = []
    match_methods: Counter[str] = Counter()

    event_type_correct = event_type_total = 0
    claim_correct = claim_total = 0
    temporal_correct = temporal_total = 0
    date_correct = date_total = 0

    entity_counts = {
        category: {"tp": 0, "fp": 0, "fn": 0}
        for category in ENTITY_CATEGORIES
    }

    relationship_tp = relationship_fp = relationship_fn = 0

    metric_tp = metric_fp = metric_fn = 0
    metric_unit_correct = 0
    metric_period_correct = 0
    metric_matched = 0

    valid_snippets = 0
    evaluated_snippets = 0

    per_source: List[Dict[str, Any]] = []

    for source_id in source_ids:
        gold_source = gold_by_source.get(source_id, {})
        pred_source = pred_by_source.get(source_id, {})

        gold_events = _events(gold_source)
        pred_events = _events(pred_source)

        gold_event_count += len(gold_events)
        predicted_event_count += len(pred_events)

        if len(gold_events) == len(pred_events):
            exact_count_sources += 1

        matches = match_events(
            gold_events,
            pred_events,
            minimum_similarity=minimum_event_similarity,
        )

        matched_event_count += len(matches)

        matched_gold = {m["gold_index"] for m in matches}
        matched_pred = {m["predicted_index"] for m in matches}

        source_text = _article_text(
            gold_source if gold_source else pred_source
        )

        for match in matches:
            gold = gold_events[match["gold_index"]]
            pred = pred_events[match["predicted_index"]]

            match_scores.append(float(match["score"]))
            match_methods[match["method"]] += 1

            gold_type = _canonical(gold.get("event_type"))
            if gold_type:
                event_type_total += 1
                event_type_correct += int(
                    gold_type == _canonical(pred.get("event_type"))
                )

            gold_claim = _normalize_enum(gold.get("claim_status"))
            if gold_claim:
                claim_total += 1
                claim_correct += int(
                    gold_claim == _normalize_enum(pred.get("claim_status"))
                )

            gold_temporal = _normalize_enum(gold.get("temporal_status"))
            if gold_temporal:
                temporal_total += 1
                temporal_correct += int(
                    gold_temporal
                    == _normalize_enum(pred.get("temporal_status"))
                )

            gold_date = _normalize_date_text(
                gold.get("event_date_text")
            )
            if gold_date:
                date_total += 1
                date_correct += int(
                    gold_date
                    == _normalize_date_text(pred.get("event_date_text"))
                )

            for category in ENTITY_CATEGORIES:
                gold_entities = _entity_values(gold, category)
                pred_entities = _entity_values(pred, category)

                entity_counts[category]["tp"] += len(
                    gold_entities & pred_entities
                )
                entity_counts[category]["fp"] += len(
                    pred_entities - gold_entities
                )
                entity_counts[category]["fn"] += len(
                    gold_entities - pred_entities
                )

            gold_rel = _relationships(gold)
            pred_rel = _relationships(pred)

            relationship_tp += len(gold_rel & pred_rel)
            relationship_fp += len(pred_rel - gold_rel)
            relationship_fn += len(gold_rel - pred_rel)

            gold_metrics = _metrics(gold)
            pred_metrics = _metrics(pred)
            metric_matches = _match_metrics(
                gold_metrics,
                pred_metrics,
            )

            metric_tp += len(metric_matches)
            metric_fp += len(pred_metrics) - len(metric_matches)
            metric_fn += len(gold_metrics) - len(metric_matches)
            metric_matched += len(metric_matches)

            for g_index, p_index in metric_matches:
                gold_metric = gold_metrics[g_index]
                pred_metric = pred_metrics[p_index]

                _, gold_unit = _metric_value_unit(gold_metric)
                _, pred_unit = _metric_value_unit(pred_metric)

                metric_unit_correct += int(
                    gold_unit == pred_unit
                )
                metric_period_correct += int(
                    _metric_period(gold_metric)
                    == _metric_period(pred_metric)
                )

            if source_text:
                for snippet in _snippets(pred):
                    evaluated_snippets += 1
                    normalized = " ".join(
                        snippet.casefold().split()
                    )
                    valid_snippets += int(
                        normalized in source_text
                    )

        per_source.append({
            "source_id": source_id,
            "gold_event_count": len(gold_events),
            "predicted_event_count": len(pred_events),
            "matched_event_count": len(matches),
            "missed_event_count": len(gold_events) - len(matched_gold),
            "extra_event_count": len(pred_events) - len(matched_pred),
            "exact_event_count_match": len(gold_events) == len(pred_events),
            "matches": [
                {
                    "gold_index": m["gold_index"],
                    "predicted_index": m["predicted_index"],
                    "score": round(float(m["score"]), 4),
                    "method": m["method"],
                }
                for m in matches
            ],
        })

    event_metrics = _prf(
        matched_event_count,
        predicted_event_count - matched_event_count,
        gold_event_count - matched_event_count,
    )

    entity_per_category: Dict[str, Any] = {}
    entity_tp = entity_fp = entity_fn = 0

    for category, counts in entity_counts.items():
        entity_per_category[category] = _prf(
            counts["tp"],
            counts["fp"],
            counts["fn"],
        )
        entity_tp += counts["tp"]
        entity_fp += counts["fp"]
        entity_fn += counts["fn"]

    return {
        "source_count": len(source_ids),
        "gold_source_count": len(gold_by_source),
        "predicted_source_count": len(pred_by_source),

        "gold_event_count": gold_event_count,
        "predicted_event_count": predicted_event_count,
        "matched_event_count": matched_event_count,
        "missed_event_count": gold_event_count - matched_event_count,
        "extra_event_count": predicted_event_count - matched_event_count,

        "event_precision": event_metrics["precision"],
        "event_recall": event_metrics["recall"],
        "event_f1": event_metrics["f1"],

        "article_event_count_exact_match_rate": _round(
            _safe_divide(exact_count_sources, len(source_ids))
        ),
        "mean_event_match_score": _round(
            (
                sum(match_scores) / len(match_scores)
                if match_scores
                else None
            )
        ),
        "event_match_method_distribution": dict(match_methods),

        "event_type_accuracy": _round(
            _safe_divide(event_type_correct, event_type_total)
        ),
        "claim_status_accuracy": _round(
            _safe_divide(claim_correct, claim_total)
        ),
        "temporal_status_accuracy": _round(
            _safe_divide(temporal_correct, temporal_total)
        ),
        "event_date_accuracy": _round(
            _safe_divide(date_correct, date_total)
        ),

        "entity_micro": _prf(entity_tp, entity_fp, entity_fn),
        "entity_per_category": entity_per_category,

        "relationship_micro": _prf(
            relationship_tp,
            relationship_fp,
            relationship_fn,
        ),

        "metric_micro": _prf(
            metric_tp,
            metric_fp,
            metric_fn,
        ),
        "metric_matched_count": metric_matched,
        "metric_unit_equivalence_accuracy": _round(
            _safe_divide(
                metric_unit_correct,
                metric_matched,
            )
        ),
        "metric_period_accuracy": _round(
            _safe_divide(
                metric_period_correct,
                metric_matched,
            )
        ),

        "evidence_snippet_source_validity_rate": _round(
            _safe_divide(
                valid_snippets,
                evaluated_snippets,
            )
        ),
        "evaluated_evidence_snippet_count": evaluated_snippets,

        "per_source": per_source,

        "interpretation_notes": {
            "event_matching": (
                "Explicit gold_event_id mapping is preferred. "
                "Otherwise deterministic structural similarity is used."
            ),
            "metric_matching": (
                "Lexical numeric/unit equivalence is allowed; "
                "arithmetic scale conversion is not."
            ),
            "snippet_validity": (
                "Source occurrence establishes traceability, "
                "not independent truthfulness."
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
    gold_records = load_jsonl(gold_path)

    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for extraction evaluation."
        )

    prediction_records = load_jsonl(predictions_path)

    minimum_event_similarity = float(
        kwargs.pop("minimum_event_similarity", 0.45)
    )

    if kwargs:
        raise TypeError(
            "Unknown extraction evaluation option(s): "
            + ", ".join(sorted(kwargs))
        )

    return evaluate_extraction_records(
        gold_records,
        prediction_records,
        minimum_event_similarity=minimum_event_similarity,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate ExtractionAgent predictions."
    )
    parser.add_argument("--gold", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument(
        "--minimum-event-similarity",
        type=float,
        default=0.45,
    )
    parser.add_argument("--output", default="")

    args = parser.parse_args()

    result = run_evaluation(
        gold_path=args.gold,
        predictions_path=args.predictions,
        minimum_event_similarity=args.minimum_event_similarity,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()