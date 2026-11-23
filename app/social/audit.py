"""Audit trail of a briefing: AI steps and human actions of the social media module.

Entries of this module have no inbox item id; they carry `briefing_id` in their parameters.
SQLite databases created before the ids were made unique (`sqlite_autoincrement`) may give a
new briefing the number of a deleted one; entries older than the briefing belong to the
deleted briefing and are left out, so they never show up in the trail of the new one.
"""

from sqlmodel import Session, col, select

from app.core.models import AuditEntry
from app.social.models import Briefing, as_utc

STEP_PREFIX = "social."


def briefing_audit_trail(session: Session, briefing_id: int) -> list[AuditEntry]:
    statement = (
        select(AuditEntry)
        .where(col(AuditEntry.step).startswith(STEP_PREFIX))
        .order_by(AuditEntry.created_at, AuditEntry.id)  # type: ignore[arg-type]
    )
    briefing = session.get(Briefing, briefing_id)
    since = as_utc(briefing.created_at) if briefing is not None else None
    return [
        entry
        for entry in session.exec(statement).all()
        if entry.model_params.get("briefing_id") == briefing_id
        and (since is None or as_utc(entry.created_at) >= since)
    ]
