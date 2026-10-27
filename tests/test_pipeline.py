import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.audit import hash_input
from app.core.llm.base import LLMTask
from app.core.llm.fake import FakeProvider
from app.core.models import AuditEntry
from app.core.profiles import PROFILES_DIR
from app.inbox import cli
from app.inbox.ingest.service import import_samples
from app.inbox.llm.client import LLMClient
from app.inbox.models import (
    Classification,
    Extraction,
    InboxItem,
    ItemSource,
    ItemStatus,
    ValidationStatus,
)
from app.inbox.pipeline import process_item, process_new_items
from app.inbox.profiles import Profile, load_profile
from app.inbox.web import get_llm_client
from app.main import app
from tests.conftest import SAMPLES_DIR

KUENDIGUNG = json.dumps(
    {
        "name": "Erika Beispiel",
        "mitgliedsnummer": "M-10001",
        "eingangsdatum": "2026-09-14",
        "gewuenschtes_kuendigungsdatum": "2026-10-31",
    }
)
CLASSIFY_OK = '{"document_type": "kuendigung", "confidence": 0.93}'


@pytest.fixture
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


def _item(session: Session, text: str | None = "Ich kündige M-10001 zum 31.10.2026.") -> InboxItem:
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text=text)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _audit(session: Session) -> list[AuditEntry]:
    return list(session.exec(select(AuditEntry).order_by(AuditEntry.id)).all())  # type: ignore[arg-type]


def test_successful_processing(session: Session, profile: Profile) -> None:
    item = _item(session)
    client = LLMClient(FakeProvider([CLASSIFY_OK, KUENDIGUNG]))

    result = process_item(session, item, profile, client)

    assert result.status == ItemStatus.PROCESSED
    assert result.document_type == "kuendigung"
    classification = session.exec(select(Classification)).one()
    assert classification.confidence == pytest.approx(0.93)
    extraction = session.exec(select(Extraction)).one()
    assert extraction.validation_status == ValidationStatus.VALID
    assert extraction.fields["gewuenschtes_kuendigungsdatum"] == "2026-10-31"


def test_every_llm_step_is_audited(session: Session, profile: Profile) -> None:
    item = _item(session)
    fake = FakeProvider([CLASSIFY_OK, KUENDIGUNG])

    process_item(session, item, profile, LLMClient(fake))

    entries = _audit(session)
    assert [e.step for e in entries] == ["classify", "extract"]
    assert [e.prompt_version for e in entries] == ["classify_v1", "extract_v2"]
    for entry, (task, system, user) in zip(entries, fake.calls, strict=True):
        assert entry.item_id == item.id
        assert entry.model == "fake-scripted"
        assert entry.model_params["attempt"] == 1
        assert entry.error is None
        assert entry.duration_ms >= 0
        assert entry.input_hash == hash_input(f"{system}\n\n{user}")
        assert entry.step == task
        assert "Ich kündige" not in entry.model_dump_json()  # only the hash of the input
    assert entries[1].output_hash == hash_input(KUENDIGUNG)


def test_invalid_classification_is_retried_once(session: Session, profile: Profile) -> None:
    item = _item(session)
    client = LLMClient(FakeProvider(["kein JSON", CLASSIFY_OK, KUENDIGUNG]))

    result = process_item(session, item, profile, client)

    assert result.status == ItemStatus.PROCESSED
    entries = _audit(session)
    assert [(e.step, e.model_params["attempt"]) for e in entries] == [
        ("classify", 1),
        ("classify", 2),
        ("extract", 1),
    ]
    assert entries[0].error is not None and "invalid output" in entries[0].error
    assert entries[0].output_hash == hash_input("kein JSON")


def test_twice_invalid_classification_needs_manual_review(
    session: Session, profile: Profile
) -> None:
    item = _item(session)
    fake = FakeProvider(["kein JSON", '{"document_type": "mahnung", "confidence": 1}'])

    result = process_item(session, item, profile, LLMClient(fake))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert result.reason == "Klassifizierung fehlgeschlagen"
    assert len(fake.calls) == 2
    assert session.exec(select(Classification)).all() == []
    assert all(e.error for e in _audit(session))


def test_invalid_extraction_is_retried_once(session: Session, profile: Profile) -> None:
    item = _item(session)
    client = LLMClient(FakeProvider([CLASSIFY_OK, '{"name": "x"}', KUENDIGUNG]))

    result = process_item(session, item, profile, client)

    assert result.status == ItemStatus.PROCESSED
    assert [e.step for e in _audit(session)] == ["classify", "extract", "extract"]


def test_twice_invalid_extraction_needs_manual_review(session: Session, profile: Profile) -> None:
    item = _item(session)
    client = LLMClient(FakeProvider([CLASSIFY_OK, '{"name": "x"}', '{"betrag": 1.5}']))

    result = process_item(session, item, profile, client)

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert result.document_type == "kuendigung"
    extraction = session.exec(select(Extraction)).one()
    assert extraction.validation_status == ValidationStatus.INVALID
    assert extraction.fields == {}


def test_provider_failure_needs_manual_review_and_is_audited(
    session: Session, profile: Profile
) -> None:
    item = _item(session)

    result = process_item(session, item, profile, LLMClient(FakeProvider([])))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    entries = _audit(session)
    assert len(entries) == 2
    assert entries[0].model == "fake"
    assert entries[0].output_hash is None
    assert entries[0].error is not None and "LLMProviderError" in entries[0].error


