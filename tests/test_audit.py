import hashlib

import pytest
from sqlalchemy import Engine, delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.audit import AuditEntryImmutableError, audit_outputs, hash_input, write_audit_entry
from app.core.models import AuditEntry


def _write(session: Session, **overrides: object) -> AuditEntry:
    params: dict[str, object] = {
        "step": "classify",
        "model": "fake",
        "input_text": "Synthetischer Eingangstext",
        "duration_ms": 12,
        "item_id": 1,
        "model_params": {"temperature": 0.0},
        "prompt_version": "classify_v1",
        "output": '{"type": "sonstiges"}',
    } | overrides
    return write_audit_entry(session, **params)  # type: ignore[arg-type]


def test_write_audit_entry_persists_all_fields(session: Session) -> None:
    entry = _write(session)

    stored = session.exec(select(AuditEntry)).one()
    assert stored.id == entry.id
    assert stored.step == "classify"
    assert stored.model == "fake"
    assert stored.model_params == {"temperature": 0.0}
    assert stored.prompt_version == "classify_v1"
    assert stored.output_hash == hash_input('{"type": "sonstiges"}')
    assert audit_outputs(session, [stored.id or 0]) == {stored.id: '{"type": "sonstiges"}'}
    assert stored.duration_ms == 12
    assert stored.error is None
    assert stored.created_at is not None


def test_audit_entry_stores_hash_not_raw_input(session: Session) -> None:
    entry = _write(session, input_text="Geheimer Rohtext")

    expected = hashlib.sha256(b"Geheimer Rohtext").hexdigest()
    assert entry.input_hash == expected == hash_input("Geheimer Rohtext")
    assert "Geheimer Rohtext" not in entry.model_dump_json()


def test_audit_entry_records_errors(session: Session) -> None:
    entry = _write(session, output=None, error="invalid LLM output")

    assert entry.error == "invalid LLM output"


def test_negative_duration_is_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="duration_ms"):
        _write(session, duration_ms=-1)


def test_audit_entry_may_exist_without_inbox_item(session: Session) -> None:
    entry = _write(session, item_id=4711)

    assert entry.item_id == 4711


def test_orm_update_is_blocked(session: Session) -> None:
    entry = _write(session)
    entry.step = "manipuliert"
    session.add(entry)

    with pytest.raises(AuditEntryImmutableError):
        session.commit()


def test_orm_delete_is_blocked(session: Session) -> None:
    entry = _write(session)
    session.delete(entry)

    with pytest.raises(AuditEntryImmutableError):
        session.commit()


def test_bulk_update_is_blocked_by_database(session: Session) -> None:
    _write(session)

    with pytest.raises(IntegrityError, match="immutable"):
        session.exec(update(AuditEntry).values(step="manipuliert"))  # type: ignore[call-overload]


def test_bulk_delete_is_blocked_by_database(session: Session) -> None:
    _write(session)

    with pytest.raises(IntegrityError, match="immutable"):
        session.exec(delete(AuditEntry))  # type: ignore[call-overload]


def test_raw_sql_modification_is_blocked(engine: Engine, session: Session) -> None:
    _write(session)

    with engine.connect() as connection, pytest.raises(IntegrityError, match="immutable"):
        connection.execute(text("UPDATE auditentry SET step = 'x'"))


def test_entries_survive_blocked_modification(engine: Engine, session: Session) -> None:
    _write(session, step="original")
    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(text("DELETE FROM auditentry"))

    with Session(engine) as fresh:
        assert fresh.exec(select(AuditEntry)).one().step == "original"
