"""
SQLAlchemy canonical relationship and relationship-observation models.

Architecture
------------

    Event.relationship_mentions
            +
    resolved ENT-* entities
            +
    admitted evidence
            ↓
    RelationshipService
            ↓
    Relationship
            +
    RelationshipObservation
            ↓
    GraphService

Why two tables?
---------------

Relationship:
    Canonical semantic graph edge.

RelationshipObservation:
    One source/event observation supporting, qualifying, contradicting,
    or otherwise describing that semantic edge.

Example:

    Alpha SUPPLIES Beta
            ↓
          REL-123

may have:

    RELOBS-1 -> Event A / Source A / Evidence A
    RELOBS-2 -> Event B / Source B / Evidence B

This allows one stale/re-extracted observation to be removed without
destroying independent provenance attached to the same canonical edge.

Critical epistemic boundaries
-----------------------------

Relationship != grounded fact

RelationshipObservation != independent corroboration

claim_status != grounding confidence

evidence_ids must refer only to evidence records already admitted by the
evidence pipeline. Raw source snippets or Event.relationship_mentions are
not automatically evidence.

RelationshipService owns:
- controlled relationship vocabulary;
- symmetric-edge canonicalization;
- REL-* identity generation;
- RELOBS-* identity generation;
- event/source consistency validation;
- entity-resolution requirements;
- observation evidence requirements.

GroundingService owns final support assessment.
SourceProvenanceService owns source-independence assessment.
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
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from services.database_service import Base

# Register ForeignKey target tables with shared Base.metadata.
from models.article import Article  # noqa: F401
from models.entity import Entity  # noqa: F401
from models.event import Event  # noqa: F401


def _utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


class Relationship(Base):
    """
    Canonical semantic relationship between two canonical entities.

    Canonical semantic identity:

        source_entity_id
        +
        relation_type
        +
        target_entity_id

    Example:

        ENT-ALPHA
            SUPPLIES
        ENT-BETA

    Temporal assertions, source wording, event provenance, evidence,
    and observation-specific attributes belong in RelationshipObservation.

    This means repeated observations of the same semantic relationship
    do not create unnecessary duplicate graph edges.

    For symmetric relationship types such as PARTNERS_WITH, the
    RelationshipService must canonicalize entity ordering before
    persistence.
    """

    __tablename__ = "relationships"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    relationship_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    source_entity_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "entities.entity_id",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        nullable=False,
    )

    relation_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    target_entity_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "entities.entity_id",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Canonical edge metadata
    # ------------------------------------------------------------------

    # Relationship-level metadata only.
    #
    # Appropriate examples:
    #   canonicalization version
    #   relation vocabulary version
    #   graph classification metadata
    #
    # Do NOT place:
    #   grounding confidence
    #   source provenance
    #   observation-specific quantities
    #   private reasoning
    # here.
    attributes: Mapped[dict] = mapped_column(
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
    # ORM linkage
    # ------------------------------------------------------------------

    observations: Mapped[
        list["RelationshipObservation"]
    ] = relationship(
        back_populates="relationship_record",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ------------------------------------------------------------------
    # Constraints / indexes
    # ------------------------------------------------------------------

    __table_args__ = (
        # The current controlled relationship vocabulary represents
        # edges between distinct canonical entities.
        CheckConstraint(
            (
                "source_entity_id "
                "<> target_entity_id"
            ),
            name=(
                "ck_relationships_"
                "no_self_edge"
            ),
        ),

        # A canonical graph edge is unique by resolved endpoints
        # and normalized relation type.
        #
        # Individual investments, supply contracts, partnerships,
        # announcements, etc. remain separate observations rather
        # than separate canonical graph edges.
        UniqueConstraint(
            "source_entity_id",
            "relation_type",
            "target_entity_id",
            name=(
                "uq_relationships_"
                "semantic_edge"
            ),
        ),

        Index(
            "ix_relationships_source_entity",
            "source_entity_id",
        ),

        Index(
            "ix_relationships_target_entity",
            "target_entity_id",
        ),

        Index(
            "ix_relationships_relation_type",
            "relation_type",
        ),

        Index(
            "ix_relationships_source_relation",
            "source_entity_id",
            "relation_type",
        ),

        Index(
            "ix_relationships_relation_target",
            "relation_type",
            "target_entity_id",
        ),
    )

    def __repr__(
        self,
    ) -> str:
        return (
            "Relationship("
            f"relationship_id={self.relationship_id!r}, "
            f"source_entity_id={self.source_entity_id!r}, "
            f"relation_type={self.relation_type!r}, "
            f"target_entity_id={self.target_entity_id!r}"
            ")"
        )


class RelationshipObservation(Base):
    """
    One source/event observation of a canonical relationship.

    This is the provenance-bearing record.

    It preserves:
    - originating Event;
    - originating Article/Source;
    - original source/target mentions;
    - extracted relationship wording/assertion;
    - source-level claim status;
    - relationship-specific temporal information;
    - admitted evidence references;
    - observation-specific qualifiers.

    observation_id is generated deterministically by RelationshipService.

    Important:
        evidence_ids are references to admitted evidence records.

    They are NOT:
        arbitrary snippets,
        search results,
        source IDs,
        Event.relationship_mentions.

    Source independence is deliberately not stored here. Two observations
    may still originate from the same underlying press release, wire story,
    or syndicated source. SourceProvenanceService resolves that distinction.
    """

    __tablename__ = "relationship_observations"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    observation_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    relationship_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "relationships.relationship_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    event_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "events.event_id",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        nullable=False,
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

    # Original entity wording extracted from the source.
    source_mention: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
    )

    target_mention: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
    )

    # Original relationship wording where available.
    #
    # Example:
    #   "signed a long-term supply agreement with"
    #
    # This preserves source semantics even after normalization to:
    #   SUPPLIES
    relation_mention: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
    )

    # Concise extracted assertion associated with this observation.
    #
    # Example:
    #   "Alpha signed a five-year supply agreement with Beta."
    #
    # This is useful for provenance/debugging but does not itself become
    # grounding evidence merely by being stored here.
    assertion_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    # ------------------------------------------------------------------
    # Source-level epistemic description
    # ------------------------------------------------------------------

    # Describes how the source expressed the relationship assertion.
    #
    # Examples:
    #   directly_stated
    #   reported_claim
    #   inferred_from_source
    #   unknown
    #
    # This is NOT grounding status or confidence.
    claim_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unknown",
    )

    # ------------------------------------------------------------------
    # Temporal representation
    # ------------------------------------------------------------------

    # Relationship-specific temporal state.
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

    # Original relationship-specific temporal expression.
    #
    # Examples:
    #   "for five years"
    #   "through 2030"
    #   "beginning next quarter"
    #   "since 2024"
    #
    # This may differ from Event.event_time_text.
    relation_time_text: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
    )

    # Normalized lower validity boundary where defensible.
    valid_start_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    # Normalized upper validity boundary where defensible.
    valid_end_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    # Precision of the relationship-specific temporal interpretation.
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
    temporal_precision: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="unresolved",
    )

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    # IDs of evidence records already admitted by EvidenceBuilderAgent /
    # evidence-admission pipeline.
    #
    # Evidence is intentionally not represented as an ORM FK here because
    # evidence persistence is owned by the evidence subsystem rather than
    # this SQLAlchemy model registry.
    evidence_ids: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    # ------------------------------------------------------------------
    # Observation-specific qualifiers
    # ------------------------------------------------------------------

    # Appropriate examples:
    #   product supplied
    #   investment amount
    #   ownership percentage
    #   geography
    #   agreement type
    #   extracted qualifiers
    #
    # These describe this observation, not global grounding confidence.
    attributes: Mapped[dict] = mapped_column(
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
    # ORM linkage
    # ------------------------------------------------------------------

    relationship_record: Mapped[
        Relationship
    ] = relationship(
        back_populates="observations",
    )

    # ------------------------------------------------------------------
    # Constraints / indexes
    # ------------------------------------------------------------------

    __table_args__ = (
        CheckConstraint(
            (
                "valid_start_at IS NULL "
                "OR valid_end_at IS NULL "
                "OR valid_end_at >= valid_start_at"
            ),
            name=(
                "ck_relationship_observations_"
                "temporal_bounds"
            ),
        ),

        Index(
            "ix_relationship_observations_relationship",
            "relationship_id",
        ),

        Index(
            "ix_relationship_observations_event",
            "event_id",
        ),

        Index(
            "ix_relationship_observations_source",
            "source_id",
        ),

        Index(
            "ix_relationship_observations_event_source",
            "event_id",
            "source_id",
        ),

        Index(
            "ix_relationship_observations_valid_start",
            "valid_start_at",
        ),

        Index(
            "ix_relationship_observations_valid_end",
            "valid_end_at",
        ),

        Index(
            "ix_relationship_observations_temporal_status",
            "temporal_status",
        ),
    )

    def __repr__(
        self,
    ) -> str:
        return (
            "RelationshipObservation("
            f"observation_id={self.observation_id!r}, "
            f"relationship_id={self.relationship_id!r}, "
            f"event_id={self.event_id!r}, "
            f"source_id={self.source_id!r}"
            ")"
        )


__all__ = [
    "Relationship",
    "RelationshipObservation",
]