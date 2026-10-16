import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, select

from app import cli
from app.audit import audit_outputs
from app.ingest.service import import_samples, ingest_file
from app.llm.client import LLMClient
from app.llm.fake import FakeProvider
from app.logging_config import RedactingFilter, redact, setup_logging
from app.models import (
    AuditEntry,
    AuditOutput,
    Classification,
    Extraction,
    InboxItem,
    ItemSource,
    ItemStatus,
    Review,
    ReviewDecision,
    RuleResult,
    Suggestion,
)
from app.pipeline import process_new_items
from app.profiles import PROFILES_DIR, load_profile
from app.retention import delete_item, purge_expired, retention_days
from app.review import review_item
from tests.conftest import SAMPLES_DIR

CONTENT_TABLES: tuple[type[SQLModel], ...] = (
    Classification,
    Extraction,
    RuleResult,
    Suggestion,
    Review,
    AuditOutput,
)


def _processed_samples(session: Session, raw_dir: Path) -> list[InboxItem]:
    import_samples(session, SAMPLES_DIR, raw_dir)
    process_new_items(
        session, load_profile(PROFILES_DIR / "fitness.yaml"), LLMClient(FakeProvider())
    )
    return list(session.exec(select(InboxItem)).all())


def _count(session: Session, model: type[SQLModel], item_id: int) -> int:
    return len(session.exec(select(model).where(model.item_id == item_id)).all())  # type: ignore[attr-defined]


# --- deletion ---


def test_delete_item_removes_all_content(session: Session, raw_dir: Path) -> None:
    item = next(
        i for i in _processed_samples(session, raw_dir) if i.filename and "001" in i.filename
    )
    assert item.id is not None
    review_item(session, item, ReviewDecision.APPROVED, "Freigegebener Text")
    item_id, raw_file = item.id, Path(item.raw_content_ref)
    audit_before = session.exec(select(AuditEntry).where(AuditEntry.item_id == item_id)).all()
    assert raw_file.is_file()
    assert all(_count(session, model, item_id) > 0 for model in CONTENT_TABLES)

    assert delete_item(session, item_id) is True

    session.expire_all()
    assert session.get(InboxItem, item_id) is None
    assert not raw_file.exists()
    assert all(_count(session, model, item_id) == 0 for model in CONTENT_TABLES)
    audit_after = session.exec(select(AuditEntry).where(AuditEntry.item_id == item_id)).all()
    assert [e.id for e in audit_after] == [e.id for e in audit_before]
    assert audit_outputs(session, [e.id or 0 for e in audit_after]) == {}


def test_audit_after_deletion_keeps_only_hashes_and_metadata(
    session: Session, raw_dir: Path
) -> None:
    items = _processed_samples(session, raw_dir)
    item = next(i for i in items if i.filename == "001_kuendigung_standard.eml")
    assert item.id is not None
    delete_item(session, item.id)

    entries = session.exec(select(AuditEntry).where(AuditEntry.item_id == item.id)).all()
    dumped = " ".join(e.model_dump_json() for e in entries)
    assert entries
    for personal in ("Erika", "Beispiel", "erika.beispiel@example.com", "M-10001", "kündige"):
        assert personal not in dumped
    assert all(len(e.input_hash) == 64 for e in entries)
    assert all(e.output_hash is None or len(e.output_hash) == 64 for e in entries)


def test_delete_unknown_item(session: Session) -> None:
    assert delete_item(session, 999) is False


def test_other_items_are_untouched(session: Session, raw_dir: Path) -> None:
    items = _processed_samples(session, raw_dir)
    first, second = items[0], items[1]
    assert first.id is not None and second.id is not None

    delete_item(session, first.id)

    assert session.get(InboxItem, second.id) is not None
    assert Path(second.raw_content_ref).is_file()
    assert _count(session, Classification, second.id) == 1


def test_deleted_file_can_be_imported_again(session: Session, raw_dir: Path) -> None:
    content = (SAMPLES_DIR / "025_sonstiges_lob.eml").read_bytes()
    first = ingest_file(session, "lob.eml", content, ItemSource.UPLOAD, raw_dir).item
    assert first.id is not None
    delete_item(session, first.id)

    assert ingest_file(session, "lob.eml", content, ItemSource.UPLOAD, raw_dir).created


def test_delete_endpoint(client: TestClient, engine: Engine) -> None:
    client.post("/items/upload", files={"file": ("x.eml", b"Subject: A\n\nHallo")})
    with Session(engine) as session:
        item_id = session.exec(select(InboxItem)).one().id

    response = client.post(f"/items/{item_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert client.get(f"/items/{item_id}").status_code == 404
    assert client.post(f"/items/{item_id}/delete").status_code == 404


def test_audit_view_after_deletion_of_outputs(client: TestClient, engine: Engine) -> None:
    with Session(engine) as session:
        item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text="t")
        session.add(item)
        session.commit()
        session.refresh(item)
        from app.audit import write_audit_entry

        write_audit_entry(
            session, item_id=item.id, step="classify", model="fake", input_text="t",
            duration_ms=1, output="geheim",
        )  # fmt: skip
        session.exec(select(AuditOutput)).one()
        session.delete(session.exec(select(AuditOutput)).one())
        session.commit()
        item_id = item.id

    page = client.get(f"/items/{item_id}/audit").text

    assert "Ausgabe gelöscht" in page
    assert "geheim" not in page


# --- retention ---


def test_retention_days_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    assert retention_days() == 90
    monkeypatch.setenv("RETENTION_DAYS", "30")
    assert retention_days() == 30


@pytest.mark.parametrize("value", ["0", "-5", "abc"])
def test_invalid_retention_days(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("RETENTION_DAYS", value)

    with pytest.raises(ValueError, match="RETENTION_DAYS"):
        retention_days()


def test_purge_deletes_only_expired_items(session: Session) -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    for name, age in (("alt", 31), ("grenze", 30), ("neu", 1)):
        session.add(
            InboxItem(
                source=ItemSource.UPLOAD,
                raw_content_ref=f"/nonexistent/{name}",
                filename=name,
                status=ItemStatus.DONE,
                received_at=now - timedelta(days=age, seconds=1 if name == "alt" else 0),
            )
        )
    session.commit()

    summary = purge_expired(session, now=now, days=30)

    remaining = {i.filename for i in session.exec(select(InboxItem)).all()}
    assert remaining == {"grenze", "neu"}
    assert len(summary.deleted_item_ids) == 1


def test_purge_keeps_items_still_waiting_for_a_human(session: Session) -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    for status in ItemStatus:
        session.add(
            InboxItem(
                source=ItemSource.UPLOAD,
                raw_content_ref=f"/nonexistent/{status.value}",
                filename=status.value,
                status=status,
                received_at=now - timedelta(days=365),
            )
        )
    session.commit()

    purge_expired(session, now=now, days=30)

    remaining = {i.filename for i in session.exec(select(InboxItem)).all()}
    assert remaining == {"new", "processed", "needs_manual_review", "approved"}


def test_cli_purge(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setenv("RETENTION_DAYS", "1")
    with Session(engine) as session:
        session.add(
            InboxItem(
                source=ItemSource.UPLOAD,
                raw_content_ref="/nonexistent",
                status=ItemStatus.REJECTED,
                received_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        session.commit()

    assert cli.main(["purge"]) == 0

    assert "1 Eingänge gelöscht" in capsys.readouterr().out


# --- logs ---


def _personal_terms() -> set[str]:
    members = json.loads((SAMPLES_DIR / "members.json").read_text(encoding="utf-8"))["members"]
    terms = set()
    for member in members:
        terms |= {member["name"], member["email"], member["member_number"]}
    labels = json.loads((SAMPLES_DIR / "labels.json").read_text(encoding="utf-8"))["items"]
    for label in labels:
        for key in ("name", "kontakt", "lieferant", "rechnungsnummer"):
            value = label["expected_fields"].get(key)
            if value:
                terms.add(value)
    return terms


def test_no_personal_data_in_application_logs(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    from app.main import app, get_llm_client

    app.dependency_overrides[get_llm_client] = lambda: LLMClient(FakeProvider())
    caplog.set_level(logging.DEBUG)

    client.post("/samples/import")
    client.post("/items/process")
    client.post("/items/upload", files={"file": ("kaputt.pdf", b"%PDF-1.4 kaputt")})
    client.post("/items/1/review", data={"decision": "approved", "edited_text": "Erika Beispiel"})
    client.post("/items/1/delete")

    assert caplog.records, "expected some log output"
    logged = "\n".join(record.getMessage() + (record.exc_text or "") for record in caplog.records)
    for term in _personal_terms():
        assert term not in logged, f"personal data in logs: {term!r}"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("mail von erika.beispiel@example.com", "mail von [E-MAIL]"),
        ("Rückruf 030 23125 456 bitte", "Rückruf [TELEFON] bitte"),
        ("Mitglied M-10001", "Mitglied [MITGLIEDSNUMMER]"),
        ("IBAN DE00 0000 0000 0000 0000 00", "IBAN [IBAN]"),
        ("item 12 processed in 3 ms", "item 12 processed in 3 ms"),
    ],
)
def test_redact(message: str, expected: str) -> None:
    assert redact(message) == expected


def test_redacting_filter_masks_args_and_drops_tracebacks() -> None:
    record = logging.LogRecord(
        "app", logging.ERROR, __file__, 1, "Mail %s", ("a@example.com",), None
    )
    assert RedactingFilter().filter(record)
    assert record.getMessage() == "Mail [E-MAIL]"

    try:
        raise ValueError("Erika Beispiel M-10001")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "app", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    RedactingFilter().filter(record)
    assert record.exc_info is None
    assert record.getMessage() == "failed (ValueError)"


def test_setup_logging_attaches_filter_once() -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        setup_logging()
        setup_logging()
        assert sum(isinstance(f, RedactingFilter) for f in handler.filters) == 1
    finally:
        root.removeHandler(handler)
