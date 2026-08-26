"""
Canonical relationship persistence and observation-provenance service.

Recommended location:
    services/relationship_service.py

Pipeline
--------
ExtractionAgent
      ↓
EventService
      ↓
EntityResolutionService / EntityService
      ↓
canonical ENT-* identities
      +
resolved relationship mention
      +
admitted EVID-* records
      ↓
RelationshipService
      ↓
Relationship                semantic edge
RelationshipObservation     source/event/evidence observation
      ↓
GraphService projection
      ↓
ReasoningContextService / StrategicInferenceAgent

Core model
----------
Relationship is the stable semantic edge:

    source_entity_id
    relation_type
    target_entity_id

Temporal validity does NOT belong in Relationship identity.

The same semantic edge may be observed at different times. Temporal state lives
on RelationshipObservation:

    event_id
    source_id
    evidence_ids
    source_mention
    target_mention
    relation_mention
    assertion_text
    claim_status
    temporal_status
    relation_time_text
    valid_start_at
    valid_end_at
    temporal_precision
    attributes

Why two tables?
---------------
A semantic relationship and an observation of that relationship are not the
same thing.

For example:

    REL-123
        Company A PARTNERS_WITH Company B

may have several independent observations:

    RELOBS-1 -> EVENT-A / SOURCE-A / EVID-A
    RELOBS-2 -> EVENT-B / SOURCE-B / EVID-B

Event re-extraction can therefore remove or replace one stale observation
without deleting provenance belonging to other observations.

Epistemic boundaries
---------------------
RelationshipService:
- accepts canonical ENT-* IDs only;
- accepts canonical EVENT-* and SOURCE-* provenance only;
- accepts admitted EVID-* IDs only;
- validates the controlled relation vocabulary;
- persists semantic relationships and observation provenance;
- supports transactional replacement of one event/source observation set;
- validates EVENT-* -> SOURCE-* consistency when the Event model is available;
- exports deterministic records for GraphService;
- never calls an LLM;
- never performs entity resolution;
- never infers a relationship from co-occurrence;
- never decides source independence;
- never treats repeated observations as independent corroboration;
- never performs grounding;
- never performs strategic inference;
- never authorizes release.

claim_status describes how the source expressed the assertion. It is not
confidence and not grounding status.

Every exported semantic relationship and observation therefore carries:

    epistemic_status = "unresolved"

until downstream grounding evaluates the relevant claim.

Graph integration
-----------------
The relational database remains the source of truth. GraphService is a derived
projection and can always be rebuilt from RelationshipService.

No paid service or external API is required.
No private chain-of-thought is requested, persisted, or returned.
"""

from __future__ import annotations

import hashlib
import json
import re

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session


SERVICE_VERSION = "relationship-service-v4.0"
OUTPUT_SCHEMA_VERSION = "relationship-schema-v4.0"


