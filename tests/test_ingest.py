from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.inbox import cli
from app.inbox.ingest.normalize import UnsupportedFileTypeError
from app.inbox.ingest.service import MAX_UPLOAD_BYTES, import_samples, ingest_file
from app.inbox.models import InboxItem, ItemSource, ItemStatus, Member
from tests.conftest import SAMPLES_DIR


def _sample_files() -> list[Path]:
    return sorted(p for p in SAMPLES_DIR.iterdir() if p.suffix in {".eml", ".pdf"})


def test_all_samples_import_with_non_empty_text(session: Session, raw_dir: Path) -> None:
    summary = import_samples(session, SAMPLES_DIR, raw_dir)

    items = session.exec(select(InboxItem)).all()
    assert summary.imported == len(_sample_files()) == len(items)
    assert summary.members_seeded == 10
    for item in items:
        assert item.text, f"{item.filename} has no text"
        assert item.status == ItemStatus.NEW
        assert item.source == ItemSource.SAMPLE
        assert Path(item.raw_content_ref).is_file()


def test_attachment_only_sample_contains_attachment_text(session: Session, raw_dir: Path) -> None:
    import_samples(session, SAMPLES_DIR, raw_dir)

    item = session.exec(
        select(InboxItem).where(InboxItem.filename == "006_kuendigung_als_anhang.eml")
    ).one()
    assert item.text is not None
    assert "M-10005" in item.text


def test_repeated_import_skips_duplicates(session: Session, raw_dir: Path) -> None:
    import_samples(session, SAMPLES_DIR, raw_dir)

    summary = import_samples(session, SAMPLES_DIR, raw_dir)

    assert summary.imported == 0
    assert summary.skipped == len(_sample_files())
    assert summary.members_seeded == 0


def test_raw_file_is_stored_under_content_hash(session: Session, raw_dir: Path) -> None:
    result = ingest_file(session, "../../etc/brief.pdf", b"%PDF-1.4", ItemSource.UPLOAD, raw_dir)

    stored = Path(result.item.raw_content_ref)
    assert stored.parent == raw_dir
    assert stored.name == f"{result.item.content_hash}.pdf"
    assert result.item.filename == "brief.pdf"


def test_image_stays_new_for_ocr(session: Session, raw_dir: Path) -> None:
    result = ingest_file(session, "scan.png", b"\x89PNG", ItemSource.UPLOAD, raw_dir)

    assert result.item.text is None
    assert result.item.status == ItemStatus.NEW


def test_empty_email_goes_to_manual_review(session: Session, raw_dir: Path) -> None:
    result = ingest_file(session, "leer.eml", b"", ItemSource.UPLOAD, raw_dir)

    assert result.item.text is None
    assert result.item.status == ItemStatus.NEEDS_MANUAL_REVIEW


def test_unsupported_file_creates_nothing(session: Session, raw_dir: Path) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        ingest_file(session, "programm.exe", b"MZ", ItemSource.UPLOAD, raw_dir)

    assert session.exec(select(InboxItem)).all() == []
    assert not raw_dir.exists()


def test_too_large_file_is_rejected(session: Session, raw_dir: Path) -> None:
    with pytest.raises(ValueError, match="too large"):
        ingest_file(session, "x.pdf", b"0" * (MAX_UPLOAD_BYTES + 1), ItemSource.UPLOAD, raw_dir)


def test_upload_endpoint_creates_item(client: TestClient, engine: Engine) -> None:
    content = (SAMPLES_DIR / "001_kuendigung_standard.eml").read_bytes()

    response = client.post("/items/upload", files={"file": ("mail.eml", content)})

    assert response.status_code == 200
    assert "Datei hochgeladen." in response.text
    assert "mail.eml" in response.text
    with Session(engine) as session:
        item = session.exec(select(InboxItem)).one()
    assert item.source == ItemSource.UPLOAD
    assert item.text is not None and "M-10001" in item.text


def test_upload_endpoint_reports_duplicates(client: TestClient) -> None:
    files = {"file": ("mail.eml", b"Subject: Test\n\nHallo")}
    client.post("/items/upload", files=files)

    response = client.post("/items/upload", files=files)

    assert "bereits im Posteingang" in response.text


def test_upload_endpoint_rejects_unsupported_type(client: TestClient) -> None:
    response = client.post("/items/upload", files={"file": ("x.exe", b"MZ")})

    assert response.status_code == 200
    assert "Dateityp nicht unterstützt" in response.text


def test_upload_endpoint_rejects_too_large_file(client: TestClient) -> None:
    content = b"0" * (MAX_UPLOAD_BYTES + 1)

    response = client.post("/items/upload", files={"file": ("x.pdf", content)})

    assert "zu groß" in response.text


def test_sample_import_button_endpoint(client: TestClient, engine: Engine) -> None:
    response = client.post("/samples/import")

    assert response.status_code == 200
    assert f"{len(_sample_files())} Beispiel-Eingänge importiert" in response.text
    with Session(engine) as session:
        assert len(session.exec(select(Member)).all()) == 10


def test_cli_import_samples(
    engine: Engine, raw_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setenv("STORAGE_DIR", str(raw_dir))

    assert cli.main(["import-samples"]) == 0

    assert f"{len(_sample_files())} Eingänge importiert" in capsys.readouterr().out
    with Session(engine) as session:
        assert len(session.exec(select(InboxItem)).all()) == len(_sample_files())
