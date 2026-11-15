"""
Offline evaluation for the user-preference learning/ranking path.

Recommended location:
    evaluation/preference_eval.py

Purpose
-------
The product learns what kinds of insights a user finds useful while preserving
a hard epistemic boundary:

    USER PREFERENCE != FACTUAL EVIDENCE
    PREFERENCE SCORE != TRUTH PROBABILITY
    PREFERENCE SCORE != GROUNDING CONFIDENCE
    PREFERENCE SCORE != RELEASE AUTHORIZATION

This evaluator measures the preference path end to end:

    UserFeedbackService
            ↓
    UserPreferenceService
            ↓
    PreferenceDataService
            ↓
    PreferenceRankerTrainer
            ↓
    LocalPreferenceScorer
            ↓
    InsightRankerService

without requiring any web access, paid API, external training service, or
private chain-of-thought.

Evaluation dimensions
---------------------
1. Feedback interpretation
   - feedback-type extraction
   - sentiment
   - desired-depth / verbosity deltas
   - preferred reasoning patterns
   - avoided failure patterns
   - analytical lenses
   - recommended refinement actions
   - pairwise preferred/rejected target extraction

2. Preference profile
   - desired depth / verbosity
   - preferred / avoided pattern precision-recall-F1
   - analytical-lens precision-recall-F1
   - preferred/rejected insight ID precision-recall-F1
   - positive/negative example retention

3. Pointwise preference prediction
   - positive/negative/neutral classification accuracy
   - decisive accuracy
   - balanced accuracy
   - Brier score
   - log loss
   - mean positive / negative score

4. Pairwise preference ranking
   - chosen-over-rejected accuracy
   - tie rate
   - mean chosen-minus-rejected margin
   - pairwise log loss when a pairwise probability is supplied

5. Candidate ranking
   - acceptable top-1 rate
   - mean reciprocal rank
   - NDCG@K when relevance labels are supplied
   - required pairwise-order accuracy

6. Preference-data integrity
   - preference records cannot be grounding evidence
   - chosen/rejected pair cannot be the same insight
   - no split-group leakage across train/validation/test
   - test records must not be reported as optimization records
   - no factual evidence IDs/control-plane fields in preference context

7. Control-plane / epistemic contamination
   - no research_depth mutation
   - no policy or runtime modification
   - no release authorization
   - no truth/grounding semantics attached to preference score
   - no private chain-of-thought fields

Gold JSONL
----------
One labelled preference case per line. A case may label only the dimensions
that are relevant; unlabelled dimensions are omitted from aggregate
denominators.

Example feedback/profile case:

{
  "case_id": "PREF-001",
  "case_tags": ["feedback_mapping"],

  "expected_feedback_types": ["too_surface_level"],
  "expected_sentiment": "negative",
  "expected_desired_depth_delta": "increase",

  "expected_preferred_patterns": [
    "cross_event_synthesis",
    "mechanism",
    "second_order_effect"
  ],

  "expected_avoid_patterns": [
    "surface_level_summary",
    "fact_restatement"
  ],

  "expected_refinement_actions": [
    "expand_graph_context",
    "broaden_evidence_search",
    "refine_reasoning"
  ]
}

Example pairwise case:

{
  "case_id": "PREF-002",
  "case_tags": ["pairwise"],
  "expected_chosen_insight_id": "INSIGHT-3",
  "expected_rejected_insight_id": "INSIGHT-1"
}

Example ranking case:

{
  "case_id": "PREF-003",
  "case_tags": ["ranking"],

  "acceptable_top_insight_ids": ["INSIGHT-7"],

  "relevance_by_insight_id": {
    "INSIGHT-7": 3,
    "INSIGHT-2": 2,
    "INSIGHT-1": 0
  },

  "required_pairwise_preferences": [
    ["INSIGHT-7", "INSIGHT-2"],
    ["INSIGHT-2", "INSIGHT-1"]
  ]
}

Prediction JSONL
----------------
The evaluator accepts a flexible prediction object. Common fields:

{
  "case_id": "PREF-001",

  "structured_feedback": {
    "feedback_types": [...],
    "sentiment": "negative",
    "desired_depth_delta": "increase",
    "verbosity_delta": "",
    "preferred_patterns": [...],
    "avoid_patterns": [...],
    "analytical_lenses": [...],
    "recommended_refinement_actions": [...],
    "preferred_insight_ids": [...],
    "rejected_insight_ids": [...]
  },

  "preference_profile": {
    "desired_depth": "very_deep",
    "verbosity": "balanced",
    "preferred_patterns": [...],
    "avoid_patterns": [...],
    "analytical_lenses": [...],
    "preferred_insight_ids": [...],
    "rejected_insight_ids": [...],
    "positive_examples": [...],
    "negative_examples": [...]
  },

  "pointwise_score": 0.82,
  "predicted_label": "positive",

  "chosen_score": 0.76,
  "rejected_score": 0.31,
  "pairwise_probability": 0.81,

  "ranked_insights": [
    {"insight_id": "INSIGHT-7", "score": 0.84},
    {"insight_id": "INSIGHT-2", "score": 0.70}
  ],

  "preference_records": [...],

  "training_summary": {
    "training_record_ids": [...],
    "validation_record_ids": [...],
    "test_record_ids": [...]
  },

  "metadata": {
    "preference_data_is_grounding_evidence": false,
    "prediction_semantics":
      "learned_user_preference_tendency_not_truth_probability",
    "authorizes_release": false,
    "research_depth_modified_here": false
  }
}

Important:
----------
This evaluator measures agreement with labelled USER PREFERENCE targets.
It does not determine whether the preferred insight is factually true.

A user may prefer an unsupported insight. The downstream
InsightRankerService/grounding/release gates must still reject or downgrade it.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


EVALUATOR_VERSION = "preference-eval-v1.0"

ALLOWED_SENTIMENTS = {
    "positive",
    "negative",
    "mixed",
    "neutral",
}

ALLOWED_DELTAS = {
    "",
    "increase",
    "decrease",
    "keep",
    "unchanged",
}

ALLOWED_POINTWISE_LABELS = {
    "positive",
    "negative",
    "neutral",
}

ALLOWED_SPLITS = {
    "train",
    "validation",
    "test",
}

ALLOWED_PREFERENCE_RECORD_TYPES = {
    "pointwise_preference",
    "pairwise_preference",
    "quality_example",
}

QUALITY_PATTERNS = {
    "cross_event_synthesis",
    "mechanism",
    "second_order_effect",
    "structural_change",
    "non_obvious_constraint",
    "counterevidence",
    "alternative_explanation",
    "explicit_uncertainty",
    "concise_synthesis",
    "evidence_density",
    "prospective_mechanism",
    "feasibility_check",
}

AVOID_PATTERNS = {
    "fact_restatement",
    "surface_level_summary",
    "generic_recommendation",
    "obvious_implication",
    "unsupported_leap",
    "excessive_speculation",
    "repetition",
    "excessive_detail",
    "premature_certainty",
}

REFINEMENT_ACTIONS = {
    "retrieve_support",
    "retrieve_counterevidence",
    "expand_graph_context",
    "compare_baseline",
    "refine_reasoning",
    "broaden_evidence_search",
    "model_escalation",
}

PROHIBITED_CONTROL_OR_EVIDENCE_FIELDS = {
    "research_depth",
    "blueprint",
    "blueprint_id",
    "policy",
    "policy_registry",
    "execution_policy",
    "runtime_policy",
    "quality_policy",
    "release_policy",
    "release_gate",
    "release_authorized",
    "authorizes_release",
    "model_route",
    "model_routing",
    "search_budget",
    "token_budget",
    "cost_budget",
    "max_run_seconds",
    "max_search_rounds",
    "max_search_queries",
    "skip_grounding",
    "skip_red_team",
    "evidence_id",
    "evidence_ids",
    "fact_id",
    "fact_ids",
    "metric_id",
    "metric_ids",
    "triple_id",
    "triple_ids",
    "source_id",
    "source_ids",
}

PROHIBITED_PRIVATE_REASONING_FIELDS = {
    "chain_of_thought",
    "chainofthought",
    "cot",
    "scratchpad",
    "hidden_reasoning",
    "hiddenreasoning",
    "internal_monologue",
    "internalmonologue",
    "raw_reasoning",
    "rawreasoning",
    "private_reasoning",
    "privatereasoning",
    "reasoning_tokens",
    "reasoningtokens",
    "deliberation",
    "deliberation_trace",
    "deliberationtrace",
    "raw_prompt",
    "rawprompt",
    "raw_completion",
    "rawcompletion",
    "raw_response",
    "rawresponse",
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
    text = re.sub(
        r"[^a-z0-9_]+",
        "",
        text,
    )

    while "__" in text:
        text = text.replace(
            "__",
            "_",
        )

    return text.strip("_")


def _id(value: Any) -> str:
    return _clean(value).upper()


def _string_set(value: Any) -> Set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        value = [value]

    if not isinstance(
        value,
        (list, tuple, set),
    ):
        return set()

    result = set()

    for item in value:
        normalized = _key(
            item
        )

        if normalized:
            result.add(
                normalized
            )

    return result


def _id_set(value: Any) -> Set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        value = [value]

    if not isinstance(
        value,
        (list, tuple, set),
    ):
        return set()

    result = set()

    for item in value:
        if isinstance(
            item,
            Mapping,
        ):
            item = (
                item.get(
                    "insight_id"
                )
                or item.get(
                    "preference_record_id"
                )
                or item.get(
                    "id"
                )
                or ""
            )

        normalized = _id(
            item
        )

        if normalized:
            result.add(
                normalized
            )

    return result


def _safe_div(
    numerator: int | float,
    denominator: int | float,
) -> Optional[float]:
    if denominator == 0:
        return None

    return float(
        numerator
    ) / float(
        denominator
    )


def _round(
    value: Optional[float],
    digits: int = 4,
) -> Optional[float]:
    if value is None:
        return None

    return round(
        float(
            value
        ),
        digits,
    )


def _mean(
    values: Sequence[float],
) -> Optional[float]:
    if not values:
        return None

    return sum(
        values
    ) / len(
        values
    )


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
        or precision + recall == 0
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


def _clip_probability(
    value: float,
) -> float:
    return max(
        1e-9,
        min(
            1.0 - 1e-9,
            float(
                value
            ),
        ),
    )


def _log_loss(
    probability: float,
    target: float,
) -> float:
    probability = _clip_probability(
        probability
    )

    return -(
        target
        * math.log(
            probability
        )
        + (
            1.0
            - target
        )
        * math.log(
            1.0
            - probability
        )
    )


def _optional_probability(
    value: Any,
) -> Optional[float]:
    if value is None or value == "":
        return None

    try:
        numeric = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if not math.isfinite(
        numeric
    ):
        return None

    if not 0.0 <= numeric <= 1.0:
        return None

    return numeric


def _case_id(
    record: Mapping[str, Any],
) -> str:
    return _clean(
        record.get(
            "case_id"
        )
        or record.get(
            "evaluation_id"
        )
        or record.get(
            "id"
        )
    )


def _normalize_tags(
    value: Any,
) -> List[str]:
    return sorted(
        _string_set(
            value
        )
    )


def _contains_field(
    value: Any,
    prohibited: Set[str],
    path: str = "",
) -> Optional[str]:
    if isinstance(
        value,
        Mapping,
    ):
        for key, item in value.items():
            normalized = _key(
                key
            )

            current_path = (
                f"{path}.{key}"
                if path
                else str(
                    key
                )
            )

            if normalized in prohibited:
                return current_path

            nested = _contains_field(
                item,
                prohibited,
                current_path,
            )

            if nested:
                return nested

    elif isinstance(
        value,
        (list, tuple),
    ):
        for index, item in enumerate(
            value
        ):
            nested = _contains_field(
                item,
                prohibited,
                f"{path}[{index}]",
            )

            if nested:
                return nested

    return None


def load_jsonl(
    path: str | Path,
) -> List[Dict[str, Any]]:
    path = Path(
        path
    )

    rows = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        for line_number, line in enumerate(
            handle,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                payload = json.loads(
                    line
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at {path}:{line_number}: {exc}"
                ) from exc

            if not isinstance(
                payload,
                dict,
            ):
                raise ValueError(
                    f"{path}:{line_number} must contain a JSON object."
                )

            rows.append(
                payload
            )

    return rows


def _index_cases(
    records: Sequence[
        Mapping[str, Any]
    ],
    *,
    source_name: str,
) -> Dict[str, Dict[str, Any]]:
    result = {}

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

        if case_id in result:
            raise ValueError(
                f"Duplicate {source_name} case_id={case_id!r}."
            )

        result[
            case_id
        ] = dict(
            record
        )

    return result


# ---------------------------------------------------------------------------
# Prediction extraction
# ---------------------------------------------------------------------------


def _structured_feedback(
    prediction: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = (
        prediction.get(
            "structured_feedback"
        )
        or prediction.get(
            "feedback_event"
        )
        or prediction.get(
            "feedback"
        )
        or {}
    )

    if isinstance(
        value,
        Mapping,
    ):
        return value

    return {}


def _preference_profile(
    prediction: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = (
        prediction.get(
            "preference_profile"
        )
        or prediction.get(
            "profile"
        )
        or {}
    )

    if isinstance(
        value,
        Mapping,
    ):
        return value

    return {}


def _extract_examples(
    value: Any,
) -> Set[str]:
    if value is None:
        return set()

    if isinstance(
        value,
        (str, Mapping),
    ):
        value = [
            value
        ]

    if not isinstance(
        value,
        (list, tuple, set),
    ):
        return set()

    result = set()

    for item in value:
        if isinstance(
            item,
            Mapping,
        ):
            text = _clean(
                item.get(
                    "text"
                )
                or item.get(
                    "insight"
                )
                or item.get(
                    "example"
                )
            )

            insight_id = _id(
                item.get(
                    "insight_id"
                )
            )

            identity = (
                insight_id
                if insight_id
                else text.casefold()
            )

        else:
            identity = _clean(
                item
            ).casefold()

        if identity:
            result.add(
                identity
            )

    return result


def _ranked_items(
    prediction: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    raw = (
        prediction.get(
            "ranked_insights"
        )
        or prediction.get(
            "ranking"
        )
        or []
    )

    if isinstance(
        raw,
        Mapping,
    ):
        raw = (
            raw.get(
                "ranked_insights"
            )
            or raw.get(
                "items"
            )
            or []
        )

    if not isinstance(
        raw,
        list,
    ):
        return []

    output = []

    for index, item in enumerate(
        raw,
        start=1,
    ):
        if isinstance(
            item,
            str,
        ):
            insight_id = _id(
                item
            )
            if insight_id:
                output.append(
                    {
                        "insight_id": insight_id,
                        "rank": index,
                        "score": None,
                    }
                )
            continue

        if not isinstance(
            item,
            Mapping,
        ):
            continue

        insight_id = _id(
            item.get(
                "insight_id"
            )
            or item.get(
                "id"
            )
        )

        if not insight_id:
            continue

        rank = item.get(
            "rank"
        )

        try:
            rank = int(
                rank
            )
        except (
            TypeError,
            ValueError,
        ):
            rank = index

        score = (
            _optional_probability(
                item.get(
                    "diversity_adjusted_score"
                )
            )
            if item.get(
                "diversity_adjusted_score"
            )
            is not None
            else _optional_probability(
                item.get(
                    "base_score"
                )
                if item.get(
                    "base_score"
                )
                is not None
                else item.get(
                    "score"
                )
            )
        )

        output.append(
            {
                "insight_id": insight_id,
                "rank": rank,
                "score": score,
            }
        )

    output.sort(
        key=lambda item: (
            item[
                "rank"
            ],
            item[
                "insight_id"
            ],
        )
    )

    return output


def _pointwise_score(
    prediction: Mapping[str, Any],
) -> Optional[float]:
    candidates = [
        prediction.get(
            "pointwise_score"
        ),
        prediction.get(
            "preference_score"
        ),
        prediction.get(
            "score"
        ),
    ]

    model_prediction = prediction.get(
        "model_prediction"
    )

    if isinstance(
        model_prediction,
        Mapping,
    ):
        candidates.append(
            model_prediction.get(
                "score"
            )
        )

    for value in candidates:
        probability = _optional_probability(
            value
        )

        if probability is not None:
            return probability

    return None


def _predicted_label(
    prediction: Mapping[str, Any],
    score: Optional[float],
) -> str:
    explicit = _key(
        prediction.get(
            "predicted_label"
        )
        or prediction.get(
            "label"
        )
    )

    if explicit:
        return explicit

    if score is None:
        return ""

    if score > 0.55:
        return "positive"

    if score < 0.45:
        return "negative"

    return "neutral"


def _pairwise_scores(
    prediction: Mapping[str, Any],
    chosen_id: str,
    rejected_id: str,
) -> Tuple[
    Optional[float],
    Optional[float],
    Optional[float],
]:
    chosen_score = _optional_probability(
        prediction.get(
            "chosen_score"
        )
    )
    rejected_score = _optional_probability(
        prediction.get(
            "rejected_score"
        )
    )
    pairwise_probability = _optional_probability(
        prediction.get(
            "pairwise_probability"
        )
    )

    pairwise = prediction.get(
        "pairwise"
    )

    if isinstance(
        pairwise,
        Mapping,
    ):
        if chosen_score is None:
            chosen_score = _optional_probability(
                pairwise.get(
                    "chosen_score"
                )
            )

        if rejected_score is None:
            rejected_score = _optional_probability(
                pairwise.get(
                    "rejected_score"
                )
            )

        if pairwise_probability is None:
            pairwise_probability = _optional_probability(
                pairwise.get(
                    "pairwise_probability"
                )
                or pairwise.get(
                    "probability"
                )
            )

    if (
        chosen_score is None
        or rejected_score is None
    ):
        ranked = _ranked_items(
            prediction
        )

        score_by_id = {
            item[
                "insight_id"
            ]: item[
                "score"
            ]
            for item in ranked
            if item[
                "score"
            ] is not None
        }

        if chosen_score is None:
            chosen_score = score_by_id.get(
                chosen_id
            )

        if rejected_score is None:
            rejected_score = score_by_id.get(
                rejected_id
            )

    return (
        chosen_score,
        rejected_score,
        pairwise_probability,
    )


# ---------------------------------------------------------------------------
# Ranking metrics
# ---------------------------------------------------------------------------


def _dcg(
    relevance: Sequence[float],
) -> float:
    total = 0.0

    for index, rel in enumerate(
        relevance,
        start=1,
    ):
        total += (
            (
                2.0 ** float(
                    rel
                )
                - 1.0
            )
            / math.log2(
                index + 1.0
            )
        )

    return total


def _ndcg_at_k(
    ranked_ids: Sequence[str],
    relevance_by_id: Mapping[str, float],
    k: int,
) -> Optional[float]:
    if not relevance_by_id:
        return None

    k = max(
        1,
        int(
            k
        ),
    )

    observed = [
        float(
            relevance_by_id.get(
                insight_id,
                0.0,
            )
        )
        for insight_id in ranked_ids[
            :k
        ]
    ]

    ideal = sorted(
        [
            float(
                value
            )
            for value in relevance_by_id.values()
        ],
        reverse=True,
    )[:k]

    ideal_dcg = _dcg(
        ideal
    )

    if ideal_dcg <= 0.0:
        return None

    return _dcg(
        observed
    ) / ideal_dcg


def _ranking_pairwise_accuracy(
    ranked_ids: Sequence[str],
    required_pairs: Sequence[
        Sequence[str]
    ],
) -> Optional[float]:
    if not required_pairs:
        return None

    position = {
        insight_id: index
        for index, insight_id
        in enumerate(
            ranked_ids
        )
    }

    correct = 0
    labelled = 0

    for pair in required_pairs:
        if (
            not isinstance(
                pair,
                (list, tuple)
            )
            or len(
                pair
            ) != 2
        ):
            continue

        preferred = _id(
            pair[0]
        )
        rejected = _id(
            pair[1]
        )

        if not preferred or not rejected:
            continue

        labelled += 1

        preferred_position = position.get(
            preferred
        )
        rejected_position = position.get(
            rejected
        )

        if (
            preferred_position is not None
            and (
                rejected_position is None
                or preferred_position
                < rejected_position
            )
        ):
            correct += 1

    return _safe_div(
        correct,
        labelled,
    )


# ---------------------------------------------------------------------------
# Preference-data integrity
# ---------------------------------------------------------------------------


def _preference_records(
    prediction: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    raw = (
        prediction.get(
            "preference_records"
        )
        or prediction.get(
            "training_records"
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


def _record_group_key(
    record: Mapping[str, Any],
) -> str:
    for key in (
        "split_group_id",
        "group_id",
        "report_id",
        "run_id",
        "feedback_group_id",
    ):
        value = _clean(
            record.get(
                key
            )
        )

        if value:
            return (
                key
                + ":"
                + value
            )

    context = record.get(
        "context"
    )

    if isinstance(
        context,
        Mapping,
    ):
        for key in (
            "report_id",
            "run_id",
        ):
            value = _clean(
                context.get(
                    key
                )
            )

            if value:
                return (
                    "context."
                    + key
                    + ":"
                    + value
                )

    return ""


def _preference_record_integrity(
    prediction: Mapping[str, Any],
) -> Dict[str, Any]:
    records = _preference_records(
        prediction
    )

    grounding_evidence_violations = 0
    pair_self_comparison_violations = 0
    invalid_split_count = 0
    prohibited_context_count = 0
    private_reasoning_count = 0
    duplicate_id_conflict_count = 0

    seen_record_payloads: Dict[
        str,
        str,
    ] = {}

    split_by_group: Dict[
        str,
        Set[str],
    ] = defaultdict(
        set
    )

    for record in records:
        if bool(
            record.get(
                "is_grounding_evidence"
            )
        ):
            grounding_evidence_violations += 1

        metadata = record.get(
            "metadata"
        )

        if isinstance(
            metadata,
            Mapping,
        ) and bool(
            metadata.get(
                "preference_data_is_grounding_evidence"
            )
        ):
            grounding_evidence_violations += 1

        record_type = _key(
            record.get(
                "record_type"
            )
        )

        if (
            record_type
            and record_type
            not in ALLOWED_PREFERENCE_RECORD_TYPES
        ):
            # Contract mismatch is represented as invalid split/context style
            # failure through prohibited count to keep aggregate compact.
            prohibited_context_count += 1

        if record_type == "pairwise_preference":
            chosen = (
                record.get(
                    "chosen"
                )
                or {}
            )
            rejected = (
                record.get(
                    "rejected"
                )
                or {}
            )

            if isinstance(
                chosen,
                Mapping,
            ) and isinstance(
                rejected,
                Mapping,
            ):
                chosen_id = _id(
                    chosen.get(
                        "insight_id"
                    )
                    or chosen.get(
                        "id"
                    )
                )
                rejected_id = _id(
                    rejected.get(
                        "insight_id"
                    )
                    or rejected.get(
                        "id"
                    )
                )

                chosen_text = _clean(
                    chosen.get(
                        "text"
                    )
                    or chosen.get(
                        "insight"
                    )
                )
                rejected_text = _clean(
                    rejected.get(
                        "text"
                    )
                    or rejected.get(
                        "insight"
                    )
                )

                if (
                    (
                        chosen_id
                        and rejected_id
                        and chosen_id
                        == rejected_id
                    )
                    or (
                        chosen_text
                        and rejected_text
                        and chosen_text
                        == rejected_text
                    )
                ):
                    pair_self_comparison_violations += 1

        split = _key(
            record.get(
                "dataset_split"
            )
        )

        if split:
            if split not in ALLOWED_SPLITS:
                invalid_split_count += 1

            group_key = _record_group_key(
                record
            )

            if group_key:
                split_by_group[
                    group_key
                ].add(
                    split
                )

        context = record.get(
            "context"
        )

        if isinstance(
            context,
            Mapping,
        ):
            prohibited = _contains_field(
                context,
                PROHIBITED_CONTROL_OR_EVIDENCE_FIELDS,
            )

            if prohibited:
                prohibited_context_count += 1

            private = _contains_field(
                context,
                PROHIBITED_PRIVATE_REASONING_FIELDS,
            )

            if private:
                private_reasoning_count += 1

        private_anywhere = _contains_field(
            record,
            PROHIBITED_PRIVATE_REASONING_FIELDS,
        )

        if private_anywhere:
            private_reasoning_count += 1

        record_id = _id(
            record.get(
                "preference_record_id"
            )
        )

        if record_id:
            canonical = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

            if (
                record_id
                in seen_record_payloads
                and seen_record_payloads[
                    record_id
                ] != canonical
            ):
                duplicate_id_conflict_count += 1

            seen_record_payloads[
                record_id
            ] = canonical

    split_leakage_groups = sorted(
        group_key
        for group_key, splits
        in split_by_group.items()
        if len(
            {
                split
                for split in splits
                if split
            }
        ) > 1
    )

    training_summary = (
        prediction.get(
            "training_summary"
        )
        or {}
    )

    test_optimization_overlap = []

    if isinstance(
        training_summary,
        Mapping,
    ):
        train_ids = _id_set(
            training_summary.get(
                "training_record_ids"
            )
            or []
        )
        test_ids = _id_set(
            training_summary.get(
                "test_record_ids"
            )
            or []
        )

        test_optimization_overlap = sorted(
            train_ids
            & test_ids
        )

        optimization_ids = _id_set(
            training_summary.get(
                "optimization_record_ids"
            )
            or []
        )

        if optimization_ids:
            test_optimization_overlap = sorted(
                set(
                    test_optimization_overlap
                )
                | (
                    optimization_ids
                    & test_ids
                )
            )

    return {
        "record_count": len(
            records
        ),
        "grounding_evidence_violation_count": (
            grounding_evidence_violations
        ),
        "pair_self_comparison_violation_count": (
            pair_self_comparison_violations
        ),
        "invalid_split_count": invalid_split_count,
        "prohibited_context_count": prohibited_context_count,
        "private_reasoning_count": private_reasoning_count,
        "duplicate_record_id_conflict_count": (
            duplicate_id_conflict_count
        ),
        "split_leakage_group_count": len(
            split_leakage_groups
        ),
        "split_leakage_groups": split_leakage_groups,
        "test_optimization_overlap_count": len(
            test_optimization_overlap
        ),
        "test_optimization_overlap_record_ids": (
            test_optimization_overlap
        ),
        "passes": bool(
            grounding_evidence_violations == 0
            and pair_self_comparison_violations == 0
            and invalid_split_count == 0
            and prohibited_context_count == 0
            and private_reasoning_count == 0
            and duplicate_id_conflict_count == 0
            and not split_leakage_groups
            and not test_optimization_overlap
        ),
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

    structured = _structured_feedback(
        prediction
    )
    profile = _preference_profile(
        prediction
    )

    # ------------------------------------------------------------------
    # Feedback interpretation
    # ------------------------------------------------------------------
    feedback_types_gold = _string_set(
        gold.get(
            "expected_feedback_types"
        )
        or []
    )
    feedback_types_pred = _string_set(
        structured.get(
            "feedback_types"
        )
        or structured.get(
            "feedback_type"
        )
        or []
    )

    feedback_type_metrics = (
        _prf(
            feedback_types_pred,
            feedback_types_gold,
        )
        if "expected_feedback_types" in gold
        else None
    )

    expected_sentiment = _key(
        gold.get(
            "expected_sentiment"
        )
    )
    predicted_sentiment = _key(
        structured.get(
            "sentiment"
        )
    )

    sentiment_correct = (
        None
        if not expected_sentiment
        else predicted_sentiment
        == expected_sentiment
    )

    expected_depth_delta = _key(
        gold.get(
            "expected_desired_depth_delta"
        )
    )
    predicted_depth_delta = _key(
        structured.get(
            "desired_depth_delta"
        )
        or structured.get(
            "depth_delta"
        )
    )

    depth_delta_correct = (
        None
        if "expected_desired_depth_delta"
        not in gold
        else predicted_depth_delta
        == expected_depth_delta
    )

    expected_verbosity_delta = _key(
        gold.get(
            "expected_verbosity_delta"
        )
    )
    predicted_verbosity_delta = _key(
        structured.get(
            "verbosity_delta"
        )
    )

    verbosity_delta_correct = (
        None
        if "expected_verbosity_delta"
        not in gold
        else predicted_verbosity_delta
        == expected_verbosity_delta
    )

    feedback_preferred_patterns_gold = _string_set(
        gold.get(
            "expected_preferred_patterns"
        )
        or []
    )
    feedback_preferred_patterns_pred = _string_set(
        structured.get(
            "preferred_patterns"
        )
        or []
    )

    feedback_preferred_pattern_metrics = (
        _prf(
            feedback_preferred_patterns_pred,
            feedback_preferred_patterns_gold,
        )
        if "expected_preferred_patterns" in gold
        else None
    )

    feedback_avoid_patterns_gold = _string_set(
        gold.get(
            "expected_avoid_patterns"
        )
        or []
    )
    feedback_avoid_patterns_pred = _string_set(
        structured.get(
            "avoid_patterns"
        )
        or []
    )

    feedback_avoid_pattern_metrics = (
        _prf(
            feedback_avoid_patterns_pred,
            feedback_avoid_patterns_gold,
        )
        if "expected_avoid_patterns" in gold
        else None
    )

    feedback_lenses_gold = {
        _clean(
            item
        ).casefold()
        for item in (
            gold.get(
                "expected_analytical_lenses"
            )
            or []
        )
        if _clean(
            item
        )
    }
    feedback_lenses_pred = {
        _clean(
            item
        ).casefold()
        for item in (
            structured.get(
                "analytical_lenses"
            )
            or []
        )
        if _clean(
            item
        )
    }

    feedback_lens_metrics = (
        _prf(
            feedback_lenses_pred,
            feedback_lenses_gold,
        )
        if "expected_analytical_lenses" in gold
        else None
    )

    expected_actions = _string_set(
        gold.get(
            "expected_refinement_actions"
        )
        or []
    )
    predicted_actions = _string_set(
        structured.get(
            "recommended_refinement_actions"
        )
        or structured.get(
            "refinement_actions"
        )
        or []
    )

    action_metrics = (
        _prf(
            predicted_actions,
            expected_actions,
        )
        if "expected_refinement_actions" in gold
        else None
    )

    expected_preferred_targets = _id_set(
        gold.get(
            "expected_preferred_insight_ids"
        )
        or []
    )
    expected_rejected_targets = _id_set(
        gold.get(
            "expected_rejected_insight_ids"
        )
        or []
    )

    predicted_preferred_targets = _id_set(
        structured.get(
            "preferred_insight_ids"
        )
        or []
    )
    predicted_rejected_targets = _id_set(
        structured.get(
            "rejected_insight_ids"
        )
        or []
    )

    preferred_target_metrics = (
        _prf(
            predicted_preferred_targets,
            expected_preferred_targets,
        )
        if "expected_preferred_insight_ids" in gold
        else None
    )

    rejected_target_metrics = (
        _prf(
            predicted_rejected_targets,
            expected_rejected_targets,
        )
        if "expected_rejected_insight_ids" in gold
        else None
    )

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    expected_profile = (
        gold.get(
            "expected_profile"
        )
        or {}
    )

    if (
        expected_profile
        and not isinstance(
            expected_profile,
            Mapping,
        )
    ):
        raise ValueError(
            f"{case_id}: expected_profile must be an object."
        )

    expected_profile = (
        expected_profile
        if isinstance(
            expected_profile,
            Mapping,
        )
        else {}
    )

    profile_depth_expected = _key(
        expected_profile.get(
            "desired_depth"
        )
    )
    profile_depth_pred = _key(
        profile.get(
            "desired_depth"
        )
    )

    profile_depth_correct = (
        None
        if "desired_depth"
        not in expected_profile
        else profile_depth_pred
        == profile_depth_expected
    )

    profile_verbosity_expected = _key(
        expected_profile.get(
            "verbosity"
        )
    )
    profile_verbosity_pred = _key(
        profile.get(
            "verbosity"
        )
    )

    profile_verbosity_correct = (
        None
        if "verbosity"
        not in expected_profile
        else profile_verbosity_pred
        == profile_verbosity_expected
    )

    profile_preferred_metrics = (
        _prf(
            _string_set(
                profile.get(
                    "preferred_patterns"
                )
                or []
            ),
            _string_set(
                expected_profile.get(
                    "preferred_patterns"
                )
                or []
            ),
        )
        if "preferred_patterns"
        in expected_profile
        else None
    )

    profile_avoid_metrics = (
        _prf(
            _string_set(
                profile.get(
                    "avoid_patterns"
                )
                or []
            ),
            _string_set(
                expected_profile.get(
                    "avoid_patterns"
                )
                or []
            ),
        )
        if "avoid_patterns"
        in expected_profile
        else None
    )

    profile_lens_metrics = (
        _prf(
            {
                _clean(
                    item
                ).casefold()
                for item in (
                    profile.get(
                        "analytical_lenses"
                    )
                    or []
                )
                if _clean(
                    item
                )
            },
            {
                _clean(
                    item
                ).casefold()
                for item in (
                    expected_profile.get(
                        "analytical_lenses"
                    )
                    or []
                )
                if _clean(
                    item
                )
            },
        )
        if "analytical_lenses"
        in expected_profile
        else None
    )

    profile_preferred_ids_metrics = (
        _prf(
            _id_set(
                profile.get(
                    "preferred_insight_ids"
                )
                or []
            ),
            _id_set(
                expected_profile.get(
                    "preferred_insight_ids"
                )
                or []
            ),
        )
        if "preferred_insight_ids"
        in expected_profile
        else None
    )

    profile_rejected_ids_metrics = (
        _prf(
            _id_set(
                profile.get(
                    "rejected_insight_ids"
                )
                or []
            ),
            _id_set(
                expected_profile.get(
                    "rejected_insight_ids"
                )
                or []
            ),
        )
        if "rejected_insight_ids"
        in expected_profile
        else None
    )

    profile_positive_example_metrics = (
        _prf(
            _extract_examples(
                profile.get(
                    "positive_examples"
                )
                or []
            ),
            _extract_examples(
                expected_profile.get(
                    "positive_examples"
                )
                or []
            ),
        )
        if "positive_examples"
        in expected_profile
        else None
    )

    profile_negative_example_metrics = (
        _prf(
            _extract_examples(
                profile.get(
                    "negative_examples"
                )
                or []
            ),
            _extract_examples(
                expected_profile.get(
                    "negative_examples"
                )
                or []
            ),
        )
        if "negative_examples"
        in expected_profile
        else None
    )

    # ------------------------------------------------------------------
    # Pointwise score
    # ------------------------------------------------------------------
    expected_pointwise_label = _key(
        gold.get(
            "expected_pointwise_label"
        )
    )

    pointwise_score = _pointwise_score(
        prediction
    )
    pointwise_predicted_label = _predicted_label(
        prediction,
        pointwise_score,
    )

    pointwise_label_correct = (
        None
        if not expected_pointwise_label
        else pointwise_predicted_label
        == expected_pointwise_label
    )

    pointwise_target = None

    if expected_pointwise_label:
        if expected_pointwise_label not in ALLOWED_POINTWISE_LABELS:
            raise ValueError(
                f"{case_id}: unsupported expected_pointwise_label="
                f"{expected_pointwise_label!r}."
            )

        pointwise_target = {
            "positive": 1.0,
            "negative": 0.0,
            "neutral": 0.5,
        }[
            expected_pointwise_label
        ]

    pointwise_brier = None
    pointwise_log_loss = None

    if (
        pointwise_score is not None
        and pointwise_target is not None
    ):
        pointwise_brier = (
            pointwise_score
            - pointwise_target
        ) ** 2

        pointwise_log_loss = _log_loss(
            pointwise_score,
            pointwise_target,
        )

    # ------------------------------------------------------------------
    # Pairwise
    # ------------------------------------------------------------------
    expected_chosen_id = _id(
        gold.get(
            "expected_chosen_insight_id"
        )
    )
    expected_rejected_id = _id(
        gold.get(
            "expected_rejected_insight_id"
        )
    )

    pairwise_labelled = bool(
        expected_chosen_id
        and expected_rejected_id
    )

    pairwise_correct = None
    pairwise_tie = None
    pairwise_margin = None
    pairwise_log_loss = None

    if pairwise_labelled:
        chosen_score, rejected_score, pair_probability = _pairwise_scores(
            prediction,
            expected_chosen_id,
            expected_rejected_id,
        )

        if (
            chosen_score is not None
            and rejected_score is not None
        ):
            pairwise_margin = (
                chosen_score
                - rejected_score
            )

            pairwise_tie = bool(
                abs(
                    pairwise_margin
                ) < 1e-12
            )

            pairwise_correct = bool(
                pairwise_margin > 0.0
            )

        else:
            ranked_ids = [
                item[
                    "insight_id"
                ]
                for item in _ranked_items(
                    prediction
                )
            ]

            if ranked_ids:
                position = {
                    insight_id: index
                    for index, insight_id
                    in enumerate(
                        ranked_ids
                    )
                }

                chosen_pos = position.get(
                    expected_chosen_id
                )
                rejected_pos = position.get(
                    expected_rejected_id
                )

                pairwise_tie = False

                pairwise_correct = bool(
                    chosen_pos is not None
                    and (
                        rejected_pos is None
                        or chosen_pos
                        < rejected_pos
                    )
                )

        if pair_probability is not None:
            pairwise_log_loss = _log_loss(
                pair_probability,
                1.0,
            )

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------
    ranked_items = _ranked_items(
        prediction
    )
    ranked_ids = [
        item[
            "insight_id"
        ]
        for item in ranked_items
    ]

    acceptable_top_ids = _id_set(
        gold.get(
            "acceptable_top_insight_ids"
        )
        or []
    )

    top1_acceptable = (
        None
        if not acceptable_top_ids
        else bool(
            ranked_ids
            and ranked_ids[0]
            in acceptable_top_ids
        )
    )

    preferred_ids_for_mrr = _id_set(
        gold.get(
            "preferred_insight_ids_for_mrr"
        )
        or gold.get(
            "acceptable_top_insight_ids"
        )
        or []
    )

    reciprocal_rank = None

    if preferred_ids_for_mrr:
        reciprocal_rank = 0.0

        for index, insight_id in enumerate(
            ranked_ids,
            start=1,
        ):
            if insight_id in preferred_ids_for_mrr:
                reciprocal_rank = (
                    1.0
                    / index
                )
                break

    relevance_raw = (
        gold.get(
            "relevance_by_insight_id"
        )
        or {}
    )

    relevance_by_id = {}

    if relevance_raw:
        if not isinstance(
            relevance_raw,
            Mapping,
        ):
            raise ValueError(
                f"{case_id}: relevance_by_insight_id must be an object."
            )

        for raw_id, value in relevance_raw.items():
            try:
                relevance = float(
                    value
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise ValueError(
                    f"{case_id}: relevance for {raw_id!r} must be numeric."
                ) from exc

            relevance_by_id[
                _id(
                    raw_id
                )
            ] = relevance

    ndcg_k = int(
        gold.get(
            "ndcg_k"
        )
        or max(
            1,
            len(
                relevance_by_id
            ),
        )
    )

    ndcg = _ndcg_at_k(
        ranked_ids,
        relevance_by_id,
        ndcg_k,
    )

    required_pairs = (
        gold.get(
            "required_pairwise_preferences"
        )
        or []
    )

    if not isinstance(
        required_pairs,
        list,
    ):
        raise ValueError(
            f"{case_id}: required_pairwise_preferences must be a list."
        )

    ranking_pairwise_accuracy = _ranking_pairwise_accuracy(
        ranked_ids,
        required_pairs,
    )

    # ------------------------------------------------------------------
    # Data integrity
    # ------------------------------------------------------------------
    data_integrity = _preference_record_integrity(
        prediction
    )

    # ------------------------------------------------------------------
    # Epistemic/control-plane contamination
    # ------------------------------------------------------------------
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

    preference_payload = {
        "structured_feedback": structured,
        "preference_profile": profile,
        "preference_records": _preference_records(
            prediction
        ),
    }

    prohibited_control_path = _contains_field(
        preference_payload,
        PROHIBITED_CONTROL_OR_EVIDENCE_FIELDS,
    )

    private_reasoning_path = _contains_field(
        prediction,
        PROHIBITED_PRIVATE_REASONING_FIELDS,
    )

    research_depth_mutation = bool(
        metadata.get(
            "research_depth_modified_here"
        )
        or metadata.get(
            "modified_research_depth"
        )
        or _key(
            "research_depth"
        )
        in {
            _key(
                key
            )
            for key in profile
        }
    )

    policy_modification = bool(
        metadata.get(
            "modifies_policy"
        )
        or metadata.get(
            "policy_modified_here"
        )
    )

    release_authorization = bool(
        metadata.get(
            "authorizes_release"
        )
        or metadata.get(
            "release_authorized"
        )
    )

    preference_is_grounding_evidence = bool(
        metadata.get(
            "preference_data_is_grounding_evidence"
        )
        or metadata.get(
            "is_grounding_evidence"
        )
    )

    score_claims_truth = bool(
        metadata.get(
            "model_predicts_truth"
        )
        or metadata.get(
            "score_is_truth_probability"
        )
        or metadata.get(
            "score_is_grounding_confidence"
        )
    )

    prediction_semantics = _clean(
        metadata.get(
            "prediction_semantics"
        )
        or metadata.get(
            "ranking_score_semantics"
        )
    ).casefold()

    if prediction_semantics:
        if (
            "truth probability"
            in prediction_semantics
            and "not truth probability"
            not in prediction_semantics
        ):
            score_claims_truth = True

        if (
            "grounding confidence"
            in prediction_semantics
            and "not grounding confidence"
            not in prediction_semantics
        ):
            score_claims_truth = True

    return {
        "case_id": case_id,
        "case_tags": _normalize_tags(
            gold.get(
                "case_tags"
            )
            or []
        ),

        "feedback_interpretation": {
            "feedback_types": feedback_type_metrics,
            "sentiment_expected": expected_sentiment,
            "sentiment_predicted": predicted_sentiment,
            "sentiment_correct": sentiment_correct,
            "desired_depth_delta_expected": expected_depth_delta,
            "desired_depth_delta_predicted": predicted_depth_delta,
            "desired_depth_delta_correct": depth_delta_correct,
            "verbosity_delta_expected": expected_verbosity_delta,
            "verbosity_delta_predicted": predicted_verbosity_delta,
            "verbosity_delta_correct": verbosity_delta_correct,
            "preferred_patterns": feedback_preferred_pattern_metrics,
            "avoid_patterns": feedback_avoid_pattern_metrics,
            "analytical_lenses": feedback_lens_metrics,
            "refinement_actions": action_metrics,
            "preferred_insight_targets": preferred_target_metrics,
            "rejected_insight_targets": rejected_target_metrics,
        },

        "profile": {
            "desired_depth_correct": profile_depth_correct,
            "verbosity_correct": profile_verbosity_correct,
            "preferred_patterns": profile_preferred_metrics,
            "avoid_patterns": profile_avoid_metrics,
            "analytical_lenses": profile_lens_metrics,
            "preferred_insight_ids": profile_preferred_ids_metrics,
            "rejected_insight_ids": profile_rejected_ids_metrics,
            "positive_examples": profile_positive_example_metrics,
            "negative_examples": profile_negative_example_metrics,
        },

        "pointwise": {
            "labelled": bool(
                expected_pointwise_label
            ),
            "expected_label": expected_pointwise_label,
            "predicted_label": pointwise_predicted_label,
            "label_correct": pointwise_label_correct,
            "score": pointwise_score,
            "target": pointwise_target,
            "brier_score": _round(
                pointwise_brier
            ),
            "log_loss": _round(
                pointwise_log_loss
            ),
        },

        "pairwise": {
            "labelled": pairwise_labelled,
            "expected_chosen_insight_id": expected_chosen_id,
            "expected_rejected_insight_id": expected_rejected_id,
            "correct": pairwise_correct,
            "tie": pairwise_tie,
            "chosen_minus_rejected_margin": _round(
                pairwise_margin
            ),
            "pairwise_log_loss": _round(
                pairwise_log_loss
            ),
        },

        "ranking": {
            "ranked_insight_ids": ranked_ids,
            "top1_acceptable": top1_acceptable,
            "reciprocal_rank": _round(
                reciprocal_rank
            ),
            "ndcg_k": ndcg_k if relevance_by_id else None,
            "ndcg": _round(
                ndcg
            ),
            "required_pairwise_order_accuracy": _round(
                ranking_pairwise_accuracy
            ),
        },

        "preference_data_integrity": data_integrity,

        "epistemic_contract": {
            "preference_data_is_grounding_evidence": (
                preference_is_grounding_evidence
            ),
            "score_claims_truth_or_grounding_confidence": (
                score_claims_truth
            ),
            "research_depth_mutation": (
                research_depth_mutation
            ),
            "policy_modification": (
                policy_modification
            ),
            "release_authorization": (
                release_authorization
            ),
            "prohibited_control_or_evidence_field_path": (
                prohibited_control_path
                or ""
            ),
            "prohibited_control_or_evidence_field_present": bool(
                prohibited_control_path
            ),
            "private_reasoning_path": (
                private_reasoning_path
                or ""
            ),
            "private_reasoning_contamination": bool(
                private_reasoning_path
            ),
            "passes": bool(
                not preference_is_grounding_evidence
                and not score_claims_truth
                and not research_depth_mutation
                and not policy_modification
                and not release_authorization
                and not prohibited_control_path
                and not private_reasoning_path
                and data_integrity[
                    "passes"
                ]
            ),
        },
    }


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate_prf(
    cases: Sequence[
        Mapping[str, Any]
    ],
    path: Sequence[str],
) -> Dict[str, Optional[float]]:
    tp = fp = fn = 0
    labelled = 0

    for case in cases:
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

        if not isinstance(
            value,
            Mapping,
        ):
            continue

        if not all(
            key in value
            for key in (
                "tp",
                "fp",
                "fn",
            )
        ):
            continue

        labelled += 1
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
        or precision + recall == 0
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
        "labelled_case_count": labelled,
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
    cases: Sequence[
        Mapping[str, Any]
    ],
    path: Sequence[str],
) -> Dict[str, Any]:
    values = []

    for case in cases:
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

    passes = sum(
        1
        for value in values
        if value
    )

    return {
        "labelled_count": len(
            values
        ),
        "pass_count": passes,
        "rate": _round(
            _safe_div(
                passes,
                len(
                    values
                ),
            )
        ),
    }


def _numeric_mean(
    cases: Sequence[
        Mapping[str, Any]
    ],
    path: Sequence[str],
) -> Optional[float]:
    values = []

    for case in cases:
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
        ) and math.isfinite(
            float(
                value
            )
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


def _aggregate_pointwise(
    cases: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    labelled = [
        case[
            "pointwise"
        ]
        for case in cases
        if case[
            "pointwise"
        ][
            "labelled"
        ]
    ]

    decisive = [
        item
        for item in labelled
        if item[
            "expected_label"
        ]
        in {
            "positive",
            "negative",
        }
    ]

    accuracy = _safe_div(
        sum(
            1
            for item in labelled
            if item[
                "label_correct"
            ]
        ),
        len(
            labelled
        ),
    )

    decisive_accuracy = _safe_div(
        sum(
            1
            for item in decisive
            if item[
                "label_correct"
            ]
        ),
        len(
            decisive
        ),
    )

    per_class_recall = {}

    for label in (
        "positive",
        "negative",
        "neutral",
    ):
        subset = [
            item
            for item in labelled
            if item[
                "expected_label"
            ] == label
        ]

        per_class_recall[
            label
        ] = _safe_div(
            sum(
                1
                for item in subset
                if item[
                    "predicted_label"
                ] == label
            ),
            len(
                subset
            ),
        )

    decisive_class_recalls = [
        value
        for label, value in per_class_recall.items()
        if (
            label
            in {
                "positive",
                "negative",
            }
            and value
            is not None
        )
    ]

    balanced_accuracy = _mean(
        decisive_class_recalls
    )

    brier_values = [
        item[
            "brier_score"
        ]
        for item in labelled
        if item[
            "brier_score"
        ] is not None
    ]

    log_loss_values = [
        item[
            "log_loss"
        ]
        for item in labelled
        if item[
            "log_loss"
        ] is not None
    ]

    positive_scores = [
        item[
            "score"
        ]
        for item in labelled
        if (
            item[
                "expected_label"
            ] == "positive"
            and item[
                "score"
            ] is not None
        )
    ]

    negative_scores = [
        item[
            "score"
        ]
        for item in labelled
        if (
            item[
                "expected_label"
            ] == "negative"
            and item[
                "score"
            ] is not None
        )
    ]

    return {
        "labelled_count": len(
            labelled
        ),
        "accuracy": _round(
            accuracy
        ),
        "decisive_count": len(
            decisive
        ),
        "decisive_accuracy": _round(
            decisive_accuracy
        ),
        "balanced_accuracy_positive_negative": _round(
            balanced_accuracy
        ),
        "per_class_recall": {
            key: _round(
                value
            )
            for key, value in per_class_recall.items()
        },
        "mean_brier_score": _round(
            _mean(
                [
                    float(
                        value
                    )
                    for value in brier_values
                ]
            )
        ),
        "mean_log_loss": _round(
            _mean(
                [
                    float(
                        value
                    )
                    for value in log_loss_values
                ]
            )
        ),
        "mean_positive_score": _round(
            _mean(
                [
                    float(
                        value
                    )
                    for value in positive_scores
                ]
            )
        ),
        "mean_negative_score": _round(
            _mean(
                [
                    float(
                        value
                    )
                    for value in negative_scores
                ]
            )
        ),
        "score_semantics": (
            "preference_tendency_not_truth_probability"
        ),
    }


def _aggregate_pairwise(
    cases: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    labelled = [
        case[
            "pairwise"
        ]
        for case in cases
        if case[
            "pairwise"
        ][
            "labelled"
        ]
    ]

    decisions = [
        item
        for item in labelled
        if isinstance(
            item[
                "correct"
            ],
            bool,
        )
    ]

    margins = [
        float(
            item[
                "chosen_minus_rejected_margin"
            ]
        )
        for item in labelled
        if item[
            "chosen_minus_rejected_margin"
        ] is not None
    ]

    losses = [
        float(
            item[
                "pairwise_log_loss"
            ]
        )
        for item in labelled
        if item[
            "pairwise_log_loss"
        ] is not None
    ]

    ties = [
        item
        for item in labelled
        if isinstance(
            item[
                "tie"
            ],
            bool,
        )
    ]

    return {
        "labelled_count": len(
            labelled
        ),
        "decision_count": len(
            decisions
        ),
        "accuracy": _round(
            _safe_div(
                sum(
                    1
                    for item in decisions
                    if item[
                        "correct"
                    ]
                ),
                len(
                    decisions
                ),
            )
        ),
        "tie_rate": _round(
            _safe_div(
                sum(
                    1
                    for item in ties
                    if item[
                        "tie"
                    ]
                ),
                len(
                    ties
                ),
            )
        ),
        "mean_chosen_minus_rejected_margin": _round(
            _mean(
                margins
            )
        ),
        "mean_pairwise_log_loss": _round(
            _mean(
                losses
            )
        ),
    }


def _aggregate_ranking(
    cases: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    return {
        "top1_acceptance_rate": _boolean_rate(
            cases,
            (
                "ranking",
                "top1_acceptable",
            ),
        ),
        "mean_reciprocal_rank": _numeric_mean(
            cases,
            (
                "ranking",
                "reciprocal_rank",
            ),
        ),
        "mean_ndcg": _numeric_mean(
            cases,
            (
                "ranking",
                "ndcg",
            ),
        ),
        "mean_required_pairwise_order_accuracy": _numeric_mean(
            cases,
            (
                "ranking",
                "required_pairwise_order_accuracy",
            ),
        ),
    }


def _tag_breakdown(
    cases: Sequence[
        Mapping[str, Any]
    ],
) -> Dict[str, Any]:
    by_tag: Dict[
        str,
        List[
            Mapping[
                str,
                Any,
            ]
        ],
    ] = defaultdict(
        list
    )

    for case in cases:
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
            "pointwise_accuracy": _aggregate_pointwise(
                subset
            )[
                "accuracy"
            ],
            "pairwise_accuracy": _aggregate_pairwise(
                subset
            )[
                "accuracy"
            ],
            "ranking_top1_acceptance_rate": _aggregate_ranking(
                subset
            )[
                "top1_acceptance_rate"
            ][
                "rate"
            ],
            "epistemic_contract_pass_rate": _boolean_rate(
                subset,
                (
                    "epistemic_contract",
                    "passes",
                ),
            )[
                "rate"
            ],
        }

    return result


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------


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

        "feedback_interpretation": {
            "feedback_type_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "feedback_types",
                ),
            ),
            "sentiment_accuracy": _boolean_rate(
                cases,
                (
                    "feedback_interpretation",
                    "sentiment_correct",
                ),
            ),
            "desired_depth_delta_accuracy": _boolean_rate(
                cases,
                (
                    "feedback_interpretation",
                    "desired_depth_delta_correct",
                ),
            ),
            "verbosity_delta_accuracy": _boolean_rate(
                cases,
                (
                    "feedback_interpretation",
                    "verbosity_delta_correct",
                ),
            ),
            "preferred_pattern_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "preferred_patterns",
                ),
            ),
            "avoid_pattern_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "avoid_patterns",
                ),
            ),
            "analytical_lens_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "analytical_lenses",
                ),
            ),
            "refinement_action_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "refinement_actions",
                ),
            ),
            "preferred_insight_target_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "preferred_insight_targets",
                ),
            ),
            "rejected_insight_target_micro": _aggregate_prf(
                cases,
                (
                    "feedback_interpretation",
                    "rejected_insight_targets",
                ),
            ),
        },

        "profile": {
            "desired_depth_accuracy": _boolean_rate(
                cases,
                (
                    "profile",
                    "desired_depth_correct",
                ),
            ),
            "verbosity_accuracy": _boolean_rate(
                cases,
                (
                    "profile",
                    "verbosity_correct",
                ),
            ),
            "preferred_pattern_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "preferred_patterns",
                ),
            ),
            "avoid_pattern_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "avoid_patterns",
                ),
            ),
            "analytical_lens_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "analytical_lenses",
                ),
            ),
            "preferred_insight_id_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "preferred_insight_ids",
                ),
            ),
            "rejected_insight_id_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "rejected_insight_ids",
                ),
            ),
            "positive_example_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "positive_examples",
                ),
            ),
            "negative_example_micro": _aggregate_prf(
                cases,
                (
                    "profile",
                    "negative_examples",
                ),
            ),
        },

        "pointwise": _aggregate_pointwise(
            cases
        ),

        "pairwise": _aggregate_pairwise(
            cases
        ),

        "ranking": _aggregate_ranking(
            cases
        ),

        "preference_data_integrity": {
            "case_pass_rate": _boolean_rate(
                cases,
                (
                    "preference_data_integrity",
                    "passes",
                ),
            ),
            "total_grounding_evidence_violations": sum(
                int(
                    case[
                        "preference_data_integrity"
                    ][
                        "grounding_evidence_violation_count"
                    ]
                )
                for case in cases
            ),
            "total_pair_self_comparison_violations": sum(
                int(
                    case[
                        "preference_data_integrity"
                    ][
                        "pair_self_comparison_violation_count"
                    ]
                )
                for case in cases
            ),
            "total_split_leakage_groups": sum(
                int(
                    case[
                        "preference_data_integrity"
                    ][
                        "split_leakage_group_count"
                    ]
                )
                for case in cases
            ),
            "total_test_optimization_overlaps": sum(
                int(
                    case[
                        "preference_data_integrity"
                    ][
                        "test_optimization_overlap_count"
                    ]
                )
                for case in cases
            ),
            "total_prohibited_context_violations": sum(
                int(
                    case[
                        "preference_data_integrity"
                    ][
                        "prohibited_context_count"
                    ]
                )
                for case in cases
            ),
        },

        "epistemic_contract": {
            "pass_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "passes",
                ),
            ),
            "preference_as_grounding_evidence_violation_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "preference_data_is_grounding_evidence",
                ),
            ),
            "truth_or_grounding_score_claim_violation_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "score_claims_truth_or_grounding_confidence",
                ),
            ),
            "research_depth_mutation_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "research_depth_mutation",
                ),
            ),
            "policy_modification_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "policy_modification",
                ),
            ),
            "release_authorization_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "release_authorization",
                ),
            ),
            "prohibited_control_or_evidence_field_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "prohibited_control_or_evidence_field_present",
                ),
            ),
            "private_reasoning_contamination_rate": _boolean_rate(
                cases,
                (
                    "epistemic_contract",
                    "private_reasoning_contamination",
                ),
            ),
        },

        "by_case_tag": _tag_breakdown(
            cases
        ),
    }

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "metrics": metrics,
        "missing_prediction_ids": missing_prediction_ids,
        "extra_prediction_ids": extra_prediction_ids,
        "case_results": cases,
        "metadata": {
            "evaluation_scope": (
                "labelled_user_preference_targets_only"
            ),
            "preference_data_is_factual_evidence": False,
            "preference_score_is_truth_probability": False,
            "preference_score_is_grounding_confidence": False,
            "preference_score_authorizes_release": False,
            "test_split_should_be_used_for_optimization": False,
            "research_depth_is_semantic_preference": False,
            "private_chain_of_thought_required": False,
            "paid_service_used": False,
        },
    }


# ---------------------------------------------------------------------------
# EvaluationRunner contract
# ---------------------------------------------------------------------------


def run_evaluation(
    gold_path: str | Path,
    predictions_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    EvaluationRunner-compatible entry point.
    """
    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for preference evaluation."
        )

    return evaluate_predictions(
        load_jsonl(
            gold_path
        ),
        load_jsonl(
            predictions_path
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    argv: Optional[
        Sequence[str]
    ] = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the user-preference learning and ranking path "
            "against labelled gold."
        )
    )

    parser.add_argument(
        "--gold",
        required=True,
        help="Path to preference_gold.jsonl",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to preference predictions JSONL",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output JSON path",
    )

    args = parser.parse_args(
        argv
    )

    result = run_evaluation(
        args.gold,
        args.predictions,
    )

    serialized = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    if args.output:
        output_path = Path(
            args.output
        )
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output_path.write_text(
            serialized,
            encoding="utf-8",
        )
    else:
        print(
            serialized
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )