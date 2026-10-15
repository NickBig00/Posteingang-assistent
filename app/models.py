"""Core data model (SPEC section 6)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import JSON, CheckConstraint, Column, DateTime, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Return the current time as timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _timestamp_column() -> Column:
    return Column(DateTime(timezone=True), nullable=False)


class ItemStatus(StrEnum):
    NEW = "new"
    PROCESSED = "processed"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"


class ItemSource(StrEnum):
    UPLOAD = "upload"
    SAMPLE = "sample"
    IMAP = "imap"


class ValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


class RuleOutcome(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class InboxItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: ItemSource
    received_at: datetime = Field(default_factory=utcnow, sa_column=_timestamp_column())
    raw_content_ref: str
    status: ItemStatus = Field(default=ItemStatus.NEW, index=True)
    # Original file name (metadata only; the raw file is stored under its content hash).
    filename: str | None = None
    # SHA-256 of the raw content, used to skip duplicate imports.
    content_hash: str | None = Field(default=None, unique=True, index=True)
    # Normalized plain text (e-mail body, attachments, PDF text); None if not extractable.
    text: str | None = None


class Classification(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_confidence_range"),
    )

    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    document_type: str
    confidence: float


class Extraction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    fields: dict[str, JsonValue] = Field(default_factory=dict, sa_column=Column(JSON))
    validation_status: ValidationStatus


class RuleResult(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    rule_name: str
    outcome: RuleOutcome
    reason: str


class Suggestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    action: str
    draft_text: str | None = None
    # Transparency requirement: every suggestion is flagged as AI-generated.
    is_ai_suggestion: bool = True


class Review(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    decision: ReviewDecision
    edited_text: str | None = None
    reviewed_at: datetime = Field(default_factory=utcnow, sa_column=_timestamp_column())
    reviewer: str


class Member(SQLModel, table=True):
    """Fictitious member with contract data (demo table the rules check against)."""

    id: int | None = Field(default=None, primary_key=True)
    member_number: str = Field(unique=True, index=True)
    name: str
    email: str
    contract_start: date
    minimum_term_months: int = Field(ge=1)
    notice_period_months: int = Field(ge=0)
    renewal_period_months: int = Field(ge=1)
    monthly_fee_eur: Decimal = Field(max_digits=8, decimal_places=2)


class AuditEntry(SQLModel, table=True):
    """Immutable record of a single AI processing step.

    `item_id` deliberately has no foreign key: audit entries must outlive the deletion of
    an inbox item (SPEC section 7, deletion concept). Raw input is never stored, only its hash.
    """

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamp_column())
    item_id: int | None = Field(default=None, index=True)
    step: str
    model: str
    model_params: dict[str, JsonValue] = Field(default_factory=dict, sa_column=Column(JSON))
    prompt_version: str | None = None
    input_hash: str
    output: str | None = None
    duration_ms: int
    error: str | None = None


class AuditEntryImmutableError(RuntimeError):
    """Raised when the application tries to modify or delete an audit entry."""


# ORM-level guard; the database triggers in app.db additionally block raw SQL.
@event.listens_for(AuditEntry, "before_update")
def _block_audit_update(_mapper: Mapper, _connection: Connection, target: AuditEntry) -> None:
    raise AuditEntryImmutableError(f"audit entry {target.id} must not be modified")


@event.listens_for(AuditEntry, "before_delete")
def _block_audit_delete(_mapper: Mapper, _connection: Connection, target: AuditEntry) -> None:
    raise AuditEntryImmutableError(f"audit entry {target.id} must not be deleted")
