from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from collections.abc import Generator

from services.database_service import Base
from models.article import Article
from models.entity import Entity
from models.event import Event

# Ensure all FK target tables that may already be registered
# on the shared Base are loaded.
from models.relationship import (  # noqa: F401
    Relationship,
    RelationshipObservation,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """
    Fresh isolated SQLite database for every model test.

    Foreign-key enforcement is explicitly enabled because
    SQLite does not enable it by default.
    """

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    @sqlalchemy_event.listens_for(
        engine,
        "connect",
    )
    def _enable_sqlite_foreign_keys(
        dbapi_connection,
        _connection_record,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(
            "PRAGMA foreign_keys=ON"
        )
        cursor.close()

    Base.metadata.create_all(
        bind=engine
    )

    with Session(engine) as session:
        yield session

    Base.metadata.drop_all(
        bind=engine
    )

    engine.dispose()


def _article(
    source_id: str,
    *,
    canonical_url: str = "",
) -> Article:
    return Article(
        source_id=source_id,
        url=canonical_url,
        canonical_url=canonical_url,
        title=f"Article {source_id}",
        content="Full source content.",
        source_name="Test Source",
        content_hash=(
            source_id
            .lower()
            .replace("-", "")
            + ("0" * 64)
        )[:64],
    )


# ---------------------------------------------------------------------------
# Article
# ---------------------------------------------------------------------------


def test_article_defaults_and_source_identity(
    db_session: Session,
) -> None:
    article = _article(
        "SOURCE-001"
    )

    db_session.add(
        article
    )
    db_session.flush()

    assert article.id is not None
    assert article.source_id == "SOURCE-001"

    assert article.raw_metadata == {}

    assert (
        article.created_at
        is not None
    )

    assert (
        article.updated_at
        is not None
    )

    assert (
        article.fetched_at
        is not None
    )


def test_article_nonempty_canonical_url_is_unique(
    db_session: Session,
) -> None:
    db_session.add(
        _article(
            "SOURCE-001",
            canonical_url=(
                "https://example.com/a"
            ),
        )
    )

    db_session.flush()

    db_session.add(
        _article(
            "SOURCE-002",
            canonical_url=(
                "https://example.com/a"
            ),
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.flush()


def test_article_empty_canonical_urls_can_repeat(
    db_session: Session,
) -> None:
    """
    Local/non-URL sources are allowed to have an empty
    canonical_url, so the partial unique index must not
    reject multiple empty values.
    """

    db_session.add_all(
        [
            _article(
                "SOURCE-001"
            ),
            _article(
                "SOURCE-002"
            ),
        ]
    )

    db_session.flush()

    rows = (
        db_session.query(
            Article
        )
        .order_by(
            Article.source_id
        )
        .all()
    )

    assert [
        row.source_id
        for row in rows
    ] == [
        "SOURCE-001",
        "SOURCE-002",
    ]


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


def test_entity_same_name_and_type_can_exist_as_distinct_identities(
    db_session: Session,
) -> None:
    """
    Same normalized name + same type is NOT sufficient
    to establish canonical real-world identity.

    EntityResolutionService owns that decision.
    """

    first = Entity(
        entity_id="ENT-001",
        name="Mercury",
        normalized_name="mercury",
        entity_type="company",
        aliases=[
            "Mercury A"
        ],
        attributes={
            "country": "US",
        },
    )

    second = Entity(
        entity_id="ENT-002",
        name="Mercury",
        normalized_name="mercury",
        entity_type="company",
        aliases=[
            "Mercury B"
        ],
        attributes={
            "country": "UK",
        },
    )

    db_session.add_all(
        [
            first,
            second,
        ]
    )

    db_session.flush()

    matches = (
        db_session.query(
            Entity
        )
        .filter(
            Entity.entity_type
            == "company",
            Entity.normalized_name
            == "mercury",
        )
        .all()
    )

    assert {
        item.entity_id
        for item in matches
    } == {
        "ENT-001",
        "ENT-002",
    }


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


def test_event_uses_extraction_stage_field_names() -> None:
    """
    Protect the new Event epistemic boundary.

    Old names such as entities / relationships / evidence
    could imply canonical or grounded objects and must not
    silently return.
    """

    columns = set(
        Event.__table__.columns.keys()
    )

    assert {
        "entity_mentions",
        "quantitative_claims",
        "relationship_mentions",
        "snippets",
        "event_time_text",
        "event_start_at",
        "event_end_at",
        "temporal_precision",
    } <= columns

    assert {
        "evidence",
        "entities",
        "metrics",
        "relationships",
        "event_date",
    }.isdisjoint(
        columns
    )


def test_event_persists_temporal_and_extraction_state(
    db_session: Session,
) -> None:
    article = _article(
        "SOURCE-001"
    )

    db_session.add(
        article
    )

    db_session.flush()

    event = Event(
        event_id="EVENT-001",
        source_id=(
            article.source_id
        ),
        title=(
            "Company announced expansion"
        ),
        event_type="expansion",
        summary=(
            "The company announced "
            "a new expansion."
        ),
        claim_status=(
            "directly_stated"
        ),
        temporal_status="future",
        event_time_text="Q4 2026",
        event_start_at=datetime(
            2026,
            10,
            1,
            tzinfo=timezone.utc,
        ),
        event_end_at=datetime(
            2026,
            12,
            31,
            tzinfo=timezone.utc,
        ),
        temporal_precision="quarter",
        entity_mentions=[
            {
                "mention": (
                    "Example Corp"
                ),
                "entity_type": (
                    "company"
                ),
            }
        ],
        quantitative_claims=[
            {
                "name": (
                    "investment"
                ),
                "value": 5,
                "unit": (
                    "USD billion"
                ),
            }
        ],
        relationship_mentions=[
            {
                "source_mention": (
                    "Example Corp"
                ),
                "relation_type": (
                    "INVESTS_IN"
                ),
                "target_mention": (
                    "Project X"
                ),
            }
        ],
        snippets=[
            (
                "Example Corp will "
                "invest $5 billion."
            )
        ],
        extraction_metadata={
            "schema_version": (
                "test-v1"
            ),
        },
    )

    db_session.add(
        event
    )

    db_session.flush()

    db_session.expire(
        event
    )

    db_session.refresh(
        event
    )

    assert (
        event.source_id
        == "SOURCE-001"
    )

    assert (
        event.entity_mentions[0][
            "mention"
        ]
        == "Example Corp"
    )

    assert (
        event.quantitative_claims[0][
            "value"
        ]
        == 5
    )

    assert (
        event.relationship_mentions[0][
            "relation_type"
        ]
        == "INVESTS_IN"
    )

    assert (
        event.temporal_precision
        == "quarter"
    )


def test_event_requires_existing_article(
    db_session: Session,
) -> None:
    event = Event(
        event_id="EVENT-ORPHAN",
        source_id="SOURCE-MISSING",
        title="Orphan event",
        summary=(
            "This event has no "
            "persisted source."
        ),
    )

    db_session.add(
        event
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.flush()


def test_event_rejects_reversed_temporal_bounds(
    db_session: Session,
) -> None:
    article = _article(
        "SOURCE-001"
    )

    db_session.add(
        article
    )

    db_session.flush()

    event = Event(
        event_id="EVENT-001",
        source_id=(
            article.source_id
        ),
        title=(
            "Invalid temporal interval"
        ),
        summary=(
            "The end is before "
            "the start."
        ),
        event_start_at=datetime(
            2026,
            12,
            31,
            tzinfo=timezone.utc,
        ),
        event_end_at=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
    )

    db_session.add(
        event
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.flush()