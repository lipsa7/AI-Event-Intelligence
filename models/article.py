"""
SQLAlchemy Article model for the event-intelligence source layer.

Article represents a persisted source/document captured by ArticleService.

Important epistemic boundary:
    Article / Source != Evidence

A source becomes grounding-eligible evidence only after the downstream
extraction/evidence-admission pipeline has processed it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
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


class Article(Base):
    """
    Persisted source document handed downstream to ExtractionAgent.

    Stable external identity:
        source_id

    Internal database identity:
        id

    ArticleService owns:
    - URL canonicalization
    - SOURCE-* ID generation
    - content hashing
    - updates / re-fetch handling

    This model only persists the resulting state.
    """

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    source_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )

    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    canonical_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    source_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
    )

    published_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=True,
    )

    fetched_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=_utc_now,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    raw_metadata: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=_utc_now,
    )

    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        # Empty canonical URLs are permitted for non-URL/local sources.
        # Real canonical URLs must remain unique.
        Index(
            "ux_articles_canonical_url_nonempty",
            "canonical_url",
            unique=True,
            sqlite_where=text(
                "canonical_url <> ''"
            ),
            postgresql_where=text(
                "canonical_url <> ''"
            ),
        ),
        Index(
            "ix_articles_content_hash",
            "content_hash",
        ),
        Index(
            "ix_articles_published_at",
            "published_at",
        ),
        Index(
            "ix_articles_source_name",
            "source_name",
        ),
    )

    def __repr__(self) -> str:
        return (
            "Article("
            f"source_id={self.source_id!r}, "
            f"title={self.title!r}"
            ")"
        )