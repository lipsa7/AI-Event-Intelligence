"""
Preference-aware insight ranking service.

Recommended location:
    services/insight_ranker_service.py

Purpose
-------
StrategicInferenceAgent
        ↓
candidate insights
        ↓
InsightQualityService
        ↓
quality assessments
        ↓
InsightRankerService
        ├─ current user preference profile
        ├─ positive / negative quality examples
        ├─ explicit reasoning-pattern preferences
        ├─ optional locally trained preference scorer
        └─ optional redundancy/diversity penalty
        ↓
ranked insight candidates
        ↓
Grounding / Red Team / report composition / release gates

Critical boundary
-----------------
RANKING != TRUTH
PREFERENCE != EVIDENCE
RANKING SCORE != CONFIDENCE

This service ranks candidate insights by usefulness/preference fit. It does NOT:
- generate new factual claims;
- create evidence;
- change grounding status;
- lower evidence standards;
- convert a weak insight into a releasable one;
- authorize report release;
- change model routing;
- change search/runtime budgets;
- change Fast/Deep execution mode.

A highly preferred but unsupported insight remains ineligible.

Typical ranking dimensions
--------------------------
1. quality score supplied by InsightQualityService;
2. fit to preferred reasoning patterns;
3. avoidance of known disliked failure patterns;
4. similarity to positive quality examples;
5. similarity to negative quality examples;
6. analytical-lens alignment;
7. optional learned preference-model score;
8. optional redundancy penalty among already selected insights.

The default deterministic scorer is local/free and requires no LLM or paid API.

Fast / Deep
-----------
The ranker is execution-profile agnostic. Fast and Deep runs use the same code.
`research_depth` is deliberately not accepted as a preference signal here.

Learned scorer
--------------
A future local model trained from PreferenceDataService may be injected through
`learned_preference_scorer`.

Accepted forms:
    callable(...)
or object exposing:
    score(...)

Expected return:
    float in [0, 1]
or:
    {
        "score": 0.0 .. 1.0,
        "model_version": "...",
        "metadata": {...}
    }

The learned score is bounded and can never override eligibility gates.

Diversity
---------
Optional greedy re-ranking reduces near-duplicate insight ordering:

    adjusted_score =
        base_score
        - diversity_strength * max_similarity_to_already_selected

This is not DPP and should not be described as DPP.

No private chain-of-thought is stored. The service returns only structured
scoring features and policy-safe ranking diagnostics.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "insight-ranker-service-v2.0"

ALLOWED_INSIGHT_TYPES = {
    "descriptive",
    "prospective",
}

ALLOWED_QUALITY_STATUSES = {
    "strong",
    "usable_with_limits",
    "needs_refinement",
    "insufficient_evidence",
}

DEFAULT_RANKING_ELIGIBLE_STATUSES = {
    "strong",
    "usable_with_limits",
    "needs_refinement",
}

DEFAULT_RELEASE_SAFE_STATUSES = {
    "strong",
    "usable_with_limits",
}

ALLOWED_QUALITY_PATTERNS = {
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

ALLOWED_AVOID_PATTERNS = {
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

# Quality defect -> disliked pattern mapping.
DEFECT_TO_AVOID_PATTERN = {
    "fact_restatement": "fact_restatement",
    "weak_cross_event_synthesis": "surface_level_summary",
    "missing_mechanism": "surface_level_summary",
    "missing_second_order_effect": "surface_level_summary",
    "baseline_restatement": "obvious_implication",
    "unsupported_inference": "unsupported_leap",
    "contradicted_insight": "unsupported_leap",
    "missing_uncertainty": "premature_certainty",
    "counterevidence_not_established": "premature_certainty",
    "prospective_mechanism_missing": "unsupported_leap",
    "prospective_feasibility_unchecked": "excessive_speculation",
}

DEFAULT_CONFIG = {
    # Composite-score weights. These are ranking heuristics and should be
    # calibrated using preference_eval.py rather than treated as universal.
    "quality_weight": 0.50,
    "preferred_pattern_weight": 0.15,
    "example_preference_weight": 0.12,
    "lens_weight": 0.05,
    "learned_preference_weight": 0.18,
    "avoid_pattern_penalty": 0.18,
    "direct_preference_bonus": 0.05,
    "direct_rejection_penalty": 0.10,

    # Greedy diversity penalty after base scoring.
    "diversity_strength": 0.10,

    # Similarity calibration.
    "positive_example_similarity_floor": 0.55,
    "negative_example_similarity_floor": 0.55,

    # Production should normally require quality assessments.
    "require_quality_assessment": True,

    # Candidate ranking may include needs_refinement so the orchestrator can
    # decide which weak candidates deserve another refinement round.
    "ranking_eligible_statuses": sorted(
        DEFAULT_RANKING_ELIGIBLE_STATUSES
    ),

    # This service does not release anything, but exposes whether the quality
    # status is in a conservative release-safe set for downstream gates.
    "release_safe_statuses": sorted(
        DEFAULT_RELEASE_SAFE_STATUSES
    ),

    # When no learned scorer is installed, its weight is redistributed over
    # deterministic positive components rather than lowering all scores.
    "renormalize_missing_learned_scorer": True,
}


class InsightRankerError(RuntimeError):
    """Base exception."""


class InsightRankerValidationError(InsightRankerError):
    """Raised when an input violates the ranking contract."""


class InsightRankerIntegrityError(InsightRankerError):
    """Raised when IDs/quality lineage are inconsistent."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_enum(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    return str(value)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")

    return hashlib.sha256(
        value
    ).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(
                payload
            )
        )[:length].upper()
    )


def _fingerprint(value: Any) -> str:
    return _sha256(
        _canonical_json_bytes(
            value
        )
    )


def _stable_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(
        values,
        (list, tuple, set),
    ):
        raise InsightRankerValidationError(
            "Expected a string or sequence of strings."
        )

    result = []
    seen = set()

    for value in values:
        text = _clean(
            value
        )

        if text and text not in seen:
            seen.add(
                text
            )
            result.append(
                text
            )

    return result


