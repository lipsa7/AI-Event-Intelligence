"""
Database infrastructure for the AI Event Intelligence system.

Recommended location:
    services/database_service.py

Responsibilities
----------------
- Define the shared SQLAlchemy Declarative Base.
- Create the database engine.
- Expose the SessionLocal factory used by persistence services.
- Apply SQLite reliability settings.
- Provide a transaction-safe session_scope() helper.
- Provide a lightweight database health check.

Non-responsibilities
--------------------
- No model imports.
- No table creation on import.
- No migrations.
- No domain CRUD.
- No LLM/vector/graph logic.

Database initialization belongs in the project-level `database.py`:

    from services.database_service import Base, engine
    from models.article import Article      # noqa: F401
    from models.event import Event          # noqa: F401
    from models.entity import Entity        # noqa: F401
    from models.relationship import Relationship  # noqa: F401
    from models.report import Report        # noqa: F401
    from models.source_provenance import SourceProvenance  # noqa: F401

    Base.metadata.create_all(bind=engine)

For schema evolution beyond local development, use Alembic rather than
treating create_all() as a migration system.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DATABASE_URL = "sqlite:///data/event_intelligence.db"
DATABASE_URL_ENV = "EVENT_INTELLIGENCE_DATABASE_URL"


class DatabaseServiceError(RuntimeError):
    """Base exception for database infrastructure failures."""


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy ORM models."""


def _database_url() -> str:
    """
    Resolve the database URL.

    The project-specific environment variable is preferred so the application
    does not accidentally inherit an unrelated generic DATABASE_URL.
    """
    return (
        os.getenv(DATABASE_URL_ENV, "").strip()
        or DEFAULT_DATABASE_URL
    )


def _prepare_sqlite_parent(database_url: str) -> None:
    """
    Ensure the parent directory exists for a file-backed SQLite database.

    No table or database schema is created here.
    """
    url = make_url(database_url)

    if url.get_backend_name() != "sqlite":
        return

    database = url.database

    if not database or database == ":memory:":
        return

    path = Path(database)

    if not path.is_absolute():
        path = Path.cwd() / path

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


def build_engine(
    database_url: str | None = None,
    *,
    echo: bool = False,
) -> Engine:
    """
    Build a SQLAlchemy engine with safe local defaults.

    SQLite:
    - check_same_thread=False supports Streamlit / service-layer access.
    - timeout=30 reduces transient "database is locked" failures.
    - foreign keys are enabled on every connection.
    - WAL mode improves local read/write concurrency for file-backed DBs.
    - synchronous=NORMAL is a pragmatic WAL durability/performance balance.

    Other SQL databases receive normal SQLAlchemy defaults.
    """
    resolved_url = (
        database_url.strip()
        if database_url
        else _database_url()
    )

    if not resolved_url:
        raise DatabaseServiceError(
            "Database URL cannot be empty."
        )

    _prepare_sqlite_parent(
        resolved_url
    )

    url = make_url(
        resolved_url
    )

    connect_args: dict[str, Any] = {}

    if url.get_backend_name() == "sqlite":
        connect_args = {
            "check_same_thread": False,
            "timeout": 30,
        }

    engine = create_engine(
        resolved_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if url.get_backend_name() == "sqlite":
        _configure_sqlite(
            engine,
            file_backed=(
                bool(url.database)
                and url.database != ":memory:"
            ),
        )

    return engine


def _configure_sqlite(
    engine: Engine,
    *,
    file_backed: bool,
) -> None:
    """
    Register SQLite connection PRAGMAs once on the engine.
    """

    @event.listens_for(
        engine,
        "connect",
    )
    def _set_sqlite_pragmas(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()

        try:
            cursor.execute(
                "PRAGMA foreign_keys=ON"
            )
            cursor.execute(
                "PRAGMA busy_timeout=30000"
            )

            if file_backed:
                cursor.execute(
                    "PRAGMA journal_mode=WAL"
                )
                cursor.execute(
                    "PRAGMA synchronous=NORMAL"
                )

        finally:
            cursor.close()


def build_session_factory(
    database_engine: Engine,
) -> sessionmaker[Session]:
    """
    Create the shared session factory.

    expire_on_commit=False is intentional because service methods may return
    already-loaded ORM objects after committing/closing a session.
    """
    return sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )


# Shared application engine/session factory.
engine = build_engine()
SessionLocal = build_session_factory(
    engine
)


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> Generator[Session, None, None]:
    """
    Transaction boundary for service code.

    Example
    -------
    with session_scope() as session:
        session.add(record)

    Success:
        commit

    Failure:
        rollback + re-raise

    Always:
        close
    """
    session = session_factory()

    try:
        yield session
        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def health_check(
    database_engine: Engine = engine,
) -> dict[str, Any]:
    """
    Execute a minimal connectivity check without mutating application data.
    """
    try:
        with database_engine.connect() as connection:
            result = connection.execute(
                text("SELECT 1")
            ).scalar_one()

        return {
            "healthy": result == 1,
            "backend": database_engine.url.get_backend_name(),
        }

    except Exception as exc:
        return {
            "healthy": False,
            "backend": database_engine.url.get_backend_name(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def dispose_engine(
    database_engine: Engine = engine,
) -> None:
    """
    Dispose pooled connections.

    Useful in tests and controlled application shutdown.
    """
    database_engine.dispose()