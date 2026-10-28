"""Deletion concept (SPEC section 7): per-item deletion and configurable retention.

Deleting an item removes the raw file and every derived record with content
(text, classification, extracted fields, rule results, suggestion, reviews and the
raw model outputs). Audit entries stay – they only hold hashes and metadata.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, SQLModel, delete, select

from app.core.models import AuditOutput
from app.core.retention import retention_days
from app.inbox.models import (
    AdditionalRequest,
    Classification,
    Extraction,
    InboxItem,
    ItemStatus,
    Review,
    RuleResult,
    Suggestion,
)

logger = logging.getLogger(__name__)

# Only finished items are purged automatically; items still waiting for processing,
# review or execution are kept until a human has dealt with them.
PURGEABLE_STATUSES = frozenset({ItemStatus.DONE, ItemStatus.REJECTED})
_DERIVED: tuple[type[SQLModel], ...] = (
    AuditOutput,
    Suggestion,
    Review,
    RuleResult,
    AdditionalRequest,
    Extraction,
    Classification,
)


@dataclass
class PurgeSummary:
    deleted_item_ids: list[int]
    cutoff: datetime


def delete_item(session: Session, item_id: int) -> bool:
    """Delete one inbox item with all content. Returns False if it does not exist."""
    item = session.get(InboxItem, item_id)
    if item is None:
        return False
    for model in _DERIVED:
        session.exec(delete(model).where(model.item_id == item_id))  # type: ignore[attr-defined,call-overload]
    raw_path = Path(item.raw_content_ref)
    session.delete(item)
    session.commit()
    if raw_path.is_file():
        raw_path.unlink()
    logger.info("inbox item %s deleted", item_id)
    return True


def _as_utc(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; they are stored as UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def purge_expired(
    session: Session, now: datetime | None = None, days: int | None = None
) -> PurgeSummary:
    """Delete finished items (`done`, `rejected`) received before the retention period."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days or retention_days())
    statement = select(InboxItem).where(InboxItem.status.in_(PURGEABLE_STATUSES))  # type: ignore[attr-defined]
    expired = [
        item.id
        for item in session.exec(statement).all()
        if item.id is not None and _as_utc(item.received_at) < cutoff
    ]
    for item_id in expired:
        delete_item(session, item_id)
    logger.info("retention purge deleted %d inbox items", len(expired))
    return PurgeSummary(deleted_item_ids=expired, cutoff=cutoff)
