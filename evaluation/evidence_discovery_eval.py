"""
Offline evaluation for EvidenceDiscoveryAgent.

Recommended location:
    evaluation/evidence_discovery_eval.py

Purpose
-------
EvidenceDiscoveryAgent is not just a retriever. It performs a bounded research
loop:

    grounding gap
        ↓
    query planning
        ↓
    web search
        ↓
    source fetch
        ↓
    evidence admission
        ↓
    material-information classification
        ↓
    grounding reassessment / stop decision

The evaluator therefore measures the complete discovery behavior rather than
only search recall.

Preferred evaluator contract
----------------------------
This module exposes:

    run_evaluation(
        gold_path: str | Path,
        predictions_path: str | Path | None = None,
        **kwargs,
    ) -> dict

so it can be registered in evaluation_runner.py as the
`evidence_discovery` stage.

Gold JSONL
----------
One discovery case per line.

Example:

{
  "case_id": "ED-001",
  "insight_type": "prospective",

  "required_search_modes": [
    "premise_support",
    "analogical_mechanism",
    "feasibility",
    "counterevidence"
  ],

  "gold_evidence_by_role": {
    "direct_premise_evidence": ["FACT-1", "FACT-2"],
    "analogical_mechanism_evidence": ["FACT-3"],
    "feasibility_evidence": ["FACT-4"],
    "counterevidence": [],
    "counter_incentive_evidence": ["COUNTER-1"],
    "alternative_explanation_evidence": []
  },

  "relevant_result_ids": [
    "RESULT-1",
    "RESULT-3",
    "RESULT-6"
  ],

  "relevant_source_ids": [
    "SOURCE-1",
    "SOURCE-3",
    "SOURCE-6"
  ],

  "expected_resolved_premise_ids": [
    "PREMISE-1",
    "PREMISE-2"
  ],

  "materiality_gold": {
    "FACT-3": {
      "classification": "materially_new",
      "materially_new": true
    },
    "FACT-5": {
      "classification": "duplicate_underlying_source",
      "materially_new": false
    }
  },

  "source_independence_expectations": [
    {
      "left_evidence_id": "FACT-3",
      "right_evidence_id": "FACT-5",
      "relationship": "same_origin"
    },
    {
      "left_evidence_id": "FACT-3",
      "right_evidence_id": "FACT-7",
      "relationship": "unknown"
    }
  ],

  "query_requirements": {
    "counterevidence": [
      {
        "must_include_any": [
          ["barrier", "blocker", "constraint", "failed"],
          ["why", "not"]
        ]
      }
    ]
  },

  "acceptable_stop_reasons": [
    "all_requested_gaps_resolved",
    "search_saturation_no_material_information_gain"
  ],

  "case_tags": [
    "prospective",
    "counterevidence_required"
  ]
}

Prediction JSONL
----------------
The preferred prediction is the raw dictionary returned by
EvidenceDiscoveryAgent.discover(...), with `case_id` added by the evaluation
harness:

{
  "case_id": "ED-001",
  "discovery_id": "DISCOVERY-...",
  "insight_id": "INSIGHT-...",
  "status": "complete",
  "stop_reason": "all_requested_gaps_resolved",
  "discovered_evidence": [...],
  "discovered_evidence_ids": [...],
  "evidence_materiality": {...},
  "resolved_premise_ids": [...],
  "search_manifest": [...],
  "planned_queries": [...],
  "usage": {...},
  "policy_snapshot": {...},
  "metadata": {...}
}

Core metrics
------------
Evidence admission:
- overall evidence precision / recall / F1;
- role-specific precision / recall / F1;
- case-level support hit rate;
- counterevidence hit rate;
- counter-incentive hit rate;
- analogical-mechanism hit rate;
- feasibility-evidence hit rate;
- alternative-explanation hit rate.

Search:
- required search-mode recall;
- case-level required-mode completion rate;
- query duplication rate;
- labelled query-requirement pass rate;
- irrelevant search-result rate;
- irrelevant fetched-source rate.

Material information:
- materiality classification accuracy;
- materially-new binary accuracy;
- duplicate-underlying-source detection recall;
- independent-corroboration classification precision.

Source independence:
- pairwise relationship accuracy;
- false-independence rate;
- unknown-preservation accuracy.

Stopping / saturation:
- acceptable stop-reason rate;
- saturation decision accuracy when explicitly labelled;
- unresolved-gap correctness when gold gap IDs are provided.

Budget / contract:
- query-count policy violation rate;
- source-fetch policy violation rate;
- search-round policy violation rate;
- search-result-as-evidence contract violation rate;
- open-web-overclaim rate.

Important epistemic distinctions
--------------------------------
SEARCH RESULT
    != SOURCE
    != EVIDENCE
    != SUPPORTED PREMISE
    != SUPPORTED INFERENCE

A high retrieval score cannot compensate for:
- admitting irrelevant evidence;
- missing counterevidence;
- falsely claiming source independence;
- silently treating snippets as evidence;
- claiming exhaustive coverage of the open web.

Source independence semantics
-----------------------------
Different non-empty independence-group IDs do NOT prove independence.

Gold labels use:
    same_origin
    independent
    unknown

The evaluator does not infer pairwise independence from unequal group IDs.
It evaluates only an explicit relationship surfaced by the prediction, such as
`evidence_materiality[evidence_id].source_independence` against its
`nearest_evidence_id`, or an explicit `source_independence_assessments` list.

This is deliberate because unknown independence must remain unknown unless a
provenance process actually establishes independence.

Prospective reasoning
---------------------
Prospective cases should normally include:
- premise support;
- analogical mechanism search;
- feasibility / magnitude / blocker search;
- counter-incentive / counterevidence search.

Absence of exact precedent is not automatically an error.

Offline scope
-------------
These metrics measure behavior against a labelled gold set. They do NOT prove
that every relevant internet source was found.

Search saturation means:
    no materially useful information gain within the configured search process

not:
    nothing else exists on the internet.

No web/API/paid service is used by this evaluator.
No private chain-of-thought is required or stored.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


EVALUATOR_VERSION = "evidence-discovery-eval-v1.0"

ALLOWED_SEARCH_MODES = {
    "premise_support",
    "direct_precedent",
    "analogical_mechanism",
    "feasibility",
    "counterevidence",
    "alternative_explanation",
}

ALLOWED_EVIDENCE_ROLES = {
    "direct_premise_evidence",
    "analogical_mechanism_evidence",
    "feasibility_evidence",
    "counterevidence",
    "counter_incentive_evidence",
    "alternative_explanation_evidence",
}

MATERIALITY_CLASSES = {
    "materially_new",
    "material_counterevidence",
    "independent_corroboration",
    "duplicate_underlying_source",
    "exact_duplicate_content",
    "corroboration_independence_unknown",
    "same_evidence_id",
}

INDEPENDENCE_ALIASES = {
    "same_origin": "same_origin",
    "same_underlying_origin": "same_origin",
    "same_underlying_source": "same_origin",
    "duplicate_underlying_source": "same_origin",
    "same_group": "same_origin",

    "independent": "independent",
    "independent_source": "independent",
    "independent_sources": "independent",
    "independent_corroboration": "independent",

    "unknown": "unknown",
    "unresolved": "unknown",
    "independence_unknown": "unknown",
    "corroboration_independence_unknown": "unknown",
    "group_known": "unknown",
    "": "unknown",
}

SATURATION_STOP_REASON = (
    "search_saturation_no_material_information_gain"
)

EXPECTED_SEARCH_RESULT_EVIDENCE_FLAG = False

OVERCLAIMED_COMPLETENESS_VALUES = {
    "complete_open_web",
    "exhaustive",
    "exhaustive_web_search",
    "all_relevant_sources_found",
    "complete",
    "guaranteed_complete",
}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _key(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)

    while "__" in text:
        text = text.replace("__", "_")

    return text.strip("_")


def _id(value: Any) -> str:
    return _clean(value).upper()


def _id_list(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, (list, tuple, set)):
        return []

    out: List[str] = []
    seen: Set[str] = set()

    for item in value:
        if isinstance(item, Mapping):
            item = (
                item.get("evidence_id")
                or item.get("source_id")
                or item.get("result_id")
                or item.get("premise_id")
                or item.get("gap_id")
                or item.get("id")
                or ""
            )

        normalized = _id(item)

        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)

    return out


def _safe_div(
    numerator: int | float,
    denominator: int | float,
) -> Optional[float]:
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _round(
    value: Optional[float],
    digits: int = 4,
) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _mean(
    values: Sequence[float],
) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _prf(
    predicted: Set[str],
    gold: Set[str],
) -> Dict[str, Optional[float]]:
    tp = len(
        predicted
        & gold
    )
    fp = len(
        predicted
        - gold
    )
    fn = len(
        gold
        - predicted
    )

    precision = _safe_div(
        tp,
        tp + fp,
    )
    recall = _safe_div(
        tp,
        tp + fn,
    )

    if (
        precision is None
        or recall is None
        or (
            precision
            + recall
        ) == 0
    ):
        f1 = None
    else:
        f1 = (
            2.0
            * precision
            * recall
            / (
                precision
                + recall
            )
        )

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _round(
            precision
        ),
        "recall": _round(
            recall
        ),
        "f1": _round(
            f1
        ),
    }


def _normalize_relationship(
    value: Any,
) -> str:
    normalized = _key(
        value
    )

    return INDEPENDENCE_ALIASES.get(
        normalized,
        normalized or "unknown",
    )


def _normalize_tags(
    value: Any,
) -> List[str]:
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, (list, tuple, set)):
        return []

    result = []
    seen = set()

    for item in value:
        normalized = _key(
            item
        )

        if normalized and normalized not in seen:
            seen.add(
                normalized
            )
            result.append(
                normalized
            )

    return result


def load_jsonl(
    path: str | Path,
) -> List[Dict[str, Any]]:
    path = Path(
        path
    )

    rows: List[
        Dict[str, Any]
    ] = []

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
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc

            if not isinstance(
                parsed,
                dict,
            ):
                raise ValueError(
                    f"Line {line_number} of {path} must contain a JSON object."
                )

            rows.append(
                parsed
            )

    return rows


def _case_id(
    record: Mapping[str, Any],
) -> str:
    return _clean(
        record.get("case_id")
        or record.get("query_id")
        or record.get("evaluation_id")
        or record.get("id")
        or record.get("discovery_id")
    )


def _index_cases(
    records: Sequence[
        Mapping[str, Any]
    ],
    *,
    source_name: str,
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for index, record in enumerate(
        records
    ):
        if not isinstance(
            record,
            Mapping,
        ):
            raise ValueError(
                f"{source_name} record {index} must be an object."
            )

        case_id = _case_id(
            record
        )

        if not case_id:
            raise ValueError(
                f"{source_name} record {index} is missing case_id."
            )

        if case_id in indexed:
            raise ValueError(
                f"Duplicate {source_name} case_id={case_id!r}."
            )

        indexed[
            case_id
        ] = dict(
            record
        )

    return indexed


# ---------------------------------------------------------------------------
# Gold normalization
# ---------------------------------------------------------------------------


def _gold_evidence_by_role(
    gold: Mapping[str, Any],
) -> Dict[str, Set[str]]:
    raw = (
        gold.get(
            "gold_evidence_by_role"
        )
        or gold.get(
            "relevant_evidence_by_role"
        )
        or {}
    )

    if not isinstance(
        raw,
        Mapping,
    ):
        raise ValueError(
            f"{_case_id(gold)}: gold_evidence_by_role must be an object."
        )

    result = {
        role: set()
        for role in sorted(
            ALLOWED_EVIDENCE_ROLES
        )
    }

    for role, values in raw.items():
        normalized_role = _key(
            role
        )

        if normalized_role not in ALLOWED_EVIDENCE_ROLES:
            raise ValueError(
                f"{_case_id(gold)}: unsupported evidence role "
                f"{normalized_role!r}."
            )

        result[
            normalized_role
        ] = set(
            _id_list(
                values
            )
        )

    # Optional compact union form.
    unlabeled = set(
        _id_list(
            gold.get(
                "relevant_evidence_ids"
            )
            or []
        )
    )

    if unlabeled:
        union = set().union(
            *result.values()
        )

        # Preserve unlabeled relevant evidence by placing it in a special
        # aggregate-only bucket handled by _gold_all_evidence().
        result[
            "__unlabeled_relevant__"
        ] = (
            unlabeled
            - union
        )

    return result


def _gold_all_evidence(
    gold: Mapping[str, Any],
) -> Set[str]:
    by_role = _gold_evidence_by_role(
        gold
    )

    union: Set[str] = set()

    for values in by_role.values():
        union.update(
            values
        )

    return union


def _gold_required_modes(
    gold: Mapping[str, Any],
) -> Set[str]:
    modes = {
        _key(
            item
        )
        for item in (
            gold.get(
                "required_search_modes"
            )
            or gold.get(
                "expected_search_modes"
            )
            or []
        )
        if _key(
            item
        )
    }

    unsupported = (
        modes
        - ALLOWED_SEARCH_MODES
    )

    if unsupported:
        raise ValueError(
            f"{_case_id(gold)}: unsupported required search modes: "
            + ", ".join(
                sorted(
                    unsupported
                )
            )
        )

    return modes


def _gold_independence_expectations(
    gold: Mapping[str, Any],
) -> Dict[
    Tuple[str, str],
    str,
]:
    raw = (
        gold.get(
            "source_independence_expectations"
        )
        or []
    )

    if not isinstance(
        raw,
        list,
    ):
        raise ValueError(
            f"{_case_id(gold)}: source_independence_expectations "
            "must be a list."
        )

    result = {}

    for index, item in enumerate(
        raw
    ):
        if not isinstance(
            item,
            Mapping,
        ):
            raise ValueError(
                f"{_case_id(gold)}: source independence expectation "
                f"{index} must be an object."
            )

        left = _id(
            item.get(
                "left_evidence_id"
            )
            or item.get(
                "left_source_id"
            )
        )
        right = _id(
            item.get(
                "right_evidence_id"
            )
            or item.get(
                "right_source_id"
            )
        )

        relationship = _normalize_relationship(
            item.get(
                "relationship"
            )
            or item.get(
                "status"
            )
        )

        if not left or not right:
            raise ValueError(
                f"{_case_id(gold)}: source independence expectation "
                "requires left/right IDs."
            )

        if relationship not in {
            "same_origin",
            "independent",
            "unknown",
        }:
            raise ValueError(
                f"{_case_id(gold)}: unsupported independence relationship "
                f"{relationship!r}."
            )

        key = tuple(
            sorted(
                (
                    left,
                    right,
                )
            )
        )

        if (
            key in result
            and result[
                key
            ] != relationship
        ):
            raise ValueError(
                f"{_case_id(gold)}: conflicting independence labels for "
                f"{key}."
            )

        result[
            key
        ] = relationship

    return result


# ---------------------------------------------------------------------------
# Prediction extraction
# ---------------------------------------------------------------------------


def _predicted_evidence_records(
    prediction: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    raw = (
        prediction.get(
            "discovered_evidence"
        )
        or prediction.get(
            "accepted_evidence"
        )
        or prediction.get(
            "evidence"
        )
        or []
    )

    if not isinstance(
        raw,
        list,
    ):
        return []

    return [
        dict(
            item
        )
        for item in raw
        if isinstance(
            item,
            Mapping,
        )
    ]


def _evidence_id_from_record(
    record: Mapping[str, Any],
) -> str:
    return _id(
        record.get(
            "evidence_id"
        )
        or record.get(
            "fact_id"
        )
        or record.get(
            "metric_id"
        )
        or record.get(
            "triple_id"
        )
        or record.get(
            "id"
        )
    )


def _predicted_evidence_ids(
    prediction: Mapping[str, Any],
) -> Set[str]:
    explicit = set(
        _id_list(
            prediction.get(
                "discovered_evidence_ids"
            )
            or []
        )
    )

    for record in _predicted_evidence_records(
        prediction
    ):
        evidence_id = _evidence_id_from_record(
            record
        )

        if evidence_id:
            explicit.add(
                evidence_id
            )

    return explicit


def _predicted_evidence_by_role(
    prediction: Mapping[str, Any],
) -> Dict[str, Set[str]]:
    result = {
        role: set()
        for role in sorted(
            ALLOWED_EVIDENCE_ROLES
        )
    }

    for record in _predicted_evidence_records(
        prediction
    ):
        evidence_id = _evidence_id_from_record(
            record
        )

        if not evidence_id:
            continue

        role = _key(
            record.get(
                "evidence_role"
            )
            or record.get(
                "role"
            )
        )

        if role in ALLOWED_EVIDENCE_ROLES:
            result[
                role
            ].add(
                evidence_id
            )

    # Some harnesses may emit explicit role mapping.
    explicit = (
        prediction.get(
            "evidence_by_role"
        )
        or {}
    )

    if isinstance(
        explicit,
        Mapping,
    ):
        for role, ids in explicit.items():
            normalized_role = _key(
                role
            )

            if normalized_role in ALLOWED_EVIDENCE_ROLES:
                result[
                    normalized_role
                ].update(
                    _id_list(
                        ids
                    )
                )

    return result


def _queries(
    prediction: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    raw = (
        prediction.get(
            "planned_queries"
        )
        or []
    )

    if isinstance(
        raw,
        list,
    ) and raw:
        return [
            dict(
                item
            )
            for item in raw
            if isinstance(
                item,
                Mapping,
            )
        ]

    # Fall back to search_manifest.
    output = []

    manifest = (
        prediction.get(
            "search_manifest"
        )
        or []
    )

    if not isinstance(
        manifest,
        list,
    ):
        return []

    for round_record in manifest:
        if not isinstance(
            round_record,
            Mapping,
        ):
            continue

        round_queries = (
            round_record.get(
                "queries"
            )
            or round_record.get(
                "searches"
            )
            or []
        )

        if not isinstance(
            round_queries,
            list,
        ):
            continue

        for item in round_queries:
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            query = (
                item.get(
                    "query"
                )
            )

            if isinstance(
                query,
                Mapping,
            ):
                output.append(
                    dict(
                        query
                    )
                )
            else:
                output.append(
                    dict(
                        item
                    )
                )

    return output


def _executed_queries(
    prediction: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    output = []

    manifest = (
        prediction.get(
            "search_manifest"
        )
        or []
    )

    if not isinstance(
        manifest,
        list,
    ):
        return output

    for round_record in manifest:
        if not isinstance(
            round_record,
            Mapping,
        ):
            continue

        searches = (
            round_record.get(
                "queries"
            )
            or round_record.get(
                "searches"
            )
            or []
        )

        if not isinstance(
            searches,
            list,
        ):
            continue

        for item in searches:
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            query = (
                item.get(
                    "query"
                )
            )

            if isinstance(
                query,
                Mapping,
            ):
                normalized = dict(
                    query
                )
            else:
                normalized = dict(
                    item
                )

            status = _key(
                item.get(
                    "status"
                )
                or "complete"
            )

            if status not in {
                "not_run",
                "unexecuted",
                "skipped",
                "denied",
            }:
                output.append(
                    normalized
                )

    # If no executable manifest is available, planned queries are the best
    # available approximation.
    if not output:
        output = _queries(
            prediction
        )

    return output


def _query_mode(
    query: Mapping[str, Any],
) -> str:
    return _key(
        query.get(
            "mode"
        )
        or query.get(
            "search_mode"
        )
    )


def _query_text(
    query: Mapping[str, Any],
) -> str:
    value = query.get(
        "query"
    )

    if isinstance(
        value,
        Mapping,
    ):
        value = (
            value.get(
                "query"
            )
            or value.get(
                "text"
            )
        )

    return _clean(
        value
        or query.get(
            "text"
        )
    )


def _search_results(
    prediction: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    results = []

    manifest = (
        prediction.get(
            "search_manifest"
        )
        or []
    )

    if not isinstance(
        manifest,
        list,
    ):
        return results

    for round_record in manifest:
        if not isinstance(
            round_record,
            Mapping,
        ):
            continue

        searches = (
            round_record.get(
                "queries"
            )
            or round_record.get(
                "searches"
            )
            or []
        )

        if not isinstance(
            searches,
            list,
        ):
            continue

        for search in searches:
            if not isinstance(
                search,
                Mapping,
            ):
                continue

            raw = (
                search.get(
                    "results"
                )
                or search.get(
                    "search_results"
                )
                or []
            )

            if not isinstance(
                raw,
                list,
            ):
                continue

            for item in raw:
                if isinstance(
                    item,
                    Mapping,
                ):
                    results.append(
                        dict(
                            item
                        )
                    )

    return results


def _result_id(
    result: Mapping[str, Any],
) -> str:
    return _id(
        result.get(
            "result_id"
        )
        or result.get(
            "id"
        )
    )


def _fetched_source_ids(
    prediction: Mapping[str, Any],
) -> Set[str]:
    source_ids: Set[str] = set()

    manifest = (
        prediction.get(
            "search_manifest"
        )
        or []
    )

    if not isinstance(
        manifest,
        list,
    ):
        return source_ids

    for round_record in manifest:
        if not isinstance(
            round_record,
            Mapping,
        ):
            continue

        searches = (
            round_record.get(
                "queries"
            )
            or round_record.get(
                "searches"
            )
            or []
        )

        if not isinstance(
            searches,
            list,
        ):
            continue

        for search in searches:
            if not isinstance(
                search,
                Mapping,
            ):
                continue

            source_ids.update(
                _id_list(
                    search.get(
                        "fetched_source_ids"
                    )
                    or []
                )
            )

            fetches = (
                search.get(
                    "source_fetches"
                )
                or []
            )

            if isinstance(
                fetches,
                list,
            ):
                for fetch in fetches:
                    if not isinstance(
                        fetch,
                        Mapping,
                    ):
                        continue

                    source_id = _id(
                        fetch.get(
                            "source_id"
                        )
                        or fetch.get(
                            "fetched_source_id"
                        )
                    )

                    if source_id:
                        source_ids.add(
                            source_id
                        )

    return source_ids


def _predicted_materiality(
    prediction: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    raw = (
        prediction.get(
            "evidence_materiality"
        )
        or {}
    )

    if not isinstance(
        raw,
        Mapping,
    ):
        return {}

    result = {}

    for evidence_id, item in raw.items():
        if not isinstance(
            item,
            Mapping,
        ):
            continue

        normalized_id = _id(
            evidence_id
        )

        if not normalized_id:
            continue

        result[
            normalized_id
        ] = dict(
            item
        )

    return result


def _predicted_independence_pairs(
    prediction: Mapping[str, Any],
) -> Dict[
    Tuple[str, str],
    str,
]:
    """
    Extract only explicit pairwise assessments.

    Deliberately DOES NOT infer "independent" from unequal group IDs.
    """
    result = {}

    # Explicit assessments supplied by provenance-aware harness/service.
    explicit = (
        prediction.get(
            "source_independence_assessments"
        )
        or []
    )

    if isinstance(
        explicit,
        list,
    ):
        for item in explicit:
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            left = _id(
                item.get(
                    "left_evidence_id"
                )
                or item.get(
                    "left_source_id"
                )
            )
            right = _id(
                item.get(
                    "right_evidence_id"
                )
                or item.get(
                    "right_source_id"
                )
            )
            relationship = _normalize_relationship(
                item.get(
                    "relationship"
                )
                or item.get(
                    "status"
                )
            )

            if left and right:
                result[
                    tuple(
                        sorted(
                            (
                                left,
                                right,
                            )
                        )
                    )
                ] = relationship

    # EvidenceDiscoveryAgent materiality records compare one newly admitted
    # evidence record against its nearest prior evidence record.
    for evidence_id, item in _predicted_materiality(
        prediction
    ).items():
        nearest = _id(
            item.get(
                "nearest_evidence_id"
            )
        )

        if not nearest:
            continue

        relationship = _normalize_relationship(
            item.get(
                "source_independence"
            )
        )

        result[
            tuple(
                sorted(
                    (
                        evidence_id,
                        nearest,
                    )
                )
            )
        ] = relationship

    return result


# ---------------------------------------------------------------------------
# Query-quality labelling
# ---------------------------------------------------------------------------


def _query_requirement_passes(
    query_text: str,
    requirement: Mapping[str, Any],
) -> bool:
    text = query_text.casefold()

    must_include_all = (
        requirement.get(
            "must_include_all"
        )
        or []
    )

    if isinstance(
        must_include_all,
        str,
    ):
        must_include_all = [
            must_include_all
        ]

    for term in must_include_all:
        term = _clean(
            term
        ).casefold()

        if term and term not in text:
            return False

    must_include_any = (
        requirement.get(
            "must_include_any"
        )
        or []
    )

    # Accepted shapes:
    # ["grid", "power"] -> at least one
    # [["grid", "power"], ["barrier", "blocker"]] -> one from each group
    if isinstance(
        must_include_any,
        str,
    ):
        must_include_any = [
            must_include_any
        ]

    if isinstance(
        must_include_any,
        list,
    ):
        if (
            must_include_any
            and all(
                isinstance(
                    item,
                    (list, tuple, set),
                )
                for item in must_include_any
            )
        ):
            for group in must_include_any:
                normalized_group = [
                    _clean(
                        item
                    ).casefold()
                    for item in group
                    if _clean(
                        item
                    )
                ]

                if (
                    normalized_group
                    and not any(
                        term in text
                        for term in normalized_group
                    )
                ):
                    return False

        else:
            normalized = [
                _clean(
                    item
                ).casefold()
                for item in must_include_any
                if _clean(
                    item
                )
            ]

            if (
                normalized
                and not any(
                    term in text
                    for term in normalized
                )
            ):
                return False

    forbidden = (
        requirement.get(
            "must_not_include"
        )
        or requirement.get(
            "forbidden_terms"
        )
        or []
    )

    if isinstance(
        forbidden,
        str,
    ):
        forbidden = [
            forbidden
        ]

    for term in forbidden:
        term = _clean(
            term
        ).casefold()

        if term and term in text:
            return False

    regex_any = (
        requirement.get(
            "regex_any"
        )
        or []
    )

    if isinstance(
        regex_any,
        str,
    ):
        regex_any = [
            regex_any
        ]

    if regex_any:
        matched = False

        for pattern in regex_any:
            try:
                if re.search(
                    str(
                        pattern
                    ),
                    query_text,
                    flags=re.IGNORECASE,
                ):
                    matched = True
                    break
            except re.error as exc:
                raise ValueError(
                    f"Invalid query requirement regex {pattern!r}: {exc}"
                ) from exc

        if not matched:
            return False

    return True


def _evaluate_query_requirements(
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> Dict[str, Any]:
    raw_requirements = (
        gold.get(
            "query_requirements"
        )
        or {}
    )

    if not raw_requirements:
        return {
            "labelled": False,
            "requirement_count": 0,
            "passed_requirement_count": 0,
            "pass_rate": None,
            "details": [],
        }

    if not isinstance(
        raw_requirements,
        Mapping,
    ):
        raise ValueError(
            f"{_case_id(gold)}: query_requirements must be an object."
        )

    queries = _executed_queries(
        prediction
    )

    by_mode: Dict[
        str,
        List[str],
    ] = defaultdict(
        list
    )

    for query in queries:
        mode = _query_mode(
            query
        )
        text = _query_text(
            query
        )

        if mode and text:
            by_mode[
                mode
            ].append(
                text
            )

    details = []
    passed = 0
    total = 0

    for raw_mode, requirements in raw_requirements.items():
        mode = _key(
            raw_mode
        )

        if mode not in ALLOWED_SEARCH_MODES:
            raise ValueError(
                f"{_case_id(gold)}: unsupported query requirement mode "
                f"{mode!r}."
            )

        if isinstance(
            requirements,
            Mapping,
        ):
            requirements = [
                requirements
            ]

        if not isinstance(
            requirements,
            list,
        ):
            raise ValueError(
                f"{_case_id(gold)}: query requirements for {mode} must "
                "be a list/object."
            )

        mode_queries = by_mode.get(
            mode,
            [],
        )

        for index, requirement in enumerate(
            requirements
        ):
            if not isinstance(
                requirement,
                Mapping,
            ):
                raise ValueError(
                    f"{_case_id(gold)}: query requirement {mode}[{index}] "
                    "must be an object."
                )

            total += 1

            matched_query = ""

            for query_text in mode_queries:
                if _query_requirement_passes(
                    query_text,
                    requirement,
                ):
                    matched_query = query_text
                    break

            did_pass = bool(
                matched_query
            )

            if did_pass:
                passed += 1

            details.append(
                {
                    "mode": mode,
                    "requirement_index": index,
                    "passed": did_pass,
                    "matched_query": matched_query,
                }
            )

    return {
        "labelled": True,
        "requirement_count": total,
        "passed_requirement_count": passed,
        "pass_rate": _round(
            _safe_div(
                passed,
                total,
            )
        ),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Case evaluation
# ---------------------------------------------------------------------------


def evaluate_case(
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> Dict[str, Any]:
    case_id = _case_id(
        gold
    )

    if not case_id:
        raise ValueError(
            "Gold case is missing case_id."
        )

    gold_by_role = _gold_evidence_by_role(
        gold
    )
    gold_all = _gold_all_evidence(
        gold
    )

    predicted_all = _predicted_evidence_ids(
        prediction
    )
    predicted_by_role = _predicted_evidence_by_role(
        prediction
    )

    overall_evidence = _prf(
        predicted_all,
        gold_all,
    )

    role_metrics = {}

    for role in sorted(
        ALLOWED_EVIDENCE_ROLES
    ):
        role_metrics[
            role
        ] = _prf(
            predicted_by_role.get(
                role,
                set(),
            ),
            gold_by_role.get(
                role,
                set(),
            ),
        )

    required_modes = _gold_required_modes(
        gold
    )

    executed_modes = {
        _query_mode(
            query
        )
        for query in _executed_queries(
            prediction
        )
        if _query_mode(
            query
        )
    }

    mode_metrics = _prf(
        executed_modes,
        required_modes,
    )

    required_mode_complete = bool(
        not required_modes
        or required_modes <= executed_modes
    )

    # Query duplication.
    executed_queries = _executed_queries(
        prediction
    )

    query_keys = [
        (
            _query_mode(
                query
            ),
            _query_text(
                query
            ).casefold(),
        )
        for query in executed_queries
        if _query_text(
            query
        )
    ]

    duplicate_query_count = (
        len(
            query_keys
        )
        - len(
            set(
                query_keys
            )
        )
    )

    query_duplication_rate = _safe_div(
        duplicate_query_count,
        len(
            query_keys
        ),
    )

    query_requirement_metrics = _evaluate_query_requirements(
        gold,
        prediction,
    )

    # Search-result relevance.
    results = _search_results(
        prediction
    )

    result_ids = [
        _result_id(
            item
        )
        for item in results
        if _result_id(
            item
        )
    ]

    relevant_result_ids = set(
        _id_list(
            gold.get(
                "relevant_result_ids"
            )
            or []
        )
    )

    irrelevant_result_rate = None

    if relevant_result_ids:
        irrelevant_result_count = sum(
            1
            for result_id in result_ids
            if result_id
            not in relevant_result_ids
        )

        irrelevant_result_rate = _safe_div(
            irrelevant_result_count,
            len(
                result_ids
            ),
        )

    # Fetched-source relevance.
    fetched_source_ids = _fetched_source_ids(
        prediction
    )

    relevant_source_ids = set(
        _id_list(
            gold.get(
                "relevant_source_ids"
            )
            or []
        )
    )

    irrelevant_fetched_source_rate = None

    if relevant_source_ids:
        irrelevant_fetched_source_rate = _safe_div(
            len(
                fetched_source_ids
                - relevant_source_ids
            ),
            len(
                fetched_source_ids
            ),
        )

    # Materiality.
    materiality_gold_raw = (
        gold.get(
            "materiality_gold"
        )
        or gold.get(
            "gold_materiality"
        )
        or {}
    )

    if (
        materiality_gold_raw
        and not isinstance(
            materiality_gold_raw,
            Mapping,
        )
    ):
        raise ValueError(
            f"{case_id}: materiality_gold must be an object."
        )

    predicted_materiality = _predicted_materiality(
        prediction
    )

    materiality_total = 0
    materiality_correct = 0
    materially_new_total = 0
    materially_new_correct = 0
    duplicate_gold_total = 0
    duplicate_gold_detected = 0
    predicted_independent_total = 0
    predicted_independent_correct = 0
    materiality_errors = []

    for raw_evidence_id, expected in (
        materiality_gold_raw.items()
        if isinstance(
            materiality_gold_raw,
            Mapping,
        )
        else []
    ):
        evidence_id = _id(
            raw_evidence_id
        )

        if not isinstance(
            expected,
            Mapping,
        ):
            raise ValueError(
                f"{case_id}: materiality_gold[{raw_evidence_id}] must "
                "be an object."
            )

        predicted = predicted_materiality.get(
            evidence_id,
            {},
        )

        expected_class = _key(
            expected.get(
                "classification"
            )
        )
        predicted_class = _key(
            predicted.get(
                "classification"
            )
        )

        if expected_class:
            materiality_total += 1

            if predicted_class == expected_class:
                materiality_correct += 1
            else:
                materiality_errors.append(
                    {
                        "evidence_id": evidence_id,
                        "expected_classification": expected_class,
                        "predicted_classification": predicted_class,
                    }
                )

        if (
            "materially_new"
            in expected
        ):
            materially_new_total += 1

            if (
                bool(
                    predicted.get(
                        "materially_new"
                    )
                )
                == bool(
                    expected.get(
                        "materially_new"
                    )
                )
            ):
                materially_new_correct += 1

        if expected_class in {
            "duplicate_underlying_source",
            "exact_duplicate_content",
            "same_evidence_id",
        }:
            duplicate_gold_total += 1

            if predicted_class in {
                "duplicate_underlying_source",
                "exact_duplicate_content",
                "same_evidence_id",
            }:
                duplicate_gold_detected += 1

        if predicted_class == "independent_corroboration":
            predicted_independent_total += 1

            if expected_class == "independent_corroboration":
                predicted_independent_correct += 1

    materiality_accuracy = _safe_div(
        materiality_correct,
        materiality_total,
    )

    materially_new_accuracy = _safe_div(
        materially_new_correct,
        materially_new_total,
    )

    duplicate_detection_recall = _safe_div(
        duplicate_gold_detected,
        duplicate_gold_total,
    )

    independent_corroboration_precision = _safe_div(
        predicted_independent_correct,
        predicted_independent_total,
    )

    # Source independence.
    gold_pairs = _gold_independence_expectations(
        gold
    )
    predicted_pairs = _predicted_independence_pairs(
        prediction
    )

    pair_total = len(
        gold_pairs
    )
    pair_correct = 0
    false_independence = 0
    predicted_independence_claims_on_labelled_pairs = 0
    unknown_total = 0
    unknown_preserved = 0
    independence_errors = []

    for pair, expected_relationship in gold_pairs.items():
        predicted_relationship = predicted_pairs.get(
            pair,
            "unknown",
        )

        if predicted_relationship == expected_relationship:
            pair_correct += 1
        else:
            independence_errors.append(
                {
                    "pair": list(
                        pair
                    ),
                    "expected": expected_relationship,
                    "predicted": predicted_relationship,
                }
            )

        if predicted_relationship == "independent":
            predicted_independence_claims_on_labelled_pairs += 1

            if expected_relationship != "independent":
                false_independence += 1

        if expected_relationship == "unknown":
            unknown_total += 1

            if predicted_relationship == "unknown":
                unknown_preserved += 1

    independence_accuracy = _safe_div(
        pair_correct,
        pair_total,
    )

    false_independence_rate = _safe_div(
        false_independence,
        predicted_independence_claims_on_labelled_pairs,
    )

    unknown_preservation_accuracy = _safe_div(
        unknown_preserved,
        unknown_total,
    )

    # Premise resolution.
    expected_resolved_premises = set(
        _id_list(
            gold.get(
                "expected_resolved_premise_ids"
            )
            or []
        )
    )

    predicted_resolved_premises = set(
        _id_list(
            prediction.get(
                "resolved_premise_ids"
            )
            or []
        )
    )

    premise_resolution = (
        _prf(
            predicted_resolved_premises,
            expected_resolved_premises,
        )
        if expected_resolved_premises
        else None
    )

    # Optional remaining-gap labels.
    expected_remaining_gaps = set(
        _id_list(
            gold.get(
                "expected_remaining_gap_ids"
            )
            or []
        )
    )

    predicted_remaining_gaps = set()

    for gap in (
        prediction.get(
            "remaining_grounding_gaps"
        )
        or []
    ):
        if isinstance(
            gap,
            Mapping,
        ):
            gap_id = _id(
                gap.get(
                    "gap_id"
                )
                or gap.get(
                    "id"
                )
            )

            if gap_id:
                predicted_remaining_gaps.add(
                    gap_id
                )

    remaining_gap_metrics = (
        _prf(
            predicted_remaining_gaps,
            expected_remaining_gaps,
        )
        if (
            "expected_remaining_gap_ids"
            in gold
        )
        else None
    )

    # Stopping.
    stop_reason = _key(
        prediction.get(
            "stop_reason"
        )
    )

    acceptable_stop_reasons = {
        _key(
            item
        )
        for item in (
            gold.get(
                "acceptable_stop_reasons"
            )
            or []
        )
        if _key(
            item
        )
    }

    stop_reason_acceptable = (
        None
        if not acceptable_stop_reasons
        else stop_reason
        in acceptable_stop_reasons
    )

    saturation_expected = gold.get(
        "saturation_expected"
    )

    saturation_decision_correct = None

    if saturation_expected is not None:
        saturation_decision_correct = (
            (
                stop_reason
                == SATURATION_STOP_REASON
            )
            == bool(
                saturation_expected
            )
        )

    # Budget compliance.
    usage = (
        prediction.get(
            "usage"
        )
        or {}
    )
    policy = (
        prediction.get(
            "policy_snapshot"
        )
        or {}
    )

    if not isinstance(
        usage,
        Mapping,
    ):
        usage = {}

    if not isinstance(
        policy,
        Mapping,
    ):
        policy = {}

    def _policy_violation(
        usage_key: str,
        policy_key: str,
    ) -> Optional[bool]:
        if policy_key not in policy:
            return None

        try:
            used = float(
                usage.get(
                    usage_key,
                    0,
                )
            )
            limit = float(
                policy.get(
                    policy_key
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

        return used > limit + 1e-9

    query_limit_violation = _policy_violation(
        "queries_executed",
        "max_search_queries_per_insight",
    )
    source_fetch_limit_violation = _policy_violation(
        "sources_fetched",
        "max_sources_fetched_per_insight",
    )
    search_round_limit_violation = _policy_violation(
        "search_rounds",
        "max_search_rounds_per_insight",
    )

    # Contract checks.
    metadata = (
        prediction.get(
            "metadata"
        )
        or {}
    )

    if not isinstance(
        metadata,
        Mapping,
    ):
        metadata = {}

    search_results_are_evidence = metadata.get(
        "search_results_are_evidence"
    )

    search_result_evidence_contract_violation = (
        bool(
            search_results_are_evidence
        )
        if search_results_are_evidence
        is not None
        else False
    )

    completeness_claim = _key(
        metadata.get(
            "open_web_completeness_claim"
        )
        or metadata.get(
            "search_completeness_claim"
        )
    )

    open_web_overclaim = (
        completeness_claim
        in OVERCLAIMED_COMPLETENESS_VALUES
    )

    # Additional conservative contamination check: search result IDs should not
    # themselves appear as admitted evidence IDs unless the gold/evidence
    # pipeline intentionally reuses identifiers (which this architecture does
    # not).
    result_id_set = set(
        result_ids
    )

    result_evidence_id_overlap = sorted(
        predicted_all
        & result_id_set
    )

    if result_evidence_id_overlap:
        search_result_evidence_contract_violation = True

    # Case-level role hits.
    def _role_hit(
        role: str,
    ) -> Optional[bool]:
        gold_ids = gold_by_role.get(
            role,
            set(),
        )

        if not gold_ids:
            return None

        return bool(
            predicted_by_role.get(
                role,
                set(),
            )
            & gold_ids
        )

    counterevidence_gold = (
        gold_by_role.get(
            "counterevidence",
            set(),
        )
        | gold_by_role.get(
            "counter_incentive_evidence",
            set(),
        )
    )

    counterevidence_pred = (
        predicted_by_role.get(
            "counterevidence",
            set(),
        )
        | predicted_by_role.get(
            "counter_incentive_evidence",
            set(),
        )
    )

    counterevidence_hit = (
        None
        if not counterevidence_gold
        else bool(
            counterevidence_gold
            & counterevidence_pred
        )
    )

    case_tags = _normalize_tags(
        gold.get(
            "case_tags"
        )
        or []
    )

    return {
        "case_id": case_id,
        "insight_type": _key(
            gold.get(
                "insight_type"
            )
        ),
        "case_tags": case_tags,

        "evidence": {
            "overall": overall_evidence,
            "by_role": role_metrics,
            "support_hit": (
                None
                if not gold_all
                else bool(
                    predicted_all
                    & gold_all
                )
            ),
            "counterevidence_hit": counterevidence_hit,
            "counter_incentive_hit": _role_hit(
                "counter_incentive_evidence"
            ),
            "analogical_mechanism_hit": _role_hit(
                "analogical_mechanism_evidence"
            ),
            "feasibility_hit": _role_hit(
                "feasibility_evidence"
            ),
            "alternative_explanation_hit": _role_hit(
                "alternative_explanation_evidence"
            ),
        },

        "search": {
            "required_modes": sorted(
                required_modes
            ),
            "executed_modes": sorted(
                executed_modes
            ),
            "mode_metrics": mode_metrics,
            "required_mode_complete": required_mode_complete,
            "executed_query_count": len(
                query_keys
            ),
            "duplicate_query_count": duplicate_query_count,
            "query_duplication_rate": _round(
                query_duplication_rate
            ),
            "query_requirements": query_requirement_metrics,
            "search_result_count": len(
                result_ids
            ),
            "irrelevant_search_result_rate": _round(
                irrelevant_result_rate
            ),
            "fetched_source_count": len(
                fetched_source_ids
            ),
            "irrelevant_fetched_source_rate": _round(
                irrelevant_fetched_source_rate
            ),
        },

        "materiality": {
            "labelled_count": materiality_total,
            "classification_accuracy": _round(
                materiality_accuracy
            ),
            "materially_new_binary_accuracy": _round(
                materially_new_accuracy
            ),
            "duplicate_underlying_source_detection_recall": _round(
                duplicate_detection_recall
            ),
            "independent_corroboration_precision": _round(
                independent_corroboration_precision
            ),
            "errors": materiality_errors,
        },

        "source_independence": {
            "labelled_pair_count": pair_total,
            "relationship_accuracy": _round(
                independence_accuracy
            ),
            "false_independence_count": false_independence,
            "false_independence_rate": _round(
                false_independence_rate
            ),
            "unknown_pair_count": unknown_total,
            "unknown_preservation_accuracy": _round(
                unknown_preservation_accuracy
            ),
            "errors": independence_errors,
        },

        "premise_resolution": premise_resolution,
        "remaining_gaps": remaining_gap_metrics,

        "stopping": {
            "stop_reason": stop_reason,
            "acceptable_stop_reasons": sorted(
                acceptable_stop_reasons
            ),
            "stop_reason_acceptable": stop_reason_acceptable,
            "saturation_expected": saturation_expected,
            "saturation_decision_correct": (
                saturation_decision_correct
            ),
        },

        "budget_contract": {
            "query_limit_violation": query_limit_violation,
            "source_fetch_limit_violation": (
                source_fetch_limit_violation
            ),
            "search_round_limit_violation": (
                search_round_limit_violation
            ),
            "search_result_evidence_contract_violation": (
                search_result_evidence_contract_violation
            ),
            "result_evidence_id_overlap": (
                result_evidence_id_overlap
            ),
            "open_web_overclaim": open_web_overclaim,
        },
    }


# ---------------------------------------------------------------------------
# Aggregate evaluation
# ---------------------------------------------------------------------------


def _aggregate_prf(
    case_results: Sequence[
        Mapping[str, Any]
    ],
    path: Sequence[str],
) -> Dict[str, Optional[float]]:
    tp = 0
    fp = 0
    fn = 0

    for case in case_results:
        value: Any = case

        for key in path:
            if not isinstance(
                value,
                Mapping,
            ):
                value = {}
                break

            value = value.get(
                key,
                {},
            )

        if not isinstance(
            value,
            Mapping,
        ):
            continue

        tp += int(
            value.get(
                "tp",
                0,
            )
            or 0
        )
        fp += int(
            value.get(
                "fp",
                0,
            )
            or 0
        )
        fn += int(
            value.get(
                "fn",
                0,
            )
            or 0
        )

    precision = _safe_div(
        tp,
        tp + fp,
    )
    recall = _safe_div(
        tp,
        tp + fn,
    )

    if (
        precision is None
        or recall is None
        or (
            precision
            + recall
        ) == 0
    ):
        f1 = None
    else:
        f1 = (
            2.0
            * precision
            * recall
            / (
                precision
                + recall
            )
        )

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _round(
            precision
        ),
        "recall": _round(
            recall
        ),
        "f1": _round(
            f1
        ),
    }


def _boolean_rate(
    case_results: Sequence[
        Mapping[str, Any]
    ],
    path: Sequence[str],
) -> Dict[str, Any]:
    values: List[
        bool
    ] = []

    for case in case_results:
        value: Any = case

        for key in path:
            if not isinstance(
                value,
                Mapping,
            ):
                value = None
                break

            value = value.get(
                key
            )

        if isinstance(
            value,
            bool,
        ):
            values.append(
                value
            )

    return {
        "labelled_count": len(
            values
        ),
        "pass_count": sum(
            1
            for value in values
            if value
        ),
        "rate": _round(
            _safe_div(
                sum(
                    1
                    for value in values
                    if value
                ),
                len(
                    values
                ),
            )
        ),
    }


def _numeric_mean(
    case_results: Sequence[
        Mapping[str, Any]
    ],
    path: Sequence[str],
) -> Optional[float]:
    values = []

    for case in case_results:
        value: Any = case

        for key in path:
            if not isinstance(
                value,
                Mapping,
            ):
                value = None
                break

            value = value.get(
                key
            )

        if isinstance(
            value,
            (int, float),
        ) and not isinstance(
            value,
            bool,
        ):
            values.append(
                float(
                    value
                )
            )

    return _round(
        _mean(
            values
        )
    )


def _aggregate_role_metrics(
    case_results: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    result = {}

    for role in sorted(
        ALLOWED_EVIDENCE_ROLES
    ):
        result[
            role
        ] = _aggregate_prf(
            case_results,
            (
                "evidence",
                "by_role",
                role,
            ),
        )

    return result


def _tag_breakdown(
    case_results: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    by_tag: Dict[
        str,
        List[Mapping[str, Any]],
    ] = defaultdict(
        list
    )

    for case in case_results:
        for tag in case.get(
            "case_tags",
            [],
        ):
            by_tag[
                tag
            ].append(
                case
            )

    result = {}

    for tag, subset in sorted(
        by_tag.items()
    ):
        result[
            tag
        ] = {
            "case_count": len(
                subset
            ),
            "evidence": _aggregate_prf(
                subset,
                (
                    "evidence",
                    "overall",
                ),
            ),
            "required_mode_completion_rate": _boolean_rate(
                subset,
                (
                    "search",
                    "required_mode_complete",
                ),
            )[
                "rate"
            ],
            "counterevidence_hit_rate": _boolean_rate(
                subset,
                (
                    "evidence",
                    "counterevidence_hit",
                ),
            )[
                "rate"
            ],
            "acceptable_stop_reason_rate": _boolean_rate(
                subset,
                (
                    "stopping",
                    "stop_reason_acceptable",
                ),
            )[
                "rate"
            ],
            "false_independence_rate_mean": _numeric_mean(
                subset,
                (
                    "source_independence",
                    "false_independence_rate",
                ),
            ),
        }

    return result


def evaluate_predictions(
    gold_records: Sequence[
        Mapping[str, Any]
    ],
    prediction_records: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    gold_index = _index_cases(
        gold_records,
        source_name="gold",
    )
    prediction_index = _index_cases(
        prediction_records,
        source_name="prediction",
    )

    gold_ids = set(
        gold_index
    )
    prediction_ids = set(
        prediction_index
    )

    missing_prediction_ids = sorted(
        gold_ids
        - prediction_ids
    )
    extra_prediction_ids = sorted(
        prediction_ids
        - gold_ids
    )

    evaluated_ids = sorted(
        gold_ids
        & prediction_ids
    )

    cases = [
        evaluate_case(
            gold_index[
                case_id
            ],
            prediction_index[
                case_id
            ],
        )
        for case_id in evaluated_ids
    ]

    overall_evidence = _aggregate_prf(
        cases,
        (
            "evidence",
            "overall",
        ),
    )

    search_mode = _aggregate_prf(
        cases,
        (
            "search",
            "mode_metrics",
        ),
    )

    role_metrics = _aggregate_role_metrics(
        cases
    )

    # Aggregate labelled materiality.
    materiality_total = sum(
        int(
            case[
                "materiality"
            ][
                "labelled_count"
            ]
            or 0
        )
        for case in cases
    )

    materiality_correct_estimate = sum(
        int(
            round(
                (
                    case[
                        "materiality"
                    ][
                        "classification_accuracy"
                    ]
                    or 0.0
                )
                * (
                    case[
                        "materiality"
                    ][
                        "labelled_count"
                    ]
                    or 0
                )
            )
        )
        for case in cases
    )

    materiality_accuracy = _safe_div(
        materiality_correct_estimate,
        materiality_total,
    )

    # Aggregate independence pairs exactly from case diagnostics.
    independence_labelled = sum(
        int(
            case[
                "source_independence"
            ][
                "labelled_pair_count"
            ]
            or 0
        )
        for case in cases
    )

    independence_errors = sum(
        len(
            case[
                "source_independence"
            ][
                "errors"
            ]
        )
        for case in cases
    )

    independence_accuracy = _safe_div(
        (
            independence_labelled
            - independence_errors
        ),
        independence_labelled,
    )

    false_independence_count = sum(
        int(
            case[
                "source_independence"
            ][
                "false_independence_count"
            ]
            or 0
        )
        for case in cases
    )

    # Denominator for aggregate false-independence rate is reconstructed from
    # labelled pair outputs.
    predicted_independent_on_labelled = 0

    for case in cases:
        labelled = int(
            case[
                "source_independence"
            ][
                "labelled_pair_count"
            ]
            or 0
        )

        # We only have false count + pair diagnostics. Reconstruct by counting
        # prediction "independent" in errors plus correctly predicted independent
        # pairs from raw case errors is not available directly. For a robust
        # aggregate, report count and mean case rate rather than fabricate a
        # micro denominator.
        predicted_independent_on_labelled += 0

    metrics = {
        "case_count": len(
            gold_index
        ),
        "evaluated_case_count": len(
            cases
        ),
        "missing_prediction_count": len(
            missing_prediction_ids
        ),
        "extra_prediction_count": len(
            extra_prediction_ids
        ),

        "evidence": {
            "overall": overall_evidence,
            "by_role": role_metrics,
            "support_hit_rate": _boolean_rate(
                cases,
                (
                    "evidence",
                    "support_hit",
                ),
            ),
            "counterevidence_hit_rate": _boolean_rate(
                cases,
                (
                    "evidence",
                    "counterevidence_hit",
                ),
            ),
            "counter_incentive_hit_rate": _boolean_rate(
                cases,
                (
                    "evidence",
                    "counter_incentive_hit",
                ),
            ),
            "analogical_mechanism_hit_rate": _boolean_rate(
                cases,
                (
                    "evidence",
                    "analogical_mechanism_hit",
                ),
            ),
            "feasibility_hit_rate": _boolean_rate(
                cases,
                (
                    "evidence",
                    "feasibility_hit",
                ),
            ),
            "alternative_explanation_hit_rate": _boolean_rate(
                cases,
                (
                    "evidence",
                    "alternative_explanation_hit",
                ),
            ),
        },

        "search": {
            "required_mode_micro": search_mode,
            "required_mode_completion_rate": _boolean_rate(
                cases,
                (
                    "search",
                    "required_mode_complete",
                ),
            ),
            "mean_query_duplication_rate": _numeric_mean(
                cases,
                (
                    "search",
                    "query_duplication_rate",
                ),
            ),
            "mean_labelled_query_requirement_pass_rate": _numeric_mean(
                cases,
                (
                    "search",
                    "query_requirements",
                    "pass_rate",
                ),
            ),
            "mean_irrelevant_search_result_rate": _numeric_mean(
                cases,
                (
                    "search",
                    "irrelevant_search_result_rate",
                ),
            ),
            "mean_irrelevant_fetched_source_rate": _numeric_mean(
                cases,
                (
                    "search",
                    "irrelevant_fetched_source_rate",
                ),
            ),
        },

        "materiality": {
            "labelled_count": materiality_total,
            "classification_accuracy": _round(
                materiality_accuracy
            ),
            "mean_materially_new_binary_accuracy": _numeric_mean(
                cases,
                (
                    "materiality",
                    "materially_new_binary_accuracy",
                ),
            ),
            "mean_duplicate_underlying_source_detection_recall": _numeric_mean(
                cases,
                (
                    "materiality",
                    "duplicate_underlying_source_detection_recall",
                ),
            ),
            "mean_independent_corroboration_precision": _numeric_mean(
                cases,
                (
                    "materiality",
                    "independent_corroboration_precision",
                ),
            ),
        },

        "source_independence": {
            "labelled_pair_count": independence_labelled,
            "relationship_accuracy": _round(
                independence_accuracy
            ),
            "false_independence_count": false_independence_count,
            "mean_case_false_independence_rate": _numeric_mean(
                cases,
                (
                    "source_independence",
                    "false_independence_rate",
                ),
            ),
            "mean_unknown_preservation_accuracy": _numeric_mean(
                cases,
                (
                    "source_independence",
                    "unknown_preservation_accuracy",
                ),
            ),
        },

        "stopping": {
            "acceptable_stop_reason_rate": _boolean_rate(
                cases,
                (
                    "stopping",
                    "stop_reason_acceptable",
                ),
            ),
            "saturation_decision_accuracy": _boolean_rate(
                cases,
                (
                    "stopping",
                    "saturation_decision_correct",
                ),
            ),
        },

        "budget_contract": {
            "query_limit_violation_rate": _boolean_rate(
                cases,
                (
                    "budget_contract",
                    "query_limit_violation",
                ),
            ),
            "source_fetch_limit_violation_rate": _boolean_rate(
                cases,
                (
                    "budget_contract",
                    "source_fetch_limit_violation",
                ),
            ),
            "search_round_limit_violation_rate": _boolean_rate(
                cases,
                (
                    "budget_contract",
                    "search_round_limit_violation",
                ),
            ),
            "search_result_evidence_contract_violation_rate": _boolean_rate(
                cases,
                (
                    "budget_contract",
                    "search_result_evidence_contract_violation",
                ),
            ),
            "open_web_overclaim_rate": _boolean_rate(
                cases,
                (
                    "budget_contract",
                    "open_web_overclaim",
                ),
            ),
        },

        "by_case_tag": _tag_breakdown(
            cases
        ),
    }

    # For violation metrics, _boolean_rate().rate reports True-rate, which is
    # exactly the violation rate. For pass metrics, it reports pass rate.

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "metrics": metrics,
        "missing_prediction_ids": missing_prediction_ids,
        "extra_prediction_ids": extra_prediction_ids,
        "case_results": cases,
        "metadata": {
            "evaluation_scope": "labelled_gold_set_only",
            "proves_open_web_completeness": False,
            "search_result_equals_evidence": False,
            "different_group_ids_prove_independence": False,
            "preference_or_reasoning_score_used_as_truth": False,
            "authorizes_release": False,
            "private_chain_of_thought_required": False,
            "paid_service_used": False,
        },
    }


# ---------------------------------------------------------------------------
# Public runner contract
# ---------------------------------------------------------------------------


def run_evaluation(
    gold_path: str | Path,
    predictions_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Preferred EvaluationRunner-compatible entry point.

    `predictions_path` is required for this offline evaluator.
    """
    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for evidence discovery evaluation."
        )

    gold_records = load_jsonl(
        gold_path
    )
    prediction_records = load_jsonl(
        predictions_path
    )

    return evaluate_predictions(
        gold_records,
        prediction_records,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _json_dump(
    payload: Any,
    path: str | Path,
) -> None:
    path = Path(
        path
    )
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def main(
    argv: Optional[
        Sequence[str]
    ] = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate EvidenceDiscoveryAgent predictions against labelled gold."
        )
    )

    parser.add_argument(
        "--gold",
        required=True,
        help="Path to evidence_discovery_gold.jsonl",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to evidence-discovery prediction JSONL",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path",
    )

    args = parser.parse_args(
        argv
    )

    result = run_evaluation(
        args.gold,
        args.predictions,
    )

    if args.output:
        _json_dump(
            result,
            args.output,
        )
    else:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )