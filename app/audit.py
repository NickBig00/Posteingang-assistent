"""Writing and protecting the immutable audit trail."""

import hashlib
from collections.abc import Mapping

from pydantic import JsonValue
from sqlmodel import Session

from app.models import AuditEntry, AuditEntryImmutableError

__all__ = ["AuditEntryImmutableError", "hash_input", "write_audit_entry"]


def hash_input(text: str) -> str:
    """Return the SHA-256 hex digest of the LLM input. The raw input is never stored."""
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
        output=output,
        duration_ms=duration_ms,
        error=error,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry
