"""
Persistent structured Event service.

Recommended location:
    services/event_service.py

Pipeline
--------
ArticleService / SOURCE-*
        ↓
ExtractionAgent
        ↓
0..N structured Event payloads
        ↓
EventService
        ↓
clustering / EvidenceBuilderAgent / entity resolution /
RelationshipService / reasoning context

Responsibilities
----------------
- Persist extraction-stage Event records.
- Preserve Article -> 0..N Event provenance through source_id.
- Upsert by stable EVENT-* identity generated upstream by ExtractionAgent.
- Reject identity changes such as moving an existing EVENT-* to another SOURCE-*.
- Support transactional replacement of all extracted events for one source.
- Query events without hidden default limits.
- Export exact structured payloads for clustering and evidence construction.
- Validate temporal interval consistency before persistence.
- Preserve complete structured fields without arbitrary truncation.

Non-responsibilities
--------------------
- No event extraction.
- No LLM calls.
- No canonical entity resolution.
- No evidence admission.
- No canonical relationship persistence.
- No clustering.
- No ranking or strategic inference.
- No metric/unit conversion.
- No grounding or release authorization.

Epistemic boundary
------------------
Event is structured source-derived context.

    Event != Evidence

Therefore EventService persists source-local mentions/snippets, but it does not
manufacture EVID-* records or treat snippets as grounding evidence.

Canonical Event ORM contract
----------------------------
The Event model must expose:

    event_id: str
    source_id: str
    title: str
    event_type: str
    summary: str
    claim_status: str
    temporal_status: str
    event_time_text: str
    event_start_at: datetime | None
    event_end_at: datetime | None
    temporal_precision: str
    entity_mentions: JSON/list
    quantitative_claims: JSON/list
    relationship_mentions: JSON/list
    snippets: JSON/list
    extraction_metadata: JSON/dict
    created_at: datetime
    updated_at: datetime

Identity rule
-------------
EventService does NOT manufacture semantic EVENT-* IDs.

ExtractionAgent owns deterministic event identity from the canonical source and
validated source-local event evidence. EventService only persists that identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session


SERVICE_VERSION = "event-service-v4.0"


REQUIRED_MODEL_FIELDS = {
    "event_id",
    "source_id",
    "title",
    "event_type",
    "summary",
    "claim_status",
    "temporal_status",
    "event_time_text",
    "event_start_at",
    "event_end_at",
    "temporal_precision",
    "entity_mentions",
    "quantitative_claims",
    "relationship_mentions",
    "snippets",
    "extraction_metadata",
    "created_at",
    "updated_at",
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


class EventServiceError(RuntimeError):
    """Base exception for Event persistence."""


class EventValidationError(EventServiceError):
    """Raised when an Event payload violates the service contract."""


class EventModelContractError(EventServiceError):
    """Raised when models.event.Event does not match the canonical contract."""


@dataclass(frozen=True)
class EventWriteResult:
    """Result of inserting or updating one Event."""

    event_id: str
    source_id: str
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceEventReplacementResult:
    """Result of transactionally replacing one source's extracted Event set."""

    source_id: str
    input_event_count: int
    created_count: int
    updated_count: int
    deleted_stale_count: int
    event_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def _json_safe(value: Any) -> Any:
    """
    Convert structured values into JSON-safe deterministic Python values.
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
        raise EventValidationError(
            f"{field_name} must be an object."
        )

    return {
        str(key): _json_safe(item)
        for key, item
        in value.items()
    }


def _json_object_sequence(
    value: Any,
    *,
    field_name: str,
) -> list[dict[str, Any]]:
    """
    Validate a JSON-list field whose members must be objects.
    """

    if value is None:
        return []

    if not isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        raise EventValidationError(
            f"{field_name} must be a list."
        )

    output: list[
        dict[str, Any]
    ] = []

    for index, item in enumerate(
        value
    ):
        if not isinstance(
            item,
            Mapping,
        ):
            raise EventValidationError(
                f"{field_name}[{index}] "
                "must be an object."
            )

        output.append(
            {
                str(key): _json_safe(
                    item_value
                )
                for key, item_value
                in item.items()
            }
        )

    return output


def _json_string_sequence(
    value: Any,
    *,
    field_name: str,
) -> list[str]:
    """
    Validate a JSON-list field whose members are strings.
    """

    if value is None:
        return []

    if not isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        raise EventValidationError(
            f"{field_name} must be a list."
        )

    output: list[str] = []
    seen: set[str] = set()

    for index, item in enumerate(
        value
    ):
        if not isinstance(
            item,
            str,
        ):
            raise EventValidationError(
                f"{field_name}[{index}] "
                "must be a string."
            )

        text = item.strip()

        if not text:
            continue

        if text in seen:
            continue

        seen.add(
            text
        )

        output.append(
            text
        )

    return output


def _parse_datetime(
    value: Any,
    *,
    field_name: str,
) -> datetime | None:
    """
    Normalize datetime-like input to an aware UTC datetime.
    """

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
            raise EventValidationError(
                f"{field_name} must be a valid "
                "ISO-8601 datetime or null."
            ) from exc

    elif isinstance(
        value,
        datetime,
    ):
        parsed = value

    else:
        raise EventValidationError(
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


def _normalize_enum(
    value: Any,
    *,
    allowed: set[str],
    default: str,
    field_name: str,
) -> str:
    text = (
        _clean(value)
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

    if not text:
        return default

    if text not in allowed:
        raise EventValidationError(
            f"{field_name}={text!r} "
            "is not allowed. "
            f"Allowed values: {sorted(allowed)}."
        )

    return text


class EventService:
    """
    Persistent structured-Event service.

    Dependencies can be injected for isolated tests:

        EventService(
            session_factory=SessionLocal,
            event_model=Event,
        )

    Otherwise project defaults are imported lazily.
    """

    def __init__(
        self,
        *,
        session_factory: Any = None,
        event_model: Any = None,
    ) -> None:
        if session_factory is None:
            try:
                from services.database_service import (
                    SessionLocal,
                )

            except ImportError as exc:
                raise EventServiceError(
                    "Could not import SessionLocal from "
                    "services.database_service."
                ) from exc

            session_factory = (
                SessionLocal
            )

        if event_model is None:
            try:
                from models.event import (
                    Event,
                )

            except ImportError as exc:
                raise EventServiceError(
                    "Could not import Event from "
                    "models.event."
                ) from exc

            event_model = Event

        self.session_factory = (
            session_factory
        )

        self.Event = (
            event_model
        )

        self._validate_model_contract()

    # ------------------------------------------------------------------
    # Contract validation
    # ------------------------------------------------------------------

    def _validate_model_contract(
        self,
    ) -> None:
        missing = sorted(
            field
            for field
            in REQUIRED_MODEL_FIELDS
            if not hasattr(
                self.Event,
                field,
            )
        )

        if missing:
            raise EventModelContractError(
                "Event ORM model is missing "
                "required field(s): "
                + ", ".join(
                    missing
                )
            )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_event(
        self,
        *,
        event_id: str,
        source_id: str,
        title: str,
        event_type: str,
        summary: str,
        claim_status: str = "unknown",
        temporal_status: str = "unknown",
        event_time_text: str = "",
        event_start_at: Any = None,
        event_end_at: Any = None,
        temporal_precision: str = (
            "unresolved"
        ),
        entity_mentions: Any = None,
        quantitative_claims: Any = None,
        relationship_mentions: Any = None,
        snippets: Any = None,
        extraction_metadata: (
            Optional[
                Mapping[str, Any]
            ]
        ) = None,
    ) -> EventWriteResult:
        """
        Insert or update one Event by stable EVENT-* identity.

        Existing identity is immutable:

            event_id + source_id

        Structured extraction content may change after
        re-extraction, but an existing EVENT-* may never
        migrate to another SOURCE-*.
        """

        normalized = (
            self._normalize_event_input(
                {
                    "event_id": (
                        event_id
                    ),
                    "source_id": (
                        source_id
                    ),
                    "title": (
                        title
                    ),
                    "event_type": (
                        event_type
                    ),
                    "summary": (
                        summary
                    ),
                    "claim_status": (
                        claim_status
                    ),
                    "temporal_status": (
                        temporal_status
                    ),
                    "event_time_text": (
                        event_time_text
                    ),
                    "event_start_at": (
                        event_start_at
                    ),
                    "event_end_at": (
                        event_end_at
                    ),
                    "temporal_precision": (
                        temporal_precision
                    ),
                    "entity_mentions": (
                        entity_mentions
                    ),
                    "quantitative_claims": (
                        quantitative_claims
                    ),
                    "relationship_mentions": (
                        relationship_mentions
                    ),
                    "snippets": (
                        snippets
                    ),
                    "extraction_metadata": (
                        extraction_metadata
                    ),
                }
            )
        )

        session: Session = (
            self.session_factory()
        )

        try:
            existing = (
                session.execute(
                    select(
                        self.Event
                    ).where(
                        self.Event.event_id
                        == normalized[
                            "event_id"
                        ]
                    )
                )
                .scalar_one_or_none()
            )

            created = (
                existing is None
            )

            now = _utc_now()

            if created:
                entity = self.Event(
                    **normalized,
                    created_at=now,
                    updated_at=now,
                )

                session.add(
                    entity
                )

            else:
                entity = existing

                if (
                    _clean(
                        entity.source_id
                    )
                    != normalized[
                        "source_id"
                    ]
                ):
                    raise EventValidationError(
                        "Cannot move an existing "
                        "EVENT-* to another SOURCE-*: "
                        "event_id="
                        f"{normalized['event_id']!r}."
                    )

                self._apply_mutable_fields(
                    entity,
                    normalized,
                )

                entity.updated_at = (
                    now
                )

            session.commit()

            return EventWriteResult(
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
                created=(
                    created
                ),
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def save_event(
        self,
        event: Mapping[
            str,
            Any,
        ],
    ) -> EventWriteResult:
        """
        Persist one canonical ExtractionAgent Event payload.

        This is the convenient orchestrator-facing
        entry point.

        It intentionally accepts only the final Event
        contract rather than legacy field aliases.
        """

        normalized = (
            self._normalize_event_input(
                event
            )
        )

        return self.upsert_event(
            **normalized
        )

    def save_events(
        self,
        events: Sequence[
            Mapping[
                str,
                Any,
            ]
        ],
    ) -> list[
        EventWriteResult
    ]:
        """
        Persist multiple Event payloads one by one.
        """

        if (
            not isinstance(
                events,
                Sequence,
            )
            or isinstance(
                events,
                (
                    str,
                    bytes,
                ),
            )
        ):
            raise EventValidationError(
                "events must be a "
                "sequence of objects."
            )

        results: list[
            EventWriteResult
        ] = []

        for index, event in enumerate(
            events
        ):
            if not isinstance(
                event,
                Mapping,
            ):
                raise EventValidationError(
                    f"events[{index}] "
                    "must be an object."
                )

            results.append(
                self.save_event(
                    event
                )
            )

        return results

    def replace_events_for_source(
        self,
        *,
        source_id: str,
        events: Sequence[
            Mapping[
                str,
                Any,
            ]
        ],
    ) -> SourceEventReplacementResult:
        """
        Transactionally replace the extracted Event set
        for one SOURCE-*.

        Intended use cases:
        - Article content changed.
        - Extraction prompt/model changed.
        - Extraction bug was corrected.
        - A source now legitimately produces a different
          Event set.

        Behavior:
        - every supplied Event must reference exactly
          source_id;
        - duplicate EVENT-* IDs are rejected;
        - current matching IDs are updated;
        - new IDs are inserted;
        - stale Events from this SOURCE-* are deleted
          in the SAME transaction;
        - an empty Event list is valid and means this
          source currently yields no extractable Event
          records.

        Downstream foreign-key constraints deliberately
        prevent unsafe deletion when persisted records
        still require a stale Event.
        """

        source_id = _clean(
            source_id
        )

        if not source_id:
            raise EventValidationError(
                "source_id cannot be empty."
            )

        if not (
            source_id
            .upper()
            .startswith(
                "SOURCE-"
            )
        ):
            raise EventValidationError(
                "source_id must use canonical "
                "SOURCE-* identity."
            )

        if (
            not isinstance(
                events,
                Sequence,
            )
            or isinstance(
                events,
                (
                    str,
                    bytes,
                ),
            )
        ):
            raise EventValidationError(
                "events must be a "
                "sequence of objects."
            )

        normalized_events: list[
            dict[
                str,
                Any,
            ]
        ] = []

        seen_ids: set[str] = (
            set()
        )

        for index, event in enumerate(
            events
        ):
            if not isinstance(
                event,
                Mapping,
            ):
                raise EventValidationError(
                    f"events[{index}] "
                    "must be an object."
                )

            payload = dict(
                event
            )

            supplied_source_id = (
                _clean(
                    payload.get(
                        "source_id"
                    )
                )
            )

            if (
                supplied_source_id
                != source_id
            ):
                raise EventValidationError(
                    f"events[{index}] references "
                    "source_id="
                    f"{supplied_source_id!r}; "
                    f"expected {source_id!r}."
                )

            normalized = (
                self._normalize_event_input(
                    payload
                )
            )

            event_id = (
                normalized[
                    "event_id"
                ]
            )

            if event_id in seen_ids:
                raise EventValidationError(
                    "Duplicate event_id in "
                    "replacement set: "
                    f"{event_id!r}."
                )

            seen_ids.add(
                event_id
            )

            normalized_events.append(
                normalized
            )

        session: Session = (
            self.session_factory()
        )

        try:
            current = list(
                session.execute(
                    select(
                        self.Event
                    ).where(
                        self.Event.source_id
                        == source_id
                    )
                )
                .scalars()
                .all()
            )

            current_by_id = {
                _clean(
                    item.event_id
                ): item
                for item
                in current
            }

            created_count = 0
            updated_count = 0

            now = _utc_now()

            for normalized in (
                normalized_events
            ):
                event_id = (
                    normalized[
                        "event_id"
                    ]
                )

                entity = (
                    current_by_id.get(
                        event_id
                    )
                )

                if entity is None:
                    entity = self.Event(
                        **normalized,
                        created_at=now,
                        updated_at=now,
                    )

                    session.add(
                        entity
                    )

                    created_count += 1

                else:
                    if (
                        _clean(
                            entity.source_id
                        )
                        != normalized[
                            "source_id"
                        ]
                    ):
                        raise EventValidationError(
                            "Cannot move an existing "
                            "EVENT-* to another SOURCE-*: "
                            f"event_id={event_id!r}."
                        )

                    self._apply_mutable_fields(
                        entity,
                        normalized,
                    )

                    entity.updated_at = (
                        now
                    )

                    updated_count += 1

            stale_ids = (
                set(
                    current_by_id
                )
                - seen_ids
            )

            for stale_id in (
                stale_ids
            ):
                session.delete(
                    current_by_id[
                        stale_id
                    ]
                )

            session.commit()

            return (
                SourceEventReplacementResult(
                    source_id=(
                        source_id
                    ),
                    input_event_count=(
                        len(
                            normalized_events
                        )
                    ),
                    created_count=(
                        created_count
                    ),
                    updated_count=(
                        updated_count
                    ),
                    deleted_stale_count=(
                        len(
                            stale_ids
                        )
                    ),
                    event_ids=(
                        sorted(
                            seen_ids
                        )
                    ),
                )
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def delete_by_event_id(
        self,
        event_id: str,
    ) -> bool:
        event_id = _clean(
            event_id
        )

        if not event_id:
            raise EventValidationError(
                "event_id cannot be empty."
            )

        session: Session = (
            self.session_factory()
        )

        try:
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
                return False

            session.delete(
                event
            )

            session.commit()

            return True

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_event_id(
        self,
        event_id: str,
    ) -> Optional[Any]:
        event_id = _clean(
            event_id
        )

        if not event_id:
            return None

        session: Session = (
            self.session_factory()
        )

        try:
            return (
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

        finally:
            session.close()

    def list_by_source_id(
        self,
        source_id: str,
    ) -> list[Any]:
        source_id = _clean(
            source_id
        )

        if not source_id:
            return []

        session: Session = (
            self.session_factory()
        )

        try:
            return list(
                session.execute(
                    select(
                        self.Event
                    )
                    .where(
                        self.Event.source_id
                        == source_id
                    )
                    .order_by(
                        self.Event.event_id.asc()
                    )
                )
                .scalars()
                .all()
            )

        finally:
            session.close()

    def list_by_source_ids(
        self,
        source_ids: Sequence[
            str
        ],
    ) -> list[Any]:
        """
        Fetch all Events for a caller-supplied SOURCE-*
        set with no hidden cap.
        """

        cleaned_ids = list(
            dict.fromkeys(
                _clean(
                    item
                )
                for item
                in source_ids
                if _clean(
                    item
                )
            )
        )

        if not cleaned_ids:
            return []

        session: Session = (
            self.session_factory()
        )

        try:
            return list(
                session.execute(
                    select(
                        self.Event
                    )
                    .where(
                        self.Event.source_id.in_(
                            cleaned_ids
                        )
                    )
                    .order_by(
                        self.Event.source_id.asc(),
                        self.Event.event_id.asc(),
                    )
                )
                .scalars()
                .all()
            )

        finally:
            session.close()

    def list_events(
        self,
        *,
        source_id: str = "",
        event_type: str = "",
        claim_status: str = "",
        temporal_status: str = "",
        temporal_precision: str = "",
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> list[Any]:
        """
        Query persisted Events.

        No hidden default limit is applied.
        """

        if offset < 0:
            raise EventValidationError(
                "offset must be >= 0."
            )

        if (
            limit is not None
            and limit <= 0
        ):
            raise EventValidationError(
                "limit must be > 0 "
                "when supplied."
            )

        query = select(
            self.Event
        )

        if source_id:
            query = query.where(
                self.Event.source_id
                == _clean(
                    source_id
                )
            )

        if event_type:
            query = query.where(
                self.Event.event_type
                == _clean(
                    event_type
                )
            )

        if claim_status:
            normalized_claim_status = (
                _normalize_enum(
                    claim_status,
                    allowed=(
                        ALLOWED_CLAIM_STATUSES
                    ),
                    default="unknown",
                    field_name=(
                        "claim_status"
                    ),
                )
            )

            query = query.where(
                self.Event.claim_status
                == normalized_claim_status
            )

        if temporal_status:
            normalized_temporal_status = (
                _normalize_enum(
                    temporal_status,
                    allowed=(
                        ALLOWED_TEMPORAL_STATUSES
                    ),
                    default="unknown",
                    field_name=(
                        "temporal_status"
                    ),
                )
            )

            query = query.where(
                self.Event.temporal_status
                == normalized_temporal_status
            )

        if temporal_precision:
            normalized_temporal_precision = (
                _normalize_enum(
                    temporal_precision,
                    allowed=(
                        ALLOWED_TEMPORAL_PRECISIONS
                    ),
                    default="unresolved",
                    field_name=(
                        "temporal_precision"
                    ),
                )
            )

            query = query.where(
                self.Event.temporal_precision
                == normalized_temporal_precision
            )

        query = (
            query
            .order_by(
                self.Event.source_id.asc(),
                self.Event.event_id.asc(),
            )
            .offset(
                offset
            )
        )

        if limit is not None:
            query = query.limit(
                limit
            )

        session: Session = (
            self.session_factory()
        )

        try:
            return list(
                session.execute(
                    query
                )
                .scalars()
                .all()
            )

        finally:
            session.close()

    def count_events(
        self,
        *,
        source_id: str = "",
    ) -> int:
        if source_id:
            return len(
                self.list_by_source_id(
                    source_id
                )
            )

        return len(
            self.list_events()
        )

    # ------------------------------------------------------------------
    # Pipeline payloads
    # ------------------------------------------------------------------

    def to_dict(
        self,
        event: Any,
    ) -> dict[str, Any]:
        """
        Export the complete persisted Event contract.
        """

        return {
            "event_id": (
                event.event_id
            ),

            "source_id": (
                event.source_id
            ),

            "title": (
                event.title
            ),

            "event_type": (
                event.event_type
            ),

            "summary": (
                event.summary
            ),

            "claim_status": (
                event.claim_status
            ),

            "temporal_status": (
                event.temporal_status
            ),

            "event_time_text": (
                event.event_time_text
            ),

            "event_start_at": (
                _json_safe(
                    event.event_start_at
                )
            ),

            "event_end_at": (
                _json_safe(
                    event.event_end_at
                )
            ),

            "temporal_precision": (
                event.temporal_precision
            ),

            "entity_mentions": (
                _json_safe(
                    event.entity_mentions
                    or []
                )
            ),

            "quantitative_claims": (
                _json_safe(
                    event.quantitative_claims
                    or []
                )
            ),

            "relationship_mentions": (
                _json_safe(
                    event.relationship_mentions
                    or []
                )
            ),

            "snippets": (
                _json_safe(
                    event.snippets
                    or []
                )
            ),

            "extraction_metadata": (
                _json_safe(
                    event.extraction_metadata
                    or {}
                )
            ),

            "created_at": (
                _json_safe(
                    event.created_at
                )
            ),

            "updated_at": (
                _json_safe(
                    event.updated_at
                )
            ),
        }

    def to_clustering_payload(
        self,
        event: Any,
    ) -> dict[str, Any]:
        """
        Export Event semantics useful to clustering.

        This service deliberately does not concatenate
        or truncate text or choose an embedding
        representation.

        EventClusterService/EmbeddingService owns the
        tested representation.
        """

        return {
            "event_id": (
                event.event_id
            ),

            "source_id": (
                event.source_id
            ),

            "title": (
                event.title
            ),

            "summary": (
                event.summary
            ),

            "event_type": (
                event.event_type
            ),

            "claim_status": (
                event.claim_status
            ),

            "temporal_status": (
                event.temporal_status
            ),

            "event_time_text": (
                event.event_time_text
            ),

            "event_start_at": (
                _json_safe(
                    event.event_start_at
                )
            ),

            "event_end_at": (
                _json_safe(
                    event.event_end_at
                )
            ),

            "temporal_precision": (
                event.temporal_precision
            ),

            "entity_mentions": (
                _json_safe(
                    event.entity_mentions
                    or []
                )
            ),

            "quantitative_claims": (
                _json_safe(
                    event.quantitative_claims
                    or []
                )
            ),
        }

    def to_evidence_payload(
        self,
        event: Any,
    ) -> dict[str, Any]:
        """
        Export the full Event handoff for
        EvidenceBuilderAgent.

        The name means "payload for the evidence-building
        stage".

        It does NOT mean the Event is already evidence.

        No Event field is promoted to EVID-* here.
        """

        return {
            "event_id": (
                event.event_id
            ),

            "source_id": (
                event.source_id
            ),

            "title": (
                event.title
            ),

            "event_type": (
                event.event_type
            ),

            "summary": (
                event.summary
            ),

            "claim_status": (
                event.claim_status
            ),

            "temporal_status": (
                event.temporal_status
            ),

            "event_time_text": (
                event.event_time_text
            ),

            "event_start_at": (
                _json_safe(
                    event.event_start_at
                )
            ),

            "event_end_at": (
                _json_safe(
                    event.event_end_at
                )
            ),

            "temporal_precision": (
                event.temporal_precision
            ),

            "entity_mentions": (
                _json_safe(
                    event.entity_mentions
                    or []
                )
            ),

            "quantitative_claims": (
                _json_safe(
                    event.quantitative_claims
                    or []
                )
            ),

            "relationship_mentions": (
                _json_safe(
                    event.relationship_mentions
                    or []
                )
            ),

            "snippets": (
                _json_safe(
                    event.snippets
                    or []
                )
            ),

            "extraction_metadata": (
                _json_safe(
                    event.extraction_metadata
                    or {}
                )
            ),
        }

    def export_events(
        self,
        events: Sequence[Any],
    ) -> list[
        dict[str, Any]
    ]:
        """
        Export complete Event dictionaries in
        caller-supplied order.
        """

        return [
            self.to_dict(
                event
            )
            for event in events
        ]

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mutable_field_names(
    ) -> tuple[str, ...]:
        return (
            "title",
            "event_type",
            "summary",
            "claim_status",
            "temporal_status",
            "event_time_text",
            "event_start_at",
            "event_end_at",
            "temporal_precision",
            "entity_mentions",
            "quantitative_claims",
            "relationship_mentions",
            "snippets",
            "extraction_metadata",
        )

    def _apply_mutable_fields(
        self,
        entity: Any,
        normalized: Mapping[
            str,
            Any,
        ],
    ) -> None:
        for field in (
            self._mutable_field_names()
        ):
            setattr(
                entity,
                field,
                normalized[
                    field
                ],
            )

    # ------------------------------------------------------------------
    # Input normalization
    # ------------------------------------------------------------------

    def _normalize_event_input(
        self,
        event: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:
        """
        Validate the canonical:

            ExtractionAgent
                ↓
            EventService

        payload.

        Legacy aliases such as:

            event_title
            event_summary
            event_date
            evidence
            entities
            metrics
            relationships

        are intentionally NOT accepted here.

        The architecture now has one canonical Event
        contract. Compatibility belongs at an explicit
        migration boundary rather than normal
        persistence.
        """

        if not isinstance(
            event,
            Mapping,
        ):
            raise EventValidationError(
                "event must be an object."
            )

        event_id = _clean(
            event.get(
                "event_id"
            )
        )

        source_id = _clean(
            event.get(
                "source_id"
            )
        )

        title = _clean(
            event.get(
                "title"
            )
        )

        event_type = _clean(
            event.get(
                "event_type"
            )
        )

        summary = _clean(
            event.get(
                "summary"
            )
        )

        if not event_id:
            raise EventValidationError(
                "event_id is required."
            )

        if not (
            event_id
            .upper()
            .startswith(
                "EVENT-"
            )
        ):
            raise EventValidationError(
                "event_id must use canonical "
                "EVENT-* identity."
            )

        if not source_id:
            raise EventValidationError(
                f"Event {event_id!r} "
                "is missing source_id."
            )

        if not (
            source_id
            .upper()
            .startswith(
                "SOURCE-"
            )
        ):
            raise EventValidationError(
                "source_id must use canonical "
                "SOURCE-* identity."
            )

        if not title:
            raise EventValidationError(
                f"Event {event_id!r} "
                "is missing title."
            )

        if not event_type:
            raise EventValidationError(
                f"Event {event_id!r} "
                "is missing event_type."
            )

        if not summary:
            raise EventValidationError(
                f"Event {event_id!r} "
                "is missing summary."
            )

        claim_status = (
            _normalize_enum(
                event.get(
                    "claim_status"
                ),
                allowed=(
                    ALLOWED_CLAIM_STATUSES
                ),
                default="unknown",
                field_name=(
                    "claim_status"
                ),
            )
        )

        temporal_status = (
            _normalize_enum(
                event.get(
                    "temporal_status"
                ),
                allowed=(
                    ALLOWED_TEMPORAL_STATUSES
                ),
                default="unknown",
                field_name=(
                    "temporal_status"
                ),
            )
        )

        event_time_text = _clean(
            event.get(
                "event_time_text"
            )
        )

        event_start_at = (
            _parse_datetime(
                event.get(
                    "event_start_at"
                ),
                field_name=(
                    "event_start_at"
                ),
            )
        )

        event_end_at = (
            _parse_datetime(
                event.get(
                    "event_end_at"
                ),
                field_name=(
                    "event_end_at"
                ),
            )
        )

        temporal_precision = (
            _normalize_enum(
                event.get(
                    "temporal_precision"
                ),
                allowed=(
                    ALLOWED_TEMPORAL_PRECISIONS
                ),
                default="unresolved",
                field_name=(
                    "temporal_precision"
                ),
            )
        )

        if (
            event_start_at
            is not None
            and event_end_at
            is not None
            and event_end_at
            < event_start_at
        ):
            raise EventValidationError(
                "event_end_at cannot precede "
                "event_start_at."
            )

        # Normalized temporal bounds must have a
        # source temporal expression. Otherwise they
        # become temporal claims without source-local
        # provenance.
        if (
            not event_time_text
            and (
                event_start_at
                is not None
                or event_end_at
                is not None
            )
        ):
            raise EventValidationError(
                "event_start_at/event_end_at "
                "require event_time_text provenance."
            )

        if (
            temporal_precision
            != "unresolved"
            and not event_time_text
        ):
            raise EventValidationError(
                "Resolved temporal_precision "
                "requires event_time_text."
            )

        entity_mentions = (
            _json_object_sequence(
                event.get(
                    "entity_mentions"
                ),
                field_name=(
                    "entity_mentions"
                ),
            )
        )

        quantitative_claims = (
            _json_object_sequence(
                event.get(
                    "quantitative_claims"
                ),
                field_name=(
                    "quantitative_claims"
                ),
            )
        )

        relationship_mentions = (
            _json_object_sequence(
                event.get(
                    "relationship_mentions"
                ),
                field_name=(
                    "relationship_mentions"
                ),
            )
        )

        snippets = (
            _json_string_sequence(
                event.get(
                    "snippets"
                ),
                field_name=(
                    "snippets"
                ),
            )
        )

        extraction_metadata = (
            _json_mapping(
                event.get(
                    "extraction_metadata"
                ),
                field_name=(
                    "extraction_metadata"
                ),
            )
        )

        return {
            "event_id": (
                event_id
            ),

            "source_id": (
                source_id
            ),

            "title": (
                title
            ),

            "event_type": (
                event_type
            ),

            "summary": (
                summary
            ),

            "claim_status": (
                claim_status
            ),

            "temporal_status": (
                temporal_status
            ),

            "event_time_text": (
                event_time_text
            ),

            "event_start_at": (
                event_start_at
            ),

            "event_end_at": (
                event_end_at
            ),

            "temporal_precision": (
                temporal_precision
            ),

            "entity_mentions": (
                entity_mentions
            ),

            "quantitative_claims": (
                quantitative_claims
            ),

            "relationship_mentions": (
                relationship_mentions
            ),

            "snippets": (
                snippets
            ),

            "extraction_metadata": (
                extraction_metadata
            ),
        }


__all__ = [
    "SERVICE_VERSION",
    "REQUIRED_MODEL_FIELDS",
    "ALLOWED_CLAIM_STATUSES",
    "ALLOWED_TEMPORAL_STATUSES",
    "ALLOWED_TEMPORAL_PRECISIONS",
    "EventServiceError",
    "EventValidationError",
    "EventModelContractError",
    "EventWriteResult",
    "SourceEventReplacementResult",
    "EventService",
]