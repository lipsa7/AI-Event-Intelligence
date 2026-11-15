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
from models.relationship import (
    Relationship,
    RelationshipObservation,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
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
        cursor = (
            dbapi_connection.cursor()
        )

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
) -> Article:
    return Article(
        source_id=source_id,
        url=(
            "https://example.com/"
            + source_id.lower()
        ),
        canonical_url=(
            "https://example.com/"
            + source_id.lower()
        ),
        title=(
            f"Article {source_id}"
        ),
        content="Source content.",
        source_name="Test Source",
        content_hash=(
            source_id
            .lower()
            .replace("-", "")
            + ("0" * 64)
        )[:64],
    )


def _event(
    event_id: str,
    source_id: str,
) -> Event:
    return Event(
        event_id=event_id,
        source_id=source_id,
        title=(
            f"Event {event_id}"
        ),
        event_type=(
            "commercial_agreement"
        ),
        summary=(
            "A relationship was reported."
        ),
    )


def _seed_graph_context(
    db_session: Session,
) -> None:
    articles = [
        _article(
            "SOURCE-001"
        ),
        _article(
            "SOURCE-002"
        ),
    ]

    entities = [
        Entity(
            entity_id="ENT-ALPHA",
            name="Alpha",
            normalized_name="alpha",
            entity_type="company",
        ),
        Entity(
            entity_id="ENT-BETA",
            name="Beta",
            normalized_name="beta",
            entity_type="company",
        ),
    ]

    db_session.add_all(
        articles + entities
    )

    db_session.flush()

    db_session.add_all(
        [
            _event(
                "EVENT-001",
                "SOURCE-001",
            ),
            _event(
                "EVENT-002",
                "SOURCE-002",
            ),
        ]
    )

    db_session.flush()


def _relationship(
    relationship_id: str = (
        "REL-001"
    ),
    *,
    source_entity_id: str = (
        "ENT-ALPHA"
    ),
    target_entity_id: str = (
        "ENT-BETA"
    ),
) -> Relationship:
    return Relationship(
        relationship_id=(
            relationship_id
        ),
        source_entity_id=(
            source_entity_id
        ),
        relation_type="SUPPLIES",
        target_entity_id=(
            target_entity_id
        ),
    )


def test_relationship_identity_has_no_temporal_columns() -> None:
    """
    Temporal validity belongs to individual observations,
    not the canonical graph edge.
    """

    relationship_columns = set(
        Relationship
        .__table__
        .columns
        .keys()
    )

    observation_columns = set(
        RelationshipObservation
        .__table__
        .columns
        .keys()
    )

    assert (
        "valid_from"
        not in relationship_columns
    )

    assert (
        "valid_to"
        not in relationship_columns
    )

    assert {
        "relation_time_text",
        "valid_start_at",
        "valid_end_at",
        "temporal_precision",
    } <= observation_columns


