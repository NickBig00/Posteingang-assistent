"""Writing and protecting the immutable audit trail."""

import hashlib
from collections.abc import Mapping

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.models import AuditEntry, AuditEntryImmutableError, AuditOutput

__all__ = [
    "HUMAN_ACTOR_MODEL",
    "AuditEntryImmutableError",
    "audit_outputs",
    "hash_input",
    "write_audit_entry",
    "write_human_audit_entry",
]

# `model` value of audit entries for actions taken by a person instead of an AI model.
HUMAN_ACTOR_MODEL = "mensch"


def hash_input(text: str) -> str:
    """Return the SHA-256 hex digest of a text (LLM input or output)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_audit_entry(
    session: Session,
    *,
    step: str,
    model: str,
    input_text: str,
    duration_ms: int,
    item_id: int | None = None,
    model_params: Mapping[str, JsonValue] | None = None,
    prompt_version: str | None = None,
    output: str | None = None,
    error: str | None = None,
) -> AuditEntry:
    """Persist an audit entry for one AI processing step and commit it immediately."""
    if duration_ms < 0:
        raise ValueError("duration_ms must not be negative")
    entry = AuditEntry(
        item_id=item_id,
        step=step,
        model=model,
        model_params=dict(model_params or {}),
        prompt_version=prompt_version,
        input_hash=hash_input(input_text),
        output_hash=hash_input(output) if output is not None else None,
        duration_ms=duration_ms,
        error=error,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    if output is not None and entry.id is not None:
        session.add(AuditOutput(audit_entry_id=entry.id, item_id=item_id, output=output))
        session.commit()
    return entry


def audit_outputs(session: Session, entry_ids: list[int]) -> dict[int, str]:
    """Stored raw outputs by audit entry id (absent once the item has been deleted)."""
    if not entry_ids:
        return {}
    rows = session.exec(select(AuditOutput).where(AuditOutput.audit_entry_id.in_(entry_ids)))  # type: ignore[attr-defined]
    return {row.audit_entry_id: row.output for row in rows}


def write_human_audit_entry(
    session: Session,
    *,
    item_id: int | None,
    step: str,
    actor: str,
    details: Mapping[str, JsonValue] | None = None,
) -> AuditEntry:
    """Record a human action that changes the AI processing (e.g. reprocess, reopen).

    Uses the same immutable trail as the AI steps so the history of an item is complete.
    """
    return write_audit_entry(
        session,
        item_id=item_id,
        step=step,
        model=HUMAN_ACTOR_MODEL,
        model_params={"actor": actor, **(details or {})},
        input_text=f"{step}:{item_id}",
        duration_ms=0,
    )