def test_low_confidence_needs_manual_review_without_extraction(
    session: Session, profile: Profile
) -> None:
    item = _item(session)
    fake = FakeProvider(['{"document_type": "kuendigung", "confidence": 0.5}'])

    result = process_item(session, item, profile, LLMClient(fake))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert result.reason == "Konfidenz unter Schwellwert"
    assert len(fake.calls) == 1
    assert session.exec(select(Classification)).one().document_type == "kuendigung"
    assert session.exec(select(Extraction)).all() == []


def test_confidence_exactly_at_threshold_is_accepted(session: Session, profile: Profile) -> None:
    item = _item(session)
    threshold = json.dumps(
        {"document_type": "kuendigung", "confidence": profile.confidence_threshold}
    )

    result = process_item(session, item, profile, LLMClient(FakeProvider([threshold, KUENDIGUNG])))

    assert result.status == ItemStatus.PROCESSED


def test_item_without_text_is_not_sent_to_llm(session: Session, profile: Profile) -> None:
    item = _item(session, text=None)
    fake = FakeProvider([])

    result = process_item(session, item, profile, LLMClient(fake))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert fake.calls == []
    assert _audit(session) == []


def test_only_new_items_are_processed(session: Session, profile: Profile) -> None:
    item = _item(session)
    process_item(session, item, profile, LLMClient(FakeProvider([CLASSIFY_OK, KUENDIGUNG])))

    with pytest.raises(ValueError, match="status 'new'"):
        process_item(session, item, profile, LLMClient(FakeProvider([])))


def test_only_document_text_is_sent_to_llm(session: Session, profile: Profile) -> None:
    item = _item(session)
    fake = FakeProvider([CLASSIFY_OK, KUENDIGUNG])

    process_item(session, item, profile, LLMClient(fake))

    for _, _, user in fake.calls:
        assert "<dokument>\nIch kündige M-10001 zum 31.10.2026.\n</dokument>" in user
        assert "raw_content_ref" not in user


def test_pipeline_runs_for_all_samples(session: Session, profile: Profile, raw_dir: Path) -> None:
    """Acceptance criterion M5 with the deterministic heuristic fake provider."""
    import_samples(session, SAMPLES_DIR, raw_dir)
    fake = FakeProvider()

    results = process_new_items(session, profile, LLMClient(fake))

    items = session.exec(select(InboxItem)).all()
    assert len(results) == len(items) == 27
    assert all(
        item.status in {ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW} for item in items
    )
    # Audit is complete: exactly one entry per LLM call, each tied to its item.
    entries = _audit(session)
    assert len(entries) == len(fake.calls)
    assert {e.item_id for e in entries} == {item.id for item in items}
    assert all(e.prompt_version and e.model and e.input_hash for e in entries)
    for item in items:
        steps = [e.step for e in entries if e.item_id == item.id]
        assert steps[0] == "classify"
        if item.status == ItemStatus.PROCESSED:
            assert "extract" in steps
    # Running again processes nothing new.
    assert process_new_items(session, profile, LLMClient(fake)) == []


def test_heuristic_classification_quality_on_samples(
    session: Session, profile: Profile, raw_dir: Path
) -> None:
    import_samples(session, SAMPLES_DIR, raw_dir)
    process_new_items(session, profile, LLMClient(FakeProvider()))
    labels = {
        entry["file"]: entry["expected_type"]
        for entry in json.loads((SAMPLES_DIR / "labels.json").read_text())["items"]
    }

    rows = session.exec(select(InboxItem, Classification).join(Classification)).all()
    correct = sum(labels[item.filename] == c.document_type for item, c in rows)

    assert correct / len(labels) >= 0.9


@pytest.fixture
def scripted_client() -> Iterator[None]:
    def responder(task: LLMTask, system: str, user: str) -> str:
        return CLASSIFY_OK if task == "classify" else KUENDIGUNG

    app.dependency_overrides[get_llm_client] = lambda: LLMClient(FakeProvider(responder))
    yield


def test_process_endpoint(client: TestClient, engine: Engine, scripted_client: None) -> None:
    client.post("/items/upload", files={"file": ("mail.eml", b"Subject: K\n\nIch kuendige.")})

    response = client.post("/items/process")

    assert "1 Eingänge verarbeitet, davon 0 zur manuellen Prüfung" in response.text
    assert "kuendigung" in response.text
    with Session(engine) as session:
        assert session.exec(select(InboxItem)).one().status == ItemStatus.PROCESSED


def test_cli_process(
    engine: Engine, raw_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setenv("STORAGE_DIR", str(raw_dir))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    cli.main(["import-samples"])

    assert cli.main(["process"]) == 0

    out = capsys.readouterr().out
    assert "27 Eingänge verarbeitet" in out
    assert "Anbieter: fake" in out


def test_rule_results_are_stored(session: Session, profile: Profile) -> None:
    from app.inbox.models import RuleResult
    from app.inbox.seed import seed_members

    seed_members(session)
    item = _item(session)

    draft = "Wir bestätigen die Kündigung M-10001 zum 31.10.2026."
    result = process_item(
        session, item, profile, LLMClient(FakeProvider([CLASSIFY_OK, KUENDIGUNG, draft]))
    )

    rules = {r.rule_name: r for r in session.exec(select(RuleResult)).all()}
    assert result.status == ItemStatus.PROCESSED
    assert set(rules) == {"required_fields", "notice_period"}
    assert rules["notice_period"].data["vertragsende"] == "2026-10-31"


def test_rule_error_needs_manual_review(session: Session, profile: Profile) -> None:
    item = _item(session)
    missing_number = json.loads(KUENDIGUNG) | {"mitgliedsnummer": None}
    client = LLMClient(FakeProvider([CLASSIFY_OK, json.dumps(missing_number)]))

    result = process_item(session, item, profile, client)

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert result.reason == "Regelprüfung mit Fehler"
