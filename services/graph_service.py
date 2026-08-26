"""
Knowledge-graph projection, persistence and query service.

Recommended location:
    services/graph_service.py

Role
----
EntityService
        ↓
canonical ENT-* nodes

RelationshipService
        ↓
canonical REL-* semantic relationships
        +
aggregated RELOBS-* provenance
        ↓
GraphService
        ↓
persistent/queryable NetworkX projection
        ↓
ReasoningContextService / StrategicInferenceAgent

Authority boundary
------------------
RelationshipService / relational database is the source of truth.

GraphService is a derived projection.

Therefore:

    database relationship state
        ↓
    GraphService projection

not:

    GraphService
        ↓
    epistemic truth

A graph edge does not prove that a relationship is grounded.

Design principles
-----------------
- Deterministic service: no LLM calls.
- Entity resolution happens before this service.
- Accepts canonical ENT-* identity only.
- Accepts canonical REL-* relationship identity only.
- Preserves EVID-*, EVENT-*, SOURCE-* and RELOBS-* provenance.
- Uses NetworkX MultiDiGraph because multiple semantic relationship records may
  exist between the same entities.
- Relationship IDs are globally unique even though NetworkX edge keys are
  endpoint-scoped.
- RelationshipService remains reconstructible source of truth.
- Projection updates REPLACE current provenance snapshots by default rather than
  indefinitely merging stale provenance.
- No arbitrary top-k truncation.
- Context-window selection belongs to ReasoningContextService.
- Temporal validity belongs to observations, not semantic REL-* identity.
- Source count is not interpreted as source independence.
- Graph traversal is structural retrieval, not grounding.
- Persistence uses explicit versioned JSON rather than NetworkX-specific
  serialization formats.
- No private chain-of-thought is stored.

Canonical node
--------------
{
    "entity_id": "ENT-*",
    "name": "...",
    "entity_type": "...",
    "aliases": [...],
    "attributes": {...}
}

Canonical projected edge
------------------------
{
    "relationship_id": "REL-*",
    "source_entity_id": "ENT-*",
    "relation_type": "...",
    "target_entity_id": "ENT-*",

    "evidence_ids": ["EVID-*"],
    "event_ids": ["EVENT-*"],
    "source_ids": ["SOURCE-*"],
    "observation_ids": ["RELOBS-*"],

    "temporal_status": "...",
    "temporal_observations": [
        {
            "observation_id": "RELOBS-*",
            "event_id": "EVENT-*",
            "source_id": "SOURCE-*",
            "temporal_status": "...",
            "relation_time_text": "...",
            "valid_start_at": "...",
            "valid_end_at": "...",
            "temporal_precision": "..."
        }
    ],

    "attributes": {...},
    "epistemic_status": "unresolved"
}

Important
---------
The edge-level temporal_status is only a projection summary.

Detailed temporal state remains attached to individual observations.
"""

from __future__ import annotations

import hashlib
import json
import re

from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Sequence,
)

import networkx as nx


GRAPH_SCHEMA_VERSION = (
    "graph-schema-v2.0"
)

SERVICE_VERSION = (
    "graph-service-v3.0"
)


# Keep aligned with the canonical extraction /
# relationship vocabulary.
DEFAULT_ALLOWED_RELATION_TYPES = {
    "ACQUIRES",
    "MERGES_WITH",
    "INVESTS_IN",
    "PARTNERS_WITH",
    "COMPETES_WITH",
    "SUPPLIES",
    "MANUFACTURES",
    "EXPORTS_TO",
    "IMPORTS_FROM",
    "REGULATES",
    "FUNDS",
    "COLLABORATES_WITH",
    "RESTRICTS",
    "DEPENDS_ON",
    "USES",
    "DEVELOPS",
    "LOCATED_IN",
    "OWNS",
    "SUBSIDIARY_OF",
    "AFFECTED_BY",
    "LICENSES",
    "APPROVES",
    "BANS",
    "SUES",
    "APPOINTS",
    "EMPLOYS",
    "OPERATES_IN",
    "SELLS_TO",
    "PROVIDES_TO",
    "LAUNCHES",
    "ANNOUNCES",
    "CONTRACTS_WITH",
    "REQUIRES",
    "PROHIBITS",
    "AUTHORIZES",
    "OTHER",
}


ALLOWED_TEMPORAL_STATUSES = {
    "past",
    "current",
    "ongoing",
    "future",
    "uncertain",
    "unknown",
    "mixed",
}


ALLOWED_TEMPORAL_PRECISIONS = {
    "instant",
    "day",
    "week",
    "month",
    "quarter",
    "year",
    "interval",
    "relative",
    "unresolved",
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
    "deliberation",
    "deliberation_trace",
    "deliberationtrace",
    "reasoning_tokens",
    "reasoningtokens",
    "raw_prompt",
    "rawprompt",
    "raw_completion",
    "rawcompletion",
    "raw_response",
    "rawresponse",
}


class GraphServiceError(
    RuntimeError
):
    """Base GraphService error."""


class GraphValidationError(
    GraphServiceError
):
    """Raised when graph state violates the projection contract."""


class GraphIdentityConflictError(
    GraphServiceError
):
    """
    Raised when one REL-* identity is reused for different
    endpoints or relation semantics.
    """


def _clean(
    value: Any,
) -> str:
    if value is None:
        return ""

    return " ".join(
        str(
            value
        ).split()
    ).strip()


def _enum(
    value: Any,
) -> str:
    text = (
        _clean(
            value
        )
        .casefold()
        .replace(
            "-",
            "_",
        )
        .replace(
            " ",
            "_",
        )
    )

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

    return text.strip(
        "_"
    )


def _normalize_relation_type(
    value: Any,
) -> str:
    text = (
        _clean(
            value
        )
        .upper()
    )

    text = re.sub(
        r"[^A-Z0-9]+",
        "_",
        text,
    )

    return text.strip(
        "_"
    )


def _normalize_entity_type(
    value: Any,
) -> str:
    text = (
        _clean(
            value
        )
        .casefold()
    )

    text = re.sub(
        r"[^a-z0-9]+",
        "_",
        text,
    )

    return (
        text.strip(
            "_"
        )
        or "unknown"
    )


def _normalize_id(
    value: Any,
) -> str:
    return _clean(
        value
    )


def _utc_now_iso(
) -> str:
    return (
        datetime.now(
            timezone.utc
        )
        .isoformat()
    )


def _json_safe(
    value: Any,
) -> Any:
    """
    Convert common Python values to JSON-safe values.

    Unknown values are represented rather than silently
    discarded.
    """

    if value is None or isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    if isinstance(
        value,
        datetime,
    ):
        if value.tzinfo is None:
            value = (
                value.replace(
                    tzinfo=timezone.utc
                )
            )

        else:
            value = (
                value.astimezone(
                    timezone.utc
                )
            )

        return value.isoformat()

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(
                key
            ): _json_safe(
                item
            )
            for (
                key,
                item,
            )
            in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            _json_safe(
                item
            )
            for item
            in value
        ]

    return str(
        value
    )


def _unique_strings(
    values: Any,
) -> list[str]:
    if values is None:
        return []

    if isinstance(
        values,
        str,
    ):
        values = [
            values
        ]

    if not isinstance(
        values,
        (
            list,
            tuple,
            set,
        ),
    ):
        values = [
            values
        ]

    result: list[
        str
    ] = []

    seen: set[
        str
    ] = set()

    for value in values:
        if isinstance(
            value,
            Mapping,
        ):
            value = (
                value.get(
                    "id"
                )
                or value.get(
                    "evidence_id"
                )
                or value.get(
                    "event_id"
                )
                or value.get(
                    "source_id"
                )
                or value.get(
                    "observation_id"
                )
                or value.get(
                    "relationship_id"
                )
                or ""
            )

        normalized = (
            _clean(
                value
            )
        )

        if (
            normalized
            and normalized
            not in seen
        ):
            seen.add(
                normalized
            )

            result.append(
                normalized
            )

    return result


def _merge_unique(
    existing: Any,
    incoming: Any,
) -> list[str]:
    return _unique_strings(
        _unique_strings(
            existing
        )
        + _unique_strings(
            incoming
        )
    )


def _stable_hash(
    parts: Sequence[
        str
    ],
    length: int = 24,
) -> str:
    payload = (
        "\x1f".join(
            parts
        )
        .encode(
            "utf-8"
        )
    )

    return (
        hashlib.sha256(
            payload
        )
        .hexdigest()
        [:length]
    )


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[
    str
]:
    if isinstance(
        value,
        Mapping,
    ):
        for (
            key,
            item,
        ) in value.items():
            normalized = (
                _enum(
                    key
                )
            )

            current = (
                f"{path}.{key}"
                if path
                else str(
                    key
                )
            )

            if (
                normalized
                in PROHIBITED_PRIVATE_REASONING_FIELDS
            ):
                return current

            nested = (
                _contains_prohibited_field(
                    item,
                    current,
                )
            )

            if nested:
                return nested

    elif isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        for (
            index,
            item,
        ) in enumerate(
            value
        ):
            nested = (
                _contains_prohibited_field(
                    item,
                    (
                        f"{path}"
                        f"[{index}]"
                    ),
                )
            )

            if nested:
                return nested

    return None


class GraphService:
    """
    Deterministic knowledge-graph projection.

    NetworkX is an analytical/query projection.

    RelationshipService remains the authoritative
    relationship/provenance store.
    """

    def __init__(
        self,
        storage_dir: (
            str
            | Path
        ) = (
            "data/knowledge_graph"
        ),
        filename: str = (
            "knowledge_graph.json"
        ),
        *,
        allowed_relation_types: Optional[
            Iterable[
                str
            ]
        ] = None,
        auto_load: bool = True,
    ) -> None:
        self.storage_dir = Path(
            storage_dir
        )

        self.storage_path = (
            self.storage_dir
            / filename
        )

        selected_relation_types = (
            allowed_relation_types
            if allowed_relation_types
            is not None
            else DEFAULT_ALLOWED_RELATION_TYPES
        )

        self.allowed_relation_types = {
            _normalize_relation_type(
                value
            )
            for value
            in selected_relation_types
            if _normalize_relation_type(
                value
            )
        }

        if not self.allowed_relation_types:
            raise GraphValidationError(
                "allowed_relation_types "
                "cannot be empty."
            )

        self.graph = (
            nx.MultiDiGraph()
        )

        if (
            auto_load
            and self.storage_path.exists()
        ):
            self.load()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _require_prefixed_id(
        value: Any,
        *,
        prefix: str,
        field_name: str,
    ) -> str:
        identifier = (
            _normalize_id(
                value
            )
        )

        if not identifier:
            raise GraphValidationError(
                f"{field_name} "
                "cannot be empty."
            )

        if not (
            identifier
            .upper()
            .startswith(
                f"{prefix.upper()}-"
            )
        ):
            raise GraphValidationError(
                f"{field_name} must use "
                f"{prefix.upper()}-* identity."
            )

        return identifier

    @classmethod
    def _validate_entity_id(
        cls,
        value: Any,
    ) -> str:
        return (
            cls._require_prefixed_id(
                value,
                prefix="ENT",
                field_name=(
                    "entity_id"
                ),
            )
        )

    @classmethod
    def _validate_relationship_id(
        cls,
        value: Any,
    ) -> str:
        return (
            cls._require_prefixed_id(
                value,
                prefix="REL",
                field_name=(
                    "relationship_id"
                ),
            )
        )

    @classmethod
    def _validate_evidence_ids(
        cls,
        values: Any,
    ) -> list[str]:
        output = (
            _unique_strings(
                values
            )
        )

        for value in output:
            cls._require_prefixed_id(
                value,
                prefix="EVID",
                field_name=(
                    "evidence_id"
                ),
            )

        return output

    @classmethod
    def _validate_event_ids(
        cls,
        values: Any,
    ) -> list[str]:
        output = (
            _unique_strings(
                values
            )
        )

        for value in output:
            cls._require_prefixed_id(
                value,
                prefix="EVENT",
                field_name=(
                    "event_id"
                ),
            )

        return output

    @classmethod
    def _validate_source_ids(
        cls,
        values: Any,
    ) -> list[str]:
        output = (
            _unique_strings(
                values
            )
        )

        for value in output:
            cls._require_prefixed_id(
                value,
                prefix="SOURCE",
                field_name=(
                    "source_id"
                ),
            )

        return output

    @classmethod
    def _validate_observation_ids(
        cls,
        values: Any,
    ) -> list[str]:
        output = (
            _unique_strings(
                values
            )
        )

        for value in output:
            cls._require_prefixed_id(
                value,
                prefix="RELOBS",
                field_name=(
                    "observation_id"
                ),
            )

        return output

    def _validate_relation_type(
        self,
        relation_type: Any,
    ) -> str:
        normalized = (
            _normalize_relation_type(
                relation_type
            )
        )

        if not normalized:
            raise GraphValidationError(
                "relation_type "
                "cannot be empty."
            )

        if (
            normalized
            not in self.allowed_relation_types
        ):
            raise GraphValidationError(
                "Unsupported relation_type="
                f"{normalized!r}."
            )

        return normalized

    @staticmethod
    def _validate_temporal_status(
        value: Any,
    ) -> str:
        status = (
            _enum(
                value
            )
            or "unknown"
        )

        aliases = {
            "historical": (
                "past"
            ),
            "future_commitment": (
                "future"
            ),
            "future_planned": (
                "future"
            ),
            "present": (
                "current"
            ),
        }

        status = aliases.get(
            status,
            status,
        )

        if (
            status
            not in ALLOWED_TEMPORAL_STATUSES
        ):
            raise GraphValidationError(
                "Unsupported temporal_status="
                f"{status!r}."
            )

        return status

    @staticmethod
    def _validate_temporal_precision(
        value: Any,
    ) -> str:
        precision = (
            _enum(
                value
            )
            or "unresolved"
        )

        if (
            precision
            not in ALLOWED_TEMPORAL_PRECISIONS
        ):
            raise GraphValidationError(
                "Unsupported temporal_precision="
                f"{precision!r}."
            )

        return precision

    def _require_entity(
        self,
        entity_id: str,
    ) -> None:
        if (
            entity_id
            not in self.graph
        ):
            raise GraphValidationError(
                "Unknown entity_id="
                f"{entity_id!r}. "
                "Entities must be projected "
                "before relationships."
            )

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        *,
        entity_id: str,
        name: str,
        entity_type: str,
        aliases: Optional[
            Iterable[
                str
            ]
        ] = None,
        attributes: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Insert/update one canonical entity node.

        GraphService does not perform alias resolution.
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        name = (
            _clean(
                name
            )
        )

        normalized_type = (
            _normalize_entity_type(
                entity_type
            )
        )

        if not name:
            raise GraphValidationError(
                "entity name "
                "cannot be empty."
            )

        if (
            attributes is not None
            and not isinstance(
                attributes,
                Mapping,
            )
        ):
            raise GraphValidationError(
                "attributes must "
                "be an object."
            )

        prohibited = (
            _contains_prohibited_field(
                attributes
                or {}
            )
        )

        if prohibited:
            raise GraphValidationError(
                "Entity attributes contain "
                "prohibited private/raw "
                "reasoning field at "
                f"{prohibited!r}."
            )

        now = (
            _utc_now_iso()
        )

        existing = (
            dict(
                self.graph.nodes[
                    entity_id
                ]
            )
            if entity_id
            in self.graph
            else {}
        )

        merged_aliases = (
            _merge_unique(
                existing.get(
                    "aliases",
                    [],
                ),
                aliases
                or [],
            )
        )

        previous_name = (
            _clean(
                existing.get(
                    "name"
                )
            )
        )

        if (
            previous_name
            and previous_name
            != name
        ):
            merged_aliases = (
                _merge_unique(
                    merged_aliases,
                    [
                        previous_name
                    ],
                )
            )

        # Current canonical name should not
        # remain duplicated in aliases.
        merged_aliases = [
            alias
            for alias
            in merged_aliases
            if (
                alias.casefold()
                != name.casefold()
            )
        ]

        merged_attributes = dict(
            existing.get(
                "attributes",
                {},
            )
        )

        if attributes:
            merged_attributes.update(
                {
                    str(
                        key
                    ): _json_safe(
                        value
                    )
                    for (
                        key,
                        value,
                    )
                    in attributes.items()
                }
            )

        payload = {
            "entity_id": (
                entity_id
            ),
            "name": (
                name
            ),
            "entity_type": (
                normalized_type
            ),
            "aliases": (
                merged_aliases
            ),
            "attributes": (
                merged_attributes
            ),
            "created_at": (
                existing.get(
                    "created_at",
                    now,
                )
            ),
            "updated_at": (
                now
            ),
        }

        self.graph.add_node(
            entity_id,
            **payload,
        )

        return dict(
            payload
        )

    def remove_entity(
        self,
        entity_id: str,
        *,
        require_isolated: bool = True,
    ) -> bool:
        """
        Remove a projected node.

        By default an entity cannot be removed while
        projected relationships still reference it.
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        if (
            entity_id
            not in self.graph
        ):
            return False

        if (
            require_isolated
            and self.graph.degree(
                entity_id
            )
            > 0
        ):
            raise GraphValidationError(
                "Cannot remove entity with "
                "projected relationships: "
                f"{entity_id!r}."
            )

        self.graph.remove_node(
            entity_id
        )

        return True

    def get_entity(
        self,
        entity_id: str,
    ) -> Optional[
        dict[
            str,
            Any,
        ]
    ]:
        entity_id = (
            _normalize_id(
                entity_id
            )
        )

        if (
            entity_id
            not in self.graph
        ):
            return None

        return dict(
            self.graph.nodes[
                entity_id
            ]
        )

    def list_entities(
        self,
        *,
        entity_type: Optional[
            str
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        normalized_type = (
            _normalize_entity_type(
                entity_type
            )
            if entity_type
            else None
        )

        entities: list[
            dict[
                str,
                Any,
            ]
        ] = []

        for (
            entity_id,
            data,
        ) in self.graph.nodes(
            data=True
        ):
            if (
                normalized_type
                and data.get(
                    "entity_type"
                )
                != normalized_type
            ):
                continue

            entities.append(
                {
                    **dict(
                        data
                    ),
                    "entity_id": (
                        entity_id
                    ),
                }
            )

        entities.sort(
            key=lambda item: (
                item.get(
                    "entity_type",
                    "",
                ),
                item.get(
                    "name",
                    "",
                ).casefold(),
                item.get(
                    "entity_id",
                    "",
                ),
            )
        )

        return entities

    # ------------------------------------------------------------------
    # Entity registry projection
    # ------------------------------------------------------------------

    def project_entities(
        self,
        entities: Sequence[
            Mapping[
                str,
                Any,
            ]
        ],
        *,
        remove_absent: bool = False,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Project canonical EntityService records.

        When remove_absent=True, currently isolated graph
        nodes absent from the supplied registry are removed.

        Nodes still referenced by edges are never silently
        deleted.
        """

        if (
            not isinstance(
                entities,
                Sequence,
            )
            or isinstance(
                entities,
                (
                    str,
                    bytes,
                ),
            )
        ):
            raise GraphValidationError(
                "entities must be a "
                "sequence of objects."
            )

        supplied_ids: set[
            str
        ] = set()

        for (
            index,
            entity,
        ) in enumerate(
            entities
        ):
            if not isinstance(
                entity,
                Mapping,
            ):
                raise GraphValidationError(
                    f"entities[{index}] "
                    "must be an object."
                )

            entity_id = (
                self._validate_entity_id(
                    entity.get(
                        "entity_id"
                    )
                )
            )

            supplied_ids.add(
                entity_id
            )

            self.upsert_entity(
                entity_id=(
                    entity_id
                ),
                name=(
                    entity.get(
                        "name",
                        "",
                    )
                ),
                entity_type=(
                    entity.get(
                        "entity_type",
                        "unknown",
                    )
                ),
                aliases=(
                    entity.get(
                        "aliases",
                        []
                    )
                ),
                attributes=(
                    entity.get(
                        "attributes",
                        {}
                    )
                ),
            )

        removed_ids: list[
            str
        ] = []

        if remove_absent:
            for entity_id in list(
                self.graph.nodes()
            ):
                if (
                    entity_id
                    in supplied_ids
                ):
                    continue

                if (
                    self.graph.degree(
                        entity_id
                    )
                    != 0
                ):
                    continue

                self.graph.remove_node(
                    entity_id
                )

                removed_ids.append(
                    entity_id
                )

        return {
            "projected_entity_count": (
                len(
                    supplied_ids
                )
            ),
            "removed_isolated_entity_ids": (
                sorted(
                    removed_ids
                )
            ),
        }

    # ------------------------------------------------------------------
    # Relationship operations
    # ------------------------------------------------------------------

    @staticmethod
    def _relationship_id(
        *,
        source_entity_id: str,
        relation_type: str,
        target_entity_id: str,
    ) -> str:
        """
        Deterministic fallback REL-* identity.

        RelationshipService normally supplies relationship_id.

        Temporal state is deliberately excluded.
        """

        return (
            "REL-"
            + _stable_hash(
                [
                    source_entity_id,
                    relation_type,
                    target_entity_id,
                ]
            ).upper()
        )

    def _find_relationship_location(
        self,
        relationship_id: str,
    ) -> Optional[
        tuple[
            str,
            str,
        ]
    ]:
        """
        Find endpoints for a globally unique REL-*.

        NetworkX MultiDiGraph edge keys are only
        endpoint-scoped, so global uniqueness must be
        enforced explicitly.
        """

        for (
            source,
            target,
            key,
        ) in self.graph.edges(
            keys=True
        ):
            if (
                key
                == relationship_id
            ):
                return (
                    source,
                    target,
                )

        return None

    def _normalize_temporal_observations(
        self,
        values: Any,
        *,
        permitted_observation_ids: set[
            str
        ],
        permitted_event_ids: set[
            str
        ],
        permitted_source_ids: set[
            str
        ],
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        if values is None:
            return []

        if not isinstance(
            values,
            (
                list,
                tuple,
            ),
        ):
            raise GraphValidationError(
                "temporal_observations "
                "must be a list."
            )

        output: list[
            dict[
                str,
                Any,
            ]
        ] = []

        seen_observation_ids: set[
            str
        ] = set()

        for (
            index,
            value,
        ) in enumerate(
            values
        ):
            if not isinstance(
                value,
                Mapping,
            ):
                raise GraphValidationError(
                    "temporal_observations"
                    f"[{index}] must be an object."
                )

            observation_id = (
                self._require_prefixed_id(
                    value.get(
                        "observation_id"
                    ),
                    prefix="RELOBS",
                    field_name=(
                        "observation_id"
                    ),
                )
            )

            event_id = (
                self._require_prefixed_id(
                    value.get(
                        "event_id"
                    ),
                    prefix="EVENT",
                    field_name=(
                        "event_id"
                    ),
                )
            )

            source_id = (
                self._require_prefixed_id(
                    value.get(
                        "source_id"
                    ),
                    prefix="SOURCE",
                    field_name=(
                        "source_id"
                    ),
                )
            )

            if (
                permitted_observation_ids
                and observation_id
                not in permitted_observation_ids
            ):
                raise GraphValidationError(
                    "Temporal observation references "
                    "an observation_id absent from "
                    "observation_ids."
                )

            if (
                permitted_event_ids
                and event_id
                not in permitted_event_ids
            ):
                raise GraphValidationError(
                    "Temporal observation references "
                    "an event_id absent from "
                    "event_ids."
                )

            if (
                permitted_source_ids
                and source_id
                not in permitted_source_ids
            ):
                raise GraphValidationError(
                    "Temporal observation references "
                    "a source_id absent from "
                    "source_ids."
                )

            if (
                observation_id
                in seen_observation_ids
            ):
                raise GraphValidationError(
                    "Duplicate temporal observation "
                    f"{observation_id!r}."
                )

            seen_observation_ids.add(
                observation_id
            )

            temporal_status = (
                self._validate_temporal_status(
                    value.get(
                        "temporal_status",
                        "unknown",
                    )
                )
            )

            precision = (
                self._validate_temporal_precision(
                    value.get(
                        "temporal_precision",
                        "unresolved",
                    )
                )
            )

            relation_time_text = (
                _clean(
                    value.get(
                        "relation_time_text"
                    )
                )
            )

            valid_start_at = (
                _json_safe(
                    value.get(
                        "valid_start_at"
                    )
                )
            )

            valid_end_at = (
                _json_safe(
                    value.get(
                        "valid_end_at"
                    )
                )
            )

            if (
                precision
                != "unresolved"
                and not relation_time_text
            ):
                raise GraphValidationError(
                    "Resolved temporal_precision "
                    "requires relation_time_text."
                )

            output.append(
                {
                    "observation_id": (
                        observation_id
                    ),
                    "event_id": (
                        event_id
                    ),
                    "source_id": (
                        source_id
                    ),
                    "temporal_status": (
                        temporal_status
                    ),
                    "relation_time_text": (
                        relation_time_text
                    ),
                    "valid_start_at": (
                        valid_start_at
                    ),
                    "valid_end_at": (
                        valid_end_at
                    ),
                    "temporal_precision": (
                        precision
                    ),
                }
            )

        output.sort(
            key=lambda item: (
                item[
                    "observation_id"
                ]
            )
        )

        return output

    def upsert_relationship(
        self,
        *,
        source_entity_id: str,
        relation_type: str,
        target_entity_id: str,
        relationship_id: Optional[
            str
        ] = None,
        evidence_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        event_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        source_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        observation_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        temporal_status: str = (
            "unknown"
        ),
        temporal_observations: Optional[
            Sequence[
                Mapping[
                    str,
                    Any,
                ]
            ]
        ] = None,
        attributes: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
        replace_projection: bool = True,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Insert/update one projected semantic relationship.

        Default behavior:
            replace_projection=True

        This is deliberate.

        RelationshipService supplies the current authoritative
        aggregated provenance. If GraphService endlessly merged
        old and new lists, evidence removed from the relational
        source of truth would remain stale in the graph.

        Set replace_projection=False only for explicitly
        non-authoritative incremental use.
        """

        source_entity_id = (
            self._validate_entity_id(
                source_entity_id
            )
        )

        target_entity_id = (
            self._validate_entity_id(
                target_entity_id
            )
        )

        relation_type = (
            self._validate_relation_type(
                relation_type
            )
        )

        if (
            source_entity_id
            == target_entity_id
        ):
            raise GraphValidationError(
                "Self-referential graph "
                "relationships are not allowed."
            )

        self._require_entity(
            source_entity_id
        )

        self._require_entity(
            target_entity_id
        )

        relationship_id = (
            self._validate_relationship_id(
                relationship_id
            )
            if relationship_id
            else self._relationship_id(
                source_entity_id=(
                    source_entity_id
                ),
                relation_type=(
                    relation_type
                ),
                target_entity_id=(
                    target_entity_id
                ),
            )
        )

        existing_location = (
            self._find_relationship_location(
                relationship_id
            )
        )

        if (
            existing_location
            is not None
            and existing_location
            != (
                source_entity_id,
                target_entity_id,
            )
        ):
            raise GraphIdentityConflictError(
                "relationship_id is globally "
                "associated with different "
                "graph endpoints: "
                f"{relationship_id!r}."
            )

        existing: dict[
            str,
            Any,
        ] = {}

        if self.graph.has_edge(
            source_entity_id,
            target_entity_id,
            key=(
                relationship_id
            ),
        ):
            existing = dict(
                self.graph[
                    source_entity_id
                ][
                    target_entity_id
                ][
                    relationship_id
                ]
            )

            if (
                existing.get(
                    "relation_type"
                )
                and existing.get(
                    "relation_type"
                )
                != relation_type
            ):
                raise GraphIdentityConflictError(
                    "Existing REL-* is being "
                    "reused for a different "
                    "relation_type."
                )

        normalized_evidence_ids = (
            self._validate_evidence_ids(
                evidence_ids
                or []
            )
        )

        normalized_event_ids = (
            self._validate_event_ids(
                event_ids
                or []
            )
        )

        normalized_source_ids = (
            self._validate_source_ids(
                source_ids
                or []
            )
        )

        normalized_observation_ids = (
            self._validate_observation_ids(
                observation_ids
                or []
            )
        )

        temporal_status = (
            self._validate_temporal_status(
                temporal_status
            )
        )

        normalized_temporal_observations = (
            self._normalize_temporal_observations(
                temporal_observations
                or [],
                permitted_observation_ids=set(
                    normalized_observation_ids
                ),
                permitted_event_ids=set(
                    normalized_event_ids
                ),
                permitted_source_ids=set(
                    normalized_source_ids
                ),
            )
        )

        if (
            attributes is not None
            and not isinstance(
                attributes,
                Mapping,
            )
        ):
            raise GraphValidationError(
                "attributes must "
                "be an object."
            )

        prohibited = (
            _contains_prohibited_field(
                attributes
                or {}
            )
        )

        if prohibited:
            raise GraphValidationError(
                "Relationship attributes contain "
                "prohibited private/raw reasoning "
                f"field at {prohibited!r}."
            )

        incoming_attributes = {
            str(
                key
            ): _json_safe(
                value
            )
            for (
                key,
                value,
            )
            in (
                attributes
                or {}
            ).items()
        }

        now = (
            _utc_now_iso()
        )

        if replace_projection:
            projected_evidence_ids = (
                normalized_evidence_ids
            )

            projected_event_ids = (
                normalized_event_ids
            )

            projected_source_ids = (
                normalized_source_ids
            )

            projected_observation_ids = (
                normalized_observation_ids
            )

            projected_temporal_observations = (
                normalized_temporal_observations
            )

            # RelationshipService projection is a
            # current snapshot. Replace its attributes
            # rather than retaining obsolete keys.
            merged_attributes = (
                incoming_attributes
            )

        else:
            projected_evidence_ids = (
                _merge_unique(
                    existing.get(
                        "evidence_ids",
                        [],
                    ),
                    normalized_evidence_ids,
                )
            )

            projected_event_ids = (
                _merge_unique(
                    existing.get(
                        "event_ids",
                        [],
                    ),
                    normalized_event_ids,
                )
            )

            projected_source_ids = (
                _merge_unique(
                    existing.get(
                        "source_ids",
                        [],
                    ),
                    normalized_source_ids,
                )
            )

            projected_observation_ids = (
                _merge_unique(
                    existing.get(
                        "observation_ids",
                        [],
                    ),
                    normalized_observation_ids,
                )
            )

            temporal_by_id = {
                item[
                    "observation_id"
                ]: dict(
                    item
                )
                for item
                in (
                    existing.get(
                        "temporal_observations",
                        [],
                    )
                    or []
                )
                if isinstance(
                    item,
                    Mapping,
                )
                and item.get(
                    "observation_id"
                )
            }

            for item in (
                normalized_temporal_observations
            ):
                temporal_by_id[
                    item[
                        "observation_id"
                    ]
                ] = dict(
                    item
                )

            projected_temporal_observations = [
                temporal_by_id[
                    observation_id
                ]
                for observation_id
                in sorted(
                    temporal_by_id
                )
            ]

            merged_attributes = dict(
                existing.get(
                    "attributes",
                    {},
                )
            )

            merged_attributes.update(
                incoming_attributes
            )

        payload = {
            "relationship_id": (
                relationship_id
            ),
            "source_entity_id": (
                source_entity_id
            ),
            "relation_type": (
                relation_type
            ),
            "target_entity_id": (
                target_entity_id
            ),
            "evidence_ids": (
                projected_evidence_ids
            ),
            "event_ids": (
                projected_event_ids
            ),
            "source_ids": (
                projected_source_ids
            ),
            "observation_ids": (
                projected_observation_ids
            ),
            "temporal_status": (
                temporal_status
            ),
            "temporal_observations": (
                projected_temporal_observations
            ),
            "attributes": (
                merged_attributes
            ),

            # Projection does not ground the edge.
            "epistemic_status": (
                "unresolved"
            ),

            "source_independence_assessed_here": (
                False
            ),

            "projection_source": (
                "relationship_service"
            ),

            "created_at": (
                existing.get(
                    "created_at",
                    now,
                )
            ),

            "updated_at": (
                now
            ),
        }

        self.graph.add_edge(
            source_entity_id,
            target_entity_id,
            key=(
                relationship_id
            ),
            **payload,
        )

        return dict(
            payload
        )

    def remove_relationship(
        self,
        relationship_id: str,
    ) -> bool:
        """
        Remove one projected REL-* edge.
        """

        relationship_id = (
            self._validate_relationship_id(
                relationship_id
            )
        )

        location = (
            self._find_relationship_location(
                relationship_id
            )
        )

        if location is None:
            return False

        (
            source,
            target,
        ) = location

        self.graph.remove_edge(
            source,
            target,
            key=(
                relationship_id
            ),
        )

        return True

    def get_relationship(
        self,
        relationship_id: str,
    ) -> Optional[
        dict[
            str,
            Any,
        ]
    ]:
        relationship_id = (
            _normalize_id(
                relationship_id
            )
        )

        if not relationship_id:
            return None

        for (
            source,
            target,
            key,
            data,
        ) in self.graph.edges(
            keys=True,
            data=True,
        ):
            if (
                key
                == relationship_id
            ):
                return {
                    **dict(
                        data
                    ),
                    "source_entity_id": (
                        source
                    ),
                    "target_entity_id": (
                        target
                    ),
                    "relationship_id": (
                        key
                    ),
                }

        return None

    def list_relationships(
        self,
        *,
        relationship_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        entity_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        relation_types: Optional[
            Iterable[
                str
            ]
        ] = None,
        event_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        evidence_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        source_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        observation_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """
        Return every projected relationship matching
        supplied filters.

        There is no hidden top-k or arbitrary truncation.
        """

        relationship_filter = (
            set(
                _unique_strings(
                    relationship_ids
                )
            )
            if relationship_ids
            is not None
            else None
        )

        entity_filter = (
            {
                _normalize_id(
                    value
                )
                for value
                in entity_ids
                if _normalize_id(
                    value
                )
            }
            if entity_ids
            is not None
            else None
        )

        relation_filter = (
            {
                self._validate_relation_type(
                    value
                )
                for value
                in relation_types
            }
            if relation_types
            is not None
            else None
        )

        event_filter = (
            set(
                _unique_strings(
                    event_ids
                )
            )
            if event_ids
            is not None
            else None
        )

        evidence_filter = (
            set(
                _unique_strings(
                    evidence_ids
                )
            )
            if evidence_ids
            is not None
            else None
        )

        source_filter = (
            set(
                _unique_strings(
                    source_ids
                )
            )
            if source_ids
            is not None
            else None
        )

        observation_filter = (
            set(
                _unique_strings(
                    observation_ids
                )
            )
            if observation_ids
            is not None
            else None
        )

        relationships: list[
            dict[
                str,
                Any,
            ]
        ] = []

        for (
            source,
            target,
            key,
            data,
        ) in self.graph.edges(
            keys=True,
            data=True,
        ):
            if (
                relationship_filter
                is not None
                and key
                not in relationship_filter
            ):
                continue

            if (
                entity_filter
                is not None
                and source
                not in entity_filter
                and target
                not in entity_filter
            ):
                continue

            if (
                relation_filter
                is not None
                and data.get(
                    "relation_type"
                )
                not in relation_filter
            ):
                continue

            if (
                event_filter
                is not None
                and not (
                    event_filter
                    .intersection(
                        set(
                            data.get(
                                "event_ids",
                                [],
                            )
                        )
                    )
                )
            ):
                continue

            if (
                evidence_filter
                is not None
                and not (
                    evidence_filter
                    .intersection(
                        set(
                            data.get(
                                "evidence_ids",
                                [],
                            )
                        )
                    )
                )
            ):
                continue

            if (
                source_filter
                is not None
                and not (
                    source_filter
                    .intersection(
                        set(
                            data.get(
                                "source_ids",
                                [],
                            )
                        )
                    )
                )
            ):
                continue

            if (
                observation_filter
                is not None
                and not (
                    observation_filter
                    .intersection(
                        set(
                            data.get(
                                "observation_ids",
                                [],
                            )
                        )
                    )
                )
            ):
                continue

            relationships.append(
                {
                    **dict(
                        data
                    ),
                    "relationship_id": (
                        key
                    ),
                    "source_entity_id": (
                        source
                    ),
                    "target_entity_id": (
                        target
                    ),
                }
            )

        relationships.sort(
            key=lambda item: (
                item.get(
                    "source_entity_id",
                    "",
                ),
                item.get(
                    "relation_type",
                    "",
                ),
                item.get(
                    "target_entity_id",
                    "",
                ),
                item.get(
                    "relationship_id",
                    "",
                ),
            )
        )

        return relationships

    # ------------------------------------------------------------------
    # Authoritative projection replacement
    # ------------------------------------------------------------------

    def replace_relationship_projection(
        self,
        relationships: Sequence[
            Mapping[
                str,
                Any,
            ]
        ],
        *,
        remove_absent: bool = True,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Replace the projected relationship snapshot.

        This is the preferred full rebuild path from
        RelationshipService.build_graph_projection().

        It prevents stale relationship/evidence/event/source
        provenance surviving in NetworkX after relational
        corrections.
        """

        if (
            not isinstance(
                relationships,
                Sequence,
            )
            or isinstance(
                relationships,
                (
                    str,
                    bytes,
                ),
            )
        ):
            raise GraphValidationError(
                "relationships must be "
                "a sequence of objects."
            )

        supplied_ids: set[
            str
        ] = set()

        for (
            index,
            relationship,
        ) in enumerate(
            relationships
        ):
            if not isinstance(
                relationship,
                Mapping,
            ):
                raise GraphValidationError(
                    f"relationships[{index}] "
                    "must be an object."
                )

            result = (
                self.upsert_relationship(
                    source_entity_id=(
                        relationship.get(
                            "source_entity_id"
                        )
                    ),
                    relation_type=(
                        relationship.get(
                            "relation_type"
                        )
                    ),
                    target_entity_id=(
                        relationship.get(
                            "target_entity_id"
                        )
                    ),
                    relationship_id=(
                        relationship.get(
                            "relationship_id"
                        )
                    ),
                    evidence_ids=(
                        relationship.get(
                            "evidence_ids",
                            [],
                        )
                    ),
                    event_ids=(
                        relationship.get(
                            "event_ids",
                            [],
                        )
                    ),
                    source_ids=(
                        relationship.get(
                            "source_ids",
                            [],
                        )
                    ),
                    observation_ids=(
                        relationship.get(
                            "observation_ids",
                            [],
                        )
                    ),
                    temporal_status=(
                        relationship.get(
                            "temporal_status",
                            "unknown",
                        )
                    ),
                    temporal_observations=(
                        relationship.get(
                            "temporal_observations",
                            [],
                        )
                    ),
                    attributes=(
                        relationship.get(
                            "attributes",
                            {},
                        )
                    ),
                    replace_projection=True,
                )
            )

            supplied_ids.add(
                result[
                    "relationship_id"
                ]
            )

        removed_ids: list[
            str
        ] = []

        if remove_absent:
            current_ids = {
                key
                for (
                    _,
                    _,
                    key,
                )
                in self.graph.edges(
                    keys=True
                )
            }

            for relationship_id in sorted(
                current_ids
                - supplied_ids
            ):
                if self.remove_relationship(
                    relationship_id
                ):
                    removed_ids.append(
                        relationship_id
                    )

        return {
            "projected_relationship_count": (
                len(
                    supplied_ids
                )
            ),
            "removed_relationship_ids": (
                removed_ids
            ),
            "database_is_source_of_truth": (
                True
            ),
            "graph_projection_is_epistemic_authority": (
                False
            ),
        }

    def rebuild_from_services(
        self,
        *,
        entity_service: Any,
        relationship_service: Any,
        save: bool = False,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Rebuild the graph from canonical service state.

        This is the safest recovery path after:
        - Event re-extraction;
        - entity correction;
        - relationship correction;
        - graph corruption;
        - graph-schema migration.
        """

        if not hasattr(
            entity_service,
            "registry_records",
        ):
            raise GraphValidationError(
                "entity_service must expose "
                "registry_records()."
            )

        if not hasattr(
            relationship_service,
            "build_graph_projection",
        ):
            raise GraphValidationError(
                "relationship_service must expose "
                "build_graph_projection()."
            )

        entities = (
            entity_service
            .registry_records()
        )

        relationships = (
            relationship_service
            .build_graph_projection()
        )

        new_graph = (
            nx.MultiDiGraph()
        )

        previous_graph = (
            self.graph
        )

        self.graph = (
            new_graph
        )

        try:
            entity_result = (
                self.project_entities(
                    entities,
                    remove_absent=False,
                )
            )

            relationship_result = (
                self.replace_relationship_projection(
                    relationships,
                    remove_absent=False,
                )
            )

            integrity = (
                self.validate_integrity()
            )

            if not integrity[
                "valid"
            ]:
                raise GraphValidationError(
                    "Rebuilt graph failed "
                    "integrity validation: "
                    + json.dumps(
                        integrity[
                            "issues"
                        ],
                        ensure_ascii=False,
                    )
                )

            saved_path = ""

            if save:
                saved_path = str(
                    self.save()
                )

            return {
                "entity_projection": (
                    entity_result
                ),
                "relationship_projection": (
                    relationship_result
                ),
                "integrity": (
                    integrity
                ),
                "saved_path": (
                    saved_path
                ),
            }

        except Exception:
            self.graph = (
                previous_graph
            )

            raise

    # ------------------------------------------------------------------
    # Graph queries
    # ------------------------------------------------------------------

    def neighborhood(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        direction: str = "both",
    ) -> dict[
        str,
        Any,
    ]:
        """
        Return a complete depth-bounded neighborhood.

        depth is semantic query scope rather than
        arbitrary output truncation.
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        self._require_entity(
            entity_id
        )

        if depth < 0:
            raise GraphValidationError(
                "depth must be >= 0."
            )

        direction = (
            _clean(
                direction
            )
            .casefold()
        )

        if (
            direction
            not in {
                "in",
                "out",
                "both",
            }
        ):
            raise GraphValidationError(
                "direction must be one "
                "of: in, out, both."
            )

        visited: set[
            str
        ] = {
            entity_id
        }

        frontier: set[
            str
        ] = {
            entity_id
        }

        for _ in range(
            depth
        ):
            next_frontier: set[
                str
            ] = set()

            for node in frontier:
                if (
                    direction
                    in {
                        "out",
                        "both",
                    }
                ):
                    next_frontier.update(
                        self.graph.successors(
                            node
                        )
                    )

                if (
                    direction
                    in {
                        "in",
                        "both",
                    }
                ):
                    next_frontier.update(
                        self.graph.predecessors(
                            node
                        )
                    )

            next_frontier -= (
                visited
            )

            if not next_frontier:
                break

            visited.update(
                next_frontier
            )

            frontier = (
                next_frontier
            )

        relationships: list[
            dict[
                str,
                Any,
            ]
        ] = []

        for (
            source,
            target,
            key,
            data,
        ) in self.graph.edges(
            keys=True,
            data=True,
        ):
            if (
                source in visited
                and target in visited
            ):
                relationships.append(
                    {
                        **dict(
                            data
                        ),
                        "relationship_id": (
                            key
                        ),
                        "source_entity_id": (
                            source
                        ),
                        "target_entity_id": (
                            target
                        ),
                    }
                )

        entities = [
            {
                **dict(
                    self.graph.nodes[
                        entity
                    ]
                ),
                "entity_id": (
                    entity
                ),
            }
            for entity
            in sorted(
                visited
            )
        ]

        relationships.sort(
            key=lambda item: (
                item.get(
                    "source_entity_id",
                    "",
                ),
                item.get(
                    "relation_type",
                    "",
                ),
                item.get(
                    "target_entity_id",
                    "",
                ),
                item.get(
                    "relationship_id",
                    "",
                ),
            )
        )

        return {
            "root_entity_id": (
                entity_id
            ),
            "depth": (
                depth
            ),
            "direction": (
                direction
            ),
            "entities": (
                entities
            ),
            "relationships": (
                relationships
            ),
        }

    def build_reasoning_context(
        self,
        *,
        relationship_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        entity_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        event_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        evidence_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        source_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        observation_ids: Optional[
            Iterable[
                str
            ]
        ] = None,
        relation_types: Optional[
            Iterable[
                str
            ]
        ] = None,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Export complete structured graph records.

        This method does NOT perform model-context
        truncation or ranking.

        ReasoningContextService owns record-preserving
        context assembly.
        """

        relationships = (
            self.list_relationships(
                relationship_ids=(
                    relationship_ids
                ),
                entity_ids=(
                    entity_ids
                ),
                relation_types=(
                    relation_types
                ),
                event_ids=(
                    event_ids
                ),
                evidence_ids=(
                    evidence_ids
                ),
                source_ids=(
                    source_ids
                ),
                observation_ids=(
                    observation_ids
                ),
            )
        )

        node_ids: set[
            str
        ] = set()

        for relationship in (
            relationships
        ):
            node_ids.add(
                relationship[
                    "source_entity_id"
                ]
            )

            node_ids.add(
                relationship[
                    "target_entity_id"
                ]
            )

        if entity_ids is not None:
            node_ids.update(
                _normalize_id(
                    value
                )
                for value
                in entity_ids
                if (
                    _normalize_id(
                        value
                    )
                    in self.graph
                )
            )

        entities = [
            {
                **dict(
                    self.graph.nodes[
                        entity_id
                    ]
                ),
                "entity_id": (
                    entity_id
                ),
            }
            for entity_id
            in sorted(
                node_ids
            )
        ]

        all_evidence_ids = sorted(
            {
                evidence_id
                for relationship
                in relationships
                for evidence_id
                in relationship.get(
                    "evidence_ids",
                    [],
                )
            }
        )

        all_event_ids = sorted(
            {
                event_id
                for relationship
                in relationships
                for event_id
                in relationship.get(
                    "event_ids",
                    [],
                )
            }
        )

        all_source_ids = sorted(
            {
                source_id
                for relationship
                in relationships
                for source_id
                in relationship.get(
                    "source_ids",
                    [],
                )
            }
        )

        all_observation_ids = sorted(
            {
                observation_id
                for relationship
                in relationships
                for observation_id
                in relationship.get(
                    "observation_ids",
                    [],
                )
            }
        )

        return {
            "entities": (
                entities
            ),
            "relationships": (
                relationships
            ),
            "entity_count": (
                len(
                    entities
                )
            ),
            "relationship_count": (
                len(
                    relationships
                )
            ),
            "evidence_ids": (
                all_evidence_ids
            ),
            "event_ids": (
                all_event_ids
            ),
            "source_ids": (
                all_source_ids
            ),
            "observation_ids": (
                all_observation_ids
            ),
            "complete_records": (
                True
            ),
            "context_truncated": (
                False
            ),
            "epistemic_authority": (
                False
            ),
        }

    # ------------------------------------------------------------------
    # Text export
    # ------------------------------------------------------------------

    def export_graph_text(
        self,
    ) -> str:
        """
        Human-readable deterministic graph projection.

        Intended for diagnostics/UI only.

        Reasoning agents should prefer structured context.
        """

        lines: list[
            str
        ] = []

        for relationship in (
            self.list_relationships()
        ):
            source_id = (
                relationship[
                    "source_entity_id"
                ]
            )

            target_id = (
                relationship[
                    "target_entity_id"
                ]
            )

            source = (
                self.graph.nodes[
                    source_id
                ]
            )

            target = (
                self.graph.nodes[
                    target_id
                ]
            )

            source_name = (
                source.get(
                    "name"
                )
                or source_id
            )

            target_name = (
                target.get(
                    "name"
                )
                or target_id
            )

            relation_type = (
                relationship.get(
                    "relation_type",
                    "",
                )
            )

            evidence_count = len(
                relationship.get(
                    "evidence_ids",
                    [],
                )
            )

            event_count = len(
                relationship.get(
                    "event_ids",
                    [],
                )
            )

            lines.append(
                (
                    f"{source_name} "
                    f"[{source_id}] "
                    f"--{relation_type}--> "
                    f"{target_name} "
                    f"[{target_id}] "
                    f"(REL={relationship['relationship_id']}, "
                    f"events={event_count}, "
                    f"evidence={evidence_count})"
                )
            )

        return "\n".join(
            lines
        )

    # ------------------------------------------------------------------
    # Integrity validation
    # ------------------------------------------------------------------

    def validate_integrity(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        issues: list[
            dict[
                str,
                Any,
            ]
        ] = []

        relationship_ids: set[
            str
        ] = set()

        for (
            entity_id,
            data,
        ) in self.graph.nodes(
            data=True
        ):
            if not (
                _clean(
                    entity_id
                )
                .upper()
                .startswith(
                    "ENT-"
                )
            ):
                issues.append(
                    {
                        "type": (
                            "invalid_entity_id"
                        ),
                        "entity_id": (
                            entity_id
                        ),
                    }
                )

            if (
                data.get(
                    "entity_id"
                )
                != entity_id
            ):
                issues.append(
                    {
                        "type": (
                            "entity_id_payload_mismatch"
                        ),
                        "entity_id": (
                            entity_id
                        ),
                    }
                )

        for (
            source,
            target,
            key,
            data,
        ) in self.graph.edges(
            keys=True,
            data=True,
        ):
            if (
                source
                not in self.graph
            ):
                issues.append(
                    {
                        "type": (
                            "missing_source_node"
                        ),
                        "relationship_id": (
                            key
                        ),
                        "entity_id": (
                            source
                        ),
                    }
                )

            if (
                target
                not in self.graph
            ):
                issues.append(
                    {
                        "type": (
                            "missing_target_node"
                        ),
                        "relationship_id": (
                            key
                        ),
                        "entity_id": (
                            target
                        ),
                    }
                )

            if (
                key
                in relationship_ids
            ):
                issues.append(
                    {
                        "type": (
                            "duplicate_relationship_id"
                        ),
                        "relationship_id": (
                            key
                        ),
                    }
                )

            relationship_ids.add(
                key
            )

            if not (
                _clean(
                    key
                )
                .upper()
                .startswith(
                    "REL-"
                )
            ):
                issues.append(
                    {
                        "type": (
                            "invalid_relationship_id"
                        ),
                        "relationship_id": (
                            key
                        ),
                    }
                )

            if (
                data.get(
                    "relationship_id"
                )
                != key
            ):
                issues.append(
                    {
                        "type": (
                            "relationship_key_mismatch"
                        ),
                        "relationship_id": (
                            key
                        ),
                    }
                )

            if (
                data.get(
                    "source_entity_id"
                )
                != source
            ):
                issues.append(
                    {
                        "type": (
                            "source_endpoint_mismatch"
                        ),
                        "relationship_id": (
                            key
                        ),
                    }
                )

            if (
                data.get(
                    "target_entity_id"
                )
                != target
            ):
                issues.append(
                    {
                        "type": (
                            "target_endpoint_mismatch"
                        ),
                        "relationship_id": (
                            key
                        ),
                    }
                )

            relation_type = (
                _normalize_relation_type(
                    data.get(
                        "relation_type"
                    )
                )
            )

            if (
                relation_type
                not in self.allowed_relation_types
            ):
                issues.append(
                    {
                        "type": (
                            "invalid_relation_type"
                        ),
                        "relationship_id": (
                            key
                        ),
                        "relation_type": (
                            relation_type
                        ),
                    }
                )

            for (
                field_name,
                prefix,
            ) in (
                (
                    "evidence_ids",
                    "EVID-",
                ),
                (
                    "event_ids",
                    "EVENT-",
                ),
                (
                    "source_ids",
                    "SOURCE-",
                ),
                (
                    "observation_ids",
                    "RELOBS-",
                ),
            ):
                values = (
                    data.get(
                        field_name,
                        [],
                    )
                    or []
                )

                if not isinstance(
                    values,
                    list,
                ):
                    issues.append(
                        {
                            "type": (
                                "invalid_provenance_container"
                            ),
                            "relationship_id": (
                                key
                            ),
                            "field": (
                                field_name
                            ),
                        }
                    )

                    continue

                for value in values:
                    if not (
                        _clean(
                            value
                        )
                        .upper()
                        .startswith(
                            prefix
                        )
                    ):
                        issues.append(
                            {
                                "type": (
                                    "invalid_provenance_id"
                                ),
                                "relationship_id": (
                                    key
                                ),
                                "field": (
                                    field_name
                                ),
                                "value": (
                                    value
                                ),
                            }
                        )

            temporal_observations = (
                data.get(
                    "temporal_observations",
                    [],
                )
                or []
            )

            if not isinstance(
                temporal_observations,
                list,
            ):
                issues.append(
                    {
                        "type": (
                            "invalid_temporal_observations"
                        ),
                        "relationship_id": (
                            key
                        ),
                    }
                )

            prohibited = (
                _contains_prohibited_field(
                    data
                )
            )

            if prohibited:
                issues.append(
                    {
                        "type": (
                            "prohibited_private_reasoning_field"
                        ),
                        "relationship_id": (
                            key
                        ),
                        "path": (
                            prohibited
                        ),
                    }
                )

        return {
            "valid": (
                not issues
            ),
            "issue_count": (
                len(
                    issues
                )
            ),
            "issues": (
                issues
            ),
            "entity_count": (
                self.graph
                .number_of_nodes()
            ),
            "relationship_count": (
                self.graph
                .number_of_edges()
            ),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "schema_version": (
                GRAPH_SCHEMA_VERSION
            ),
            "service_version": (
                SERVICE_VERSION
            ),
            "saved_at": (
                _utc_now_iso()
            ),
            "database_is_source_of_truth": (
                True
            ),
            "graph_projection_is_epistemic_authority": (
                False
            ),
            "entities": (
                self.list_entities()
            ),
            "relationships": (
                self.list_relationships()
            ),
        }

    def save(
        self,
    ) -> Path:
        integrity = (
            self.validate_integrity()
        )

        if not integrity[
            "valid"
        ]:
            raise GraphValidationError(
                "Refusing to persist "
                "an invalid graph: "
                + json.dumps(
                    integrity[
                        "issues"
                    ],
                    ensure_ascii=False,
                )
            )

        self.storage_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = (
            self.to_dict()
        )

        temporary_path = (
            self.storage_path
            .with_suffix(
                self.storage_path.suffix
                + ".tmp"
            )
        )

        with temporary_path.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )

        temporary_path.replace(
            self.storage_path
        )

        return (
            self.storage_path
        )

    # Compatibility with the old app-facing method name.
    def save_graph(
        self,
    ) -> Path:
        return self.save()

    def load(
        self,
    ) -> None:
        if not (
            self.storage_path.exists()
        ):
            self.graph = (
                nx.MultiDiGraph()
            )

            return

        try:
            with self.storage_path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                payload = (
                    json.load(
                        handle
                    )
                )

        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise GraphServiceError(
                "Could not load graph "
                f"from {self.storage_path}: "
                f"{exc}"
            ) from exc

        if not isinstance(
            payload,
            Mapping,
        ):
            raise GraphServiceError(
                "Knowledge graph file must "
                "contain a JSON object."
            )

        schema_version = (
            _clean(
                payload.get(
                    "schema_version"
                )
            )
        )

        if (
            schema_version
            != GRAPH_SCHEMA_VERSION
        ):
            raise GraphServiceError(
                "Unsupported graph "
                "schema_version="
                f"{schema_version!r}; "
                "expected "
                f"{GRAPH_SCHEMA_VERSION!r}. "
                "Rebuild the graph projection "
                "from EntityService and "
                "RelationshipService."
            )

        entities = (
            payload.get(
                "entities",
                [],
            )
        )

        relationships = (
            payload.get(
                "relationships",
                [],
            )
        )

        if (
            not isinstance(
                entities,
                list,
            )
            or not isinstance(
                relationships,
                list,
            )
        ):
            raise GraphServiceError(
                "Graph JSON must contain "
                "list fields: entities and "
                "relationships."
            )

        loaded_graph = (
            nx.MultiDiGraph()
        )

        original_graph = (
            self.graph
        )

        self.graph = (
            loaded_graph
        )

        try:
            for entity in entities:
                if not isinstance(
                    entity,
                    Mapping,
                ):
                    raise GraphServiceError(
                        "Every persisted entity "
                        "must be an object."
                    )

                self.upsert_entity(
                    entity_id=(
                        entity.get(
                            "entity_id"
                        )
                    ),
                    name=(
                        entity.get(
                            "name"
                        )
                    ),
                    entity_type=(
                        entity.get(
                            "entity_type"
                        )
                    ),
                    aliases=(
                        entity.get(
                            "aliases",
                            [],
                        )
                    ),
                    attributes=(
                        entity.get(
                            "attributes",
                            {},
                        )
                    ),
                )

                # Preserve original timestamps.
                entity_id = (
                    entity[
                        "entity_id"
                    ]
                )

                self.graph.nodes[
                    entity_id
                ][
                    "created_at"
                ] = entity.get(
                    "created_at",
                    self.graph.nodes[
                        entity_id
                    ][
                        "created_at"
                    ],
                )

                self.graph.nodes[
                    entity_id
                ][
                    "updated_at"
                ] = entity.get(
                    "updated_at",
                    self.graph.nodes[
                        entity_id
                    ][
                        "updated_at"
                    ],
                )

            for relationship in (
                relationships
            ):
                if not isinstance(
                    relationship,
                    Mapping,
                ):
                    raise GraphServiceError(
                        "Every persisted "
                        "relationship must "
                        "be an object."
                    )

                projected = (
                    self.upsert_relationship(
                        source_entity_id=(
                            relationship.get(
                                "source_entity_id"
                            )
                        ),
                        relation_type=(
                            relationship.get(
                                "relation_type"
                            )
                        ),
                        target_entity_id=(
                            relationship.get(
                                "target_entity_id"
                            )
                        ),
                        relationship_id=(
                            relationship.get(
                                "relationship_id"
                            )
                        ),
                        evidence_ids=(
                            relationship.get(
                                "evidence_ids",
                                [],
                            )
                        ),
                        event_ids=(
                            relationship.get(
                                "event_ids",
                                [],
                            )
                        ),
                        source_ids=(
                            relationship.get(
                                "source_ids",
                                [],
                            )
                        ),
                        observation_ids=(
                            relationship.get(
                                "observation_ids",
                                [],
                            )
                        ),
                        temporal_status=(
                            relationship.get(
                                "temporal_status",
                                "unknown",
                            )
                        ),
                        temporal_observations=(
                            relationship.get(
                                "temporal_observations",
                                [],
                            )
                        ),
                        attributes=(
                            relationship.get(
                                "attributes",
                                {},
                            )
                        ),
                        replace_projection=True,
                    )
                )

                relationship_id = (
                    projected[
                        "relationship_id"
                    ]
                )

                source_id = (
                    projected[
                        "source_entity_id"
                    ]
                )

                target_id = (
                    projected[
                        "target_entity_id"
                    ]
                )

                edge = (
                    self.graph[
                        source_id
                    ][
                        target_id
                    ][
                        relationship_id
                    ]
                )

                edge[
                    "created_at"
                ] = relationship.get(
                    "created_at",
                    edge[
                        "created_at"
                    ],
                )

                edge[
                    "updated_at"
                ] = relationship.get(
                    "updated_at",
                    edge[
                        "updated_at"
                    ],
                )

            integrity = (
                self.validate_integrity()
            )

            if not integrity[
                "valid"
            ]:
                raise GraphServiceError(
                    "Loaded graph failed "
                    "integrity validation: "
                    + json.dumps(
                        integrity[
                            "issues"
                        ],
                        ensure_ascii=False,
                    )
                )

        except Exception:
            self.graph = (
                original_graph
            )

            raise

    def clear(
        self,
        *,
        delete_persisted_file: bool = False,
    ) -> None:
        self.graph.clear()

        if (
            delete_persisted_file
            and self.storage_path.exists()
        ):
            self.storage_path.unlink()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        relation_counts = (
            CounterLike()
        )

        entity_type_counts = (
            CounterLike()
        )

        evidence_ids: set[
            str
        ] = set()

        event_ids: set[
            str
        ] = set()

        source_ids: set[
            str
        ] = set()

        observation_ids: set[
            str
        ] = set()

        for (
            _,
            data,
        ) in self.graph.nodes(
            data=True
        ):
            entity_type_counts.add(
                data.get(
                    "entity_type",
                    "unknown",
                )
            )

        for (
            _,
            _,
            _,
            data,
        ) in self.graph.edges(
            keys=True,
            data=True,
        ):
            relation_counts.add(
                data.get(
                    "relation_type",
                    "UNKNOWN",
                )
            )

            evidence_ids.update(
                data.get(
                    "evidence_ids",
                    [],
                )
            )

            event_ids.update(
                data.get(
                    "event_ids",
                    [],
                )
            )

            source_ids.update(
                data.get(
                    "source_ids",
                    [],
                )
            )

            observation_ids.update(
                data.get(
                    "observation_ids",
                    [],
                )
            )

        return {
            "entity_count": (
                self.graph
                .number_of_nodes()
            ),

            "relationship_count": (
                self.graph
                .number_of_edges()
            ),

            "evidence_id_count": (
                len(
                    evidence_ids
                )
            ),

            "event_id_count": (
                len(
                    event_ids
                )
            ),

            "source_id_count": (
                len(
                    source_ids
                )
            ),

            "observation_id_count": (
                len(
                    observation_ids
                )
            ),

            "entity_type_counts": (
                entity_type_counts
                .to_dict()
            ),

            "relation_type_counts": (
                relation_counts
                .to_dict()
            ),

            "weakly_connected_component_count": (
                nx.number_weakly_connected_components(
                    self.graph
                )
                if (
                    self.graph
                    .number_of_nodes()
                )
                else 0
            ),

            "database_is_source_of_truth": (
                True
            ),

            "graph_projection_is_epistemic_authority": (
                False
            ),
        }


class CounterLike:
    """
    Small deterministic counter for stable serialized
    statistics.
    """

    def __init__(
        self,
    ) -> None:
        self._counts: dict[
            str,
            int,
        ] = {}

    def add(
        self,
        value: Any,
    ) -> None:
        key = (
            _clean(
                value
            )
            or "unknown"
        )

        self._counts[
            key
        ] = (
            self._counts.get(
                key,
                0,
            )
            + 1
        )

    def to_dict(
        self,
    ) -> dict[
        str,
        int,
    ]:
        return dict(
            sorted(
                self._counts.items()
            )
        )


__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "SERVICE_VERSION",
    "DEFAULT_ALLOWED_RELATION_TYPES",
    "GraphServiceError",
    "GraphValidationError",
    "GraphIdentityConflictError",
    "GraphService",
]