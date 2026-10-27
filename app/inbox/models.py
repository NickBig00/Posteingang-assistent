"""Data model of the inbox module (SPEC section 6)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import JSON, CheckConstraint, Column
from sqlmodel import Field, SQLModel

from app.core.approval import ReviewDecision
from app.core.models import timestamp_column, utcnow


class ItemStatus(StrEnum):
    NEW = "new"
    PROCESSED = "processed"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"


# German labels for the user interface; the enum values stay technical identifiers.
STATUS_LABELS: dict[ItemStatus, str] = {
    ItemStatus.NEW: "Neu",
    ItemStatus.PROCESSED: "Verarbeitet",
    ItemStatus.NEEDS_MANUAL_REVIEW: "Manuelle Prüfung",
    ItemStatus.APPROVED: "Freigegeben",
    ItemStatus.REJECTED: "Verworfen",
    ItemStatus.DONE: "Erledigt",
}


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


class InboxItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: ItemSource
    received_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
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


class AdditionalRequest(SQLModel, table=True):
    """Further request found in the document that the profile fields and reply template do
    not cover (AI result). Shown to the reviewer as a hint; the draft does not answer it."""

    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    topic: str
    # Transparency requirement: produced by the extraction model.
    is_ai_suggestion: bool = True


class RuleResult(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="inboxitem.id", index=True)
    rule_name: str
    outcome: RuleOutcome
    reason: str
    # Deterministically computed values, e.g. {"vertragsende": "2026-10-31"}.
    data: dict[str, JsonValue] = Field(default_factory=dict, sa_column=Column(JSON))


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
    reviewed_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
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
