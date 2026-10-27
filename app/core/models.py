"""Data model shared by all modules: the immutable audit trail (SPEC section 7)."""

from datetime import UTC, datetime

from pydantic import JsonValue
from sqlalchemy import JSON, Column, DateTime, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Return the current time as timezone-aware UTC datetime."""
    return datetime.now(UTC)


def timestamp_column() -> Column:
    return Column(DateTime(timezone=True), nullable=False)


class AuditEntry(SQLModel, table=True):
    """Immutable record of a single AI processing step.

    `item_id` deliberately has no foreign key: audit entries must outlive the deletion of
    an inbox item (SPEC section 7, deletion concept). Raw input is never stored, only its hash.
    The model output may contain personal data; it lives in the deletable `AuditOutput`
    table, the entry itself keeps only its hash.
    """

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
    item_id: int | None = Field(default=None, index=True)
    step: str
    model: str
    model_params: dict[str, JsonValue] = Field(default_factory=dict, sa_column=Column(JSON))
    prompt_version: str | None = None
    input_hash: str
    output_hash: str | None = None
    duration_ms: int
    error: str | None = None


class AuditOutput(SQLModel, table=True):
    """Raw model output of an audit entry. Deleted together with its inbox item."""

    id: int | None = Field(default=None, primary_key=True)
    audit_entry_id: int = Field(foreign_key="auditentry.id", unique=True, index=True)
    item_id: int | None = Field(default=None, index=True)
    output: str


class AuditEntryImmutableError(RuntimeError):
    """Raised when the application tries to modify or delete an audit entry."""


# ORM-level guard; the database triggers in app.core.db additionally block raw SQL.
@event.listens_for(AuditEntry, "before_update")
def _block_audit_update(_mapper: Mapper, _connection: Connection, target: AuditEntry) -> None:
    raise AuditEntryImmutableError(f"audit entry {target.id} must not be modified")


@event.listens_for(AuditEntry, "before_delete")
def _block_audit_delete(_mapper: Mapper, _connection: Connection, target: AuditEntry) -> None:
    raise AuditEntryImmutableError(f"audit entry {target.id} must not be deleted")
