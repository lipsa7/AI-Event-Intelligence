"""
Deterministic strategic topic selector.

Recommended location:
    services/strategic_topic_selector.py

Role
----
Event clusters + cluster labels + evidence/novelty/update metrics
        ↓
StrategicTopicSelector
        ↓
ranked cluster/topic candidates
        ↓
selected topics for expensive reasoning/discovery

The selector decides WHICH clusters deserve scarce research budget. It does not
generate strategic insights and does not decide truth.

Selection principles:
- evidence density
- event density
- cross-event connectivity
- novelty versus prior reports
- material update significance
- strategic relevance
- temporal recency
- diversity among selected topics

All score inputs are explicit. The selector never invents domain-specific
importance from names such as AI, semiconductors, regulation, etc.

The default score is deterministic and configurable from Blueprint
selection_policy.

Fast vs Deep:
- same algorithm
- max_selected_clusters and candidate breadth may differ in Blueprint
- epistemic standards do not differ

Similarity/diversity can use an injected deterministic similarity function or
pre-computed `similarity_by_cluster_id`. Diversity is NOT source independence.

No LLM, search, grounding or release authorization occurs here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "strategic-topic-selector-v2.0"

DEFAULT_WEIGHTS = {
    "evidence_density": 0.20,
    "event_density": 0.10,
    "connectivity": 0.15,
    "novelty": 0.20,
    "material_update": 0.15,
    "strategic_relevance": 0.15,
    "recency": 0.05,
}

PROHIBITED_PRIVATE_REASONING_FIELDS = {
    "chain_of_thought", "chainofthought", "cot", "scratchpad",
    "hidden_reasoning", "hiddenreasoning", "raw_reasoning", "rawreasoning",
    "private_reasoning", "privatereasoning",
}


class StrategicTopicSelectorError(RuntimeError):
    pass


class StrategicTopicSelectorValidationError(StrategicTopicSelectorError):
    pass


def _clean(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    text = _clean(value).casefold().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _score(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise StrategicTopicSelectorValidationError(
            f"{name} must be numeric, not bool."
        )
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise StrategicTopicSelectorValidationError(
            f"{name} must be numeric."
        ) from exc

    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise StrategicTopicSelectorValidationError(
            f"{name} must be in [0, 1]."
        )
    return value


def _positive_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise StrategicTopicSelectorValidationError(
            f"{name} must be integer."
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise StrategicTopicSelectorValidationError(
            f"{name} must be integer."
        ) from exc
    if number <= 0:
        raise StrategicTopicSelectorValidationError(
            f"{name} must be > 0."
        )
    return number


def _private_path(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if _enum(key) in PROHIBITED_PRIVATE_REASONING_FIELDS:
                return current
            nested = _private_path(item, current)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            nested = _private_path(item, f"{path}[{i}]")
            if nested:
                return nested
    return None


class StrategicTopicSelector:
    def __init__(
        self,
        *,
        weights: Optional[Mapping[str, float]] = None,
        similarity_fn: Optional[Callable[[Mapping[str, Any], Mapping[str, Any]], float]] = None,
    ) -> None:
        merged = dict(DEFAULT_WEIGHTS)
        if weights is not None:
            if not isinstance(weights, Mapping):
                raise StrategicTopicSelectorValidationError(
                    "weights must be an object."
                )
            for key, value in weights.items():
                key = _enum(key)
                if key not in DEFAULT_WEIGHTS:
                    raise StrategicTopicSelectorValidationError(
                        f"Unknown weight={key!r}."
                    )
                merged[key] = float(value)

        if any(value < 0 for value in merged.values()):
            raise StrategicTopicSelectorValidationError(
                "weights cannot be negative."
            )

        total = sum(merged.values())
        if total <= 0:
            raise StrategicTopicSelectorValidationError(
                "At least one selector weight must be positive."
            )

        self.weights = {
            key: value / total
            for key, value in merged.items()
        }

        if similarity_fn is not None and not callable(similarity_fn):
            raise StrategicTopicSelectorValidationError(
                "similarity_fn must be callable."
            )

        self.similarity_fn = similarity_fn

    def rank_candidates(
        self,
        clusters: Sequence[Mapping[str, Any]],
        *,
        selection_policy: Optional[Mapping[str, Any]] = None,
    ) -> list[Dict[str, Any]]:
        if isinstance(clusters, (str, bytes)) or not isinstance(clusters, Sequence):
            raise StrategicTopicSelectorValidationError(
                "clusters must be a sequence."
            )

        policy = dict(selection_policy or {})
        minimum_score = _score(
            policy.get("minimum_candidate_score", 0.0),
            name="minimum_candidate_score",
        )

        ranked = []
        seen = set()

        for index, cluster in enumerate(clusters):
            if not isinstance(cluster, Mapping):
                raise StrategicTopicSelectorValidationError(
                    f"clusters[{index}] must be an object."
                )

            private = _private_path(cluster)
            if private:
                raise StrategicTopicSelectorValidationError(
                    f"cluster contains private/raw reasoning field at {private!r}."
                )

            cluster_id = _clean(cluster.get("cluster_id"))
            if not cluster_id:
                raise StrategicTopicSelectorValidationError(
                    f"clusters[{index}] missing cluster_id."
                )
            if cluster_id in seen:
                raise StrategicTopicSelectorValidationError(
                    f"duplicate cluster_id={cluster_id!r}."
                )
            seen.add(cluster_id)

            components = {
                "evidence_density": _score(
                    cluster.get("evidence_density", 0.0),
                    name=f"{cluster_id}.evidence_density",
                ),
                "event_density": _score(
                    cluster.get("event_density", 0.0),
                    name=f"{cluster_id}.event_density",
                ),
                "connectivity": _score(
                    cluster.get("connectivity", 0.0),
                    name=f"{cluster_id}.connectivity",
                ),
                "novelty": _score(
                    cluster.get("novelty", 0.0),
                    name=f"{cluster_id}.novelty",
                ),
                "material_update": _score(
                    cluster.get("material_update", 0.0),
                    name=f"{cluster_id}.material_update",
                ),
                "strategic_relevance": _score(
                    cluster.get("strategic_relevance", 0.0),
                    name=f"{cluster_id}.strategic_relevance",
                ),
                "recency": _score(
                    cluster.get("recency", 0.0),
                    name=f"{cluster_id}.recency",
                ),
            }

            base_score = sum(
                self.weights[name] * components[name]
                for name in self.weights
            )

            # Minimum evidence availability is an execution-quality precondition,
            # not a truth judgement.
            event_count = self._nonnegative_count(
                cluster.get("event_count", 0),
                f"{cluster_id}.event_count",
            )
            evidence_count = self._nonnegative_count(
                cluster.get("evidence_count", 0),
                f"{cluster_id}.evidence_count",
            )

            eligible = bool(
                base_score >= minimum_score
                and event_count > 0
                and evidence_count > 0
            )

            ranked.append(
                {
                    "cluster_id": cluster_id,
                    "label": _clean(
                        cluster.get("label")
                        or cluster.get("cluster_label")
                        or cluster.get("title")
                    ),
                    "base_score": round(base_score, 8),
                    "score_components": components,
                    "event_count": event_count,
                    "evidence_count": evidence_count,
                    "eligible": eligible,
                    "exclusion_reasons": (
                        []
                        if eligible
                        else self._exclusion_reasons(
                            base_score,
                            minimum_score,
                            event_count,
                            evidence_count,
                        )
                    ),
                    "metadata": deepcopy(dict(cluster.get("metadata") or {})),
                    "selector_semantics": {
                        "strategic_score_is_truth_probability": False,
                        "novelty_is_grounding_strength": False,
                        "diversity_is_source_independence": False,
                    },
                }
            )

        ranked.sort(
            key=lambda item: (
                not item["eligible"],
                -item["base_score"],
                item["cluster_id"],
            )
        )

        for rank, item in enumerate(ranked, start=1):
            item["candidate_rank"] = rank

        return ranked

    def select(
        self,
        clusters: Sequence[Mapping[str, Any]],
        *,
        selection_policy: Optional[Mapping[str, Any]] = None,
        max_selected_clusters: Optional[int] = None,
        similarity_by_cluster_id: Optional[
            Mapping[str, Mapping[str, float]]
        ] = None,
    ) -> Dict[str, Any]:
        policy = dict(selection_policy or {})

        max_selected = (
            _positive_int(
                max_selected_clusters,
                name="max_selected_clusters",
            )
            if max_selected_clusters is not None
            else _positive_int(
                policy.get("max_selected_clusters", 3),
                name="selection_policy.max_selected_clusters",
            )
        )

        diversity_lambda = _score(
            policy.get("diversity_lambda", 0.75),
            name="selection_policy.diversity_lambda",
        )

        ranked = self.rank_candidates(
            clusters,
            selection_policy=policy,
        )

        by_id = {
            _clean(cluster.get("cluster_id")): cluster
            for cluster in clusters
            if isinstance(cluster, Mapping)
        }

        eligible = [item for item in ranked if item["eligible"]]
        selected = []

        while eligible and len(selected) < max_selected:
            best = None
            best_key = None

            for candidate in eligible:
                if not selected:
                    similarity_penalty = 0.0
                else:
                    similarities = [
                        self._similarity(
                            candidate["cluster_id"],
                            chosen["cluster_id"],
                            by_id,
                            similarity_by_cluster_id,
                        )
                        for chosen in selected
                    ]
                    similarity_penalty = max(similarities) if similarities else 0.0

                selection_score = (
                    diversity_lambda * candidate["base_score"]
                    - (1.0 - diversity_lambda) * similarity_penalty
                )

                key = (
                    selection_score,
                    candidate["base_score"],
                    candidate["cluster_id"],
                )
                if best is None or key > best_key:
                    best = candidate
                    best_key = key

            selected.append(
                {
                    **best,
                    "selection_score": round(float(best_key[0]), 8),
                    "max_similarity_to_already_selected": round(
                        float(
                            diversity_lambda * 0.0
                            + (
                                (best["base_score"] * diversity_lambda - best_key[0])
                                / (1.0 - diversity_lambda)
                                if diversity_lambda < 1.0
                                else 0.0
                            )
                        ),
                        8,
                    ),
                    "selected_rank": len(selected) + 1,
                }
            )

            eligible = [
                item
                for item in eligible
                if item["cluster_id"] != best["cluster_id"]
            ]

        return {
            "selected_topics": selected,
            "ranked_candidates": ranked,
            "selected_cluster_ids": [
                item["cluster_id"]
                for item in selected
            ],
            "metadata": {
                "service_version": SERVICE_VERSION,
                "max_selected_clusters": max_selected,
                "diversity_lambda": diversity_lambda,
                "llm_called": False,
                "search_performed": False,
                "grounding_performed": False,
                "release_authorized": False,
                "domain_specific_priority_hardcoded": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def _similarity(
        self,
        left_id: str,
        right_id: str,
        by_id: Mapping[str, Mapping[str, Any]],
        matrix: Optional[Mapping[str, Mapping[str, float]]],
    ) -> float:
        if matrix is not None:
            raw = (
                (matrix.get(left_id) or {}).get(right_id)
                if isinstance(matrix.get(left_id), Mapping)
                else None
            )
            if raw is None and isinstance(matrix.get(right_id), Mapping):
                raw = (matrix.get(right_id) or {}).get(left_id)
            return _score(raw or 0.0, name="cluster_similarity")

        if self.similarity_fn is not None:
            return _score(
                self.similarity_fn(by_id[left_id], by_id[right_id]),
                name="cluster_similarity",
            )

        # No similarity input means no diversity penalty; never invent one.
        return 0.0

    @staticmethod
    def _nonnegative_count(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise StrategicTopicSelectorValidationError(
                f"{name} must be integer."
            )
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise StrategicTopicSelectorValidationError(
                f"{name} must be integer."
            ) from exc
        if number < 0:
            raise StrategicTopicSelectorValidationError(
                f"{name} must be >= 0."
            )
        return number

    @staticmethod
    def _exclusion_reasons(
        score: float,
        minimum_score: float,
        event_count: int,
        evidence_count: int,
    ) -> list[str]:
        reasons = []
        if score < minimum_score:
            reasons.append("below_minimum_candidate_score")
        if event_count == 0:
            reasons.append("no_events")
        if evidence_count == 0:
            reasons.append("no_evidence")
        return reasons


StrategicTopicSelectionService = StrategicTopicSelector