ALLOWED_RELATION_TYPES = {
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


SYMMETRIC_RELATION_TYPES = {
    "MERGES_WITH",
    "PARTNERS_WITH",
    "COMPETES_WITH",
    "COLLABORATES_WITH",
}


ALLOWED_CLAIM_STATUSES = {
    "directly_stated",
    "official_statement",
    "reported_claim",
    "historical_context",
    "inferred_from_source",
    "unknown",
}


ALLOWED_TEMPORAL_STATUSES = {
    "past",
    "current",
    "ongoing",
    "future",
    "uncertain",
    "unknown",
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


RELATIONSHIP_REQUIRED_FIELDS = {
    "relationship_id",
    "source_entity_id",
    "relation_type",
    "target_entity_id",
    "attributes",
    "created_at",
    "updated_at",
}


OBSERVATION_REQUIRED_FIELDS = {
    "observation_id",
    "relationship_id",
    "event_id",
    "source_id",
    "source_mention",
    "target_mention",
    "relation_mention",
    "assertion_text",
    "claim_status",
    "temporal_status",
    "relation_time_text",
    "valid_start_at",
    "valid_end_at",
    "temporal_precision",
    "evidence_ids",
    "attributes",
    "created_at",
    "updated_at",
}


ENTITY_REQUIRED_FIELDS = {
    "entity_id",
}


EVENT_REQUIRED_FIELDS = {
    "event_id",
    "source_id",
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


class RelationshipServiceError(RuntimeError):
    """Base RelationshipService error."""


class RelationshipValidationError(RelationshipServiceError):
    """Input violates relationship semantics or provenance contract."""


class RelationshipModelContractError(RelationshipServiceError):
    """An ORM model does not expose the canonical service contract."""


class RelationshipIdentityConflictError(RelationshipServiceError):
    """A stable ID is being reused for different immutable semantics."""


@dataclass(frozen=True)
class RelationshipObservationWriteResult:
    relationship_id: str
    observation_id: str
    relationship_created: bool
    observation_created: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EventRelationshipReplacementResult:
    event_id: str
    source_id: str
    input_relationship_count: int
    semantic_relationships_created: int
    observations_created: int
    observations_updated: int
    stale_observations_deleted: int
    orphan_relationships_deleted: int
    relationship_ids: list[str]
    observation_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def _normalize_relation_type(value: Any) -> str:
    text = _clean(value).upper()

    text = re.sub(
        r"[^A-Z0-9]+",
        "_",
        text,
    )

    return text.strip("_")


def _enum(value: Any) -> str:
    text = _clean(value).casefold()

    text = (
        text.replace(
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

    return text.strip("_")


def _json_safe(value: Any) -> Any:
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
            value = value.replace(
                tzinfo=timezone.utc
            )

        else:
            value = value.astimezone(
                timezone.utc
            )

        return value.isoformat()

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(key): _json_safe(item)
            for key, item
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
            _json_safe(item)
            for item in value
        ]

    return str(value)


def _json_mapping(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    if value is None:
        return {}

    if not isinstance(
        value,
        Mapping,
    ):
        raise RelationshipValidationError(
            f"{field_name} must be an object."
        )

    prohibited = _contains_prohibited_field(
        value
    )

    if prohibited:
        raise RelationshipValidationError(
            f"{field_name} contains prohibited private/raw reasoning field "
            f"at {prohibited!r}."
        )

    return {
        str(key): _json_safe(item)
        for key, item
        in value.items()
    }


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

    output: list[str] = []
    seen: set[str] = set()

    for item in values:
        if isinstance(
            item,
            Mapping,
        ):
            item = (
                item.get(
                    "evidence_id"
                )
                or item.get(
                    "id"
                )
                or ""
            )

        text = _clean(
            item
        )

        if (
            text
            and text not in seen
        ):
            seen.add(
                text
            )

            output.append(
                text
            )

    return output


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


def _sha256(
    value: Any,
) -> str:
    if isinstance(
        value,
        str,
    ):
        payload = value.encode(
            "utf-8"
        )

    else:
        payload = json.dumps(
            _json_safe(
                value
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
            allow_nan=False,
        ).encode(
            "utf-8"
        )

    return hashlib.sha256(
        payload
    ).hexdigest()


def _stable_id(
    prefix: str,
    payload: Any,
    *,
    length: int = 24,
) -> str:
    return (
        _clean(
            prefix
        ).upper()
        + "-"
        + _sha256(
            payload
        )[:length].upper()
    )


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(
        value,
        Mapping,
    ):
        for (
            key,
            item,
        ) in value.items():
            normalized = _enum(
                key
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
                    f"{path}[{index}]",
                )
            )

            if nested:
                return nested

    return None


def _parse_datetime(
    value: Any,
    *,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    if isinstance(
        value,
        str,
    ):
        text = value.strip()

        if not text:
            return None

        if text.endswith(
            "Z"
        ):
            text = (
                text[:-1]
                + "+00:00"
            )

        try:
            parsed = (
                datetime.fromisoformat(
                    text
                )
            )

        except ValueError as exc:
            raise RelationshipValidationError(
                f"{field_name} must be a valid "
                "ISO-8601 datetime or null."
            ) from exc

    elif isinstance(
        value,
        datetime,
    ):
        parsed = value

    else:
        raise RelationshipValidationError(
            f"{field_name} must be a datetime, "
            "ISO-8601 string, or null."
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    else:
        parsed = parsed.astimezone(
            timezone.utc
        )

    return parsed


class RelationshipService:
    """
    Canonical semantic-relationship + observation-provenance service.

    Dependencies may be injected for isolated tests.

    The default runtime lazily imports:
    - SessionLocal
    - Relationship
    - RelationshipObservation
    - Entity
    - Event
    """

    def __init__(
        self,
        *,
        session_factory: Any = None,
        relationship_model: Any = None,
        observation_model: Any = None,
        entity_model: Any = None,
        event_model: Any = None,
        allowed_relation_types: Optional[
            Iterable[
                str
            ]
        ] = None,
        require_known_entities: bool = True,
        require_known_event: bool = True,
    ) -> None:
        if session_factory is None:
            try:
                from services.database_service import (
                    SessionLocal,
                )

            except ImportError as exc:
                raise RelationshipServiceError(
                    "Could not import SessionLocal from "
                    "services.database_service."
                ) from exc

            session_factory = (
                SessionLocal
            )

        if (
            relationship_model is None
            or observation_model is None
        ):
            try:
                from models.relationship import (
                    Relationship,
                    RelationshipObservation,
                )

            except ImportError as exc:
                raise RelationshipServiceError(
                    "Could not import Relationship and "
                    "RelationshipObservation from "
                    "models.relationship."
                ) from exc

            relationship_model = (
                relationship_model
                or Relationship
            )

            observation_model = (
                observation_model
                or RelationshipObservation
            )

        if (
            entity_model is None
            and require_known_entities
        ):
            try:
                from models.entity import (
                    Entity,
                )

            except ImportError as exc:
                raise RelationshipServiceError(
                    "Could not import Entity "
                    "from models.entity."
                ) from exc

            entity_model = (
                Entity
            )

        if (
            event_model is None
            and require_known_event
        ):
            try:
                from models.event import (
                    Event,
                )

            except ImportError as exc:
                raise RelationshipServiceError(
                    "Could not import Event "
                    "from models.event."
                ) from exc

            event_model = (
                Event
            )

        self.session_factory = (
            session_factory
        )

        self.Relationship = (
            relationship_model
        )

        self.Observation = (
            observation_model
        )

        self.Entity = (
            entity_model
        )

        self.Event = (
            event_model
        )

        self.require_known_entities = bool(
            require_known_entities
        )

        self.require_known_event = bool(
            require_known_event
        )

        self.allowed_relation_types = {
            _normalize_relation_type(
                value
            )
            for value in (
                allowed_relation_types
                if allowed_relation_types
                is not None
                else ALLOWED_RELATION_TYPES
            )
            if _normalize_relation_type(
                value
            )
        }

        if not self.allowed_relation_types:
            raise RelationshipValidationError(
                "allowed_relation_types "
                "cannot be empty."
            )

        self._validate_model_contracts()

    # ------------------------------------------------------------------
    # Contract validation
    # ------------------------------------------------------------------

    def _validate_model_contracts(
        self,
    ) -> None:
        relationship_missing = sorted(
            field
            for field
            in RELATIONSHIP_REQUIRED_FIELDS
            if not hasattr(
                self.Relationship,
                field,
            )
        )

        if relationship_missing:
            raise RelationshipModelContractError(
                "Relationship ORM model is missing "
                "required field(s): "
                + ", ".join(
                    relationship_missing
                )
            )

        observation_missing = sorted(
            field
            for field
            in OBSERVATION_REQUIRED_FIELDS
            if not hasattr(
                self.Observation,
                field,
            )
        )

        if observation_missing:
            raise RelationshipModelContractError(
                "RelationshipObservation ORM model "
                "is missing required field(s): "
                + ", ".join(
                    observation_missing
                )
            )

        if (
            self.require_known_entities
            and self.Entity is not None
        ):
            entity_missing = sorted(
                field
                for field
                in ENTITY_REQUIRED_FIELDS
                if not hasattr(
                    self.Entity,
                    field,
                )
            )

            if entity_missing:
                raise RelationshipModelContractError(
                    "Entity ORM model is missing "
                    "required field(s): "
                    + ", ".join(
                        entity_missing
                    )
                )

        if (
            self.require_known_event
            and self.Event is not None
        ):
            event_missing = sorted(
                field
                for field
                in EVENT_REQUIRED_FIELDS
                if not hasattr(
                    self.Event,
                    field,
                )
            )

            if event_missing:
                raise RelationshipModelContractError(
                    "Event ORM model is missing "
                    "required field(s): "
                    + ", ".join(
                        event_missing
                    )
                )

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def upsert_observation(
        self,
        *,
        source_entity_id: str,
        relation_type: str,
        target_entity_id: str,
        event_id: str,
        source_id: str,
        evidence_ids: Sequence[str],
        source_mention: str = "",
        target_mention: str = "",
        relation_mention: str = "",
        assertion_text: str = "",
        claim_status: str = "unknown",
        temporal_status: str = "unknown",
        relation_time_text: str = "",
        valid_start_at: Any = None,
        valid_end_at: Any = None,
        temporal_precision: str = (
            "unresolved"
        ),
        relationship_id: str = "",
        observation_id: str = "",
        relationship_attributes: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
        observation_attributes: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
    ) -> RelationshipObservationWriteResult:
        """
        Upsert one observed assertion of a canonical relationship.

        The semantic REL-* identity excludes temporal validity.
        Temporal state belongs to the observation.
        """

        normalized = (
            self._normalize_observation_input(
                {
                    "source_entity_id": (
                        source_entity_id
                    ),
                    "relation_type": (
                        relation_type
                    ),
                    "target_entity_id": (
                        target_entity_id
                    ),
                    "event_id": (
                        event_id
                    ),
                    "source_id": (
                        source_id
                    ),
                    "evidence_ids": (
                        evidence_ids
                    ),
                    "source_mention": (
                        source_mention
                    ),
                    "target_mention": (
                        target_mention
                    ),
                    "relation_mention": (
                        relation_mention
                    ),
                    "assertion_text": (
                        assertion_text
                    ),
                    "claim_status": (
                        claim_status
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
                        temporal_precision
                    ),
                    "relationship_id": (
                        relationship_id
                    ),
                    "observation_id": (
                        observation_id
                    ),
                    "relationship_attributes": (
                        relationship_attributes
                    ),
                    "observation_attributes": (
                        observation_attributes
                    ),
                }
            )
        )

        session: Session = (
            self.session_factory()
        )

        try:
            self._require_entities(
                session,
                {
                    normalized[
                        "source_entity_id"
                    ],
                    normalized[
                        "target_entity_id"
                    ],
                },
            )

            self._require_event_source_pair(
                session,
                event_id=(
                    normalized[
                        "event_id"
                    ]
                ),
                source_id=(
                    normalized[
                        "source_id"
                    ]
                ),
            )

            (
                relationship_created,
                observation_created,
            ) = (
                self._upsert_normalized_in_session(
                    session,
                    normalized,
                )
            )

            session.commit()

            return RelationshipObservationWriteResult(
                relationship_id=(
                    normalized[
                        "relationship_id"
                    ]
                ),
                observation_id=(
                    normalized[
                        "observation_id"
                    ]
                ),
                relationship_created=(
                    relationship_created
                ),
                observation_created=(
                    observation_created
                ),
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def replace_event_relationships(
        self,
        *,
        event_id: str,
        source_id: str,
        relationships: Sequence[
            Mapping[
                str,
                Any,
            ]
        ],
    ) -> EventRelationshipReplacementResult:
        """
        Transactionally replace every relationship observation
        for one EVENT-* / SOURCE-* pair.

        Stale observations are deleted. A semantic Relationship
        is removed only when no observations remain anywhere.
        """

        event_id = (
            self._validate_event_id(
                event_id
            )
        )

        source_id = (
            self._validate_source_id(
                source_id
            )
        )

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
            raise RelationshipValidationError(
                "relationships must be "
                "a sequence of objects."
            )

        normalized_items: list[
            dict[
                str,
                Any,
            ]
        ] = []

        for (
            index,
            item,
        ) in enumerate(
            relationships
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                raise RelationshipValidationError(
                    f"relationships[{index}] "
                    "must be an object."
                )

            candidate = deepcopy(
                dict(
                    item
                )
            )

            candidate[
                "event_id"
            ] = event_id

            candidate[
                "source_id"
            ] = source_id

            normalized_items.append(
                self._normalize_observation_input(
                    candidate
                )
            )

        desired_observation_ids = {
            item[
                "observation_id"
            ]
            for item
            in normalized_items
        }

        if (
            len(
                desired_observation_ids
            )
            != len(
                normalized_items
            )
        ):
            raise RelationshipValidationError(
                "Input contains duplicate "
                "relationship observations."
            )

        all_entity_ids = {
            entity_id
            for item
            in normalized_items
            for entity_id
            in (
                item[
                    "source_entity_id"
                ],
                item[
                    "target_entity_id"
                ],
            )
        }

        session: Session = (
            self.session_factory()
        )

        try:
            self._require_event_source_pair(
                session,
                event_id=(
                    event_id
                ),
                source_id=(
                    source_id
                ),
            )

            self._require_entities(
                session,
                all_entity_ids,
            )

            existing_observations = (
                session.execute(
                    select(
                        self.Observation
                    ).where(
                        self.Observation.event_id
                        == event_id,
                        self.Observation.source_id
                        == source_id,
                    )
                )
                .scalars()
                .all()
            )

            existing_by_id = {
                _clean(
                    item.observation_id
                ): item
                for item
                in existing_observations
            }

            affected_relationship_ids = {
                _clean(
                    item.relationship_id
                )
                for item
                in existing_observations
                if _clean(
                    item.relationship_id
                )
            }

            stale_ids = (
                set(
                    existing_by_id
                )
                - desired_observation_ids
            )

            for observation_id in sorted(
                stale_ids
            ):
                session.delete(
                    existing_by_id[
                        observation_id
                    ]
                )

            semantic_created = 0
            observations_created = 0
            observations_updated = 0

            for normalized in (
                normalized_items
            ):
                (
                    relationship_created,
                    observation_created,
                ) = (
                    self._upsert_normalized_in_session(
                        session,
                        normalized,
                    )
                )

                semantic_created += int(
                    relationship_created
                )

                observations_created += int(
                    observation_created
                )

                observations_updated += int(
                    not observation_created
                )

                affected_relationship_ids.add(
                    normalized[
                        "relationship_id"
                    ]
                )

            session.flush()

            orphan_deleted = (
                self._delete_orphan_relationships(
                    session,
                    affected_relationship_ids,
                )
            )

            session.commit()

            return EventRelationshipReplacementResult(
                event_id=(
                    event_id
                ),
                source_id=(
                    source_id
                ),
                input_relationship_count=(
                    len(
                        normalized_items
                    )
                ),
                semantic_relationships_created=(
                    semantic_created
                ),
                observations_created=(
                    observations_created
                ),
                observations_updated=(
                    observations_updated
                ),
                stale_observations_deleted=(
                    len(
                        stale_ids
                    )
                ),
                orphan_relationships_deleted=(
                    orphan_deleted
                ),
                relationship_ids=sorted(
                    {
                        item[
                            "relationship_id"
                        ]
                        for item
                        in normalized_items
                    }
                ),
                observation_ids=sorted(
                    desired_observation_ids
                ),
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def delete_event_relationships(
        self,
        *,
        event_id: str,
        source_id: str = "",
    ) -> dict[
        str,
        Any,
    ]:
        """
        Delete relationship observations belonging to one Event.

        When source_id is supplied, deletion is scoped to the
        exact event/source pair. Orphan semantic rows are removed.
        """

        event_id = (
            self._validate_event_id(
                event_id
            )
        )

        source_id = (
            self._validate_source_id(
                source_id
            )
            if _clean(
                source_id
            )
            else ""
        )

        session: Session = (
            self.session_factory()
        )

        try:
            statement = (
                select(
                    self.Observation
                )
                .where(
                    self.Observation.event_id
                    == event_id
                )
            )

            if source_id:
                statement = (
                    statement.where(
                        self.Observation.source_id
                        == source_id
                    )
                )

            observations = (
                session.execute(
                    statement
                )
                .scalars()
                .all()
            )

            affected_relationship_ids = {
                _clean(
                    item.relationship_id
                )
                for item
                in observations
                if _clean(
                    item.relationship_id
                )
            }

            for item in observations:
                session.delete(
                    item
                )

            session.flush()

            orphan_deleted = (
                self._delete_orphan_relationships(
                    session,
                    affected_relationship_ids,
                )
            )

            session.commit()

            return {
                "event_id": (
                    event_id
                ),
                "source_id": (
                    source_id
                ),
                "observations_deleted": (
                    len(
                        observations
                    )
                ),
                "orphan_relationships_deleted": (
                    orphan_deleted
                ),
            }

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def get_relationship(
        self,
        relationship_id: str,
        *,
        include_observations: bool = True,
    ) -> Optional[
        dict[
            str,
            Any,
        ]
    ]:
        relationship_id = _clean(
            relationship_id
        )

        if not relationship_id:
            return None

        session: Session = (
            self.session_factory()
        )

        try:
            relationship = (
                session.execute(
                    select(
                        self.Relationship
                    ).where(
                        self.Relationship.relationship_id
                        == relationship_id
                    )
                )
                .scalar_one_or_none()
            )

            if relationship is None:
                return None

            record = (
                self._relationship_record(
                    relationship
                )
            )

            if include_observations:
                observations = (
                    session.execute(
                        select(
                            self.Observation
                        ).where(
                            self.Observation.relationship_id
                            == relationship_id
                        )
                    )
                    .scalars()
                    .all()
                )

                observations.sort(
                    key=lambda item: (
                        _clean(
                            item.event_id
                        ),
                        _clean(
                            item.source_id
                        ),
                        _clean(
                            item.observation_id
                        ),
                    )
                )

                record[
                    "observations"
                ] = [
                    self._observation_record(
                        item
                    )
                    for item
                    in observations
                ]

            return record

        finally:
            session.close()

    def get_observation(
        self,
        observation_id: str,
    ) -> Optional[
        dict[
            str,
            Any,
        ]
    ]:
        observation_id = _clean(
            observation_id
        )

        if not observation_id:
            return None

        session: Session = (
            self.session_factory()
        )

        try:
            observation = (
                session.execute(
                    select(
                        self.Observation
                    ).where(
                        self.Observation.observation_id
                        == observation_id
                    )
                )
                .scalar_one_or_none()
            )

            return (
                self._observation_record(
                    observation
                )
                if observation is not None
                else None
            )

        finally:
            session.close()

    def list_relationships(
        self,
        *,
        source_entity_id: str = "",
        target_entity_id: str = "",
        entity_id: str = "",
        relation_type: str = "",
        event_id: str = "",
        source_id: str = "",
        include_observations: bool = False,
        limit: Optional[
            int
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """
        Query semantic relationships.

        There is no hidden default limit.
        """

        source_entity_id = _clean(
            source_entity_id
        )

        target_entity_id = _clean(
            target_entity_id
        )

        entity_id = _clean(
            entity_id
        )

        event_id = _clean(
            event_id
        )

        source_id = _clean(
            source_id
        )

        normalized_relation = (
            self._validate_relation_type(
                relation_type
            )
            if relation_type
            else ""
        )

        limit = (
            self._normalize_optional_limit(
                limit
            )
        )

        session: Session = (
            self.session_factory()
        )

        try:
            statement = select(
                self.Relationship
            )

            if (
                event_id
                or source_id
            ):
                statement = statement.join(
                    self.Observation,
                    self.Observation.relationship_id
                    == self.Relationship.relationship_id,
                )

            if source_entity_id:
                statement = statement.where(
                    self.Relationship.source_entity_id
                    == source_entity_id
                )

            if target_entity_id:
                statement = statement.where(
                    self.Relationship.target_entity_id
                    == target_entity_id
                )

            if entity_id:
                statement = statement.where(
                    (
                        self.Relationship.source_entity_id
                        == entity_id
                    )
                    |
                    (
                        self.Relationship.target_entity_id
                        == entity_id
                    )
                )

            if normalized_relation:
                statement = statement.where(
                    self.Relationship.relation_type
                    == normalized_relation
                )

            if event_id:
                statement = statement.where(
                    self.Observation.event_id
                    == event_id
                )

            if source_id:
                statement = statement.where(
                    self.Observation.source_id
                    == source_id
                )

            relationships = (
                session.execute(
                    statement
                )
                .scalars()
                .unique()
                .all()
            )

            relationships.sort(
                key=lambda item: (
                    _clean(
                        item.relation_type
                    ),
                    _clean(
                        item.source_entity_id
                    ),
                    _clean(
                        item.target_entity_id
                    ),
                    _clean(
                        item.relationship_id
                    ),
                )
            )

            if limit is not None:
                relationships = relationships[
                    :limit
                ]

            result = [
                self._relationship_record(
                    relationship
                )
                for relationship
                in relationships
            ]

            if include_observations:
                for record in result:
                    observations = (
                        session.execute(
                            select(
                                self.Observation
                            ).where(
                                self.Observation.relationship_id
                                == record[
                                    "relationship_id"
                                ]
                            )
                        )
                        .scalars()
                        .all()
                    )

                    observations.sort(
                        key=lambda item: (
                            _clean(
                                item.event_id
                            ),
                            _clean(
                                item.source_id
                            ),
                            _clean(
                                item.observation_id
                            ),
                        )
                    )

                    record[
                        "observations"
                    ] = [
                        self._observation_record(
                            item
                        )
                        for item
                        in observations
                    ]

            return result

        finally:
            session.close()

    def list_observations(
        self,
        *,
        relationship_id: str = "",
        event_id: str = "",
        source_id: str = "",
        claim_status: str = "",
        temporal_status: str = "",
        temporal_precision: str = "",
        limit: Optional[
            int
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        relationship_id = _clean(
            relationship_id
        )

        event_id = _clean(
            event_id
        )

        source_id = _clean(
            source_id
        )

        normalized_claim = (
            self._validate_claim_status(
                claim_status
            )
            if claim_status
            else ""
        )

        normalized_temporal = (
            self._validate_temporal_status(
                temporal_status
            )
            if temporal_status
            else ""
        )

        normalized_precision = (
            self._validate_temporal_precision(
                temporal_precision
            )
            if temporal_precision
            else ""
        )

        limit = (
            self._normalize_optional_limit(
                limit
            )
        )

        session: Session = (
            self.session_factory()
        )

        try:
            statement = select(
                self.Observation
            )

            if relationship_id:
                statement = statement.where(
                    self.Observation.relationship_id
                    == relationship_id
                )

            if event_id:
                statement = statement.where(
                    self.Observation.event_id
                    == event_id
                )

            if source_id:
                statement = statement.where(
                    self.Observation.source_id
                    == source_id
                )

            if normalized_claim:
                statement = statement.where(
                    self.Observation.claim_status
                    == normalized_claim
                )

            if normalized_temporal:
                statement = statement.where(
                    self.Observation.temporal_status
                    == normalized_temporal
                )

            if normalized_precision:
                statement = statement.where(
                    self.Observation.temporal_precision
                    == normalized_precision
                )

            observations = (
                session.execute(
                    statement
                )
                .scalars()
                .all()
            )

            observations.sort(
                key=lambda item: (
                    _clean(
                        item.event_id
                    ),
                    _clean(
                        item.source_id
                    ),
                    _clean(
                        item.relationship_id
                    ),
                    _clean(
                        item.observation_id
                    ),
                )
            )

            if limit is not None:
                observations = observations[
                    :limit
                ]

            return [
                self._observation_record(
                    item
                )
                for item
                in observations
            ]

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Graph projection
    # ------------------------------------------------------------------

    def build_graph_projection(
        self,
        *,
        relationship_ids: Optional[
            Sequence[
                str
            ]
        ] = None,
        event_id: str = "",
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """
        Aggregate observation provenance into GraphService records.

        Temporal state stays observation-specific.

        This aggregation does not establish:
        - source independence;
        - grounding;
        - causal truth.
        """

        requested_ids = (
            set(
                _unique_strings(
                    relationship_ids
                )
            )
            if relationship_ids is not None
            else None
        )

        relationships = (
            self.list_relationships(
                event_id=(
                    event_id
                ),
                include_observations=True,
            )
        )

        output: list[
            dict[
                str,
                Any,
            ]
        ] = []

        for relationship in (
            relationships
        ):
            relationship_id = (
                relationship[
                    "relationship_id"
                ]
            )

            if (
                requested_ids
                is not None
                and relationship_id
                not in requested_ids
            ):
                continue

            observations = (
                relationship.get(
                    "observations",
                    [],
                )
            )

            evidence_ids: list[
                str
            ] = []

            event_ids: list[
                str
            ] = []

            source_ids: list[
                str
            ] = []

            observation_ids: list[
                str
            ] = []

            claim_statuses: list[
                str
            ] = []

            temporal_statuses: list[
                str
            ] = []

            source_mentions: list[
                str
            ] = []

            target_mentions: list[
                str
            ] = []

            relation_mentions: list[
                str
            ] = []

            temporal_observations: list[
                dict[
                    str,
                    Any,
                ]
            ] = []

            for observation in (
                observations
            ):
                evidence_ids = (
                    _merge_unique(
                        evidence_ids,
                        observation.get(
                            "evidence_ids"
                        ),
                    )
                )

                event_ids = (
                    _merge_unique(
                        event_ids,
                        [
                            observation.get(
                                "event_id"
                            )
                        ],
                    )
                )

                source_ids = (
                    _merge_unique(
                        source_ids,
                        [
                            observation.get(
                                "source_id"
                            )
                        ],
                    )
                )

                observation_ids = (
                    _merge_unique(
                        observation_ids,
                        [
                            observation.get(
                                "observation_id"
                            )
                        ],
                    )
                )

                claim_statuses = (
                    _merge_unique(
                        claim_statuses,
                        [
                            observation.get(
                                "claim_status"
                            )
                        ],
                    )
                )

                temporal_statuses = (
                    _merge_unique(
                        temporal_statuses,
                        [
                            observation.get(
                                "temporal_status"
                            )
                        ],
                    )
                )

                source_mentions = (
                    _merge_unique(
                        source_mentions,
                        [
                            observation.get(
                                "source_mention"
                            )
                        ],
                    )
                )

                target_mentions = (
                    _merge_unique(
                        target_mentions,
                        [
                            observation.get(
                                "target_mention"
                            )
                        ],
                    )
                )

                relation_mentions = (
                    _merge_unique(
                        relation_mentions,
                        [
                            observation.get(
                                "relation_mention"
                            )
                        ],
                    )
                )

                temporal_observations.append(
                    {
                        "observation_id": (
                            observation.get(
                                "observation_id"
                            )
                        ),
                        "event_id": (
                            observation.get(
                                "event_id"
                            )
                        ),
                        "source_id": (
                            observation.get(
                                "source_id"
                            )
                        ),
                        "temporal_status": (
                            observation.get(
                                "temporal_status"
                            )
                        ),
                        "relation_time_text": (
                            observation.get(
                                "relation_time_text"
                            )
                        ),
                        "valid_start_at": (
                            observation.get(
                                "valid_start_at"
                            )
                        ),
                        "valid_end_at": (
                            observation.get(
                                "valid_end_at"
                            )
                        ),
                        "temporal_precision": (
                            observation.get(
                                "temporal_precision"
                            )
                        ),
                    }
                )

            temporal_status = (
                temporal_statuses[
                    0
                ]
                if len(
                    temporal_statuses
                )
                == 1
                else (
                    "mixed"
                    if temporal_statuses
                    else "unknown"
                )
            )

            projection_attributes = (
                deepcopy(
                    relationship.get(
                        "attributes",
                        {},
                    )
                )
            )

            projection_attributes.update(
                {
                    "relationship_observation_ids": (
                        observation_ids
                    ),
                    "claim_statuses": (
                        claim_statuses
                    ),
                    "temporal_statuses": (
                        temporal_statuses
                    ),
                    "source_mentions": (
                        source_mentions
                    ),
                    "target_mentions": (
                        target_mentions
                    ),
                    "relation_mentions": (
                        relation_mentions
                    ),
                    "temporal_observations": (
                        temporal_observations
                    ),
                    "epistemic_status": (
                        "unresolved"
                    ),
                    "source_independence_assessed_here": (
                        False
                    ),
                    "relationship_service_version": (
                        SERVICE_VERSION
                    ),
                }
            )

            output.append(
                {
                    "relationship_id": (
                        relationship_id
                    ),
                    "source_entity_id": (
                        relationship[
                            "source_entity_id"
                        ]
                    ),
                    "relation_type": (
                        relationship[
                            "relation_type"
                        ]
                    ),
                    "target_entity_id": (
                        relationship[
                            "target_entity_id"
                        ]
                    ),
                    "evidence_ids": (
                        evidence_ids
                    ),
                    "event_ids": (
                        event_ids
                    ),
                    "source_ids": (
                        source_ids
                    ),
                    "observation_ids": (
                        observation_ids
                    ),
                    "temporal_status": (
                        temporal_status
                    ),
                    "temporal_observations": (
                        temporal_observations
                    ),
                    "attributes": (
                        projection_attributes
                    ),
                    "epistemic_status": (
                        "unresolved"
                    ),
                }
            )

        output.sort(
            key=lambda item: (
                item[
                    "relationship_id"
                ]
            )
        )

        return output

    def project_to_graph(
        self,
        graph_service: Any,
        *,
        relationship_ids: Optional[
            Sequence[
                str
            ]
        ] = None,
        event_id: str = "",
    ) -> dict[
        str,
        Any,
    ]:
        """
        Upsert the relational projection into GraphService.

        RelationshipService remains the reconstructible source
        of truth. Graph projection is outside the DB transaction.
        """

        if not hasattr(
            graph_service,
            "upsert_relationship",
        ):
            raise RelationshipValidationError(
                "graph_service must expose "
                "upsert_relationship(...)."
            )

        records = (
            self.build_graph_projection(
                relationship_ids=(
                    relationship_ids
                ),
                event_id=(
                    event_id
                ),
            )
        )

        projected_ids: list[
            str
        ] = []

        for record in records:
            graph_service.upsert_relationship(
                source_entity_id=record[
                    "source_entity_id"
                ],
                relation_type=record[
                    "relation_type"
                ],
                target_entity_id=record[
                    "target_entity_id"
                ],
                relationship_id=record[
                    "relationship_id"
                ],
                evidence_ids=record[
                    "evidence_ids"
                ],
                event_ids=record[
                    "event_ids"
                ],
                source_ids=record[
                    "source_ids"
                ],
                observation_ids=record[
                    "observation_ids"
                ],
                temporal_status=record[
                    "temporal_status"
                ],
                temporal_observations=record[
                    "temporal_observations"
                ],
                attributes=record[
                    "attributes"
                ],
            )

            projected_ids.append(
                record[
                    "relationship_id"
                ]
            )

        return {
            "relationship_count": (
                len(
                    projected_ids
                )
            ),
            "relationship_ids": (
                projected_ids
            ),
            "database_is_source_of_truth": (
                True
            ),
            "graph_projection_is_epistemic_authority": (
                False
            ),
        }

    # ------------------------------------------------------------------
    # Internal normalization
    # ------------------------------------------------------------------

    def _normalize_observation_input(
        self,
        value: Mapping[
            str,
            Any,
        ],
    ) -> dict[
        str,
        Any,
    ]:
        prohibited = (
            _contains_prohibited_field(
                value
            )
        )

        if prohibited:
            raise RelationshipValidationError(
                "Relationship input contains "
                "prohibited private/raw reasoning "
                f"field at {prohibited!r}."
            )

        source_entity_id = (
            self._validate_entity_id(
                value.get(
                    "source_entity_id"
                )
                or value.get(
                    "source_entity"
                )
            )
        )

        target_entity_id = (
            self._validate_entity_id(
                value.get(
                    "target_entity_id"
                )
                or value.get(
                    "target_entity"
                )
            )
        )

        relation_type = (
            self._validate_relation_type(
                value.get(
                    "relation_type"
                )
                or value.get(
                    "relation"
                )
            )
        )

        if (
            source_entity_id
            == target_entity_id
        ):
            raise RelationshipValidationError(
                "Self-referential relationships "
                "are not allowed."
            )

        source_mention = _clean(
            value.get(
                "source_mention"
            )
            or value.get(
                "source"
            )
        )

        target_mention = _clean(
            value.get(
                "target_mention"
            )
            or value.get(
                "target"
            )
        )

        relation_mention = _clean(
            value.get(
                "relation_mention"
            )
        )

        assertion_text = _clean(
            value.get(
                "assertion_text"
            )
        )

        # Canonicalize endpoints only for symmetric relations.
        if (
            relation_type
            in SYMMETRIC_RELATION_TYPES
            and target_entity_id
            < source_entity_id
        ):
            (
                source_entity_id,
                target_entity_id,
            ) = (
                target_entity_id,
                source_entity_id,
            )

            (
                source_mention,
                target_mention,
            ) = (
                target_mention,
                source_mention,
            )

        event_id = (
            self._validate_event_id(
                value.get(
                    "event_id"
                )
            )
        )

        source_id = (
            self._validate_source_id(
                value.get(
                    "source_id"
                )
            )
        )

        evidence_ids = (
            self._validate_evidence_ids(
                value.get(
                    "evidence_ids"
                )
                or []
            )
        )

        claim_status = (
            self._validate_claim_status(
                value.get(
                    "claim_status"
                )
                or "unknown"
            )
        )

        temporal_status = (
            self._validate_temporal_status(
                value.get(
                    "temporal_status"
                )
                or "unknown"
            )
        )

        relation_time_text = _clean(
            value.get(
                "relation_time_text"
            )
        )

        valid_start_at = (
            _parse_datetime(
                value.get(
                    "valid_start_at"
                ),
                field_name=(
                    "valid_start_at"
                ),
            )
        )

        valid_end_at = (
            _parse_datetime(
                value.get(
                    "valid_end_at"
                ),
                field_name=(
                    "valid_end_at"
                ),
            )
        )

        temporal_precision = (
            self._validate_temporal_precision(
                value.get(
                    "temporal_precision"
                )
                or "unresolved"
            )
        )

        if (
            valid_start_at
            is not None
            and valid_end_at
            is not None
            and valid_end_at
            < valid_start_at
        ):
            raise RelationshipValidationError(
                "valid_end_at cannot precede "
                "valid_start_at."
            )

        if (
            (
                valid_start_at
                is not None
                or valid_end_at
                is not None
            )
            and not relation_time_text
        ):
            raise RelationshipValidationError(
                "valid_start_at/valid_end_at require "
                "relation_time_text provenance."
            )

        if (
            temporal_precision
            != "unresolved"
            and not relation_time_text
        ):
            raise RelationshipValidationError(
                "Resolved temporal_precision requires "
                "relation_time_text provenance."
            )

        relationship_attributes = (
            _json_mapping(
                value.get(
                    "relationship_attributes"
                )
                or {},
                field_name=(
                    "relationship_attributes"
                ),
            )
        )

        observation_attributes = (
            _json_mapping(
                value.get(
                    "observation_attributes"
                )
                or value.get(
                    "attributes"
                )
                or {},
                field_name=(
                    "observation_attributes"
                ),
            )
        )

        generated_relationship_id = (
            self.stable_relationship_id(
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

        explicit_relationship_id = (
            _clean(
                value.get(
                    "relationship_id"
                )
            )
        )

        if (
            explicit_relationship_id
            and explicit_relationship_id
            != generated_relationship_id
        ):
            raise RelationshipIdentityConflictError(
                "Explicit relationship_id does not "
                "match canonical semantic REL-* identity."
            )

        relationship_id = (
            explicit_relationship_id
            or generated_relationship_id
        )

        generated_observation_id = (
            self.stable_observation_id(
                relationship_id=(
                    relationship_id
                ),
                event_id=(
                    event_id
                ),
                source_id=(
                    source_id
                ),
                evidence_ids=(
                    evidence_ids
                ),
                source_mention=(
                    source_mention
                ),
                target_mention=(
                    target_mention
                ),
                relation_mention=(
                    relation_mention
                ),
                assertion_text=(
                    assertion_text
                ),
                claim_status=(
                    claim_status
                ),
                temporal_status=(
                    temporal_status
                ),
                relation_time_text=(
                    relation_time_text
                ),
                valid_start_at=(
                    valid_start_at
                ),
                valid_end_at=(
                    valid_end_at
                ),
                temporal_precision=(
                    temporal_precision
                ),
            )
        )

        explicit_observation_id = (
            _clean(
                value.get(
                    "observation_id"
                )
            )
        )

        if (
            explicit_observation_id
            and explicit_observation_id
            != generated_observation_id
        ):
            raise RelationshipIdentityConflictError(
                "Explicit observation_id does not "
                "match canonical RELOBS-* identity."
            )

        observation_id = (
            explicit_observation_id
            or generated_observation_id
        )

        return {
            "relationship_id": (
                relationship_id
            ),
            "generated_relationship_id": (
                generated_relationship_id
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
            "relationship_attributes": (
                relationship_attributes
            ),
            "observation_id": (
                observation_id
            ),
            "event_id": (
                event_id
            ),
            "source_id": (
                source_id
            ),
            "source_mention": (
                source_mention
            ),
            "target_mention": (
                target_mention
            ),
            "relation_mention": (
                relation_mention
            ),
            "assertion_text": (
                assertion_text
            ),
            "claim_status": (
                claim_status
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
                temporal_precision
            ),
            "evidence_ids": (
                evidence_ids
            ),
            "observation_attributes": (
                observation_attributes
            ),
        }

    # ------------------------------------------------------------------
    # Controlled values / IDs
    # ------------------------------------------------------------------

    def _validate_relation_type(
        self,
        value: Any,
    ) -> str:
        relation_type = (
            _normalize_relation_type(
                value
            )
        )

        if not relation_type:
            raise RelationshipValidationError(
                "relation_type cannot be empty."
            )

        if (
            relation_type
            not in self.allowed_relation_types
        ):
            raise RelationshipValidationError(
                f"Unsupported relation_type="
                f"{relation_type!r}. "
                "Unknown values must not be "
                "silently mapped to OTHER."
            )

        return relation_type

    @staticmethod
    def _validate_claim_status(
        value: Any,
    ) -> str:
        status = _enum(
            value
        )

        if (
            status
            not in ALLOWED_CLAIM_STATUSES
        ):
            raise RelationshipValidationError(
                f"Unsupported claim_status="
                f"{status!r}."
            )

        return status

    @staticmethod
    def _validate_temporal_status(
        value: Any,
    ) -> str:
        status = _enum(
            value
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
            raise RelationshipValidationError(
                f"Unsupported temporal_status="
                f"{status!r}."
            )

        return status

    @staticmethod
    def _validate_temporal_precision(
        value: Any,
    ) -> str:
        precision = _enum(
            value
        )

        if (
            precision
            not in ALLOWED_TEMPORAL_PRECISIONS
        ):
            raise RelationshipValidationError(
                f"Unsupported temporal_precision="
                f"{precision!r}."
            )

        return precision

    @staticmethod
    def _validate_entity_id(
        value: Any,
    ) -> str:
        entity_id = _clean(
            value
        )

        if not entity_id:
            raise RelationshipValidationError(
                "canonical entity_id cannot be empty."
            )

        if not (
            entity_id
            .upper()
            .startswith(
                "ENT-"
            )
        ):
            raise RelationshipValidationError(
                "RelationshipService accepts "
                "canonical ENT-* IDs only."
            )

        return entity_id

    @staticmethod
    def _validate_event_id(
        value: Any,
    ) -> str:
        event_id = _clean(
            value
        )

        if not event_id:
            raise RelationshipValidationError(
                "event_id cannot be empty."
            )

        if not (
            event_id
            .upper()
            .startswith(
                "EVENT-"
            )
        ):
            raise RelationshipValidationError(
                "event_id must use canonical "
                "EVENT-* identity."
            )

        return event_id

    @staticmethod
    def _validate_source_id(
        value: Any,
    ) -> str:
        source_id = _clean(
            value
        )

        if not source_id:
            raise RelationshipValidationError(
                "source_id cannot be empty."
            )

        if not (
            source_id
            .upper()
            .startswith(
                "SOURCE-"
            )
        ):
            raise RelationshipValidationError(
                "source_id must use canonical "
                "SOURCE-* identity."
            )

        return source_id

    @staticmethod
    def _validate_evidence_ids(
        value: Any,
    ) -> list[str]:
        evidence_ids = (
            _unique_strings(
                value
            )
        )

        if not evidence_ids:
            raise RelationshipValidationError(
                "Every relationship observation "
                "requires at least one admitted "
                "EVID-* ID."
            )

        invalid = [
            evidence_id
            for evidence_id
            in evidence_ids
            if not (
                evidence_id
                .upper()
                .startswith(
                    "EVID-"
                )
            )
        ]

        if invalid:
            raise RelationshipValidationError(
                "Relationship observations accept "
                "admitted EVID-* IDs only. "
                "Invalid IDs: "
                + ", ".join(
                    sorted(
                        invalid
                    )
                )
            )

        return evidence_ids

    @staticmethod
    def _normalize_optional_limit(
        limit: Optional[
            int
        ],
    ) -> Optional[int]:
        if limit is None:
            return None

        if isinstance(
            limit,
            bool,
        ):
            raise RelationshipValidationError(
                "limit must be a positive integer."
            )

        try:
            normalized = int(
                limit
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise RelationshipValidationError(
                "limit must be a positive integer."
            ) from exc

        if normalized <= 0:
            raise RelationshipValidationError(
                "limit must be a positive integer."
            )

        return normalized

    # ------------------------------------------------------------------
    # Stable identities
    # ------------------------------------------------------------------

    @staticmethod
    def stable_relationship_id(
        *,
        source_entity_id: str,
        relation_type: str,
        target_entity_id: str,
    ) -> str:
        """
        Stable semantic REL-* identity.

        Temporal validity is deliberately excluded.
        """

        source_entity_id = _clean(
            source_entity_id
        )

        target_entity_id = _clean(
            target_entity_id
        )

        relation_type = (
            _normalize_relation_type(
                relation_type
            )
        )

        if (
            relation_type
            in SYMMETRIC_RELATION_TYPES
        ):
            (
                source_entity_id,
                target_entity_id,
            ) = sorted(
                (
                    source_entity_id,
                    target_entity_id,
                )
            )

        return _stable_id(
            "REL",
            {
                "source_entity_id": (
                    source_entity_id
                ),
                "relation_type": (
                    relation_type
                ),
                "target_entity_id": (
                    target_entity_id
                ),
            },
        )

    @staticmethod
    def stable_observation_id(
        *,
        relationship_id: str,
        event_id: str,
        source_id: str,
        evidence_ids: Sequence[
            str
        ],
        source_mention: str = "",
        target_mention: str = "",
        relation_mention: str = "",
        assertion_text: str = "",
        claim_status: str = "unknown",
        temporal_status: str = "unknown",
        relation_time_text: str = "",
        valid_start_at: Any = None,
        valid_end_at: Any = None,
        temporal_precision: str = (
            "unresolved"
        ),
    ) -> str:
        """
        Deterministic RELOBS-* identity for one observation.

        Mutable observation metadata is excluded from identity.
        """

        return _stable_id(
            "RELOBS",
            {
                "relationship_id": (
                    _clean(
                        relationship_id
                    )
                ),
                "event_id": (
                    _clean(
                        event_id
                    )
                ),
                "source_id": (
                    _clean(
                        source_id
                    )
                ),
                "evidence_ids": sorted(
                    _unique_strings(
                        evidence_ids
                    )
                ),
                "source_mention": (
                    _clean(
                        source_mention
                    )
                ),
                "target_mention": (
                    _clean(
                        target_mention
                    )
                ),
                "relation_mention": (
                    _clean(
                        relation_mention
                    )
                ),
                "assertion_text": (
                    _clean(
                        assertion_text
                    )
                ),
                "claim_status": (
                    _enum(
                        claim_status
                    )
                ),
                "temporal_status": (
                    _enum(
                        temporal_status
                    )
                ),
                "relation_time_text": (
                    _clean(
                        relation_time_text
                    )
                ),
                "valid_start_at": (
                    _json_safe(
                        valid_start_at
                    )
                ),
                "valid_end_at": (
                    _json_safe(
                        valid_end_at
                    )
                ),
                "temporal_precision": (
                    _enum(
                        temporal_precision
                    )
                ),
            },
        )

    # ------------------------------------------------------------------
    # Transaction helpers
    # ------------------------------------------------------------------

    def _upsert_normalized_in_session(
        self,
        session: Session,
        normalized: Mapping[
            str,
            Any,
        ],
    ) -> tuple[
        bool,
        bool,
    ]:
        relationship = (
            session.execute(
                select(
                    self.Relationship
                ).where(
                    self.Relationship.relationship_id
                    == normalized[
                        "relationship_id"
                    ]
                )
            )
            .scalar_one_or_none()
        )

        relationship_created = (
            relationship is None
        )

        now = (
            _utc_now()
        )

        if relationship_created:
            relationship = (
                self.Relationship(
                    relationship_id=(
                        normalized[
                            "relationship_id"
                        ]
                    ),
                    source_entity_id=(
                        normalized[
                            "source_entity_id"
                        ]
                    ),
                    relation_type=(
                        normalized[
                            "relation_type"
                        ]
                    ),
                    target_entity_id=(
                        normalized[
                            "target_entity_id"
                        ]
                    ),
                    attributes=deepcopy(
                        normalized[
                            "relationship_attributes"
                        ]
                    ),
                    created_at=(
                        now
                    ),
                    updated_at=(
                        now
                    ),
                )
            )

            session.add(
                relationship
            )

            session.flush()

        else:
            self._assert_relationship_identity(
                relationship,
                normalized,
            )

            relationship.attributes = (
                self._merge_nonconflicting_mapping(
                    relationship.attributes
                    or {},
                    normalized[
                        "relationship_attributes"
                    ],
                    field_name=(
                        "relationship_attributes"
                    ),
                )
            )

            relationship.updated_at = (
                now
            )

        observation = (
            session.execute(
                select(
                    self.Observation
                ).where(
                    self.Observation.observation_id
                    == normalized[
                        "observation_id"
                    ]
                )
            )
            .scalar_one_or_none()
        )

        observation_created = (
            observation is None
        )

        if observation_created:
            observation = (
                self.Observation(
                    observation_id=(
                        normalized[
                            "observation_id"
                        ]
                    ),
                    relationship_id=(
                        normalized[
                            "relationship_id"
                        ]
                    ),
                    event_id=(
                        normalized[
                            "event_id"
                        ]
                    ),
                    source_id=(
                        normalized[
                            "source_id"
                        ]
                    ),
                    source_mention=(
                        normalized[
                            "source_mention"
                        ]
                    ),
                    target_mention=(
                        normalized[
                            "target_mention"
                        ]
                    ),
                    relation_mention=(
                        normalized[
                            "relation_mention"
                        ]
                    ),
                    assertion_text=(
                        normalized[
                            "assertion_text"
                        ]
                    ),
                    claim_status=(
                        normalized[
                            "claim_status"
                        ]
                    ),
                    temporal_status=(
                        normalized[
                            "temporal_status"
                        ]
                    ),
                    relation_time_text=(
                        normalized[
                            "relation_time_text"
                        ]
                    ),
                    valid_start_at=(
                        normalized[
                            "valid_start_at"
                        ]
                    ),
                    valid_end_at=(
                        normalized[
                            "valid_end_at"
                        ]
                    ),
                    temporal_precision=(
                        normalized[
                            "temporal_precision"
                        ]
                    ),
                    evidence_ids=deepcopy(
                        normalized[
                            "evidence_ids"
                        ]
                    ),
                    attributes=deepcopy(
                        normalized[
                            "observation_attributes"
                        ]
                    ),
                    created_at=(
                        now
                    ),
                    updated_at=(
                        now
                    ),
                )
            )

            session.add(
                observation
            )

        else:
            self._assert_observation_identity(
                observation,
                normalized,
            )

            # Fresh containers ensure SQLAlchemy JSON
            # mutation tracking is not required.
            observation.evidence_ids = (
                deepcopy(
                    normalized[
                        "evidence_ids"
                    ]
                )
            )

            observation.attributes = (
                deepcopy(
                    normalized[
                        "observation_attributes"
                    ]
                )
            )

            observation.updated_at = (
                now
            )

        return (
            relationship_created,
            observation_created,
        )

    @staticmethod
    def _assert_relationship_identity(
        relationship: Any,
        normalized: Mapping[
            str,
            Any,
        ],
    ) -> None:
        expected = {
            "source_entity_id": (
                normalized[
                    "source_entity_id"
                ]
            ),
            "relation_type": (
                normalized[
                    "relation_type"
                ]
            ),
            "target_entity_id": (
                normalized[
                    "target_entity_id"
                ]
            ),
        }

        actual = {
            "source_entity_id": (
                _clean(
                    relationship.source_entity_id
                )
            ),
            "relation_type": (
                _normalize_relation_type(
                    relationship.relation_type
                )
            ),
            "target_entity_id": (
                _clean(
                    relationship.target_entity_id
                )
            ),
        }

        if actual != expected:
            raise RelationshipIdentityConflictError(
                "Existing relationship_id is being "
                "reused for different semantic "
                "endpoints/type."
            )

    @staticmethod
    def _assert_observation_identity(
        observation: Any,
        normalized: Mapping[
            str,
            Any,
        ],
    ) -> None:
        expected = {
            "relationship_id": (
                normalized[
                    "relationship_id"
                ]
            ),
            "event_id": (
                normalized[
                    "event_id"
                ]
            ),
            "source_id": (
                normalized[
                    "source_id"
                ]
            ),
            "source_mention": (
                normalized[
                    "source_mention"
                ]
            ),
            "target_mention": (
                normalized[
                    "target_mention"
                ]
            ),
            "relation_mention": (
                normalized[
                    "relation_mention"
                ]
            ),
            "assertion_text": (
                normalized[
                    "assertion_text"
                ]
            ),
            "claim_status": (
                normalized[
                    "claim_status"
                ]
            ),
            "temporal_status": (
                normalized[
                    "temporal_status"
                ]
            ),
            "relation_time_text": (
                normalized[
                    "relation_time_text"
                ]
            ),
            "valid_start_at": (
                _json_safe(
                    normalized[
                        "valid_start_at"
                    ]
                )
            ),
            "valid_end_at": (
                _json_safe(
                    normalized[
                        "valid_end_at"
                    ]
                )
            ),
            "temporal_precision": (
                normalized[
                    "temporal_precision"
                ]
            ),
            "evidence_ids": sorted(
                normalized[
                    "evidence_ids"
                ]
            ),
        }

        actual = {
            "relationship_id": (
                _clean(
                    observation.relationship_id
                )
            ),
            "event_id": (
                _clean(
                    observation.event_id
                )
            ),
            "source_id": (
                _clean(
                    observation.source_id
                )
            ),
            "source_mention": (
                _clean(
                    observation.source_mention
                )
            ),
            "target_mention": (
                _clean(
                    observation.target_mention
                )
            ),
            "relation_mention": (
                _clean(
                    observation.relation_mention
                )
            ),
            "assertion_text": (
                _clean(
                    observation.assertion_text
                )
            ),
            "claim_status": (
                _enum(
                    observation.claim_status
                )
            ),
            "temporal_status": (
                _enum(
                    observation.temporal_status
                )
            ),
            "relation_time_text": (
                _clean(
                    observation.relation_time_text
                )
            ),
            "valid_start_at": (
                _json_safe(
                    observation.valid_start_at
                )
            ),
            "valid_end_at": (
                _json_safe(
                    observation.valid_end_at
                )
            ),
            "temporal_precision": (
                _enum(
                    observation.temporal_precision
                )
            ),
            "evidence_ids": sorted(
                _unique_strings(
                    observation.evidence_ids
                    or []
                )
            ),
        }

        if actual != expected:
            raise RelationshipIdentityConflictError(
                "Existing observation_id is being "
                "reused for different relationship/"
                "event/source/temporal semantics."
            )

    def _delete_orphan_relationships(
        self,
        session: Session,
        relationship_ids: Iterable[
            str
        ],
    ) -> int:
        deleted = 0

        for relationship_id in sorted(
            {
                _clean(
                    item
                )
                for item
                in relationship_ids
                if _clean(
                    item
                )
            }
        ):
            observation_exists = (
                session.execute(
                    select(
                        self.Observation.observation_id
                    ).where(
                        self.Observation.relationship_id
                        == relationship_id
                    )
                )
                .first()
            )

            if observation_exists is not None:
                continue

            relationship = (
                session.execute(
                    select(
                        self.Relationship
                    ).where(
                        self.Relationship.relationship_id
                        == relationship_id
                    )
                )
                .scalar_one_or_none()
            )

            if relationship is not None:
                session.delete(
                    relationship
                )

                deleted += 1

        return deleted

    def _require_entities(
        self,
        session: Session,
        entity_ids: Iterable[
            str
        ],
    ) -> None:
        entity_ids = {
            self._validate_entity_id(
                item
            )
            for item
            in entity_ids
            if _clean(
                item
            )
        }

        if not entity_ids:
            return

        if not self.require_known_entities:
            return

        if self.Entity is None:
            raise RelationshipModelContractError(
                "Entity model is required when "
                "require_known_entities=True."
            )

        rows = (
            session.execute(
                select(
                    self.Entity.entity_id
                ).where(
                    self.Entity.entity_id.in_(
                        sorted(
                            entity_ids
                        )
                    )
                )
            )
            .all()
        )

        found = {
            _clean(
                row[
                    0
                ]
            )
            for row
            in rows
        }

        missing = (
            entity_ids
            - found
        )

        if missing:
            raise RelationshipValidationError(
                "Unknown canonical entity_id(s): "
                + ", ".join(
                    sorted(
                        missing
                    )
                )
                + ". EntityResolutionService/"
                "EntityService must run first."
            )

    def _require_event_source_pair(
        self,
        session: Session,
        *,
        event_id: str,
        source_id: str,
    ) -> None:
        if not self.require_known_event:
            return

        if self.Event is None:
            raise RelationshipModelContractError(
                "Event model is required when "
                "require_known_event=True."
            )

        event = (
            session.execute(
                select(
                    self.Event
                ).where(
                    self.Event.event_id
                    == event_id
                )
            )
            .scalar_one_or_none()
        )

        if event is None:
            raise RelationshipValidationError(
                f"Unknown event_id={event_id!r}. "
                "EventService must persist the "
                "Event first."
            )

        actual_source_id = (
            _clean(
                event.source_id
            )
        )

        if (
            actual_source_id
            != source_id
        ):
            raise RelationshipValidationError(
                "RelationshipObservation source "
                "provenance does not match Event "
                "source: "
                f"event_id={event_id!r}, "
                f"expected_source_id="
                f"{actual_source_id!r}, "
                f"supplied_source_id="
                f"{source_id!r}."
            )

    @staticmethod
    def _merge_nonconflicting_mapping(
        existing: Mapping[
            str,
            Any,
        ],
        incoming: Mapping[
            str,
            Any,
        ],
        *,
        field_name: str,
    ) -> dict[
        str,
        Any,
    ]:
        result = deepcopy(
            dict(
                existing
            )
        )

        for (
            key,
            value,
        ) in incoming.items():
            if (
                key in result
                and result[
                    key
                ]
                != value
            ):
                raise RelationshipValidationError(
                    f"Conflicting {field_name} "
                    f"value for key={key!r}. "
                    "Observation-specific values "
                    "belong in "
                    "observation_attributes."
                )

            result[
                key
            ] = deepcopy(
                value
            )

        return result

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _relationship_record(
        relationship: Any,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "relationship_id": (
                _clean(
                    relationship.relationship_id
                )
            ),
            "source_entity_id": (
                _clean(
                    relationship.source_entity_id
                )
            ),
            "relation_type": (
                _normalize_relation_type(
                    relationship.relation_type
                )
            ),
            "target_entity_id": (
                _clean(
                    relationship.target_entity_id
                )
            ),
            "attributes": (
                _json_safe(
                    relationship.attributes
                    or {}
                )
            ),
            "epistemic_status": (
                "unresolved"
            ),
            "created_at": (
                _json_safe(
                    relationship.created_at
                )
            ),
            "updated_at": (
                _json_safe(
                    relationship.updated_at
                )
            ),
        }

    @staticmethod
    def _observation_record(
        observation: Any,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "observation_id": (
                _clean(
                    observation.observation_id
                )
            ),
            "relationship_id": (
                _clean(
                    observation.relationship_id
                )
            ),
            "event_id": (
                _clean(
                    observation.event_id
                )
            ),
            "source_id": (
                _clean(
                    observation.source_id
                )
            ),
            "source_mention": (
                _clean(
                    observation.source_mention
                )
            ),
            "target_mention": (
                _clean(
                    observation.target_mention
                )
            ),
            "relation_mention": (
                _clean(
                    observation.relation_mention
                )
            ),
            "assertion_text": (
                _clean(
                    observation.assertion_text
                )
            ),
            "claim_status": (
                _enum(
                    observation.claim_status
                )
            ),
            "temporal_status": (
                _enum(
                    observation.temporal_status
                )
            ),
            "relation_time_text": (
                _clean(
                    observation.relation_time_text
                )
            ),
            "valid_start_at": (
                _json_safe(
                    observation.valid_start_at
                )
            ),
            "valid_end_at": (
                _json_safe(
                    observation.valid_end_at
                )
            ),
            "temporal_precision": (
                _enum(
                    observation.temporal_precision
                )
            ),
            "evidence_ids": (
                _unique_strings(
                    observation.evidence_ids
                    or []
                )
            ),
            "attributes": (
                _json_safe(
                    observation.attributes
                    or {}
                )
            ),
            "epistemic_status": (
                "unresolved"
            ),
            "created_at": (
                _json_safe(
                    observation.created_at
                )
            ),
            "updated_at": (
                _json_safe(
                    observation.updated_at
                )
            ),
        }


# Class-name compatibility alias only.
# This does not preserve an obsolete data contract.
CanonicalRelationshipService = (
    RelationshipService
)


__all__ = [
    "SERVICE_VERSION",
    "OUTPUT_SCHEMA_VERSION",
    "ALLOWED_RELATION_TYPES",
    "SYMMETRIC_RELATION_TYPES",
    "ALLOWED_CLAIM_STATUSES",
    "ALLOWED_TEMPORAL_STATUSES",
    "ALLOWED_TEMPORAL_PRECISIONS",
    "RELATIONSHIP_REQUIRED_FIELDS",
    "OBSERVATION_REQUIRED_FIELDS",
    "RelationshipServiceError",
    "RelationshipValidationError",
    "RelationshipModelContractError",
    "RelationshipIdentityConflictError",
    "RelationshipObservationWriteResult",
    "EventRelationshipReplacementResult",
    "RelationshipService",
    "CanonicalRelationshipService",
]