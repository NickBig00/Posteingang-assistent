"""Audit trail of a briefing: AI steps and human actions of the social media module.

Entries of this module have no inbox item id; they carry `briefing_id` in their parameters.
"""

from sqlmodel import Session, col, select

from app.core.models import AuditEntry

STEP_PREFIX = "social."


def briefing_audit_trail(session: Session, briefing_id: int) -> list[AuditEntry]:
    statement = (
        select(AuditEntry)
        .where(col(AuditEntry.step).startswith(STEP_PREFIX))
        .order_by(AuditEntry.created_at, AuditEntry.id)  # type: ignore[arg-type]
    )
    return [
        entry
        for entry in session.exec(statement).all()
        if entry.model_params.get("briefing_id") == briefing_id
    ]