def test_same_canonical_edge_cannot_be_inserted_twice(
    db_session: Session,
) -> None:
    _seed_graph_context(
        db_session
    )

    db_session.add(
        _relationship(
            "REL-001"
        )
    )

    db_session.flush()

    db_session.add(
        _relationship(
            "REL-002"
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.flush()


def test_self_edge_is_rejected(
    db_session: Session,
) -> None:
    _seed_graph_context(
        db_session
    )

    db_session.add(
        _relationship(
            "REL-SELF",
            source_entity_id=(
                "ENT-ALPHA"
            ),
            target_entity_id=(
                "ENT-ALPHA"
            ),
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.flush()


def test_multiple_observations_can_attach_to_one_relationship(
    db_session: Session,
) -> None:
    _seed_graph_context(
        db_session
    )

    relationship = (
        _relationship()
    )

    db_session.add(
        relationship
    )

    db_session.flush()

    first = RelationshipObservation(
        observation_id="RELOBS-001",
        relationship_id=(
            relationship.relationship_id
        ),
        event_id="EVENT-001",
        source_id="SOURCE-001",
        source_mention="Alpha",
        target_mention="Beta",
        relation_mention="supplies",
        assertion_text=(
            "Alpha supplies Beta."
        ),
        claim_status=(
            "directly_stated"
        ),
        temporal_status="current",
        relation_time_text=(
            "during 2026"
        ),
        valid_start_at=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        valid_end_at=datetime(
            2026,
            12,
            31,
            tzinfo=timezone.utc,
        ),
        temporal_precision="year",
        evidence_ids=[
            "EVID-001"
        ],
        attributes={
            "product": "chips",
        },
    )

    second = RelationshipObservation(
        observation_id="RELOBS-002",
        relationship_id=(
            relationship.relationship_id
        ),
        event_id="EVENT-002",
        source_id="SOURCE-002",
        source_mention="Alpha",
        target_mention="Beta",
        relation_mention=(
            "supply agreement"
        ),
        assertion_text=(
            "A second source reported "
            "the supply relationship."
        ),
        claim_status="reported_claim",
        temporal_status="current",
        evidence_ids=[
            "EVID-002"
        ],
    )

    db_session.add_all(
        [
            first,
            second,
        ]
    )

    db_session.commit()

    loaded = db_session.get(
        Relationship,
        relationship.relationship_id,
    )

    assert loaded is not None

    assert (
        len(
            loaded.observations
        )
        == 2
    )

    observation_ids = {
        item.observation_id
        for item
        in loaded.observations
    }

    assert observation_ids == {
        "RELOBS-001",
        "RELOBS-002",
    }


def test_observation_preserves_provenance_and_evidence_ids(
    db_session: Session,
) -> None:
    _seed_graph_context(
        db_session
    )

    relationship = (
        _relationship()
    )

    db_session.add(
        relationship
    )

    db_session.flush()

    observation = (
        RelationshipObservation(
            observation_id=(
                "RELOBS-001"
            ),
            relationship_id=(
                relationship.relationship_id
            ),
            event_id="EVENT-001",
            source_id="SOURCE-001",
            source_mention=(
                "Alpha Corp"
            ),
            target_mention=(
                "Beta Ltd"
            ),
            relation_mention=(
                "signed a supply "
                "agreement with"
            ),
            assertion_text=(
                "Alpha Corp signed a "
                "supply agreement "
                "with Beta Ltd."
            ),
            claim_status=(
                "directly_stated"
            ),
            temporal_status="future",
            relation_time_text=(
                "starting in 2027"
            ),
            temporal_precision="year",
            evidence_ids=[
                "EVID-001",
                "EVID-002",
            ],
            attributes={
                "agreement_type": (
                    "supply"
                ),
            },
        )
    )

    db_session.add(
        observation
    )

    db_session.commit()

    loaded = db_session.get(
        RelationshipObservation,
        "RELOBS-001",
    )

    assert loaded is not None

    assert (
        loaded.event_id
        == "EVENT-001"
    )

    assert (
        loaded.source_id
        == "SOURCE-001"
    )

    assert loaded.evidence_ids == [
        "EVID-001",
        "EVID-002",
    ]

    assert (
        loaded.attributes[
            "agreement_type"
        ]
        == "supply"
    )


def test_observation_rejects_reversed_temporal_bounds(
    db_session: Session,
) -> None:
    _seed_graph_context(
        db_session
    )

    relationship = (
        _relationship()
    )

    db_session.add(
        relationship
    )

    db_session.flush()

    db_session.add(
        RelationshipObservation(
            observation_id=(
                "RELOBS-INVALID"
            ),
            relationship_id=(
                relationship.relationship_id
            ),
            event_id="EVENT-001",
            source_id="SOURCE-001",
            valid_start_at=datetime(
                2028,
                1,
                1,
                tzinfo=timezone.utc,
            ),
            valid_end_at=datetime(
                2027,
                1,
                1,
                tzinfo=timezone.utc,
            ),
        )
    )

    with pytest.raises(
        IntegrityError
    ):
        db_session.flush()