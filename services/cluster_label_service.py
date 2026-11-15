"""
LLM-assisted cluster labelling service.

Recommended location:
    services/cluster_label_service.py

Role
----
EventClusterService
        ↓
cluster of event records
        ↓
ClusterLabelService
        ↓
precise subtopic + cluster development + coherence status
        ↓
StrategicTopicSelector / ReasoningContextService

Why this is a service, not an agent
-----------------------------------
Cluster labelling is a narrow classification task. The LLM interprets the
cluster, but it does not autonomously plan, retrieve, reason across tools, or
make release decisions.

Responsibilities
----------------
- Give each event cluster a precise, evidence-bounded label.
- Describe the concrete development represented by the cluster.
- Classify cluster coherence:
      coherent
      mixed
      insufficient_evidence
- Return only entity names and event IDs that actually occur in the supplied
  cluster.
- Record lightweight execution metadata and deterministic fingerprints.

Non-responsibilities
--------------------
- No event clustering.
- No strategic inference.
- No source-independence judgement.
- No grounding/release decision.
- No numeric confidence score.
- No arbitrary input truncation.

Expected output
---------------
{
    "cluster_id": "CLUSTER-001",
    "topic": "Artificial Intelligence",
    "subtopic": "AI data-center power procurement",
    "cluster_development": (
        "Large AI infrastructure operators are securing long-term power "
        "supply as grid access becomes a deployment constraint."
    ),
    "coherence_status": "coherent",
    "primary_entities": ["DataCo", "GridCo"],
    "supporting_event_ids": ["EVENT-001", "EVENT-002"],
    "metadata": {
        "prompt_version": "cluster-label-v1",
        "model": "...",
        "input_event_count": 2,
        "response_chars": 642,
        "response_hash": "...",
        "content_fingerprint": "...",
        "run_fingerprint": "..."
    }
}

Important epistemic rule
------------------------
This service labels WHAT development a cluster represents.

It must not turn:
    "several data-centre projects mention grid queues"
into:
    "power will become the dominant moat in AI"

The latter belongs to StrategicInferenceAgent.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set


ALLOWED_COHERENCE_STATUSES = {
    "coherent",
    "mixed",
    "insufficient_evidence",
}

PROMPT_VERSION = "cluster-label-v1"


class ClusterLabelServiceError(RuntimeError):
    """Base error raised by ClusterLabelService."""


class ClusterLabelValidationError(ClusterLabelServiceError):
    """Raised when input or LLM output violates the cluster-label contract."""


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


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")

    return hashlib.sha256(value).hexdigest()


def _unique_strings(values: Any) -> List[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        return []

    result: List[str] = []
    seen: Set[str] = set()

    for value in values:
        if isinstance(value, Mapping):
            value = (
                value.get("name")
                or value.get("id")
                or value.get("event_id")
                or ""
            )

        text = _clean(value)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def _event_id(event: Mapping[str, Any]) -> str:
    return _clean(
        event.get("event_id")
        or event.get("id")
    )


def _event_title(event: Mapping[str, Any]) -> str:
    return _clean(
        event.get("title")
        or event.get("event_title")
    )


def _event_summary(event: Mapping[str, Any]) -> str:
    return _clean(
        event.get("summary")
        or event.get("event_summary")
    )


def _event_type(event: Mapping[str, Any]) -> str:
    return _clean(
        event.get("event_type")
        or event.get("type")
    )


def _flatten_entities(value: Any) -> List[str]:
    """
    Extract entity names from common event entity structures.

    Supports:
        ["OpenAI", "Microsoft"]

    or:
        {
            "companies": ["OpenAI"],
            "countries": ["United States"]
        }

    or:
        [{"name": "OpenAI", "type": "company"}]
    """
    result: List[str] = []

    if value is None:
        return result

    if isinstance(value, str):
        return [_clean(value)] if _clean(value) else []

    if isinstance(value, Mapping):
        if (
            "name" in value
            and _clean(value.get("name"))
        ):
            return [_clean(value.get("name"))]

        for nested in value.values():
            result.extend(
                _flatten_entities(nested)
            )

        return _unique_strings(result)

    if isinstance(value, (list, tuple, set)):
        for nested in value:
            result.extend(
                _flatten_entities(nested)
            )

    return _unique_strings(result)


def _event_entities(event: Mapping[str, Any]) -> List[str]:
    return _flatten_entities(
        event.get("entities")
        or event.get("entity_names")
        or []
    )


def _normalize_coherence_status(value: Any) -> str:
    normalized = _normalize_key(value)

    aliases = {
        "insufficient": "insufficient_evidence",
        "insufficient_context": "insufficient_evidence",
        "not_enough_evidence": "insufficient_evidence",
        "incoherent": "mixed",
    }

    normalized = aliases.get(
        normalized,
        normalized,
    )

    return (
        normalized
        if normalized in ALLOWED_COHERENCE_STATUSES
        else ""
    )


def _extract_json_object(raw: str) -> Dict[str, Any]:
    """
    Parse a JSON object from a model response.

    Supports plain JSON or one fenced JSON block.
    Does not run a second model-based "repair" pass.
    """
    text = raw.strip()

    if not text:
        raise ClusterLabelValidationError(
            "LLM returned an empty response."
        )

    try:
        parsed = json.loads(text)

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
            raise ClusterLabelValidationError(
                "LLM returned malformed fenced JSON."
            ) from exc

        if isinstance(parsed, dict):
            return parsed

    # Last deterministic attempt: first complete-looking JSON object.
    first = text.find("{")
    last = text.rfind("}")

    if (
        first >= 0
        and last > first
    ):
        candidate = text[
            first:last + 1
        ]

        try:
            parsed = json.loads(
                candidate
            )
        except json.JSONDecodeError as exc:
            raise ClusterLabelValidationError(
                "LLM response did not contain valid JSON."
            ) from exc

        if isinstance(parsed, dict):
            return parsed

    raise ClusterLabelValidationError(
        "LLM response did not contain a JSON object."
    )


class ClusterLabelService:
    """
    Narrow LLM-assisted cluster classifier.

    Parameters
    ----------
    llm_callable:
        Callable receiving a prompt string and returning either:
        - str containing JSON; or
        - Mapping already parsed as JSON.

        This allows ModelRouterService/LLMService to be injected without
        hard-coding a specific provider into this service.

    model_name:
        Metadata only. The router/model service remains responsible for the
        actual model choice.

    prompt_version:
        Version string included in the deterministic run fingerprint.
    """

    def __init__(
        self,
        *,
        llm_callable: Callable[
            [str],
            str | Mapping[str, Any],
        ],
        model_name: str = "",
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        if not callable(
            llm_callable
        ):
            raise ClusterLabelValidationError(
                "llm_callable must be callable."
            )

        self.llm_callable = (
            llm_callable
        )
        self.model_name = _clean(
            model_name
        )
        self.prompt_version = _clean(
            prompt_version
        )

        if not self.prompt_version:
            raise ClusterLabelValidationError(
                "prompt_version cannot be empty."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def label_cluster(
        self,
        *,
        cluster_id: str,
        topic: str,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        """
        Label one event cluster.

        Every event is included in the model-visible payload. This service
        performs no hidden top-k or character truncation.
        """
        cluster_id = _clean(
            cluster_id
        )
        topic = _clean(
            topic
        )

        if not cluster_id:
            raise ClusterLabelValidationError(
                "cluster_id cannot be empty."
            )

        if not topic:
            raise ClusterLabelValidationError(
                "topic cannot be empty."
            )

        normalized_events = (
            self._validate_and_normalize_events(
                events
            )
        )

        content_payload = {
            "cluster_id": (
                cluster_id
            ),
            "topic": (
                topic
            ),
            "events": (
                normalized_events
            ),
        }

        content_fingerprint = (
            _sha256_hex(
                _canonical_json_bytes(
                    content_payload
                )
            )
        )

        run_payload = {
            "content_fingerprint": (
                content_fingerprint
            ),
            "prompt_version": (
                self.prompt_version
            ),
            "model_name": (
                self.model_name
            ),
        }

        run_fingerprint = (
            _sha256_hex(
                _canonical_json_bytes(
                    run_payload
                )
            )
        )

        prompt = self._build_prompt(
            cluster_id=cluster_id,
            topic=topic,
            events=normalized_events,
        )

        raw_response = self.llm_callable(
            prompt
        )

        if isinstance(
            raw_response,
            Mapping,
        ):
            parsed = dict(
                raw_response
            )
            raw_text = json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        else:
            raw_text = str(
                raw_response
            )
            parsed = _extract_json_object(
                raw_text
            )

        validated = (
            self._validate_model_output(
                parsed=parsed,
                cluster_id=cluster_id,
                topic=topic,
                events=normalized_events,
            )
        )

        validated[
            "metadata"
        ] = {
            "prompt_version": (
                self.prompt_version
            ),
            "model": (
                self.model_name
            ),
            "input_event_count": len(
                normalized_events
            ),
            "response_chars": len(
                raw_text
            ),
            "response_hash": (
                _sha256_hex(
                    raw_text
                )
            ),
            "content_fingerprint": (
                content_fingerprint
            ),
            "run_fingerprint": (
                run_fingerprint
            ),
        }

        return validated

    def label_clusters(
        self,
        *,
        topic: str,
        clusters: Sequence[
            Mapping[str, Any]
        ],
    ) -> List[Dict[str, Any]]:
        """
        Label every supplied cluster independently.

        No cluster is silently skipped. A validation/model failure raises and
        lets the caller decide retry/fallback policy.
        """
        results: List[
            Dict[str, Any]
        ] = []

        for index, cluster in enumerate(
            clusters
        ):
            if not isinstance(
                cluster,
                Mapping,
            ):
                raise ClusterLabelValidationError(
                    f"clusters[{index}] must be an object."
                )

            cluster_id = _clean(
                cluster.get(
                    "cluster_id"
                )
                or cluster.get(
                    "id"
                )
            )

            events = cluster.get(
                "events"
            )

            if not isinstance(
                events,
                list,
            ):
                raise ClusterLabelValidationError(
                    f"cluster {cluster_id or index!r} "
                    "must contain an events list."
                )

            results.append(
                self.label_cluster(
                    cluster_id=cluster_id,
                    topic=topic,
                    events=events,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Input normalization
    # ------------------------------------------------------------------

    def _validate_and_normalize_events(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> List[Dict[str, Any]]:
        if not isinstance(
            events,
            Sequence,
        ) or isinstance(
            events,
            (str, bytes),
        ):
            raise ClusterLabelValidationError(
                "events must be a sequence of event objects."
            )

        if not events:
            raise ClusterLabelValidationError(
                "events cannot be empty."
            )

        result: List[
            Dict[str, Any]
        ] = []

        seen_ids: Set[
            str
        ] = set()

        for index, event in enumerate(
            events
        ):
            if not isinstance(
                event,
                Mapping,
            ):
                raise ClusterLabelValidationError(
                    f"events[{index}] must be an object."
                )

            event_id = _event_id(
                event
            )

            if not event_id:
                raise ClusterLabelValidationError(
                    f"events[{index}] is missing event_id/id."
                )

            if event_id in seen_ids:
                raise ClusterLabelValidationError(
                    f"Duplicate event_id={event_id!r}."
                )

            seen_ids.add(
                event_id
            )

            title = _event_title(
                event
            )
            summary = _event_summary(
                event
            )

            if not title and not summary:
                raise ClusterLabelValidationError(
                    f"Event {event_id!r} must contain title or summary."
                )

            result.append(
                {
                    "event_id": (
                        event_id
                    ),
                    "title": (
                        title
                    ),
                    "summary": (
                        summary
                    ),
                    "event_type": (
                        _event_type(
                            event
                        )
                    ),
                    "temporal_status": _clean(
                        event.get(
                            "temporal_status"
                        )
                    ),
                    "claim_status": _clean(
                        event.get(
                            "claim_status"
                        )
                    ),
                    "entities": (
                        _event_entities(
                            event
                        )
                    ),
                }
            )

        return result

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        *,
        cluster_id: str,
        topic: str,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> str:
        event_json = json.dumps(
            list(
                events
            ),
            ensure_ascii=False,
            indent=2,
        )

        return f"""
You are a narrow event-cluster labelling component.

Your job is NOT to perform strategic analysis.
Your job is NOT to predict consequences.
Your job is NOT to decide importance.
Your job is NOT to infer facts that are absent from the supplied events.

Research topic:
{topic}

Cluster ID:
{cluster_id}

Events:
{event_json}

Return exactly one JSON object with these fields:

{{
  "subtopic": "short precise label",
  "cluster_development": "one or two sentences describing the concrete development represented by these events",
  "coherence_status": "coherent | mixed | insufficient_evidence",
  "primary_entities": ["entity names copied from the supplied events"],
  "supporting_event_ids": ["event IDs copied from the supplied events"]
}}

Definitions:

coherent:
The events mostly describe the same concrete underlying development.

mixed:
The cluster contains two or more materially different developments and should
not be represented as one clean story.

insufficient_evidence:
The supplied event records do not contain enough information to identify a
specific development.

Rules:
1. Use only information in the supplied events.
2. Do not introduce outside facts.
3. Do not create strategic implications, winners/losers, recommendations,
   structural forecasts, or second-order effects.
4. Do not output a numeric confidence score.
5. Every primary_entities value must appear in the supplied event entities.
6. Every supporting_event_ids value must be one of the supplied event IDs.
7. If coherence_status is mixed, say clearly what is mixed rather than forcing
   one synthetic development.
8. If coherence_status is insufficient_evidence, use:
       subtopic = "Insufficient evidence"
   and explain briefly what cannot be established.
9. Do not wrap the JSON in commentary.
""".strip()

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    def _validate_model_output(
        self,
        *,
        parsed: Mapping[str, Any],
        cluster_id: str,
        topic: str,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        if not isinstance(
            parsed,
            Mapping,
        ):
            raise ClusterLabelValidationError(
                "Cluster label output must be an object."
            )

        # Explicitly reject confidence-like numeric outputs rather than silently
        # carrying them into the product contract.
        prohibited_keys = {
            "confidence",
            "confidence_score",
            "probability",
            "score",
        }

        present_prohibited = sorted(
            key
            for key in prohibited_keys
            if key in parsed
        )

        if present_prohibited:
            raise ClusterLabelValidationError(
                "Cluster label output contains prohibited confidence/score "
                "field(s): "
                + ", ".join(
                    present_prohibited
                )
            )

        coherence_status = (
            _normalize_coherence_status(
                parsed.get(
                    "coherence_status"
                )
            )
        )

        if not coherence_status:
            raise ClusterLabelValidationError(
                "coherence_status must be one of: "
                + ", ".join(
                    sorted(
                        ALLOWED_COHERENCE_STATUSES
                    )
                )
            )

        subtopic = _clean(
            parsed.get(
                "subtopic"
            )
        )
        development = _clean(
            parsed.get(
                "cluster_development"
            )
        )

        if (
            coherence_status
            == "insufficient_evidence"
        ):
            subtopic = (
                subtopic
                or "Insufficient evidence"
            )
            development = (
                development
                or (
                    "The supplied events do not establish "
                    "one specific development."
                )
            )

        if not subtopic:
            raise ClusterLabelValidationError(
                "subtopic cannot be empty."
            )

        if not development:
            raise ClusterLabelValidationError(
                "cluster_development cannot be empty."
            )

        allowed_event_ids = {
            event[
                "event_id"
            ]
            for event in events
        }

        requested_support_ids = (
            _unique_strings(
                parsed.get(
                    "supporting_event_ids"
                )
            )
        )

        supporting_event_ids = [
            event_id
            for event_id in requested_support_ids
            if event_id in allowed_event_ids
        ]

        if (
            coherence_status
            != "insufficient_evidence"
            and not supporting_event_ids
        ):
            raise ClusterLabelValidationError(
                "A coherent/mixed cluster label must cite at least one "
                "valid supporting_event_id."
            )

        allowed_entities: List[
            str
        ] = []

        for event in events:
            allowed_entities.extend(
                event[
                    "entities"
                ]
            )

        allowed_entities = _unique_strings(
            allowed_entities
        )

        allowed_entity_lookup = {
            entity.casefold(): entity
            for entity in allowed_entities
        }

        requested_entities = (
            _unique_strings(
                parsed.get(
                    "primary_entities"
                )
            )
        )

        # Filter unsupported entities deterministically rather than letting the
        # label service manufacture canonical entities.
        primary_entities: List[
            str
        ] = []

        seen_entities: Set[
            str
        ] = set()

        for entity in requested_entities:
            canonical = (
                allowed_entity_lookup.get(
                    entity.casefold()
                )
            )

            if (
                canonical
                and canonical
                not in seen_entities
            ):
                seen_entities.add(
                    canonical
                )
                primary_entities.append(
                    canonical
                )

        return {
            "cluster_id": (
                cluster_id
            ),
            "topic": (
                topic
            ),
            "subtopic": (
                subtopic
            ),
            "cluster_development": (
                development
            ),
            "coherence_status": (
                coherence_status
            ),
            "primary_entities": (
                primary_entities
            ),
            "supporting_event_ids": (
                supporting_event_ids
            ),
        }