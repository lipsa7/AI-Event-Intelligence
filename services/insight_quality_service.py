"""
Insight quality assessment and refinement planning service.

Recommended location:
    services/insight_quality_service.py

Purpose
-------
StrategicInferenceAgent
        ↓
candidate insights
        ↓
InsightQualityService
        ↓
quality defects + refinement plan
        ↓
orchestrator
        ├─ EvidenceDiscoveryAgent
        ├─ ReasoningContextService
        ├─ StrategicInferenceAgent refinement
        └─ Grounding / Red Team

The service asks:
- Is this insight merely restating evidence?
- Does it synthesize across events/evidence?
- Is a mechanism explicit?
- Is there a meaningful second-order implication?
- Is it non-obvious relative to the supplied baseline?
- Is fact vs inference kept distinct?
- Is counterevidence considered?
- For prospective insights, are mechanism and feasibility treated correctly?
- Are source-independence limitations visible?
- Is there enough evidence to justify further refinement?

Critical epistemic boundary
---------------------------
InsightQualityService does NOT:
- generate the final strategic insight;
- manufacture facts;
- convert search results into evidence;
- declare an inference true;
- lower grounding standards;
- authorize report release;
- infer source independence from different URLs/domains/group IDs;
- treat user preference examples as evidence.

It critiques quality and creates a research/refinement plan.

Prospective insights
--------------------
A prospective insight is not penalized simply because no exact precedent exists.

Instead, quality assessment looks for:
1. factual premise grounding;
2. analogical support for the proposed mechanism when useful;
3. magnitude / feasibility / blockers;
4. counter-incentives / counterevidence;
5. explicit uncertainty.

A strong prospective insight may therefore be:
    plausible_unprecedented
without direct precedent.

Fast / Deep
-----------
There are no separate fast/deep code paths.

The service consumes the compiled Blueprint:
- Fast mode may allow one small refinement cycle.
- Deep mode may allow more refinement/search work.

The same quality defects are detected in both modes. The Blueprint determines
how much remediation work is authorized.

Scoring
-------
Scores are ordinal QUALITY diagnostics, not truth confidence.

Each dimension uses:
    0 = absent / materially defective
    1 = weak
    2 = adequate
    3 = strong
    4 = exemplary

The aggregate score is useful for ranking/refinement triage only. It must never
be shown or interpreted as a probability that an insight is true.

Traceability
------------
The returned `quality_trace` contains:
- evidence IDs considered;
- grounding statuses considered;
- source-independence warnings;
- rubric defects;
- baseline comparisons;
- recommended refinement actions;
- why refinement was or was not requested.

It intentionally does not contain private chain-of-thought.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "insight-quality-service-v2.0"

ALLOWED_INSIGHT_TYPES = {
    "descriptive",
    "prospective",
}

ALLOWED_REFINEMENT_ACTIONS = {
    "retrieve_support",
    "retrieve_counterevidence",
    "expand_graph_context",
    "compare_baseline",
    "refine_reasoning",
    "broaden_evidence_search",
    "model_escalation",
}

QUALITY_DIMENSIONS = (
    "evidence_distance",
    "cross_event_synthesis",
    "mechanism_quality",
    "second_order_value",
    "non_obviousness",
    "epistemic_discipline",
    "counterevidence_quality",
    "source_independence_awareness",
    "prospective_feasibility",
)

DEFAULT_WEIGHTS = {
    "evidence_distance": 1.0,
    "cross_event_synthesis": 1.2,
    "mechanism_quality": 1.3,
    "second_order_value": 1.0,
    "non_obviousness": 1.1,
    "epistemic_discipline": 1.5,
    "counterevidence_quality": 1.2,
    "source_independence_awareness": 0.8,
    "prospective_feasibility": 1.2,
}

DEFAULT_POLICY = {
    "desired_depth": "high",
    "max_refinement_rounds": 1,
    "refinement_round": 0,
    "automatic_refinement_enabled": True,
    "allowed_refinement_actions": sorted(ALLOWED_REFINEMENT_ACTIONS),
    "require_cross_event_synthesis": True,
    "require_mechanism": True,
    "require_second_order_effects": True,
    "require_counterevidence": True,
    "require_explicit_uncertainty": True,
    "reject_fact_restatement_as_hidden_insight": True,
    "use_positive_examples_for_quality_reference": True,
    "stop_on_no_new_evidence": True,
    "stop_on_no_material_change": True,
}

SUPPORTING_GROUNDING_STATUSES = {
    "supported",
    "partially_supported",
    "inference_supported",
}

WEAK_GROUNDING_STATUSES = {
    "partially_supported",
    "unsupported",
    "contradicted",
}

COUNTER_ROLES = {
    "counterevidence",
    "counter_incentive_evidence",
    "alternative_explanation_evidence",
}

ANALOGICAL_ROLE = "analogical_mechanism_evidence"
FEASIBILITY_ROLE = "feasibility_evidence"


class InsightQualityError(RuntimeError):
    """Base exception."""


class InsightQualityValidationError(InsightQualityError):
    """Raised when an input violates the contract."""


class InsightQualityIntegrityError(InsightQualityError):
    """Raised when evidence identity or trace integrity is inconsistent."""


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
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
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
    return hashlib.sha256(value).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return f"{prefix}-" + _sha256(
        _canonical_json_bytes(payload)
    )[:length].upper()


def _stable_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        raise InsightQualityValidationError(
            "Expected a string or sequence of strings."
        )

    result: list[str] = []
    seen: Set[str] = set()

    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def _bounded_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise InsightQualityValidationError(
            "Quality score must be an integer."
        ) from exc

    if not 0 <= score <= 4:
        raise InsightQualityValidationError(
            "Quality score must be in [0, 4]."
        )

    return score


def _tokenize(text: str) -> Set[str]:
    return {
        token.casefold()
        for token in re.findall(
            r"[a-z0-9]+",
            text,
            flags=re.IGNORECASE,
        )
    }


def _lexical_similarity(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)

    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0

    return (
        len(left_tokens & right_tokens)
        / len(left_tokens | right_tokens)
    )


def _evidence_id(record: Mapping[str, Any]) -> str:
    return _clean(
        record.get("evidence_id")
        or record.get("id")
        or record.get("fact_id")
        or record.get("metric_id")
        or record.get("triple_id")
    )


def _evidence_text(record: Mapping[str, Any]) -> str:
    for field in (
        "statement",
        "claim",
        "text",
        "fact",
        "summary",
        "exact_excerpt",
        "description",
    ):
        text = _clean(record.get(field))
        if text:
            return text

    if (
        record.get("value") is not None
        or record.get("unit") is not None
    ):
        return " ".join(
            value
            for value in (
                _clean(record.get("metric_name")),
                _clean(record.get("value")),
                _clean(record.get("unit")),
                _clean(record.get("period")),
            )
            if value
        )

    return ""


def _extract_event_ids(record: Mapping[str, Any]) -> list[str]:
    values = []

    for field in (
        "event_id",
        "source_event_id",
        "target_event_id",
    ):
        value = _clean(record.get(field))
        if value:
            values.append(value)

    values.extend(
        _stable_unique_strings(
            record.get("event_ids") or []
        )
    )

    return _stable_unique_strings(values)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class InsightQualityService:
    """
    Deterministic-first insight critic with optional narrow model assessor.

    Optional `semantic_similarity_fn`
        Callable(text_a, text_b) -> float in [0, 1].
        Usually backed by local EmbeddingService.

    Optional `llm_assessor`
        Narrow critic used only for qualitative rubric assessment.
        It receives already-supplied insight/evidence summaries and must return
        JSON. It may not invent evidence IDs or modify policy.

        The deterministic checks always run and can override unsafe model output.
    """

    def __init__(
        self,
        *,
        semantic_similarity_fn: Optional[
            Callable[[str, str], float]
        ] = None,
        llm_assessor: Optional[Any] = None,
        weights: Optional[Mapping[str, float]] = None,
        baseline_similarity_threshold: float = 0.82,
        evidence_restatement_threshold: float = 0.88,
        model_name: str = "",
    ) -> None:
        if (
            semantic_similarity_fn is not None
            and not callable(semantic_similarity_fn)
        ):
            raise InsightQualityValidationError(
                "semantic_similarity_fn must be callable."
            )

        self.semantic_similarity_fn = (
            semantic_similarity_fn
            or _lexical_similarity
        )

        self.llm_assessor = llm_assessor
        self.model_name = _clean(model_name)

        try:
            self.baseline_similarity_threshold = float(
                baseline_similarity_threshold
            )
            self.evidence_restatement_threshold = float(
                evidence_restatement_threshold
            )
        except (TypeError, ValueError) as exc:
            raise InsightQualityValidationError(
                "Similarity thresholds must be numeric."
            ) from exc

        for threshold_name, threshold in (
            (
                "baseline_similarity_threshold",
                self.baseline_similarity_threshold,
            ),
            (
                "evidence_restatement_threshold",
                self.evidence_restatement_threshold,
            ),
        ):
            if not 0.0 <= threshold <= 1.0:
                raise InsightQualityValidationError(
                    f"{threshold_name} must be in [0, 1]."
                )

        self.weights = dict(DEFAULT_WEIGHTS)

        if weights is not None:
            if not isinstance(weights, Mapping):
                raise InsightQualityValidationError(
                    "weights must be an object."
                )

            for key, value in weights.items():
                if key not in QUALITY_DIMENSIONS:
                    raise InsightQualityValidationError(
                        f"Unknown quality dimension {key!r}."
                    )

                try:
                    numeric = float(value)
                except (TypeError, ValueError) as exc:
                    raise InsightQualityValidationError(
                        f"Weight for {key!r} must be numeric."
                    ) from exc

                if numeric < 0:
                    raise InsightQualityValidationError(
                        "Quality weights cannot be negative."
                    )

                self.weights[key] = numeric

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(
        self,
        *,
        insight: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        grounding: Optional[Mapping[str, Any]] = None,
        constraints: Sequence[Mapping[str, Any]] = (),
        baseline_explanations: Sequence[str] = (),
        previous_insight: Optional[Mapping[str, Any]] = None,
        source_provenance_summary: Optional[Mapping[str, Any]] = None,
        blueprint: Optional[Mapping[str, Any]] = None,
        user_quality_preferences: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Assess one candidate insight and produce a bounded refinement plan.
        """
        normalized_insight = self._normalize_insight(insight)
        normalized_evidence = self._normalize_evidence(evidence)
        normalized_grounding = self._normalize_grounding(grounding)
        normalized_constraints = self._normalize_constraints(constraints)
        baseline = _stable_unique_strings(
            baseline_explanations
        )
        previous = (
            self._normalize_previous_insight(previous_insight)
            if previous_insight is not None
            else {}
        )
        provenance = self._normalize_provenance_summary(
            source_provenance_summary
        )
        policy = self._extract_policy(blueprint)
        preferences = self._normalize_preferences(
            user_quality_preferences
        )

        evidence_by_id = {
            _evidence_id(record): record
            for record in normalized_evidence
        }

        support_ids = self._support_ids(
            normalized_insight,
            normalized_grounding,
        )
        counter_ids = self._counter_ids(
            normalized_insight,
            normalized_grounding,
            normalized_evidence,
        )

        self._validate_references(
            support_ids=support_ids,
            counter_ids=counter_ids,
            evidence_by_id=evidence_by_id,
        )

        deterministic = self._deterministic_rubric(
            insight=normalized_insight,
            evidence=normalized_evidence,
            support_ids=support_ids,
            counter_ids=counter_ids,
            grounding=normalized_grounding,
            constraints=normalized_constraints,
            baseline=baseline,
            previous=previous,
            provenance=provenance,
            policy=policy,
            preferences=preferences,
        )

        model_assessment = {}

        if self.llm_assessor is not None:
            model_assessment = self._run_llm_assessor(
                insight=normalized_insight,
                evidence=normalized_evidence,
                grounding=normalized_grounding,
                constraints=normalized_constraints,
                baseline=baseline,
                provenance=provenance,
                policy=policy,
                preferences=preferences,
            )

        dimensions = self._merge_rubric(
            deterministic=deterministic["dimensions"],
            model_assessment=model_assessment,
        )

        defects = self._merge_defects(
            deterministic["defects"],
            model_assessment.get("defects") or [],
        )

        aggregate = self._aggregate_quality(
            dimensions,
            insight_type=normalized_insight["insight_type"],
        )

        refinement_plan = self._build_refinement_plan(
            insight=normalized_insight,
            defects=defects,
            dimensions=dimensions,
            grounding=normalized_grounding,
            provenance=provenance,
            policy=policy,
            deterministic=deterministic,
        )

        status = self._quality_status(
            aggregate=aggregate,
            defects=defects,
            grounding=normalized_grounding,
            insight_type=normalized_insight["insight_type"],
        )

        assessment_id = _stable_id(
            "INSIGHTQUALITY",
            {
                "insight_id": normalized_insight["insight_id"],
                "insight_revision_id": normalized_insight[
                    "insight_revision_id"
                ],
                "insight_text": normalized_insight["text"],
                "support_ids": support_ids,
                "counter_ids": counter_ids,
                "dimensions": dimensions,
                "policy": policy,
            },
        )

        considered_ids = [
            _evidence_id(record)
            for record in normalized_evidence
        ]

        return {
            "quality_assessment_id": assessment_id,
            "insight_id": normalized_insight["insight_id"],
            "insight_revision_id": normalized_insight[
                "insight_revision_id"
            ],
            "insight_type": normalized_insight["insight_type"],
            "quality_status": status,
            "quality_score": aggregate,
            "quality_score_semantics": (
                "ordinal_quality_diagnostic_not_truth_probability"
            ),
            "dimensions": dimensions,
            "defects": defects,
            "refinement_plan": refinement_plan,
            "grounding_summary": {
                "support_evidence_ids": support_ids,
                "counterevidence_ids": counter_ids,
                "grounding_status": normalized_grounding.get(
                    "status",
                    "",
                ),
                "critical_unknowns": list(
                    normalized_grounding.get(
                        "critical_unknowns",
                        [],
                    )
                ),
            },
            "quality_trace": {
                "considered_evidence_ids": considered_ids,
                "support_evidence_ids": support_ids,
                "counterevidence_ids": counter_ids,
                "event_ids_considered": deterministic[
                    "event_ids_considered"
                ],
                "distinct_event_count": deterministic[
                    "distinct_event_count"
                ],
                "baseline_comparison": deterministic[
                    "baseline_comparison"
                ],
                "evidence_restatement_check": deterministic[
                    "evidence_restatement_check"
                ],
                "source_independence_summary": provenance,
                "prospective_checks": deterministic[
                    "prospective_checks"
                ],
                "constraint_ids_considered": [
                    item["constraint_id"]
                    for item in normalized_constraints
                ],
                "previous_insight_id": _clean(
                    previous.get("insight_id")
                ),
                "previous_insight_revision_id": _clean(
                    previous.get("insight_revision_id")
                ),
                "user_quality_preferences_used": bool(preferences),
                "private_chain_of_thought_stored": False,
            },
            "metadata": {
                "service_version": SERVICE_VERSION,
                "model": self.model_name,
                "llm_assessor_used": self.llm_assessor is not None,
                "blueprint_id": (
                    _clean(
                        (blueprint or {}).get("blueprint_id")
                    )
                    if isinstance(blueprint, Mapping)
                    else ""
                ),
                "profile_agnostic": True,
                "examples_are_quality_guidance_not_evidence": True,
                "authorizes_release": False,
                "generates_final_insight": False,
            },
        }

    def assess_many(
        self,
        *,
        insights: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        grounding_by_insight_id: Optional[
            Mapping[str, Mapping[str, Any]]
        ] = None,
        constraints: Sequence[Mapping[str, Any]] = (),
        baseline_explanations: Sequence[str] = (),
        source_provenance_summary: Optional[Mapping[str, Any]] = None,
        blueprint: Optional[Mapping[str, Any]] = None,
        user_quality_preferences: Optional[Mapping[str, Any]] = None,
    ) -> list[Dict[str, Any]]:
        """
        Assess candidate insights independently using one shared evidence set.

        This method does not choose which insights are finally released.
        """
        if (
            not isinstance(insights, Sequence)
            or isinstance(insights, (str, bytes))
        ):
            raise InsightQualityValidationError(
                "insights must be a sequence."
            )

        grounding_by_insight_id = (
            grounding_by_insight_id
            or {}
        )

        results = []

        for insight in insights:
            if not isinstance(insight, Mapping):
                raise InsightQualityValidationError(
                    "Every insight must be an object."
                )

            insight_id = _clean(
                insight.get("insight_id")
                or insight.get("id")
            )

            results.append(
                self.assess(
                    insight=insight,
                    evidence=evidence,
                    grounding=grounding_by_insight_id.get(
                        insight_id
                    ),
                    constraints=constraints,
                    baseline_explanations=baseline_explanations,
                    source_provenance_summary=source_provenance_summary,
                    blueprint=blueprint,
                    user_quality_preferences=user_quality_preferences,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Deterministic rubric
    # ------------------------------------------------------------------

    def _deterministic_rubric(
        self,
        *,
        insight: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        support_ids: Sequence[str],
        counter_ids: Sequence[str],
        grounding: Mapping[str, Any],
        constraints: Sequence[Mapping[str, Any]],
        baseline: Sequence[str],
        previous: Mapping[str, Any],
        provenance: Mapping[str, Any],
        policy: Mapping[str, Any],
        preferences: Mapping[str, Any],
    ) -> Dict[str, Any]:
        evidence_by_id = {
            _evidence_id(record): record
            for record in evidence
        }

        support_records = [
            evidence_by_id[evidence_id]
            for evidence_id in support_ids
            if evidence_id in evidence_by_id
        ]

        event_ids: Set[str] = set()

        for record in support_records:
            event_ids.update(
                _extract_event_ids(record)
            )

        insight_event_ids = _stable_unique_strings(
            insight.get("event_ids")
            or []
        )
        event_ids.update(
            insight_event_ids
        )

        mechanism_text = _clean(
            insight.get("mechanism")
            or (
                insight.get("mechanism_details") or {}
            ).get("description")
            if isinstance(
                insight.get("mechanism_details"),
                Mapping,
            )
            else insight.get("mechanism")
        )

        second_order = _clean(
            insight.get("second_order_effect")
            or insight.get("second_order_implication")
            or insight.get("implication")
        )

        uncertainty = _clean(
            insight.get("uncertainty")
            or insight.get("main_uncertainty")
        )

        if not uncertainty:
            unknowns = insight.get(
                "critical_unknowns"
            ) or grounding.get(
                "critical_unknowns"
            ) or []
            uncertainty = "; ".join(
                _stable_unique_strings(unknowns)
            )

        baseline_comparison = self._baseline_comparison(
            insight_text=insight["text"],
            baseline=baseline,
        )

        restatement_check = self._evidence_restatement_check(
            insight_text=insight["text"],
            support_records=support_records,
        )

        dimensions: Dict[str, int] = {}
        defects: list[Dict[str, Any]] = []

        # 1. Evidence distance
        if not support_records:
            dimensions["evidence_distance"] = 0
            defects.append(
                self._defect(
                    "missing_support",
                    "critical",
                    "No supporting evidence records are attached to the insight.",
                    [
                        "retrieve_support",
                        "refine_reasoning",
                    ],
                )
            )

        elif restatement_check["is_near_restatement"]:
            dimensions["evidence_distance"] = 0
            defects.append(
                self._defect(
                    "fact_restatement",
                    "high",
                    (
                        "The insight is too close to a supporting evidence "
                        "statement and adds little synthesis."
                    ),
                    [
                        "refine_reasoning",
                        "expand_graph_context",
                    ],
                )
            )

        elif (
            len(support_records) >= 2
            and len(event_ids) >= 2
        ):
            dimensions["evidence_distance"] = 4

        elif len(support_records) >= 2:
            dimensions["evidence_distance"] = 3

        else:
            dimensions["evidence_distance"] = 2

        # 2. Cross-event synthesis
        if len(event_ids) >= 3:
            dimensions["cross_event_synthesis"] = 4
        elif len(event_ids) == 2:
            dimensions["cross_event_synthesis"] = 3
        elif len(support_records) >= 2:
            dimensions["cross_event_synthesis"] = 2
        elif support_records:
            dimensions["cross_event_synthesis"] = 1
        else:
            dimensions["cross_event_synthesis"] = 0

        if (
            policy["require_cross_event_synthesis"]
            and dimensions["cross_event_synthesis"] < 2
        ):
            defects.append(
                self._defect(
                    "weak_cross_event_synthesis",
                    "high",
                    (
                        "The insight does not yet demonstrate synthesis across "
                        "multiple evidence/event signals."
                    ),
                    [
                        "expand_graph_context",
                        "broaden_evidence_search",
                        "refine_reasoning",
                    ],
                )
            )

        # 3. Mechanism
        if mechanism_text:
            if len(support_records) >= 2:
                dimensions["mechanism_quality"] = 4
            else:
                dimensions["mechanism_quality"] = 3
        else:
            dimensions["mechanism_quality"] = 0

            if policy["require_mechanism"]:
                defects.append(
                    self._defect(
                        "missing_mechanism",
                        "high",
                        (
                            "The insight states an implication without an "
                            "explicit mechanism connecting the evidence."
                        ),
                        [
                            "expand_graph_context",
                            "refine_reasoning",
                        ],
                    )
                )

        # 4. Second-order value
        if second_order:
            dimensions["second_order_value"] = (
                4
                if mechanism_text
                else 2
            )
        else:
            dimensions["second_order_value"] = 1

            if policy["require_second_order_effects"]:
                defects.append(
                    self._defect(
                        "missing_second_order_effect",
                        "medium",
                        (
                            "The candidate does not articulate a meaningful "
                            "second-order implication."
                        ),
                        [
                            "refine_reasoning",
                            "expand_graph_context",
                        ],
                    )
                )

        # 5. Non-obviousness
        if not baseline:
            dimensions["non_obviousness"] = 2
        else:
            similarity = baseline_comparison[
                "max_similarity"
            ]

            if similarity >= self.baseline_similarity_threshold:
                dimensions["non_obviousness"] = 1
                defects.append(
                    self._defect(
                        "baseline_restatement",
                        "medium",
                        (
                            "The insight is highly similar to a supplied baseline "
                            "explanation and may not be sufficiently non-obvious."
                        ),
                        [
                            "compare_baseline",
                            "expand_graph_context",
                            "refine_reasoning",
                        ],
                    )
                )
            elif similarity >= max(
                0.0,
                self.baseline_similarity_threshold - 0.15,
            ):
                dimensions["non_obviousness"] = 2
            elif mechanism_text and len(support_records) >= 2:
                dimensions["non_obviousness"] = 4
            else:
                dimensions["non_obviousness"] = 3

        # 6. Epistemic discipline
        grounding_status = _normalize_enum(
            grounding.get("status")
        )

        if grounding_status == "contradicted":
            dimensions["epistemic_discipline"] = 0
            defects.append(
                self._defect(
                    "contradicted_insight",
                    "critical",
                    (
                        "Grounding indicates material contradiction. The "
                        "candidate should not be strengthened through wording."
                    ),
                    [
                        "retrieve_counterevidence",
                        "refine_reasoning",
                    ],
                )
            )
        elif grounding_status == "unsupported":
            dimensions["epistemic_discipline"] = 1
            defects.append(
                self._defect(
                    "unsupported_inference",
                    "critical",
                    (
                        "The current evidence does not adequately support the "
                        "candidate inference."
                    ),
                    [
                        "retrieve_support",
                        "retrieve_counterevidence",
                    ],
                )
            )
        elif (
            uncertainty
            and grounding_status in SUPPORTING_GROUNDING_STATUSES
        ):
            dimensions["epistemic_discipline"] = 4
        elif grounding_status in SUPPORTING_GROUNDING_STATUSES:
            dimensions["epistemic_discipline"] = 3
        elif uncertainty:
            dimensions["epistemic_discipline"] = 2
        else:
            dimensions["epistemic_discipline"] = 1

        if (
            policy["require_explicit_uncertainty"]
            and not uncertainty
        ):
            defects.append(
                self._defect(
                    "missing_uncertainty",
                    "medium",
                    (
                        "The insight does not expose its main uncertainty or "
                        "critical unknown."
                    ),
                    [
                        "refine_reasoning",
                    ],
                )
            )

        # 7. Counterevidence quality
        if counter_ids:
            dimensions["counterevidence_quality"] = 4
        else:
            dimensions["counterevidence_quality"] = 1

            if policy["require_counterevidence"]:
                defects.append(
                    self._defect(
                        "counterevidence_not_established",
                        "high",
                        (
                            "No counterevidence/alternative explanation is "
                            "attached to the candidate."
                        ),
                        [
                            "retrieve_counterevidence",
                            "broaden_evidence_search",
                        ],
                    )
                )

        # 8. Source independence awareness
        same_origin_pairs = provenance.get(
            "same_origin_pairs"
        ) or []
        unknown_pairs = provenance.get(
            "unknown_independence_pairs"
        ) or []
        independent_pairs = provenance.get(
            "independent_pairs"
        ) or []

        if same_origin_pairs and unknown_pairs:
            dimensions["source_independence_awareness"] = 1
        elif same_origin_pairs:
            dimensions["source_independence_awareness"] = 2
        elif unknown_pairs:
            dimensions["source_independence_awareness"] = 2
        elif independent_pairs:
            dimensions["source_independence_awareness"] = 4
        else:
            dimensions["source_independence_awareness"] = 2

        if same_origin_pairs:
            defects.append(
                self._defect(
                    "source_corroboration_overcount_risk",
                    "high",
                    (
                        "Some supporting sources share an underlying origin and "
                        "must not be counted as independent corroboration."
                    ),
                    [
                        "retrieve_support",
                    ],
                )
            )

        if unknown_pairs:
            defects.append(
                self._defect(
                    "source_independence_unresolved",
                    "medium",
                    (
                        "Source independence is unresolved for at least one "
                        "source pair."
                    ),
                    [
                        "retrieve_support",
                    ],
                )
            )

        # 9. Prospective feasibility
        prospective_checks = self._prospective_checks(
            insight=insight,
            evidence=evidence,
            support_ids=support_ids,
            counter_ids=counter_ids,
            constraints=constraints,
            grounding=grounding,
        )

        if insight["insight_type"] == "prospective":
            dimensions["prospective_feasibility"] = prospective_checks[
                "score"
            ]

            for item in prospective_checks["defects"]:
                defects.append(item)
        else:
            # Not applicable; neutral strong value and excluded from aggregate.
            dimensions["prospective_feasibility"] = 4

        return {
            "dimensions": dimensions,
            "defects": defects,
            "event_ids_considered": sorted(event_ids),
            "distinct_event_count": len(event_ids),
            "baseline_comparison": baseline_comparison,
            "evidence_restatement_check": restatement_check,
            "prospective_checks": prospective_checks,
        }

    def _prospective_checks(
        self,
        *,
        insight: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        support_ids: Sequence[str],
        counter_ids: Sequence[str],
        constraints: Sequence[Mapping[str, Any]],
        grounding: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if insight["insight_type"] != "prospective":
            return {
                "applicable": False,
                "score": 4,
                "direct_precedent_required": False,
                "analogical_mechanism_evidence_ids": [],
                "feasibility_evidence_ids": [],
                "constraint_ids": [],
                "counter_incentive_evidence_ids": [],
                "defects": [],
            }

        analogical_ids = []
        feasibility_ids = []
        counter_incentive_ids = []

        # Only evidence actually attached to this insight/grounding assessment
        # may satisfy prospective quality checks. Merely existing elsewhere in
        # the run's evidence pool is not enough.
        relevant_ids = set(
            _stable_unique_strings(
                list(support_ids)
                + list(counter_ids)
            )
        )

        for record in evidence:
            evidence_id = _evidence_id(record)

            if evidence_id not in relevant_ids:
                continue

            role = _normalize_enum(
                record.get("evidence_role")
            )

            if role == ANALOGICAL_ROLE:
                analogical_ids.append(evidence_id)

            if role == FEASIBILITY_ROLE:
                feasibility_ids.append(evidence_id)

            if (
                role == "counter_incentive_evidence"
                and evidence_id in set(counter_ids)
            ):
                counter_incentive_ids.append(evidence_id)

        constraint_ids = [
            item["constraint_id"]
            for item in constraints
        ]

        mechanism = _clean(
            insight.get("mechanism")
            or (
                insight.get("mechanism_details") or {}
            ).get("description")
            if isinstance(
                insight.get("mechanism_details"),
                Mapping,
            )
            else insight.get("mechanism")
        )

        defects = []
        score = 4

        if not mechanism:
            score = min(score, 1)
            defects.append(
                self._defect(
                    "prospective_mechanism_missing",
                    "critical",
                    (
                        "A prospective insight needs an explicit causal/behavioral "
                        "mechanism; direct precedent is not a substitute."
                    ),
                    [
                        "refine_reasoning",
                        "expand_graph_context",
                    ],
                )
            )

        if not analogical_ids:
            score = min(score, 2)
            defects.append(
                self._defect(
                    "prospective_mechanism_analogy_missing",
                    "medium",
                    (
                        "No analogical mechanism evidence is attached. This does "
                        "not make the insight false, but the mechanism has less "
                        "external support."
                    ),
                    [
                        "broaden_evidence_search",
                        "retrieve_support",
                    ],
                )
            )

        if not feasibility_ids and not constraint_ids:
            score = min(score, 1)
            defects.append(
                self._defect(
                    "prospective_feasibility_unchecked",
                    "high",
                    (
                        "Magnitude/feasibility/blockers are not sufficiently "
                        "represented for the prospective inference."
                    ),
                    [
                        "retrieve_support",
                        "broaden_evidence_search",
                    ],
                )
            )

        if not counter_incentive_ids:
            score = min(score, 2)
            defects.append(
                self._defect(
                    "prospective_counter_incentives_missing",
                    "medium",
                    (
                        "No explicit counter-incentive evidence is attached to "
                        "the prospective insight."
                    ),
                    [
                        "retrieve_counterevidence",
                    ],
                )
            )

        grounding_status = _normalize_enum(
            grounding.get("status")
        )

        if grounding_status in {
            "unsupported",
            "contradicted",
        }:
            score = min(score, 1)

        return {
            "applicable": True,
            "score": score,
            "direct_precedent_required": False,
            "direct_precedent_absence_is_not_automatic_failure": True,
            "analogical_mechanism_evidence_ids": analogical_ids,
            "feasibility_evidence_ids": feasibility_ids,
            "constraint_ids": constraint_ids,
            "counter_incentive_evidence_ids": counter_incentive_ids,
            "defects": defects,
        }

    # ------------------------------------------------------------------
    # Similarity diagnostics
    # ------------------------------------------------------------------

    def _baseline_comparison(
        self,
        *,
        insight_text: str,
        baseline: Sequence[str],
    ) -> Dict[str, Any]:
        if not baseline:
            return {
                "baseline_count": 0,
                "max_similarity": 0.0,
                "nearest_baseline": "",
                "novel_relative_to_supplied_baseline": None,
                "claim_scope": (
                    "No baseline supplied; global novelty is not assessed."
                ),
            }

        nearest = ""
        maximum = -1.0

        for item in baseline:
            similarity = float(
                self.semantic_similarity_fn(
                    insight_text,
                    item,
                )
            )

            if not 0.0 <= similarity <= 1.0:
                raise InsightQualityValidationError(
                    "semantic_similarity_fn must return [0, 1]."
                )

            if similarity > maximum:
                maximum = similarity
                nearest = item

        return {
            "baseline_count": len(baseline),
            "max_similarity": max(maximum, 0.0),
            "nearest_baseline": nearest,
            "novel_relative_to_supplied_baseline": bool(
                maximum
                < self.baseline_similarity_threshold
            ),
            "claim_scope": (
                "Novelty is only relative to the supplied/retrieved baseline, "
                "not to all human knowledge."
            ),
        }

    def _evidence_restatement_check(
        self,
        *,
        insight_text: str,
        support_records: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        nearest_id = ""
        nearest_text = ""
        maximum = -1.0

        for record in support_records:
            text = _evidence_text(record)

            if not text:
                continue

            semantic_similarity = float(
                self.semantic_similarity_fn(
                    insight_text,
                    text,
                )
            )

            if not 0.0 <= semantic_similarity <= 1.0:
                raise InsightQualityValidationError(
                    "semantic_similarity_fn must return [0, 1]."
                )

            # A short evidence statement embedded almost verbatim inside a
            # slightly longer insight can have a modest Jaccard/embedding score
            # despite being a clear restatement. Add a deterministic token
            # containment signal so the quality gate catches that case.
            insight_tokens = _tokenize(insight_text)
            evidence_tokens = _tokenize(text)

            if insight_tokens and evidence_tokens:
                containment_similarity = (
                    len(insight_tokens & evidence_tokens)
                    / min(
                        len(insight_tokens),
                        len(evidence_tokens),
                    )
                )
            else:
                containment_similarity = 0.0

            similarity = max(
                semantic_similarity,
                containment_similarity,
            )

            if similarity > maximum:
                maximum = similarity
                nearest_id = _evidence_id(record)
                nearest_text = text

        return {
            "max_similarity": max(maximum, 0.0),
            "nearest_evidence_id": nearest_id,
            "nearest_evidence_text": nearest_text,
            "is_near_restatement": bool(
                maximum
                >= self.evidence_restatement_threshold
            ),
        }

    # ------------------------------------------------------------------
    # Refinement
    # ------------------------------------------------------------------

    def _build_refinement_plan(
        self,
        *,
        insight: Mapping[str, Any],
        defects: Sequence[Mapping[str, Any]],
        dimensions: Mapping[str, int],
        grounding: Mapping[str, Any],
        provenance: Mapping[str, Any],
        policy: Mapping[str, Any],
        deterministic: Mapping[str, Any],
    ) -> Dict[str, Any]:
        remaining_rounds = max(
            0,
            policy["max_refinement_rounds"]
            - policy["refinement_round"],
        )

        requested_actions: list[str] = []

        for defect in defects:
            requested_actions.extend(
                _stable_unique_strings(
                    defect.get(
                        "recommended_actions"
                    )
                    or []
                )
            )

        requested_actions = _stable_unique_strings(
            requested_actions
        )

        allowed_actions = set(
            policy["allowed_refinement_actions"]
        )

        actions = [
            action
            for action in requested_actions
            if action in allowed_actions
        ]

        blocked_actions = [
            action
            for action in requested_actions
            if action not in allowed_actions
        ]

        critical = [
            item
            for item in defects
            if item["severity"] == "critical"
        ]

        high = [
            item
            for item in defects
            if item["severity"] == "high"
        ]

        should_refine = bool(
            policy["automatic_refinement_enabled"]
            and remaining_rounds > 0
            and actions
            and (
                critical
                or high
                or self._aggregate_quality(
                    dimensions,
                    insight_type=insight["insight_type"],
                ) < 0.72
            )
        )

        priority = "low"

        if critical:
            priority = "critical"
        elif high:
            priority = "high"
        elif defects:
            priority = "medium"

        search_gap_types = []

        defect_codes = {
            item["code"]
            for item in defects
        }

        if (
            "missing_support" in defect_codes
            or "unsupported_inference" in defect_codes
        ):
            search_gap_types.append(
                "premise_support"
            )

        if (
            "counterevidence_not_established" in defect_codes
            or "prospective_counter_incentives_missing" in defect_codes
        ):
            search_gap_types.append(
                "counterevidence"
            )

        if (
            "prospective_mechanism_analogy_missing" in defect_codes
        ):
            search_gap_types.append(
                "analogical_mechanism"
            )

        if (
            "prospective_feasibility_unchecked" in defect_codes
        ):
            search_gap_types.append(
                "feasibility"
            )

        if (
            "baseline_restatement" in defect_codes
        ):
            search_gap_types.append(
                "alternative_explanation"
            )

        # Quality service suggests. Blueprint/orchestrator authorizes actual work.
        return {
            "refinement_requested": should_refine,
            "priority": priority,
            "recommended_actions": actions,
            "blocked_by_policy_actions": blocked_actions,
            "search_gap_types": _stable_unique_strings(
                search_gap_types
            ),
            "remaining_refinement_rounds": remaining_rounds,
            "target_insight_ids": [
                insight["insight_id"]
            ],
            "target_weaknesses": [
                item["code"]
                for item in defects
            ],
            "stop_reason_if_not_refining": (
                ""
                if should_refine
                else self._refinement_stop_reason(
                    policy=policy,
                    remaining_rounds=remaining_rounds,
                    actions=actions,
                    defects=defects,
                )
            ),
            "execution_authorized_here": False,
        }

    def _refinement_stop_reason(
        self,
        *,
        policy: Mapping[str, Any],
        remaining_rounds: int,
        actions: Sequence[str],
        defects: Sequence[Mapping[str, Any]],
    ) -> str:
        if not policy["automatic_refinement_enabled"]:
            return "automatic_refinement_disabled"

        if remaining_rounds <= 0:
            return "max_refinement_rounds_reached"

        if not defects:
            return "no_material_quality_defects"

        if not actions:
            return "no_policy_allowed_refinement_action"

        return "quality_defects_below_refinement_threshold"

    # ------------------------------------------------------------------
    # Optional narrow LLM critic
    # ------------------------------------------------------------------

    def _run_llm_assessor(
        self,
        *,
        insight: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        grounding: Mapping[str, Any],
        constraints: Sequence[Mapping[str, Any]],
        baseline: Sequence[str],
        provenance: Mapping[str, Any],
        policy: Mapping[str, Any],
        preferences: Mapping[str, Any],
    ) -> Dict[str, Any]:
        prompt = self._build_llm_prompt(
            insight=insight,
            evidence=evidence,
            grounding=grounding,
            constraints=constraints,
            baseline=baseline,
            provenance=provenance,
            policy=policy,
            preferences=preferences,
        )

        assessor = self.llm_assessor

        if callable(assessor):
            raw = assessor(prompt)

        else:
            method = None

            for method_name in (
                "assess",
                "generate",
                "ask",
            ):
                candidate = getattr(
                    assessor,
                    method_name,
                    None,
                )

                if callable(candidate):
                    method = candidate
                    break

            if method is None:
                raise InsightQualityValidationError(
                    "llm_assessor must be callable or expose assess/generate/ask."
                )

            raw = method(prompt)

        payload = self._parse_json_object(raw)

        allowed_ids = {
            _evidence_id(record)
            for record in evidence
        }

        referenced_ids = _stable_unique_strings(
            payload.get(
                "referenced_evidence_ids"
            )
            or []
        )

        invented = [
            evidence_id
            for evidence_id in referenced_ids
            if evidence_id not in allowed_ids
        ]

        if invented:
            raise InsightQualityIntegrityError(
                "llm_assessor invented evidence IDs: "
                + ", ".join(invented)
            )

        dimensions = payload.get(
            "dimensions"
        ) or {}

        if not isinstance(dimensions, Mapping):
            raise InsightQualityValidationError(
                "llm_assessor.dimensions must be an object."
            )

        normalized_dimensions = {}

        for key, value in dimensions.items():
            if key not in QUALITY_DIMENSIONS:
                continue

            normalized_dimensions[key] = _bounded_score(
                value
            )

        defects = payload.get(
            "defects"
        ) or []

        if not isinstance(defects, list):
            raise InsightQualityValidationError(
                "llm_assessor.defects must be a list."
            )

        normalized_defects = []

        for item in defects:
            if not isinstance(item, Mapping):
                continue

            normalized_defects.append(
                {
                    "code": _normalize_enum(
                        item.get("code")
                    ) or "model_identified_quality_issue",
                    "severity": self._normalize_severity(
                        item.get("severity")
                    ),
                    "description": _clean(
                        item.get("description")
                        or item.get("reason")
                    ),
                    "recommended_actions": [
                        action
                        for action in _stable_unique_strings(
                            item.get(
                                "recommended_actions"
                            )
                            or []
                        )
                        if action in ALLOWED_REFINEMENT_ACTIONS
                    ],
                    "source": "llm_assessor",
                }
            )

        return {
            "dimensions": normalized_dimensions,
            "defects": normalized_defects,
            "referenced_evidence_ids": referenced_ids,
        }

    def _build_llm_prompt(
        self,
        *,
        insight: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        grounding: Mapping[str, Any],
        constraints: Sequence[Mapping[str, Any]],
        baseline: Sequence[str],
        provenance: Mapping[str, Any],
        policy: Mapping[str, Any],
        preferences: Mapping[str, Any],
    ) -> str:
        evidence_summary = [
            {
                "evidence_id": _evidence_id(record),
                "text": _evidence_text(record),
                "evidence_role": _normalize_enum(
                    record.get("evidence_role")
                ),
                "event_ids": _extract_event_ids(record),
            }
            for record in evidence
        ]

        return f"""
You are a narrow insight-quality critic.

You are NOT generating a new final insight.
You are NOT deciding truth.
You are NOT authorizing release.
You may use ONLY the supplied evidence IDs.
User examples/preferences are quality guidance, never factual evidence.

Candidate insight:
{json.dumps(dict(insight), ensure_ascii=False, indent=2)}

Evidence:
{json.dumps(evidence_summary, ensure_ascii=False, indent=2)}

Grounding:
{json.dumps(dict(grounding), ensure_ascii=False, indent=2)}

Constraints:
{json.dumps(list(constraints), ensure_ascii=False, indent=2)}

Baseline explanations:
{json.dumps(list(baseline), ensure_ascii=False, indent=2)}

Source provenance summary:
{json.dumps(dict(provenance), ensure_ascii=False, indent=2)}

Compiled quality policy:
{json.dumps(dict(policy), ensure_ascii=False, indent=2)}

User quality preferences:
{json.dumps(dict(preferences), ensure_ascii=False, indent=2)}

Assess quality dimensions from 0 to 4:
- evidence_distance
- cross_event_synthesis
- mechanism_quality
- second_order_value
- non_obviousness
- epistemic_discipline
- counterevidence_quality
- source_independence_awareness
- prospective_feasibility

For a prospective insight:
- lack of exact precedent is NOT automatically a failure;
- factual premises still require grounding;
- analogical evidence may support a mechanism;
- feasibility/magnitude/blockers matter;
- counter-incentives matter.

Return exactly one JSON object:
{{
  "dimensions": {{
    "dimension_name": 0
  }},
  "defects": [
    {{
      "code": "short_machine_code",
      "severity": "critical | high | medium | low",
      "description": "brief auditable quality defect",
      "recommended_actions": [
        "retrieve_support | retrieve_counterevidence | expand_graph_context | compare_baseline | refine_reasoning | broaden_evidence_search | model_escalation"
      ]
    }}
  ],
  "referenced_evidence_ids": []
}}

Do not include chain-of-thought.
""".strip()

    # ------------------------------------------------------------------
    # Merge / aggregate
    # ------------------------------------------------------------------

    def _merge_rubric(
        self,
        *,
        deterministic: Mapping[str, int],
        model_assessment: Mapping[str, Any],
    ) -> Dict[str, int]:
        result = {
            key: _bounded_score(
                deterministic.get(key, 0)
            )
            for key in QUALITY_DIMENSIONS
        }

        model_dimensions = (
            model_assessment.get("dimensions")
            or {}
        )

        if not isinstance(model_dimensions, Mapping):
            return result

        # Conservative merge:
        # model may identify additional weakness but cannot "upgrade away"
        # deterministic defects.
        for key, model_score in model_dimensions.items():
            if key not in result:
                continue

            result[key] = min(
                result[key],
                _bounded_score(model_score),
            )

        return result

    def _merge_defects(
        self,
        deterministic: Sequence[Mapping[str, Any]],
        model: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        result = []
        seen = set()

        for item in list(deterministic) + list(model):
            if not isinstance(item, Mapping):
                continue

            code = _normalize_enum(
                item.get("code")
            )

            if not code or code in seen:
                continue

            seen.add(code)

            result.append(
                {
                    "code": code,
                    "severity": self._normalize_severity(
                        item.get("severity")
                    ),
                    "description": _clean(
                        item.get("description")
                    ),
                    "recommended_actions": [
                        action
                        for action in _stable_unique_strings(
                            item.get(
                                "recommended_actions"
                            )
                            or []
                        )
                        if action in ALLOWED_REFINEMENT_ACTIONS
                    ],
                    "source": _clean(
                        item.get("source")
                    ) or "deterministic",
                }
            )

        severity_rank = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
        }

        result.sort(
            key=lambda item: (
                severity_rank[item["severity"]],
                item["code"],
            )
        )

        return result

    def _aggregate_quality(
        self,
        dimensions: Mapping[str, int],
        *,
        insight_type: str,
    ) -> float:
        numerator = 0.0
        denominator = 0.0

        for dimension in QUALITY_DIMENSIONS:
            if (
                dimension == "prospective_feasibility"
                and insight_type != "prospective"
            ):
                continue

            weight = float(
                self.weights[dimension]
            )
            score = _bounded_score(
                dimensions.get(
                    dimension,
                    0,
                )
            )

            numerator += (
                weight
                * (
                    score
                    / 4.0
                )
            )
            denominator += weight

        if denominator <= 0:
            return 0.0

        return round(
            numerator / denominator,
            4,
        )

    def _quality_status(
        self,
        *,
        aggregate: float,
        defects: Sequence[Mapping[str, Any]],
        grounding: Mapping[str, Any],
        insight_type: str,
    ) -> str:
        defect_codes = {
            item["code"]
            for item in defects
        }

        grounding_status = _normalize_enum(
            grounding.get("status")
        )

        if grounding_status == "contradicted":
            return "insufficient_evidence"

        if {
            "missing_support",
            "unsupported_inference",
        } & defect_codes:
            return "insufficient_evidence"

        if any(
            item["severity"] == "critical"
            for item in defects
        ):
            return "needs_refinement"

        if aggregate >= 0.80:
            return "strong"

        if aggregate >= 0.62:
            return "usable_with_limits"

        return "needs_refinement"

    # ------------------------------------------------------------------
    # Input normalization
    # ------------------------------------------------------------------

    def _normalize_insight(
        self,
        insight: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(insight, Mapping):
            raise InsightQualityValidationError(
                "insight must be an object."
            )

        insight_id = _clean(
            insight.get("insight_id")
            or insight.get("id")
        )

        if not insight_id:
            raise InsightQualityValidationError(
                "insight is missing insight_id."
            )

        text = _clean(
            insight.get("insight")
            or insight.get("text")
            or insight.get("statement")
        )

        if not text:
            raise InsightQualityValidationError(
                "insight is missing text."
            )

        insight_type = _normalize_enum(
            insight.get("insight_type")
            or "descriptive"
        )

        if insight_type not in ALLOWED_INSIGHT_TYPES:
            raise InsightQualityValidationError(
                f"Unsupported insight_type={insight_type!r}."
            )

        normalized = deepcopy(
            dict(insight)
        )

        normalized["insight_id"] = insight_id
        normalized["insight_revision_id"] = _clean(
            insight.get("insight_revision_id")
        )
        normalized["text"] = text
        normalized["insight_type"] = insight_type

        if not normalized["insight_revision_id"]:
            normalized["insight_revision_id"] = _stable_id(
                "INSIGHTREV",
                {
                    "insight_id": insight_id,
                    "text": text,
                },
            )

        normalized["evidence_ids"] = _stable_unique_strings(
            insight.get("evidence_ids")
            or insight.get("support_evidence_ids")
            or []
        )

        normalized["counterevidence_ids"] = _stable_unique_strings(
            insight.get("counterevidence_ids")
            or []
        )

        normalized["event_ids"] = _stable_unique_strings(
            insight.get("event_ids")
            or []
        )

        return normalized

    def _normalize_evidence(
        self,
        evidence: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        if (
            not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes))
        ):
            raise InsightQualityValidationError(
                "evidence must be a sequence."
            )

        result = []
        seen: Dict[str, str] = {}

        for index, record in enumerate(evidence):
            if not isinstance(record, Mapping):
                raise InsightQualityValidationError(
                    f"evidence[{index}] must be an object."
                )

            evidence_id = _evidence_id(record)

            if not evidence_id:
                raise InsightQualityValidationError(
                    f"evidence[{index}] is missing evidence_id."
                )

            normalized = deepcopy(
                dict(record)
            )
            normalized["evidence_id"] = evidence_id

            fingerprint = _sha256(
                _canonical_json_bytes(normalized)
            )

            if (
                evidence_id in seen
                and seen[evidence_id] != fingerprint
            ):
                raise InsightQualityIntegrityError(
                    "Duplicate evidence ID has different content: "
                    f"{evidence_id!r}."
                )

            if evidence_id not in seen:
                seen[evidence_id] = fingerprint
                result.append(normalized)

        return result

    def _normalize_grounding(
        self,
        grounding: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        if grounding is None:
            return {}

        if not isinstance(grounding, Mapping):
            raise InsightQualityValidationError(
                "grounding must be an object."
            )

        result = deepcopy(
            dict(grounding)
        )

        result["status"] = _normalize_enum(
            grounding.get("status")
            or grounding.get("grounding_status")
        )

        result["support_evidence_ids"] = _stable_unique_strings(
            grounding.get("support_evidence_ids")
            or grounding.get("evidence_ids")
            or []
        )

        result["counterevidence_ids"] = _stable_unique_strings(
            grounding.get("counterevidence_ids")
            or []
        )

        result["critical_unknowns"] = _stable_unique_strings(
            grounding.get("critical_unknowns")
            or []
        )

        return result

    def _normalize_constraints(
        self,
        constraints: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        if constraints is None:
            return []

        if (
            not isinstance(constraints, Sequence)
            or isinstance(constraints, (str, bytes))
        ):
            raise InsightQualityValidationError(
                "constraints must be a sequence."
            )

        result = []

        for index, item in enumerate(constraints):
            if not isinstance(item, Mapping):
                raise InsightQualityValidationError(
                    f"constraints[{index}] must be an object."
                )

            constraint_id = _clean(
                item.get("constraint_id")
                or item.get("id")
            )

            if not constraint_id:
                raise InsightQualityValidationError(
                    f"constraints[{index}] is missing constraint_id."
                )

            result.append(
                {
                    **deepcopy(dict(item)),
                    "constraint_id": constraint_id,
                }
            )

        return result

    def _normalize_previous_insight(
        self,
        previous: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(previous, Mapping):
            raise InsightQualityValidationError(
                "previous_insight must be an object."
            )

        return {
            "insight_id": _clean(
                previous.get("insight_id")
                or previous.get("id")
            ),
            "insight_revision_id": _clean(
                previous.get("insight_revision_id")
            ),
            "text": _clean(
                previous.get("insight")
                or previous.get("text")
            ),
        }

    def _normalize_provenance_summary(
        self,
        summary: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        if summary is None:
            return {
                "source_ids": [],
                "same_origin_pairs": [],
                "independent_pairs": [],
                "unknown_independence_pairs": [],
                "independence_fully_established": False,
            }

        if not isinstance(summary, Mapping):
            raise InsightQualityValidationError(
                "source_provenance_summary must be an object."
            )

        return {
            "source_ids": _stable_unique_strings(
                summary.get("source_ids")
                or []
            ),
            "same_origin_pairs": deepcopy(
                list(
                    summary.get(
                        "same_origin_pairs"
                    )
                    or []
                )
            ),
            "independent_pairs": deepcopy(
                list(
                    summary.get(
                        "independent_pairs"
                    )
                    or []
                )
            ),
            "unknown_independence_pairs": deepcopy(
                list(
                    summary.get(
                        "unknown_independence_pairs"
                    )
                    or []
                )
            ),
            "independence_fully_established": bool(
                summary.get(
                    "independence_fully_established"
                )
            ),
        }

    def _normalize_preferences(
        self,
        preferences: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        if preferences is None:
            return {}

        if not isinstance(preferences, Mapping):
            raise InsightQualityValidationError(
                "user_quality_preferences must be an object."
            )

        return {
            "desired_depth": _normalize_enum(
                preferences.get("desired_depth")
                or preferences.get("depth")
            ),
            "preferred_patterns": _stable_unique_strings(
                preferences.get("preferred_patterns")
                or preferences.get("prefer")
                or []
            ),
            "avoid_patterns": _stable_unique_strings(
                preferences.get("avoid_patterns")
                or preferences.get("avoid")
                or []
            ),
            "positive_examples": deepcopy(
                list(
                    preferences.get(
                        "positive_examples"
                    )
                    or []
                )
            ),
            "negative_examples": deepcopy(
                list(
                    preferences.get(
                        "negative_examples"
                    )
                    or []
                )
            ),
        }

    def _extract_policy(
        self,
        blueprint: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        policy = deepcopy(
            DEFAULT_POLICY
        )

        if blueprint is None:
            return policy

        if not isinstance(blueprint, Mapping):
            raise InsightQualityValidationError(
                "blueprint must be an object."
            )

        reasoning = (
            blueprint.get("reasoning_policy")
            or {}
        )
        execution = (
            blueprint.get("execution_policy")
            or {}
        )
        runtime = (
            blueprint.get("runtime_policy")
            or {}
        )

        for name, value in (
            ("reasoning_policy", reasoning),
            ("execution_policy", execution),
            ("runtime_policy", runtime),
        ):
            if not isinstance(value, Mapping):
                raise InsightQualityValidationError(
                    f"blueprint.{name} must be an object."
                )

        fields = {
            "desired_depth": reasoning,
            "require_cross_event_synthesis": reasoning,
            "require_mechanism": reasoning,
            "require_second_order_effects": reasoning,
            "require_counterevidence": reasoning,
            "require_explicit_uncertainty": reasoning,
            "reject_fact_restatement_as_hidden_insight": reasoning,
            "use_positive_examples_for_quality_reference": reasoning,
            "automatic_refinement_enabled": execution,
            "max_refinement_rounds": runtime,
            "refinement_round": runtime,
            "stop_on_no_new_evidence": runtime,
            "stop_on_no_material_change": runtime,
        }

        for field, container in fields.items():
            if field in container:
                policy[field] = container[field]

        allowed = (
            execution.get("allowed_refinement_actions")
            or runtime.get("allowed_refinement_actions")
        )

        if allowed is not None:
            normalized_allowed = [
                action
                for action in _stable_unique_strings(allowed)
                if action in ALLOWED_REFINEMENT_ACTIONS
            ]

            policy["allowed_refinement_actions"] = normalized_allowed

        for integer_field in (
            "max_refinement_rounds",
            "refinement_round",
        ):
            try:
                policy[integer_field] = int(
                    policy[integer_field]
                )
            except (TypeError, ValueError) as exc:
                raise InsightQualityValidationError(
                    f"{integer_field} must be an integer."
                ) from exc

            if policy[integer_field] < 0:
                raise InsightQualityValidationError(
                    f"{integer_field} must be >= 0."
                )

        for bool_field in (
            "require_cross_event_synthesis",
            "require_mechanism",
            "require_second_order_effects",
            "require_counterevidence",
            "require_explicit_uncertainty",
            "reject_fact_restatement_as_hidden_insight",
            "use_positive_examples_for_quality_reference",
            "automatic_refinement_enabled",
            "stop_on_no_new_evidence",
            "stop_on_no_material_change",
        ):
            policy[bool_field] = bool(
                policy[bool_field]
            )

        return policy

    # ------------------------------------------------------------------
    # References / defects / parsing
    # ------------------------------------------------------------------

    def _support_ids(
        self,
        insight: Mapping[str, Any],
        grounding: Mapping[str, Any],
    ) -> list[str]:
        return _stable_unique_strings(
            list(
                insight.get("evidence_ids")
                or []
            )
            + list(
                grounding.get(
                    "support_evidence_ids"
                )
                or []
            )
        )

    def _counter_ids(
        self,
        insight: Mapping[str, Any],
        grounding: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        # Counterevidence must be explicitly attached to this insight or its
        # grounding result. Counterevidence that merely exists elsewhere in the
        # run-wide evidence pool must not silently satisfy this insight's
        # counterevidence requirement.
        values = []

        values.extend(
            insight.get(
                "counterevidence_ids"
            )
            or []
        )

        values.extend(
            grounding.get(
                "counterevidence_ids"
            )
            or []
        )

        insight_id = _clean(
            insight.get("insight_id")
        )

        # Optional explicit applicability metadata is accepted when upstream
        # evidence discovery has already attached it.
        for record in evidence:
            applies_to = _stable_unique_strings(
                record.get("applies_to_insight_ids")
                or []
            )

            if (
                insight_id
                and insight_id in applies_to
                and _normalize_enum(
                    record.get("evidence_role")
                ) in COUNTER_ROLES
            ):
                values.append(
                    _evidence_id(record)
                )

        return _stable_unique_strings(
            values
        )

    def _validate_references(
        self,
        *,
        support_ids: Sequence[str],
        counter_ids: Sequence[str],
        evidence_by_id: Mapping[str, Mapping[str, Any]],
    ) -> None:
        referenced = set(
            support_ids
        ) | set(
            counter_ids
        )

        missing = sorted(
            referenced
            - set(
                evidence_by_id
            )
        )

        if missing:
            raise InsightQualityIntegrityError(
                "Insight/grounding references unknown evidence IDs: "
                + ", ".join(missing)
            )

    def _defect(
        self,
        code: str,
        severity: str,
        description: str,
        actions: Sequence[str],
    ) -> Dict[str, Any]:
        return {
            "code": _normalize_enum(code),
            "severity": self._normalize_severity(
                severity
            ),
            "description": _clean(
                description
            ),
            "recommended_actions": [
                action
                for action in _stable_unique_strings(
                    actions
                )
                if action in ALLOWED_REFINEMENT_ACTIONS
            ],
            "source": "deterministic",
        }

    def _normalize_severity(
        self,
        severity: Any,
    ) -> str:
        value = _normalize_enum(
            severity
        )

        if value not in {
            "critical",
            "high",
            "medium",
            "low",
        }:
            return "medium"

        return value

    def _parse_json_object(
        self,
        raw: Any,
    ) -> Dict[str, Any]:
        if isinstance(raw, Mapping):
            return deepcopy(
                dict(raw)
            )

        text = _clean(
            raw
        )

        if not text:
            raise InsightQualityValidationError(
                "llm_assessor returned an empty response."
            )

        try:
            parsed = json.loads(
                text
            )

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

        fenced = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if fenced:
            try:
                parsed = json.loads(
                    fenced.group(1)
                )
            except json.JSONDecodeError as exc:
                raise InsightQualityValidationError(
                    "llm_assessor returned malformed JSON."
                ) from exc

            if isinstance(parsed, dict):
                return parsed

        first = text.find("{")
        last = text.rfind("}")

        if first >= 0 and last > first:
            try:
                parsed = json.loads(
                    text[first : last + 1]
                )
            except json.JSONDecodeError as exc:
                raise InsightQualityValidationError(
                    "llm_assessor response did not contain valid JSON."
                ) from exc

            if isinstance(parsed, dict):
                return parsed

        raise InsightQualityValidationError(
            "llm_assessor response did not contain a JSON object."
        )