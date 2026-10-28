"""Database engine, schema creation and session handling."""

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import DDL, Engine, event
from sqlalchemy.engine import Connection
from sqlmodel import Session, SQLModel, create_engine

from app.core import members as _members  # noqa: F401 - registers the shared member table
from app.core.models import AuditEntry

DEFAULT_DATABASE_URL = "sqlite:///./data/posteingang.db"

# Database-level guard: also blocks raw SQL and bulk statements that bypass the ORM.
_AUDIT_TABLE = AuditEntry.__tablename__
_SQLITE_AUDIT_TRIGGERS = [
    DDL(
        f"CREATE TRIGGER IF NOT EXISTS {_AUDIT_TABLE}_no_update BEFORE UPDATE ON {_AUDIT_TABLE} "
        "BEGIN SELECT RAISE(ABORT, 'audit entries are immutable'); END"
    ),
    DDL(
        f"CREATE TRIGGER IF NOT EXISTS {_AUDIT_TABLE}_no_delete BEFORE DELETE ON {_AUDIT_TABLE} "
        "BEGIN SELECT RAISE(ABORT, 'audit entries are immutable'); END"
    ),
]


def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str | None = None) -> Engine:
    """Create an engine for `url` (default: env `DATABASE_URL` or local SQLite file)."""
    url = url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        db_path = url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def init_db(engine: Engine) -> None:
    """Create all tables and the audit immutability triggers."""
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            _create_audit_triggers(connection)


def _create_audit_triggers(connection: Connection) -> None:
    for ddl in _SQLITE_AUDIT_TRIGGERS:
        connection.execute(ddl)


engine = make_engine()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""
    with Session(engine) as session:
        yield session
