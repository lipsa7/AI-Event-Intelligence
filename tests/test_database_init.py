from __future__ import annotations

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)
from sqlalchemy.pool import (
    StaticPool,
)

import database.init_db as init_db


EXPECTED_ORM_TABLES = {
    "articles",
    "entities",
    "events",
    "relationships",
    "relationship_observations",
}


def _memory_engine():
    """
    Completely isolated database used instead of the
    project's real database/event_intelligence.db.
    """

    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )


def test_initialize_database_registers_exact_current_orm_contract(
    monkeypatch,
) -> None:
    engine = (
        _memory_engine()
    )

    monkeypatch.setattr(
        init_db,
        "engine",
        engine,
    )

    registered = set(
        init_db
        .initialize_database()
    )

    actual = set(
        inspect(
            engine
        ).get_table_names()
    )

    assert (
        registered
        == EXPECTED_ORM_TABLES
    )

    assert (
        actual
        == EXPECTED_ORM_TABLES
    )

    engine.dispose()


def test_non_orm_insight_tables_are_not_registered(
    monkeypatch,
) -> None:
    """
    Insight and InsightRevision are domain dataclasses.
    InsightRegistryService owns their durable lifecycle,
    so they must not silently become shared ORM tables.
    """

    engine = (
        _memory_engine()
    )

    monkeypatch.setattr(
        init_db,
        "engine",
        engine,
    )

    init_db.initialize_database()

    tables = set(
        inspect(
            engine
        ).get_table_names()
    )

    assert (
        "insights"
        not in tables
    )

    assert (
        "insight_revisions"
        not in tables
    )

    engine.dispose()


def test_initialize_database_is_non_destructive(
    monkeypatch,
) -> None:
    """
    create_all() should create missing ORM tables,
    not destroy unrelated existing data.
    """

    engine = (
        _memory_engine()
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE external_marker (
                    id INTEGER PRIMARY KEY
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO external_marker(id)
                VALUES (1)
                """
            )
        )

    monkeypatch.setattr(
        init_db,
        "engine",
        engine,
    )

    init_db.initialize_database()

    with engine.connect() as connection:
        value = (
            connection.execute(
                text(
                    """
                    SELECT id
                    FROM external_marker
                    """
                )
            )
            .scalar_one()
        )

    assert (
        value
        == 1
    )

    engine.dispose()