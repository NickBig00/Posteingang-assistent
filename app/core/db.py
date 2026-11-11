"""Database engine, schema creation and session handling."""

import os
from collections.abc import Iterator
from enum import Enum
from pathlib import Path

from sqlalchemy import DDL, Column, Engine, event, inspect, text
from sqlalchemy import Enum as SAEnum
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


def _sql_literal(column: Column[object], default: object) -> str | None:
    """SQL literal for a scalar Python default (enums as stored by SQLAlchemy: by name)."""
    if isinstance(default, Enum):
        value = default.name if isinstance(column.type, SAEnum) else str(default.value)
        return "'" + value.replace("'", "''") + "'"
    if isinstance(default, bool):
        return str(int(default))
    if isinstance(default, int | float):
        return str(default)
    if isinstance(default, str):
        return "'" + default.replace("'", "''") + "'"
    return None


def add_missing_columns(engine: Engine) -> list[str]:
    """Add columns that newer versions of the models define to existing tables.

    `create_all` only creates missing tables. This small additive step keeps existing local
    demo databases usable after a model gained a column; it never drops or changes columns.
    Returns the added columns as `table.column`.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                clause = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}"
                literal = _sql_literal(column, getattr(column.default, "arg", None))
                if not column.nullable and literal is not None:
                    clause += f" NOT NULL DEFAULT {literal}"
                connection.execute(text(clause))
                added.append(f"{table.name}.{column.name}")
    return added


def init_db(engine: Engine) -> None:
    """Create all tables, add new columns to existing ones and the audit triggers."""
    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
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
