"""
SQLAlchemy canonical Entity registry model.

Entity is the durable canonical identity used by:

    EntityService
        ↓
    EntityResolutionService
        ↓
    RelationshipService
        ↓
    GraphService

Entity resolution logic does not belong in this model.

Important identity boundary:
    same normalized name + same entity type
    does NOT necessarily mean same real-world entity.

Ambiguous names must remain resolvable as distinct canonical entities.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    String,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from services.database_service import Base


def _utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


class Entity(Base):
    """
    Canonical entity identity.

    Example:

        ENT-...
        OpenAI
        company

    `entity_id` is the canonical identity.

    `normalized_name` is a lookup/resolution aid,
    not a unique real-world identifier.

    Aliases and attributes provide additional
    resolution context but do not independently
    establish entity identity.
    """

    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    normalized_name: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    entity_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    aliases: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    attributes: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

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

    __table_args__ = (
        # Lookup indexes only.
        #
        # Deliberately NOT unique:
        # separate real-world entities may share
        # a normalized name and entity type.
        Index(
            "ix_entities_normalized_name",
            "normalized_name",
        ),
        Index(
            "ix_entities_type_normalized_name",
            "entity_type",
            "normalized_name",
        ),
    )

    def __repr__(self) -> str:
        return (
            "Entity("
            f"entity_id={self.entity_id!r}, "
            f"name={self.name!r}, "
            f"entity_type={self.entity_type!r}"
            ")"
        )