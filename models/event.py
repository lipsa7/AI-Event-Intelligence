"""
SQLAlchemy structured Event model.

Pipeline:

    Article
        ↓
    ExtractionAgent
        ↓
    Event
        ↓
    ├── EntityResolutionService
    ├── RelationshipService
    ├── EvidenceBuilderAgent
    ├── EventClusterService
    └── downstream reasoning

An Event is structured information extracted from one source.

Important epistemic boundaries
------------------------------
Event != Evidence

entity_mentions != canonical Entity records

relationship_mentions != canonical Relationship records

quantitative_claims != validated numeric evidence

claim_status describes how the SOURCE presents the event assertion.
It does not represent grounding confidence, factual truth, or independent
corroboration.

Temporal representation
-----------------------
The original temporal expression is preserved in `event_time_text`.

Where possible, ExtractionAgent / EventService may additionally normalize it
into:

    event_start_at
    event_end_at
    temporal_precision

This allows deterministic filtering, sorting, recency analysis, historical
comparison, and timeline reasoning without discarding the original wording.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from services.database_service import Base

# Register ForeignKey target with the shared SQLAlchemy metadata.
from models.article import Article  # noqa: F401


def _utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


class Event(Base):
    """
    Structured event extracted from one source.

    Stable identity:
        event_id

    Source identity:
        source_id

    EventService owns:
    - EVENT-* identity generation;
    - validation of extraction payloads;
    - temporal normalization;
    - update semantics;
    - prevention of silent source migration.

    This model persists the resulting structured extraction state.

    It does not perform:
    - entity resolution;
    - relationship canonicalization;
    - evidence admission;
    - grounding;
    - source-independence assessment.
    """

    __tablename__ = "events"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    event_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "articles.source_id",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Extracted event description
    # ------------------------------------------------------------------

    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        default="",
    )

    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Source-level epistemic / temporal description
    # ------------------------------------------------------------------

    # Describes how the source presents the event assertion.
    #
    # Examples:
    #   directly_stated
    #   reported_claim
    #   inferred_from_source
    #   unknown
    #
    # This is NOT grounding status or truth confidence.
    claim_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unknown",
    )

    # Describes the extracted event's temporal relationship.
    #
    # Examples:
    #   past
    #   current
    #   ongoing
    #   future
    #   uncertain
    #   unknown
    temporal_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unknown",
    )

    # ------------------------------------------------------------------
    # Temporal representation
    # ------------------------------------------------------------------

    # Original extracted expression.
    #
    # Examples:
    #   "15 August 2026"
    #   "Q3 2026"
    #   "during 2025"
    #   "by 2030"
    #   "over the next three years"
    #
    # The raw expression is retained even when normalized bounds exist.
    event_time_text: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
    )

    # Normalized lower temporal boundary where extraction permits it.
    #
    # Examples:
    #   exact date/time -> same instant or day boundary
    #   Q3 2026        -> 2026-07-01
    #   2026           -> 2026-01-01
    #
    # None means a defensible lower boundary could not be established.
    event_start_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    # Normalized upper temporal boundary where extraction permits it.
    #
    # Examples:
    #   exact date     -> same date boundary
    #   Q3 2026        -> end of Q3
    #   "by 2030"      -> 2030 upper boundary
    #
    # None means a defensible upper boundary could not be established.
    event_end_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    # Precision of the normalized interpretation.
    #
    # Examples:
    #   instant
    #   day
    #   week
    #   month
    #   quarter
    #   year
    #   interval
    #   relative
    #   unresolved
    #
    # EventService should validate the controlled vocabulary.
    temporal_precision: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unresolved",
    )

    # ------------------------------------------------------------------
    # Raw structured extraction
    # ------------------------------------------------------------------

    # Mentions extracted from this source/event.
    #
    # These are NOT canonical ENT-* objects.
    #
    # Example:
    #
    # [
    #     {
    #         "mention": "OpenAI",
    #         "entity_type": "company",
    #         "source_span": "...",
    #     }
    # ]
    #
    # EntityResolutionService subsequently maps these to canonical entities.
    entity_mentions: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    # Quantitative assertions extracted from this event.
    #
    # These are NOT product metrics and are not automatically validated
    # evidence.
    #
    # Example:
    #
    # [
    #     {
    #         "name": "investment",
    #         "value": 5,
    #         "unit": "USD billion",
    #         "time_reference": "2026",
    #         "source_text": "...",
    #     }
    # ]
    #
    # Numeric/temporal validation happens downstream.
    quantitative_claims: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    # Relationship assertions extracted directly from the source.
    #
    # These are NOT canonical REL-* relationships.
    #
    # Example:
    #
    # [
    #     {
    #         "source_mention": "Company A",
    #         "relation_type": "SUPPLIES",
    #         "target_mention": "Company B",
    #     }
    # ]
    #
    # EntityResolutionService + RelationshipService subsequently resolve
    # and persist canonical relationship observations.
    relationship_mentions: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    # Source-local text excerpts associated with this extracted event.
    #
    # A snippet helps downstream evidence construction but is not itself
    # automatically admitted grounding evidence.
    snippets: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    # Extraction-stage metadata.
    #
    # Appropriate examples:
    #   extraction schema version
    #   extraction model ID
    #   prompt version
    #   parser version
    #   extraction run ID
    #   source character offsets
    #
    # Do not store private chain-of-thought or raw hidden reasoning here.
    extraction_metadata: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    # ------------------------------------------------------------------
    # Persistence timestamps
    # ------------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=_utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    # ------------------------------------------------------------------
    # Constraints / indexes
    # ------------------------------------------------------------------

    __table_args__ = (
        # If both normalized bounds exist, the interval must not be reversed.
        CheckConstraint(
            (
                "event_start_at IS NULL "
                "OR event_end_at IS NULL "
                "OR event_end_at >= event_start_at"
            ),
            name=(
                "ck_events_temporal_bounds"
            ),
        ),

        # Source-level retrieval.
        Index(
            "ix_events_source_id",
            "source_id",
        ),

        # Event classification.
        Index(
            "ix_events_event_type",
            "event_type",
        ),

        # Timeline / recency queries.
        Index(
            "ix_events_start_at",
            "event_start_at",
        ),
        Index(
            "ix_events_end_at",
            "event_end_at",
        ),

        # Temporal-state filtering.
        Index(
            "ix_events_temporal_status",
            "temporal_status",
        ),

        # Common source + temporal query.
        Index(
            "ix_events_source_temporal",
            "source_id",
            "temporal_status",
        ),

        # Useful for historical/context queries by event class and time.
        Index(
            "ix_events_type_start_at",
            "event_type",
            "event_start_at",
        ),
    )

    def __repr__(
        self,
    ) -> str:
        return (
            "Event("
            f"event_id={self.event_id!r}, "
            f"source_id={self.source_id!r}, "
            f"title={self.title!r}, "
            f"event_type={self.event_type!r}"
            ")"
        )