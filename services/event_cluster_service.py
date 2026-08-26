from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


class EventClusterServiceError(RuntimeError):
    pass


class EventClusterValidationError(EventClusterServiceError):
    pass


def _clean(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split()).strip()


def _pair(left: str, right: str) -> Tuple[str, str]:
    return tuple(sorted((left, right)))


def _hash_json(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _vector(value: Any, expected_dimension: Optional[int] = None) -> List[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()

    if not isinstance(value, (list, tuple)):
        raise EventClusterValidationError(
            "Each embedding must be a numeric sequence."
        )

    result = []
    for index, item in enumerate(value):
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise EventClusterValidationError(
                f"Embedding value {index} is not numeric."
            ) from exc

        if not math.isfinite(number):
            raise EventClusterValidationError(
                f"Embedding value {index} is not finite."
            )

        result.append(number)

    if not result:
        raise EventClusterValidationError(
            "Embedding vectors cannot be empty."
        )

    if expected_dimension is not None and len(result) != expected_dimension:
        raise EventClusterValidationError(
            f"Embedding dimension mismatch: expected {expected_dimension}, "
            f"received {len(result)}."
        )

    return result


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(x * x for x in right))

    if math.isclose(left_norm, 0.0, abs_tol=1e-15) or math.isclose(
        right_norm, 0.0, abs_tol=1e-15
    ):
        raise EventClusterValidationError(
            "Zero embedding vectors are not clusterable."
        )

    value = dot / (left_norm * right_norm)
    return max(-1.0, min(1.0, value))


def _stable_cluster_id(event_ids: Sequence[str]) -> str:
    digest = _hash_json(
        {"event_ids": sorted(set(event_ids))}
    )[:24].upper()
    return f"CLUSTER-{digest}"


class EventClusterService:
    """
    Deterministic complete-linkage event clustering.

    Placement:
        services/event_cluster_service.py

    Pipeline:
        EventService
            + EmbeddingService
            -> EventClusterService
            -> ClusterLabelService
            -> StrategicTopicSelector

    The service clusters; it does not label, rank, reason, or call an LLM.

    Complete-linkage is used to reduce single-linkage "chaining", where
    A resembles B and B resembles C even though A and C describe materially
    different developments.

    The similarity threshold is injected configuration and should be tuned
    against clustering_gold.jsonl rather than treated as a universal constant.
    """

    def __init__(
        self,
        *,
        similarity_threshold: float,
        minimum_cluster_size: int = 2,
    ) -> None:
        threshold = float(similarity_threshold)

        if not -1.0 <= threshold <= 1.0:
            raise EventClusterValidationError(
                "similarity_threshold must be between -1 and 1."
            )

        if minimum_cluster_size <= 0:
            raise EventClusterValidationError(
                "minimum_cluster_size must be > 0."
            )

        self.similarity_threshold = threshold
        self.minimum_cluster_size = int(minimum_cluster_size)

    def cluster_events(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        embeddings: Mapping[str, Sequence[float]]
        | Sequence[Mapping[str, Any]],
        must_link: Optional[Iterable[Sequence[str]]] = None,
        must_not_link: Optional[Iterable[Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        event_ids = self._event_ids(events)

        if not event_ids:
            return {
                "clusters": [],
                "event_to_cluster": {},
                "metadata": {
                    "algorithm": "agglomerative_complete_linkage",
                    "similarity": "cosine",
                    "similarity_threshold": self.similarity_threshold,
                    "input_event_count": 0,
                    "cluster_count": 0,
                },
            }

        vectors = self._embedding_map(
            embeddings,
            event_ids,
        )

        must_link_pairs = self._constraint_pairs(
            must_link,
            event_ids,
            "must_link",
        )
        must_not_link_pairs = self._constraint_pairs(
            must_not_link,
            event_ids,
            "must_not_link",
        )

        overlap = must_link_pairs & must_not_link_pairs
        if overlap:
            raise EventClusterValidationError(
                f"Contradictory constraints: {sorted(overlap)}"
            )

        similarities = {
            _pair(left, right): _cosine(vectors[left], vectors[right])
            for i, left in enumerate(event_ids)
            for right in event_ids[i + 1 :]
        }

        clusters: List[Set[str]] = [{event_id} for event_id in event_ids]
        clusters = self._apply_must_link(
            clusters,
            must_link_pairs,
            must_not_link_pairs,
        )
        clusters = self._agglomerate(
            clusters,
            similarities,
            must_not_link_pairs,
        )

        clusters = sorted(
            clusters,
            key=lambda members: tuple(sorted(members)),
        )

        records = []
        event_to_cluster: Dict[str, str] = {}

        for members in clusters:
            ids = sorted(members)
            cluster_id = _stable_cluster_id(ids)

            within = [
                similarities[_pair(left, right)]
                for i, left in enumerate(ids)
                for right in ids[i + 1 :]
            ]

            records.append(
                {
                    "cluster_id": cluster_id,
                    "event_ids": ids,
                    "event_count": len(ids),
                    "is_singleton_or_small": (
                        len(ids) < self.minimum_cluster_size
                    ),
                    "minimum_pairwise_similarity": (
                        min(within) if within else None
                    ),
                    "mean_pairwise_similarity": (
                        sum(within) / len(within) if within else None
                    ),
                }
            )

            for event_id in ids:
                if event_id in event_to_cluster:
                    raise EventClusterServiceError(
                        f"Event {event_id!r} assigned more than once."
                    )
                event_to_cluster[event_id] = cluster_id

        if set(event_to_cluster) != set(event_ids):
            raise EventClusterServiceError(
                "Clustering completeness invariant failed."
            )

        fingerprint = _hash_json(
            {
                "event_ids": sorted(event_ids),
                "embeddings": {
                    key: vectors[key]
                    for key in sorted(vectors)
                },
                "threshold": self.similarity_threshold,
                "must_link": sorted(must_link_pairs),
                "must_not_link": sorted(must_not_link_pairs),
            }
        )

        return {
            "clusters": records,
            "event_to_cluster": event_to_cluster,
            "metadata": {
                "algorithm": "agglomerative_complete_linkage",
                "similarity": "cosine",
                "similarity_threshold": self.similarity_threshold,
                "minimum_cluster_size": self.minimum_cluster_size,
                "input_event_count": len(event_ids),
                "cluster_count": len(records),
                "singleton_or_small_count": sum(
                    int(record["is_singleton_or_small"])
                    for record in records
                ),
                "must_link_count": len(must_link_pairs),
                "must_not_link_count": len(must_not_link_pairs),
                "input_fingerprint": fingerprint,
            },
        }

    def _event_ids(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> List[str]:
        if not isinstance(events, Sequence) or isinstance(
            events, (str, bytes)
        ):
            raise EventClusterValidationError(
                "events must be a sequence of objects."
            )

        result = []
        seen = set()

        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                raise EventClusterValidationError(
                    f"events[{index}] must be an object."
                )

            event_id = _clean(
                event.get("event_id") or event.get("id")
            )

            if not event_id:
                raise EventClusterValidationError(
                    f"events[{index}] is missing event_id/id."
                )

            if event_id in seen:
                raise EventClusterValidationError(
                    f"Duplicate event_id={event_id!r}."
                )

            seen.add(event_id)
            result.append(event_id)

        return result

    def _embedding_map(
        self,
        embeddings: Mapping[str, Sequence[float]]
        | Sequence[Mapping[str, Any]],
        event_ids: Sequence[str],
    ) -> Dict[str, List[float]]:
        raw: Dict[str, Any] = {}

        if isinstance(embeddings, Mapping):
            raw = {
                _clean(key): value
                for key, value in embeddings.items()
            }

        elif isinstance(embeddings, Sequence) and not isinstance(
            embeddings, (str, bytes)
        ):
            for index, item in enumerate(embeddings):
                if not isinstance(item, Mapping):
                    raise EventClusterValidationError(
                        f"embeddings[{index}] must be an object."
                    )

                event_id = _clean(
                    item.get("record_id")
                    or item.get("event_id")
                    or item.get("id")
                )

                if not event_id:
                    raise EventClusterValidationError(
                        f"embeddings[{index}] has no record/event ID."
                    )

                if event_id in raw:
                    raise EventClusterValidationError(
                        f"Duplicate embedding for {event_id!r}."
                    )

                raw[event_id] = item.get("embedding")

        else:
            raise EventClusterValidationError(
                "embeddings must be an ID->vector mapping or embedding records."
            )

        required = set(event_ids)
        missing = sorted(required - set(raw))
        extra = sorted(set(raw) - required)

        if missing:
            raise EventClusterValidationError(
                "Missing embeddings: " + ", ".join(missing)
            )

        if extra:
            raise EventClusterValidationError(
                "Unknown embedding IDs: " + ", ".join(extra)
            )

        result = {}
        dimension = None

        for event_id in event_ids:
            vector = _vector(
                raw[event_id],
                expected_dimension=dimension,
            )
            if dimension is None:
                dimension = len(vector)
            result[event_id] = vector

        return result

    def _constraint_pairs(
        self,
        pairs: Optional[Iterable[Sequence[str]]],
        event_ids: Sequence[str],
        name: str,
    ) -> Set[Tuple[str, str]]:
        if pairs is None:
            return set()

        allowed = set(event_ids)
        result = set()

        for index, value in enumerate(pairs):
            if (
                not isinstance(value, Sequence)
                or isinstance(value, (str, bytes))
                or len(value) != 2
            ):
                raise EventClusterValidationError(
                    f"{name}[{index}] must contain two event IDs."
                )

            left = _clean(value[0])
            right = _clean(value[1])

            if not left or not right or left == right:
                raise EventClusterValidationError(
                    f"{name}[{index}] must contain two distinct IDs."
                )

            unknown = {left, right} - allowed
            if unknown:
                raise EventClusterValidationError(
                    f"{name}[{index}] references unknown IDs: "
                    + ", ".join(sorted(unknown))
                )

            result.add(_pair(left, right))

        return result

    def _contains_forbidden(
        self,
        members: Set[str],
        forbidden: Set[Tuple[str, str]],
    ) -> bool:
        ids = sorted(members)

        return any(
            _pair(left, right) in forbidden
            for i, left in enumerate(ids)
            for right in ids[i + 1 :]
        )

    def _apply_must_link(
        self,
        clusters: List[Set[str]],
        must_link: Set[Tuple[str, str]],
        must_not_link: Set[Tuple[str, str]],
    ) -> List[Set[str]]:
        for left, right in sorted(must_link):
            left_index = next(
                index
                for index, cluster in enumerate(clusters)
                if left in cluster
            )
            right_index = next(
                index
                for index, cluster in enumerate(clusters)
                if right in cluster
            )

            if left_index == right_index:
                continue

            merged = (
                clusters[left_index]
                | clusters[right_index]
            )

            if self._contains_forbidden(
                merged,
                must_not_link,
            ):
                raise EventClusterValidationError(
                    "must_link implies a must_not_link violation."
                )

            clusters = [
                cluster
                for index, cluster in enumerate(clusters)
                if index not in {left_index, right_index}
            ] + [merged]

        return clusters

    def _agglomerate(
        self,
        clusters: List[Set[str]],
        similarities: Mapping[Tuple[str, str], float],
        must_not_link: Set[Tuple[str, str]],
    ) -> List[Set[str]]:
        clusters = [set(cluster) for cluster in clusters]

        while True:
            candidates = []

            for left_index in range(len(clusters)):
                for right_index in range(
                    left_index + 1,
                    len(clusters),
                ):
                    left = clusters[left_index]
                    right = clusters[right_index]
                    merged = left | right

                    if self._contains_forbidden(
                        merged,
                        must_not_link,
                    ):
                        continue

                    complete_similarity = min(
                        similarities[_pair(a, b)]
                        for a in left
                        for b in right
                    )

                    if (
                        complete_similarity
                        >= self.similarity_threshold
                    ):
                        candidates.append(
                            (
                                -complete_similarity,
                                tuple(sorted(left)),
                                tuple(sorted(right)),
                                left_index,
                                right_index,
                            )
                        )

            if not candidates:
                break

            candidates.sort()
            _, _, _, left_index, right_index = candidates[0]

            merged = (
                clusters[left_index]
                | clusters[right_index]
            )

            clusters = [
                cluster
                for index, cluster in enumerate(clusters)
                if index not in {left_index, right_index}
            ] + [merged]

        return clusters