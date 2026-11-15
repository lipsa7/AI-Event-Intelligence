from sqlalchemy import JSON, DateTime, ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from services.entity_service import EntityService
from services.entity_resolution_service import EntityResolutionService
from services.relationship_service import RelationshipService
from services.graph_service import GraphService


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    normalized_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
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
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class Relationship(Base):
    __tablename__ = "relationships"

    relationship_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    source_entity_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    target_entity_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    valid_from: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
    )
    valid_to: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
    )
    attributes: Mapped[dict] = mapped_column(
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


class RelationshipObservation(Base):
    __tablename__ = "relationship_observations"

    observation_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    relationship_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("relationships.relationship_id"),
        nullable=False,
    )
    event_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    source_mention: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
    )
    target_mention: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
    )
    claim_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    temporal_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    evidence_ids: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    attributes: Mapped[dict] = mapped_column(
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


def _services():
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

    entity_service = EntityService(
        session_factory=SessionLocal,
        entity_model=Entity,
    )

    relationship_service = RelationshipService(
        session_factory=SessionLocal,
        relationship_model=Relationship,
        observation_model=RelationshipObservation,
        entity_model=Entity,
    )

    return entity_service, relationship_service


def test_entity_registry_and_resolution():
    entity_service, _ = _services()

    a = entity_service.upsert_entity(
        name="Alpha Corporation",
        entity_type="company",
        aliases=["Alpha Corp"],
    )

    resolver = EntityResolutionService(
        registry_provider=lambda entity_type="": (
            entity_service.registry_records(
                entity_type=entity_type
            )
        ),
        candidate_provider=lambda **kwargs: (
            entity_service.search_candidates(
                **kwargs
            )
        ),
    )

    resolved = resolver.resolve(
        mention="Alpha Corp",
        entity_type="company",
    )

    assert resolved.canonical_entity_id == a.entity_id
    assert resolved.method in {
        "alias_exact",
        "deterministic_candidate",
        "exact_name",
    }


def test_relationship_observations_preserve_provenance():
    entity_service, relationship_service = _services()

    a = entity_service.upsert_entity(
        name="Alpha",
        entity_type="company",
    )
    b = entity_service.upsert_entity(
        name="Beta",
        entity_type="company",
    )

    first = relationship_service.upsert_observation(
        source_entity_id=a.entity_id,
        relation_type="SUPPLIES",
        target_entity_id=b.entity_id,
        event_id="EVENT-1",
        source_id="SOURCE-1",
        evidence_ids=["FACT-1"],
        claim_status="directly_stated",
        temporal_status="current",
    )

    second = relationship_service.upsert_observation(
        source_entity_id=a.entity_id,
        relation_type="SUPPLIES",
        target_entity_id=b.entity_id,
        event_id="EVENT-2",
        source_id="SOURCE-2",
        evidence_ids=["FACT-2"],
        claim_status="reported_claim",
        temporal_status="current",
    )

    assert first.relationship_id == second.relationship_id

    relationship = relationship_service.get_relationship(
        first.relationship_id
    )

    assert len(relationship["observations"]) == 2
    assert relationship["epistemic_status"] == "unresolved"


def test_symmetric_relationship_identity_is_canonical():
    entity_service, relationship_service = _services()

    a = entity_service.upsert_entity(
        name="Alpha",
        entity_type="company",
    )
    b = entity_service.upsert_entity(
        name="Beta",
        entity_type="company",
    )

    left = relationship_service.upsert_observation(
        source_entity_id=a.entity_id,
        relation_type="PARTNERS_WITH",
        target_entity_id=b.entity_id,
        event_id="EVENT-1",
        source_id="SOURCE-1",
        evidence_ids=["FACT-1"],
    )

    right = relationship_service.upsert_observation(
        source_entity_id=b.entity_id,
        relation_type="PARTNERS_WITH",
        target_entity_id=a.entity_id,
        event_id="EVENT-2",
        source_id="SOURCE-2",
        evidence_ids=["FACT-2"],
    )

    assert left.relationship_id == right.relationship_id


def test_graph_projection_is_rebuildable(tmp_path):
    entity_service, relationship_service = _services()

    a = entity_service.upsert_entity(
        name="Alpha",
        entity_type="company",
    )
    b = entity_service.upsert_entity(
        name="Beta",
        entity_type="company",
    )

    relationship = relationship_service.upsert_observation(
        source_entity_id=a.entity_id,
        relation_type="SUPPLIES",
        target_entity_id=b.entity_id,
        event_id="EVENT-1",
        source_id="SOURCE-1",
        evidence_ids=["FACT-1"],
    )

    graph = GraphService(
        storage_dir=str(tmp_path),
        auto_load=False,
    )

    graph.upsert_entity(
        entity_id=a.entity_id,
        name="Alpha",
        entity_type="company",
    )
    graph.upsert_entity(
        entity_id=b.entity_id,
        name="Beta",
        entity_type="company",
    )

    result = relationship_service.project_to_graph(
        graph,
        relationship_ids=[
            relationship.relationship_id
        ],
    )

    assert result["database_is_source_of_truth"] is True
    assert (
        graph.get_relationship(
            relationship.relationship_id
        )
        is not None
    )