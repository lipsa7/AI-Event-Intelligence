"""
Local database initialization entry point.

Run with:

    python initdb.py

Responsibilities
----------------
- import every SQLAlchemy ORM model so its table is registered with the
  shared Base.metadata;
- create tables that do not already exist;
- report which ORM tables are registered.

This module does NOT:
- perform schema migrations;
- drop or reset existing tables;
- initialize service-owned SQLite stores;
- register non-ORM domain models.

Important
---------
`Base.metadata.create_all()` creates missing tables only.

It does NOT alter an existing table when a model schema changes.

During local development, if the ORM schema has changed and the existing
SQLite database contains an older schema, use a deliberate database reset
or a migration mechanism rather than expecting this script to modify the
existing table.
"""

from __future__ import annotations

from services.database_service import (
    Base,
    engine,
)

# ---------------------------------------------------------------------------
# ORM model registration
# ---------------------------------------------------------------------------
#
# These imports are intentionally required.
#
# Importing the model modules registers their SQLAlchemy tables with the
# shared Base.metadata before create_all() executes.

from models.article import Article  # noqa: F401
from models.entity import Entity  # noqa: F401
from models.event import Event  # noqa: F401
from models.relationship import (  # noqa: F401
    Relationship,
    RelationshipObservation,
)

# Deliberately NOT imported:
#
# models.insight.Insight
# models.insight.InsightRevision
#
# Those are immutable non-ORM domain models. Their durable lifecycle is owned
# by InsightRegistryService rather than Base.metadata.
#
# Report persistence is likewise owned by ReportService and therefore does
# not require a models.report ORM table.


from utils.logger import (
    configure_logging,
    get_logger,
)


logger = get_logger(
    "initdb"
)


def initialize_database() -> tuple[str, ...]:
    """
    Create all currently registered ORM tables that do not already exist.

    Returns
    -------
    tuple[str, ...]
        Registered SQLAlchemy table names after initialization.

    Notes
    -----
    This function is intentionally non-destructive.

    It will never:
    - drop tables;
    - delete data;
    - silently recreate an existing database;
    - attempt schema migration.
    """

    registered_tables = tuple(
        sorted(
            Base.metadata.tables.keys()
        )
    )

    if not registered_tables:
        raise RuntimeError(
            "No ORM tables are registered with Base.metadata. "
            "Check model imports before database initialization."
        )

    logger.info(
        "Initializing ORM database | registered_tables=%s",
        registered_tables,
    )

    Base.metadata.create_all(
        bind=engine
    )

    logger.info(
        "ORM database initialization completed | "
        "registered_tables=%s",
        registered_tables,
    )

    return registered_tables


def main() -> None:
    """CLI entry point for local database initialization."""

    configure_logging()

    tables = initialize_database()

    print(
        "Database initialization completed."
    )

    print(
        "Registered ORM tables:"
    )

    for table_name in tables:
        print(
            f"  - {table_name}"
        )


if __name__ == "__main__":
    main()