def _tokenize(text: str) -> Set[str]:
    return {
        token.casefold()
        for token in re.findall(
            r"[a-z0-9]+",
            text,
            flags=re.IGNORECASE,
        )
    }


def _lexical_similarity(
    left: str,
    right: str,
) -> float:
    """
    Deterministic local fallback.

    Production can inject local EmbeddingService cosine similarity.
    """
    left_tokens = _tokenize(
        left
    )
    right_tokens = _tokenize(
        right
    )

    if not left_tokens and not right_tokens:
        return 1.0

    if not left_tokens or not right_tokens:
        return 0.0

    return (
        len(
            left_tokens
            & right_tokens
        )
        / len(
            left_tokens
            | right_tokens
        )
    )


def _normalize_score(
    value: Any,
    *,
    field_name: str,
) -> float:
    try:
        numeric = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise InsightRankerValidationError(
            f"{field_name} must be numeric."
        ) from exc

    if not 0.0 <= numeric <= 1.0:
        raise InsightRankerValidationError(
            f"{field_name} must be in [0, 1]."
        )

    return numeric


def _normalize_example(
    value: Any,
) -> Dict[str, str]:
    if isinstance(
        value,
        str,
    ):
        text = _clean(
            value
        )

        if not text:
            raise InsightRankerValidationError(
                "Preference example text cannot be empty."
            )

        return {
            "text": text,
            "reason": "",
        }

    if not isinstance(
        value,
        Mapping,
    ):
        raise InsightRankerValidationError(
            "Preference example must be text or an object."
        )

    text = _clean(
        value.get("text")
        or value.get("insight")
        or value.get("example")
    )

    if not text:
        raise InsightRankerValidationError(
            "Preference example is missing text."
        )

    return {
        "text": text,
        "reason": _clean(
            value.get("reason")
            or value.get("why")
        ),
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class InsightRankerService:
    """
    Preference-aware, quality-gated insight ranker.

    `semantic_similarity_fn`
    ------------------------
    Expected:
        similarity(text_a, text_b) -> float in [0, 1]

    `learned_preference_scorer`
    ---------------------------
    Optional local scorer trained downstream from PreferenceDataService.
    """

    def __init__(
        self,
        *,
        semantic_similarity_fn: Optional[
            Callable[[str, str], float]
        ] = None,
        learned_preference_scorer: Optional[Any] = None,
        config: Optional[
            Mapping[str, Any]
        ] = None,
        model_name: str = "",
    ) -> None:
        if (
            semantic_similarity_fn is not None
            and not callable(
                semantic_similarity_fn
            )
        ):
            raise InsightRankerValidationError(
                "semantic_similarity_fn must be callable."
            )

        self.semantic_similarity_fn = (
            semantic_similarity_fn
            or _lexical_similarity
        )

        self.learned_preference_scorer = (
            learned_preference_scorer
        )
        self.model_name = _clean(
            model_name
        )
        self.config = self._normalize_config(
            config
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        *,
        insights: Sequence[
            Mapping[str, Any]
        ],
        quality_assessments: Optional[
            Mapping[str, Mapping[str, Any]]
        ] = None,
        preference_profile: Optional[
            Mapping[str, Any]
        ] = None,
        context: Optional[
            Mapping[str, Any]
        ] = None,
        top_k: Optional[int] = None,
        apply_diversity: bool = True,
    ) -> Dict[str, Any]:
        """
        Rank candidate insights.

        `quality_assessments`
        ---------------------
        Mapping:
            insight_id -> InsightQualityService output

        If `require_quality_assessment=True`, missing assessments fail closed.

        `preference_profile`
        --------------------
        UserPreferenceService profile or compiler_preferences() subset.

        This service accepts semantic preferences only. `research_depth` is
        prohibited.
        """
        normalized_insights = self._normalize_insights(
            insights
        )

        quality = self._normalize_quality_assessments(
            quality_assessments
        )

        preferences = self._normalize_preference_profile(
            preference_profile
        )

        normalized_context = self._normalize_context(
            context
        )

        if (
            self.config[
                "require_quality_assessment"
            ]
        ):
            missing = sorted(
                set(
                    normalized_insights
                )
                - set(
                    quality
                )
            )

            if missing:
                raise InsightRankerIntegrityError(
                    "Missing quality assessments for insight IDs: "
                    + ", ".join(
                        missing
                    )
                )

        if top_k is None:
            top_k = len(
                normalized_insights
            )

        try:
            top_k = int(
                top_k
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise InsightRankerValidationError(
                "top_k must be an integer."
            ) from exc

        if top_k < 0:
            raise InsightRankerValidationError(
                "top_k must be >= 0."
            )

        scored = []

        for insight_id, insight in normalized_insights.items():
            assessment = quality.get(
                insight_id,
                {}
            )

            scored.append(
                self._score_one(
                    insight=insight,
                    quality_assessment=assessment,
                    preference_profile=preferences,
                    context=normalized_context,
                )
            )

        ranked = self._rank_scored(
            scored,
            top_k=top_k,
            apply_diversity=bool(
                apply_diversity
            ),
        )

        ranking_id = _stable_id(
            "INSIGHTRANK",
            {
                "insight_ids": sorted(
                    normalized_insights
                ),
                "quality_fingerprints": {
                    insight_id: _fingerprint(
                        assessment
                    )
                    for insight_id, assessment
                    in quality.items()
                },
                "preference_profile": preferences,
                "context": normalized_context,
                "top_k": top_k,
                "apply_diversity": bool(
                    apply_diversity
                ),
                "config": self.config,
            },
        )

        return {
            "ranking_id": ranking_id,
            "ranked_insights": ranked,
            "candidate_count": len(
                normalized_insights
            ),
            "returned_count": len(
                ranked
            ),
            "ineligible_insight_ids": [
                item[
                    "insight_id"
                ]
                for item in scored
                if not item[
                    "ranking_eligible"
                ]
            ],
            "metadata": {
                "service_version": SERVICE_VERSION,
                "ranking_score_semantics": (
                    "preference_usefulness_score_not_truth_probability"
                ),
                "preference_data_is_grounding_evidence": False,
                "learned_preference_scorer_used": (
                    self.learned_preference_scorer
                    is not None
                ),
                "learned_model_name": self.model_name,
                "profile_agnostic": True,
                "research_depth_modified_here": False,
                "authorizes_release": False,
                "modifies_grounding": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def score(
        self,
        *,
        insight: Mapping[str, Any],
        quality_assessment: Optional[
            Mapping[str, Any]
        ] = None,
        preference_profile: Optional[
            Mapping[str, Any]
        ] = None,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Score one insight without diversity re-ranking.
        """
        normalized = self._normalize_insight(
            insight
        )

        quality_map = self._normalize_quality_assessments(
            {
                normalized[
                    "insight_id"
                ]: (
                    quality_assessment
                    or {}
                )
            }
            if quality_assessment is not None
            else {}
        )

        if (
            self.config[
                "require_quality_assessment"
            ]
            and normalized[
                "insight_id"
            ]
            not in quality_map
        ):
            raise InsightRankerIntegrityError(
                "quality_assessment is required."
            )

        return self._score_one(
            insight=normalized,
            quality_assessment=quality_map.get(
                normalized[
                    "insight_id"
                ],
                {},
            ),
            preference_profile=self._normalize_preference_profile(
                preference_profile
            ),
            context=self._normalize_context(
                context
            ),
        )

    def compare(
        self,
        *,
        left_insight: Mapping[str, Any],
        right_insight: Mapping[str, Any],
        quality_assessments: Mapping[
            str,
            Mapping[str, Any],
        ],
        preference_profile: Optional[
            Mapping[str, Any]
        ] = None,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Pairwise deterministic comparison for evaluation/UI.

        This is not a training operation.
        """
        result = self.rank(
            insights=[
                left_insight,
                right_insight,
            ],
            quality_assessments=quality_assessments,
            preference_profile=preference_profile,
            context=context,
            top_k=2,
            apply_diversity=False,
        )

        ranked = result[
            "ranked_insights"
        ]

        if len(
            ranked
        ) != 2:
            return {
                "preferred_insight_id": (
                    ranked[0][
                        "insight_id"
                    ]
                    if ranked
                    else ""
                ),
                "other_insight_id": "",
                "tie": False,
                "ranking_id": result[
                    "ranking_id"
                ],
            }

        difference = abs(
            ranked[0][
                "base_score"
            ]
            - ranked[1][
                "base_score"
            ]
        )

        return {
            "preferred_insight_id": ranked[
                0
            ][
                "insight_id"
            ],
            "other_insight_id": ranked[
                1
            ][
                "insight_id"
            ],
            "tie": bool(
                difference < 1e-9
            ),
            "score_difference": round(
                difference,
                6,
            ),
            "ranking_id": result[
                "ranking_id"
            ],
        }

    # ------------------------------------------------------------------
    # One-candidate scoring
    # ------------------------------------------------------------------

    def _score_one(
        self,
        *,
        insight: Mapping[str, Any],
        quality_assessment: Mapping[str, Any],
        preference_profile: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        quality_status = _normalize_enum(
            quality_assessment.get(
                "quality_status"
            )
        )

        if quality_assessment:
            if (
                quality_status
                not in ALLOWED_QUALITY_STATUSES
            ):
                raise InsightRankerValidationError(
                    "Unsupported quality_status="
                    f"{quality_status!r} for "
                    f"{insight['insight_id']!r}."
                )

            quality_score = _normalize_score(
                quality_assessment.get(
                    "quality_score"
                ),
                field_name="quality_score",
            )
        else:
            quality_status = ""
            quality_score = 0.0

        ranking_eligible = bool(
            not quality_assessment
            or quality_status
            in set(
                self.config[
                    "ranking_eligible_statuses"
                ]
            )
        )

        release_safe_quality_status = bool(
            quality_assessment
            and quality_status
            in set(
                self.config[
                    "release_safe_statuses"
                ]
            )
        )

        observed_patterns = self._observed_patterns(
            insight=insight,
            quality_assessment=quality_assessment,
        )

        observed_avoid = self._observed_avoid_patterns(
            insight=insight,
            quality_assessment=quality_assessment,
        )

        pattern_fit = self._pattern_fit(
            preferred=preference_profile[
                "preferred_patterns"
            ],
            observed=observed_patterns,
        )

        avoid_penalty = self._avoid_penalty(
            avoided=preference_profile[
                "avoid_patterns"
            ],
            observed=observed_avoid,
        )

        example_fit = self._example_fit(
            insight_text=insight[
                "text"
            ],
            positive_examples=preference_profile[
                "positive_examples"
            ],
            negative_examples=preference_profile[
                "negative_examples"
            ],
        )

        lens_fit = self._lens_fit(
            preferred_lenses=preference_profile[
                "analytical_lenses"
            ],
            insight_lenses=insight[
                "analytical_lenses"
            ],
        )

        learned_score = None
        learned_metadata = {}

        if (
            self.learned_preference_scorer
            is not None
        ):
            (
                learned_score,
                learned_metadata,
            ) = self._invoke_learned_scorer(
                insight=insight,
                quality_assessment=quality_assessment,
                preference_profile=preference_profile,
                context=context,
            )

        direct_adjustment = self._direct_preference_adjustment(
            insight_id=insight[
                "insight_id"
            ],
            preference_profile=preference_profile,
        )

        base_score = self._composite_score(
            quality_score=quality_score,
            pattern_fit=pattern_fit,
            example_fit=example_fit[
                "net_score"
            ],
            lens_fit=lens_fit,
            learned_score=learned_score,
            avoid_penalty=avoid_penalty,
            direct_adjustment=direct_adjustment,
        )

        # Preference can never resurrect an ineligible quality status.
        if not ranking_eligible:
            base_score = 0.0

        return {
            "insight_id": insight[
                "insight_id"
            ],
            "insight_revision_id": insight[
                "insight_revision_id"
            ],
            "insight_type": insight[
                "insight_type"
            ],
            "text": insight[
                "text"
            ],
            "quality_status": quality_status,
            "ranking_eligible": ranking_eligible,
            "release_safe_quality_status": (
                release_safe_quality_status
            ),
            "base_score": round(
                base_score,
                6,
            ),
            "diversity_adjusted_score": round(
                base_score,
                6,
            ),
            "score_components": {
                "quality_score": round(
                    quality_score,
                    6,
                ),
                "preferred_pattern_fit": round(
                    pattern_fit,
                    6,
                ),
                "avoid_pattern_penalty": round(
                    avoid_penalty,
                    6,
                ),
                "example_preference_fit": round(
                    example_fit[
                        "net_score"
                    ],
                    6,
                ),
                "positive_example_similarity": round(
                    example_fit[
                        "positive_similarity"
                    ],
                    6,
                ),
                "negative_example_similarity": round(
                    example_fit[
                        "negative_similarity"
                    ],
                    6,
                ),
                "analytical_lens_fit": round(
                    lens_fit,
                    6,
                ),
                "learned_preference_score": (
                    round(
                        learned_score,
                        6,
                    )
                    if learned_score
                    is not None
                    else None
                ),
                "direct_preference_adjustment": round(
                    direct_adjustment,
                    6,
                ),
            },
            "ranking_trace": {
                "observed_quality_patterns": observed_patterns,
                "observed_avoid_patterns": observed_avoid,
                "preferred_patterns_considered": list(
                    preference_profile[
                        "preferred_patterns"
                    ]
                ),
                "avoid_patterns_considered": list(
                    preference_profile[
                        "avoid_patterns"
                    ]
                ),
                "analytical_lenses_considered": list(
                    preference_profile[
                        "analytical_lenses"
                    ]
                ),
                "nearest_positive_example": example_fit[
                    "nearest_positive_example"
                ],
                "nearest_negative_example": example_fit[
                    "nearest_negative_example"
                ],
                "learned_scorer_metadata": learned_metadata,
                "quality_assessment_id": _clean(
                    quality_assessment.get(
                        "quality_assessment_id"
                    )
                ),
                "quality_defect_codes": [
                    _normalize_enum(
                        defect.get(
                            "code"
                        )
                    )
                    for defect
                    in quality_assessment.get(
                        "defects",
                        [],
                    )
                    if isinstance(
                        defect,
                        Mapping,
                    )
                ],
                "private_chain_of_thought_stored": False,
            },
        }

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    def _composite_score(
        self,
        *,
        quality_score: float,
        pattern_fit: float,
        example_fit: float,
        lens_fit: float,
        learned_score: Optional[float],
        avoid_penalty: float,
        direct_adjustment: float,
    ) -> float:
        positive_weights = {
            "quality": self.config[
                "quality_weight"
            ],
            "pattern": self.config[
                "preferred_pattern_weight"
            ],
            "example": self.config[
                "example_preference_weight"
            ],
            "lens": self.config[
                "lens_weight"
            ],
            "learned": self.config[
                "learned_preference_weight"
            ],
        }

        positive_values = {
            "quality": quality_score,
            "pattern": pattern_fit,
            "example": example_fit,
            "lens": lens_fit,
            "learned": (
                learned_score
                if learned_score
                is not None
                else 0.0
            ),
        }

        active_keys = [
            "quality",
            "pattern",
            "example",
            "lens",
        ]

        if learned_score is not None:
            active_keys.append(
                "learned"
            )

        if (
            learned_score is None
            and not self.config[
                "renormalize_missing_learned_scorer"
            ]
        ):
            active_keys.append(
                "learned"
            )

        denominator = sum(
            positive_weights[
                key
            ]
            for key
            in active_keys
        )

        if denominator <= 0:
            positive = 0.0
        else:
            positive = (
                sum(
                    positive_weights[
                        key
                    ]
                    * positive_values[
                        key
                    ]
                    for key
                    in active_keys
                )
                / denominator
            )

        score = (
            positive
            - self.config[
                "avoid_pattern_penalty"
            ]
            * avoid_penalty
            + direct_adjustment
        )

        return max(
            0.0,
            min(
                1.0,
                score,
            ),
        )

    def _pattern_fit(
        self,
        *,
        preferred: Sequence[str],
        observed: Sequence[str],
    ) -> float:
        preferred_set = set(
            preferred
        )

        if not preferred_set:
            return 0.5

        observed_set = set(
            observed
        )

        return (
            len(
                preferred_set
                & observed_set
            )
            / len(
                preferred_set
            )
        )

    def _avoid_penalty(
        self,
        *,
        avoided: Sequence[str],
        observed: Sequence[str],
    ) -> float:
        avoided_set = set(
            avoided
        )

        if not avoided_set:
            return 0.0

        observed_set = set(
            observed
        )

        return (
            len(
                avoided_set
                & observed_set
            )
            / len(
                avoided_set
            )
        )

    def _example_fit(
        self,
        *,
        insight_text: str,
        positive_examples: Sequence[
            Mapping[str, str]
        ],
        negative_examples: Sequence[
            Mapping[str, str]
        ],
    ) -> Dict[str, Any]:
        positive_similarity = 0.0
        negative_similarity = 0.0
        nearest_positive = {}
        nearest_negative = {}

        for example in positive_examples:
            similarity = self._similarity(
                insight_text,
                example[
                    "text"
                ],
            )

            if (
                similarity
                > positive_similarity
            ):
                positive_similarity = similarity
                nearest_positive = deepcopy(
                    dict(
                        example
                    )
                )

        for example in negative_examples:
            similarity = self._similarity(
                insight_text,
                example[
                    "text"
                ],
            )

            if (
                similarity
                > negative_similarity
            ):
                negative_similarity = similarity
                nearest_negative = deepcopy(
                    dict(
                        example
                    )
                )

        positive_signal = self._similarity_above_floor(
            positive_similarity,
            self.config[
                "positive_example_similarity_floor"
            ],
        )

        negative_signal = self._similarity_above_floor(
            negative_similarity,
            self.config[
                "negative_example_similarity_floor"
            ],
        )

        # 0.5 means neutral preference fit.
        net_score = max(
            0.0,
            min(
                1.0,
                0.5
                + 0.5
                * positive_signal
                - 0.5
                * negative_signal,
            ),
        )

        return {
            "net_score": net_score,
            "positive_similarity": positive_similarity,
            "negative_similarity": negative_similarity,
            "nearest_positive_example": nearest_positive,
            "nearest_negative_example": nearest_negative,
        }

    def _similarity_above_floor(
        self,
        similarity: float,
        floor: float,
    ) -> float:
        if similarity <= floor:
            return 0.0

        if floor >= 1.0:
            return 0.0

        return max(
            0.0,
            min(
                1.0,
                (
                    similarity
                    - floor
                )
                / (
                    1.0
                    - floor
                ),
            ),
        )

    def _lens_fit(
        self,
        *,
        preferred_lenses: Sequence[str],
        insight_lenses: Sequence[str],
    ) -> float:
        preferred = {
            _clean(
                item
            ).casefold()
            for item
            in preferred_lenses
            if _clean(
                item
            )
        }

        if not preferred:
            return 0.5

        observed = {
            _clean(
                item
            ).casefold()
            for item
            in insight_lenses
            if _clean(
                item
            )
        }

        return (
            len(
                preferred
                & observed
            )
            / len(
                preferred
            )
        )

    def _direct_preference_adjustment(
        self,
        *,
        insight_id: str,
        preference_profile: Mapping[str, Any],
    ) -> float:
        adjustment = 0.0

        if (
            insight_id
            in preference_profile[
                "preferred_insight_ids"
            ]
        ):
            adjustment += self.config[
                "direct_preference_bonus"
            ]

        if (
            insight_id
            in preference_profile[
                "rejected_insight_ids"
            ]
        ):
            adjustment -= self.config[
                "direct_rejection_penalty"
            ]

        return adjustment

    # ------------------------------------------------------------------
    # Pattern observation
    # ------------------------------------------------------------------

    def _observed_patterns(
        self,
        *,
        insight: Mapping[str, Any],
        quality_assessment: Mapping[str, Any],
    ) -> list[str]:
        observed = set(
            insight[
                "reasoning_patterns"
            ]
        )

        if len(
            insight[
                "event_ids"
            ]
        ) >= 2:
            observed.add(
                "cross_event_synthesis"
            )

        if insight[
            "mechanism"
        ]:
            observed.add(
                "mechanism"
            )

        if insight[
            "second_order_effect"
        ]:
            observed.add(
                "second_order_effect"
            )

        if insight[
            "structural_change"
        ]:
            observed.add(
                "structural_change"
            )

        if insight[
            "counterevidence_ids"
        ]:
            observed.add(
                "counterevidence"
            )

        if insight[
            "alternative_explanations"
        ]:
            observed.add(
                "alternative_explanation"
            )

        if insight[
            "critical_unknowns"
        ]:
            observed.add(
                "explicit_uncertainty"
            )

        if insight[
            "insight_type"
        ] == "prospective":
            if insight[
                "mechanism"
            ]:
                observed.add(
                    "prospective_mechanism"
                )

            if (
                insight[
                    "feasibility"
                ]
                or insight[
                    "constraint_ids"
                ]
            ):
                observed.add(
                    "feasibility_check"
                )

        # InsightQualityService may already have explicit quality patterns.
        trace = quality_assessment.get(
            "quality_trace"
        ) or {}

        if isinstance(
            trace,
            Mapping,
        ):
            for pattern in (
                trace.get(
                    "observed_quality_patterns"
                )
                or []
            ):
                normalized = _normalize_enum(
                    pattern
                )

                if (
                    normalized
                    in ALLOWED_QUALITY_PATTERNS
                ):
                    observed.add(
                        normalized
                    )

        return sorted(
            item
            for item in observed
            if item
            in ALLOWED_QUALITY_PATTERNS
        )

    def _observed_avoid_patterns(
        self,
        *,
        insight: Mapping[str, Any],
        quality_assessment: Mapping[str, Any],
    ) -> list[str]:
        observed = set(
            insight[
                "avoid_pattern_flags"
            ]
        )

        defects = quality_assessment.get(
            "defects"
        ) or []

        if not isinstance(
            defects,
            Sequence,
        ) or isinstance(
            defects,
            (str, bytes),
        ):
            raise InsightRankerValidationError(
                "quality_assessment.defects must be a sequence."
            )

        for defect in defects:
            if not isinstance(
                defect,
                Mapping,
            ):
                continue

            code = _normalize_enum(
                defect.get(
                    "code"
                )
            )

            mapped = DEFECT_TO_AVOID_PATTERN.get(
                code
            )

            if mapped:
                observed.add(
                    mapped
                )

        return sorted(
            item
            for item in observed
            if item
            in ALLOWED_AVOID_PATTERNS
        )

    # ------------------------------------------------------------------
    # Learned preference scorer
    # ------------------------------------------------------------------

    def _invoke_learned_scorer(
        self,
        *,
        insight: Mapping[str, Any],
        quality_assessment: Mapping[str, Any],
        preference_profile: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> tuple[
        float,
        Dict[str, Any],
    ]:
        scorer = self.learned_preference_scorer

        kwargs = {
            "insight": {
                "insight_id": insight[
                    "insight_id"
                ],
                "insight_revision_id": insight[
                    "insight_revision_id"
                ],
                "insight_type": insight[
                    "insight_type"
                ],
                "text": insight[
                    "text"
                ],
                "reasoning_patterns": insight[
                    "reasoning_patterns"
                ],
                "analytical_lenses": insight[
                    "analytical_lenses"
                ],
            },
            "quality_assessment": {
                "quality_status": _normalize_enum(
                    quality_assessment.get(
                        "quality_status"
                    )
                ),
                "quality_score": quality_assessment.get(
                    "quality_score"
                ),
                "defect_codes": [
                    _normalize_enum(
                        defect.get(
                            "code"
                        )
                    )
                    for defect
                    in quality_assessment.get(
                        "defects",
                        [],
                    )
                    if isinstance(
                        defect,
                        Mapping,
                    )
                ],
            },
            "preference_profile": deepcopy(
                dict(
                    preference_profile
                )
            ),
            "context": deepcopy(
                dict(
                    context
                )
            ),
        }

        if callable(
            scorer
        ):
            raw = self._call_with_supported_kwargs(
                scorer,
                **kwargs,
            )

        else:
            method = getattr(
                scorer,
                "score",
                None,
            )

            if not callable(
                method
            ):
                raise InsightRankerValidationError(
                    "learned_preference_scorer must be callable or expose score()."
                )

            raw = self._call_with_supported_kwargs(
                method,
                **kwargs,
            )

        if isinstance(
            raw,
            Mapping,
        ):
            score = _normalize_score(
                raw.get(
                    "score"
                ),
                field_name="learned preference score",
            )

            metadata = {
                "model_version": _clean(
                    raw.get(
                        "model_version"
                    )
                ),
                "metadata": deepcopy(
                    dict(
                        raw.get(
                            "metadata"
                        )
                        or {}
                    )
                ),
            }

        else:
            score = _normalize_score(
                raw,
                field_name="learned preference score",
            )
            metadata = {}

        return (
            score,
            metadata,
        )

    def _call_with_supported_kwargs(
        self,
        func: Callable[..., Any],
        **kwargs: Any,
    ) -> Any:
        try:
            signature = inspect.signature(
                func
            )
        except (
            TypeError,
            ValueError,
        ):
            return func(
                **kwargs
            )

        accepts_var_kw = any(
            parameter.kind
            == inspect.Parameter.VAR_KEYWORD
            for parameter
            in signature.parameters.values()
        )

        if accepts_var_kw:
            return func(
                **kwargs
            )

        supported = {
            key: value
            for key, value
            in kwargs.items()
            if key
            in signature.parameters
        }

        return func(
            **supported
        )

    # ------------------------------------------------------------------
    # Final ranking / diversity
    # ------------------------------------------------------------------

    def _rank_scored(
        self,
        scored: Sequence[
            Mapping[str, Any]
        ],
        *,
        top_k: int,
        apply_diversity: bool,
    ) -> list[Dict[str, Any]]:
        eligible = [
            deepcopy(
                dict(
                    item
                )
            )
            for item in scored
            if item[
                "ranking_eligible"
            ]
        ]

        ineligible = [
            deepcopy(
                dict(
                    item
                )
            )
            for item in scored
            if not item[
                "ranking_eligible"
            ]
        ]

        eligible.sort(
            key=lambda item: (
                -item[
                    "base_score"
                ],
                item[
                    "insight_id"
                ],
            )
        )

        if (
            apply_diversity
            and self.config[
                "diversity_strength"
            ] > 0
            and len(
                eligible
            ) > 1
        ):
            selected = []
            remaining = eligible[:]

            while remaining:
                best = None
                best_adjusted = -1.0

                for candidate in remaining:
                    if not selected:
                        max_similarity = 0.0
                    else:
                        max_similarity = max(
                            self._similarity(
                                candidate[
                                    "text"
                                ],
                                chosen[
                                    "text"
                                ],
                            )
                            for chosen
                            in selected
                        )

                    adjusted = max(
                        0.0,
                        candidate[
                            "base_score"
                        ]
                        - self.config[
                            "diversity_strength"
                        ]
                        * max_similarity,
                    )

                    candidate_copy = deepcopy(
                        candidate
                    )
                    candidate_copy[
                        "diversity_adjusted_score"
                    ] = round(
                        adjusted,
                        6,
                    )
                    candidate_copy[
                        "ranking_trace"
                    ][
                        "max_similarity_to_higher_ranked"
                    ] = round(
                        max_similarity,
                        6,
                    )

                    if (
                        best is None
                        or adjusted > best_adjusted
                        or (
                            abs(
                                adjusted
                                - best_adjusted
                            )
                            < 1e-12
                            and candidate[
                                "insight_id"
                            ]
                            < best[
                                "insight_id"
                            ]
                        )
                    ):
                        best = candidate_copy
                        best_adjusted = adjusted

                assert best is not None

                selected.append(
                    best
                )

                remaining = [
                    item
                    for item in remaining
                    if item[
                        "insight_id"
                    ]
                    != best[
                        "insight_id"
                    ]
                ]

            eligible = selected

        else:
            for item in eligible:
                item[
                    "ranking_trace"
                ][
                    "max_similarity_to_higher_ranked"
                ] = 0.0

        # Ineligible insights are intentionally excluded from ranked output.
        # They remain visible through ineligible_insight_ids in the result.
        output = eligible[
            :top_k
        ]

        for rank, item in enumerate(
            output,
            start=1,
        ):
            item[
                "rank"
            ] = rank

        return output

    def _similarity(
        self,
        left: str,
        right: str,
    ) -> float:
        similarity = float(
            self.semantic_similarity_fn(
                left,
                right,
            )
        )

        if not 0.0 <= similarity <= 1.0:
            raise InsightRankerValidationError(
                "semantic_similarity_fn must return a value in [0, 1]."
            )

        return similarity

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_insights(
        self,
        insights: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[
        str,
        Dict[str, Any],
    ]:
        if (
            not isinstance(
                insights,
                Sequence,
            )
            or isinstance(
                insights,
                (str, bytes),
            )
        ):
            raise InsightRankerValidationError(
                "insights must be a sequence."
            )

        result = {}

        for index, insight in enumerate(
            insights
        ):
            if not isinstance(
                insight,
                Mapping,
            ):
                raise InsightRankerValidationError(
                    f"insights[{index}] must be an object."
                )

            normalized = self._normalize_insight(
                insight
            )

            insight_id = normalized[
                "insight_id"
            ]

            if insight_id in result:
                if (
                    _fingerprint(
                        result[
                            insight_id
                        ]
                    )
                    != _fingerprint(
                        normalized
                    )
                ):
                    raise InsightRankerIntegrityError(
                        "Duplicate insight_id has different content: "
                        f"{insight_id!r}."
                    )

                continue

            result[
                insight_id
            ] = normalized

        return result

    def _normalize_insight(
        self,
        insight: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            insight,
            Mapping,
        ):
            raise InsightRankerValidationError(
                "insight must be an object."
            )

        insight_id = _clean(
            insight.get(
                "insight_id"
            )
            or insight.get(
                "id"
            )
        )

        if not insight_id:
            raise InsightRankerValidationError(
                "insight is missing insight_id."
            )

        text = _clean(
            insight.get(
                "insight"
            )
            or insight.get(
                "text"
            )
            or insight.get(
                "statement"
            )
        )

        if not text:
            raise InsightRankerValidationError(
                f"Insight {insight_id!r} is missing text."
            )

        insight_type = _normalize_enum(
            insight.get(
                "insight_type"
            )
            or "descriptive"
        )

        if insight_type not in ALLOWED_INSIGHT_TYPES:
            raise InsightRankerValidationError(
                f"Unsupported insight_type={insight_type!r}."
            )

        mechanism = _clean(
            insight.get(
                "mechanism"
            )
        )

        mechanism_details = insight.get(
            "mechanism_details"
        )

        if (
            not mechanism
            and isinstance(
                mechanism_details,
                Mapping,
            )
        ):
            mechanism = _clean(
                mechanism_details.get(
                    "description"
                )
                or mechanism_details.get(
                    "mechanism"
                )
            )

        feasibility = insight.get(
            "feasibility"
        )

        if feasibility is None:
            feasibility = {}

        if not isinstance(
            feasibility,
            Mapping,
        ):
            feasibility = {
                "description": _clean(
                    feasibility
                )
            }

        return {
            "insight_id": insight_id,
            "insight_revision_id": (
                _clean(
                    insight.get(
                        "insight_revision_id"
                    )
                )
                or _stable_id(
                    "INSIGHTREV",
                    {
                        "insight_id": insight_id,
                        "text": text,
                    },
                )
            ),
            "insight_type": insight_type,
            "text": text,
            "event_ids": _stable_unique_strings(
                insight.get(
                    "event_ids"
                )
                or []
            ),
            "evidence_ids": _stable_unique_strings(
                insight.get(
                    "evidence_ids"
                )
                or insight.get(
                    "support_evidence_ids"
                )
                or []
            ),
            "counterevidence_ids": _stable_unique_strings(
                insight.get(
                    "counterevidence_ids"
                )
                or []
            ),
            "constraint_ids": _stable_unique_strings(
                insight.get(
                    "constraint_ids"
                )
                or []
            ),
            "mechanism": mechanism,
            "second_order_effect": _clean(
                insight.get(
                    "second_order_effect"
                )
                or insight.get(
                    "second_order_implication"
                )
                or insight.get(
                    "implication"
                )
            ),
            "structural_change": _clean(
                insight.get(
                    "structural_change"
                )
                or insight.get(
                    "structural_implication"
                )
            ),
            "critical_unknowns": _stable_unique_strings(
                insight.get(
                    "critical_unknowns"
                )
                or []
            ),
            "alternative_explanations": _stable_unique_strings(
                insight.get(
                    "alternative_explanations"
                )
                or []
            ),
            "feasibility": deepcopy(
                dict(
                    feasibility
                )
            ),
            "analytical_lenses": _stable_unique_strings(
                insight.get(
                    "analytical_lenses"
                )
                or insight.get(
                    "lenses"
                )
                or []
            ),
            "reasoning_patterns": [
                item
                for item in _stable_unique_strings(
                    insight.get(
                        "reasoning_patterns"
                    )
                    or []
                )
                if item
                in ALLOWED_QUALITY_PATTERNS
            ],
            "avoid_pattern_flags": [
                item
                for item in _stable_unique_strings(
                    insight.get(
                        "avoid_pattern_flags"
                    )
                    or []
                )
                if item
                in ALLOWED_AVOID_PATTERNS
            ],
        }

    def _normalize_quality_assessments(
        self,
        assessments: Optional[
            Mapping[str, Mapping[str, Any]]
        ],
    ) -> Dict[str, Dict[str, Any]]:
        if assessments is None:
            return {}

        if not isinstance(
            assessments,
            Mapping,
        ):
            raise InsightRankerValidationError(
                "quality_assessments must be an object keyed by insight_id."
            )

        result = {}

        for insight_id, assessment in assessments.items():
            normalized_id = _clean(
                insight_id
            )

            if not normalized_id:
                raise InsightRankerValidationError(
                    "quality_assessments contains an empty insight ID."
                )

            if not isinstance(
                assessment,
                Mapping,
            ):
                raise InsightRankerValidationError(
                    "Each quality assessment must be an object."
                )

            assessment_copy = deepcopy(
                dict(
                    assessment
                )
            )

            embedded_id = _clean(
                assessment_copy.get(
                    "insight_id"
                )
            )

            if (
                embedded_id
                and embedded_id
                != normalized_id
            ):
                raise InsightRankerIntegrityError(
                    "quality assessment key/id mismatch."
                )

            result[
                normalized_id
            ] = assessment_copy

        return result

    def _normalize_preference_profile(
        self,
        profile: Optional[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        if profile is None:
            return {
                "desired_depth": "",
                "verbosity": "",
                "preferred_patterns": [],
                "avoid_patterns": [],
                "analytical_lenses": [],
                "positive_examples": [],
                "negative_examples": [],
                "preferred_insight_ids": [],
                "rejected_insight_ids": [],
            }

        if not isinstance(
            profile,
            Mapping,
        ):
            raise InsightRankerValidationError(
                "preference_profile must be an object."
            )

        # Execution profile must never be smuggled through semantic preference
        # ranking.
        if "research_depth" in profile:
            raise InsightRankerValidationError(
                "research_depth is an execution-profile field and is not "
                "accepted by InsightRankerService."
            )

        preferred_patterns = [
            item
            for item in _stable_unique_strings(
                profile.get(
                    "preferred_patterns"
                )
                or []
            )
            if item
            in ALLOWED_QUALITY_PATTERNS
        ]

        avoid_patterns = [
            item
            for item in _stable_unique_strings(
                profile.get(
                    "avoid_patterns"
                )
                or []
            )
            if item
            in ALLOWED_AVOID_PATTERNS
        ]

        return {
            "desired_depth": _normalize_enum(
                profile.get(
                    "desired_depth"
                )
            ),
            "verbosity": _normalize_enum(
                profile.get(
                    "verbosity"
                )
            ),
            "preferred_patterns": preferred_patterns,
            "avoid_patterns": avoid_patterns,
            "analytical_lenses": _stable_unique_strings(
                profile.get(
                    "analytical_lenses"
                )
                or profile.get(
                    "requested_lenses"
                )
                or []
            ),
            "positive_examples": [
                _normalize_example(
                    item
                )
                for item in (
                    profile.get(
                        "positive_examples"
                    )
                    or []
                )
            ],
            "negative_examples": [
                _normalize_example(
                    item
                )
                for item in (
                    profile.get(
                        "negative_examples"
                    )
                    or []
                )
            ],
            "preferred_insight_ids": _stable_unique_strings(
                profile.get(
                    "preferred_insight_ids"
                )
                or []
            ),
            "rejected_insight_ids": _stable_unique_strings(
                profile.get(
                    "rejected_insight_ids"
                )
                or []
            ),
        }

    def _normalize_context(
        self,
        context: Optional[
            Mapping[str, Any]
        ],
    ) -> Dict[str, str]:
        if context is None:
            return {}

        if not isinstance(
            context,
            Mapping,
        ):
            raise InsightRankerValidationError(
                "context must be an object."
            )

        prohibited = {
            "research_depth",
            "runtime_policy",
            "execution_policy",
            "release_policy",
            "release_gate",
            "evidence_ids",
            "source_ids",
            "model_route",
            "search_budget",
            "max_run_seconds",
        }

        found = [
            key
            for key in context
            if _normalize_enum(
                key
            )
            in prohibited
        ]

        if found:
            raise InsightRankerValidationError(
                "Ranking context contains prohibited execution/evidence fields: "
                + ", ".join(
                    sorted(
                        found
                    )
                )
            )

        allowed = {
            "research_question",
            "topic",
            "report_section",
            "cluster_id",
            "analysis_lens",
            "report_id",
            "run_id",
        }

        unknown = (
            set(
                context
            )
            - allowed
        )

        if unknown:
            raise InsightRankerValidationError(
                "Unsupported ranking context fields: "
                + ", ".join(
                    sorted(
                        unknown
                    )
                )
            )

        return {
            key: _clean(
                value
            )
            for key, value
            in context.items()
            if _clean(
                value
            )
        }

    def _normalize_config(
        self,
        config: Optional[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        result = deepcopy(
            DEFAULT_CONFIG
        )

        if config is not None:
            if not isinstance(
                config,
                Mapping,
            ):
                raise InsightRankerValidationError(
                    "config must be an object."
                )

            unknown = (
                set(
                    config
                )
                - set(
                    DEFAULT_CONFIG
                )
            )

            if unknown:
                raise InsightRankerValidationError(
                    "Unknown config fields: "
                    + ", ".join(
                        sorted(
                            unknown
                        )
                    )
                )

            result.update(
                dict(
                    config
                )
            )

        numeric_fields = {
            "quality_weight",
            "preferred_pattern_weight",
            "example_preference_weight",
            "lens_weight",
            "learned_preference_weight",
            "avoid_pattern_penalty",
            "direct_preference_bonus",
            "direct_rejection_penalty",
            "diversity_strength",
            "positive_example_similarity_floor",
            "negative_example_similarity_floor",
        }

        for field in numeric_fields:
            try:
                result[
                    field
                ] = float(
                    result[
                        field
                    ]
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise InsightRankerValidationError(
                    f"{field} must be numeric."
                ) from exc

            if result[field] < 0:
                raise InsightRankerValidationError(
                    f"{field} cannot be negative."
                )

        for field in (
            "positive_example_similarity_floor",
            "negative_example_similarity_floor",
        ):
            if not 0.0 <= result[field] <= 1.0:
                raise InsightRankerValidationError(
                    f"{field} must be in [0, 1]."
                )

        result[
            "require_quality_assessment"
        ] = bool(
            result[
                "require_quality_assessment"
            ]
        )

        result[
            "renormalize_missing_learned_scorer"
        ] = bool(
            result[
                "renormalize_missing_learned_scorer"
            ]
        )

        ranking_statuses = set(
            _stable_unique_strings(
                result[
                    "ranking_eligible_statuses"
                ]
            )
        )

        release_statuses = set(
            _stable_unique_strings(
                result[
                    "release_safe_statuses"
                ]
            )
        )

        if not ranking_statuses <= ALLOWED_QUALITY_STATUSES:
            raise InsightRankerValidationError(
                "ranking_eligible_statuses contains unsupported status."
            )

        if not release_statuses <= ALLOWED_QUALITY_STATUSES:
            raise InsightRankerValidationError(
                "release_safe_statuses contains unsupported status."
            )

        if "insufficient_evidence" in ranking_statuses:
            raise InsightRankerValidationError(
                "insufficient_evidence cannot be ranking-eligible."
            )

        if "insufficient_evidence" in release_statuses:
            raise InsightRankerValidationError(
                "insufficient_evidence cannot be release-safe."
            )

        result[
            "ranking_eligible_statuses"
        ] = sorted(
            ranking_statuses
        )

        result[
            "release_safe_statuses"
        ] = sorted(
            release_statuses
        )

        positive_weight_sum = sum(
            result[
                field
            ]
            for field in (
                "quality_weight",
                "preferred_pattern_weight",
                "example_preference_weight",
                "lens_weight",
                "learned_preference_weight",
            )
        )

        if positive_weight_sum <= 0:
            raise InsightRankerValidationError(
                "At least one positive scoring weight must be > 0."
            )

        return result