from sqlalchemy import (
    JSON,
    DateTime,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)

import pytest

from services.event_service import EventService
from services.event_cluster_service import (
    EventClusterService,
    EventClusterValidationError,
)
from services.cluster_label_service import ClusterLabelService


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    source_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    claim_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    temporal_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    event_date: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
    )
    evidence: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    entities: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    metrics: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    relationships: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    snippets: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    extraction_metadata: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


def _event_service():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        future=True,
    )

    return EventService(
        session_factory=SessionLocal,
        event_model=Event,
    )


def test_event_service_upsert_and_payloads():
    service = _event_service()

    service.upsert_event(
        event_id="EVENT-1",
        source_id="SOURCE-1",
        title="Grid queue expands",
        event_type="infrastructure",
        summary="Interconnection queue expanded.",
        claim_status="reported_claim",
        temporal_status="current",
        evidence={
            "evidence_ids": [
                "FACT-1"
            ]
        },
        entities=[
            "ENT-1"
        ],
        metrics=[],
        relationships=[],
        snippets=[
            "queue expanded"
        ],
    )

    event = service.get_by_event_id(
        "EVENT-1"
    )

    clustering = service.to_clustering_payload(
        event
    )
    evidence = service.to_evidence_payload(
        event
    )

    assert clustering["event_id"] == "EVENT-1"
    assert evidence["source_id"] == "SOURCE-1"


def test_event_clustering_assigns_every_event_once():
    clusterer = EventClusterService(
        similarity_threshold=0.8,
        minimum_cluster_size=2,
    )

    result = clusterer.cluster_events(
        events=[
            {
                "event_id": "E1"
            },
            {
                "event_id": "E2"
            },
            {
                "event_id": "E3"
            },
        ],
        embeddings={
            "E1": [
                1.0,
                0.0,
            ],
            "E2": [
                0.99,
                0.01,
            ],
            "E3": [
                0.0,
                1.0,
            ],
        },
    )

    assert set(
        result["event_to_cluster"]
    ) == {
        "E1",
        "E2",
        "E3",
    }

    assert (
        result["metadata"][
            "input_event_count"
        ]
        == 3
    )


def test_event_clustering_rejects_contradictory_constraints():
    clusterer = EventClusterService(
        similarity_threshold=0.8
    )

    with pytest.raises(
        EventClusterValidationError
    ):
        clusterer.cluster_events(
            events=[
                {
                    "event_id": "E1"
                },
                {
                    "event_id": "E2"
                },
            ],
            embeddings={
                "E1": [
                    1.0,
                    0.0,
                ],
                "E2": [
                    1.0,
                    0.0,
                ],
            },
            must_link=[
                (
                    "E1",
                    "E2",
                )
            ],
            must_not_link=[
                (
                    "E1",
                    "E2",
                )
            ],
        )


def test_cluster_label_service_includes_all_events():
    seen_prompt = {
        "value": ""
    }

    def fake_llm(prompt):
        seen_prompt[
            "value"
        ] = prompt

        return {
            "subtopic": (
                "Grid infrastructure constraints"
            ),
            "cluster_development": (
                "The supplied events concern "
                "grid and power constraints."
            ),
            "coherence_status": "coherent",
            "primary_entities": [],
            "supporting_event_ids": [
                "E1",
                "E2",
            ],
        }

    service = ClusterLabelService(
        llm_callable=fake_llm
    )

    result = service.label_cluster(
        cluster_id="CLUSTER-1",
        topic="AI infrastructure",
        events=[
            {
                "event_id": "E1",
                "title": "Power contract",
            },
            {
                "event_id": "E2",
                "title": "Grid queue",
            },
        ],
    )

    assert result["cluster_id"] == "CLUSTER-1"
    assert (
        result["metadata"][
            "input_event_count"
        ]
        == 2
    )
    assert "E1" in seen_prompt["value"]
    assert "E2" in seen_prompt["value"]