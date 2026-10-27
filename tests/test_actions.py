"""Reprocessing, reopening, OCR via vision model and the switchable refusal fallback."""

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.approval import ReviewDecision
from app.core.audit import HUMAN_ACTOR_MODEL, hash_input
from app.core.llm.anthropic_provider import AnthropicProvider
from app.core.llm.base import VisualInput
from app.core.llm.fake import FakeProvider
from app.core.llm.prompts import NO_TEXT_MARKER
from app.core.models import AuditEntry
from app.core.profiles import PROFILES_DIR
from app.inbox.ingest.service import ingest_file
from app.inbox.llm.client import LLMClient
from app.inbox.models import Classification, InboxItem, ItemSource, ItemStatus
from app.inbox.pipeline import process_item
from app.inbox.profiles import Profile, load_profile
from app.inbox.review import ReviewNotAllowedError, reopen_item, reprocess_item, review_item
from app.inbox.web import get_llm_client
from app.main import app

LOW = '{"document_type": "sonstiges", "confidence": 0.3}'
HIGH = '{"document_type": "sonstiges", "confidence": 0.95}'
SONSTIGES = '{"kurzbeschreibung": "Lob"}'
PNG = b"\x89PNG\r\n\x1a\n fiktives Bild"


@pytest.fixture
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


def _item(
    session: Session, status: ItemStatus = ItemStatus.NEW, text: str | None = "Text"
) -> InboxItem:
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text=text, status=status)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _audit(session: Session, item_id: int) -> list[AuditEntry]:
    statement = select(AuditEntry).where(AuditEntry.item_id == item_id).order_by(AuditEntry.id)  # type: ignore[arg-type]
    return list(session.exec(statement).all())


# --- reprocess ---


def test_reprocess_replaces_results_and_is_audited(session: Session, profile: Profile) -> None:
    item = _item(session)
    process_item(session, item, profile, LLMClient(FakeProvider([LOW])))
    assert item.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert item.id is not None
    before = [e.id for e in _audit(session, item.id)]

    result = reprocess_item(session, item, profile, LLMClient(FakeProvider([HIGH, SONSTIGES])))

    assert result.status == ItemStatus.PROCESSED
    classifications = session.exec(select(Classification)).all()
    assert [c.confidence for c in classifications] == [pytest.approx(0.95)]
    entries = _audit(session, item.id)
    assert [e.id for e in entries[: len(before)]] == before  # history is kept
    human = entries[len(before)]
    assert human.step == "reprocess"
    assert human.model == HUMAN_ACTOR_MODEL
    assert human.model_params == {
        "actor": "demo",
        "from_status": "needs_manual_review",
        "to_status": "new",
    }
    assert [e.step for e in entries[len(before) + 1 :]] == ["classify", "extract"]


@pytest.mark.parametrize(
    "status",
    [
        ItemStatus.NEW,
        ItemStatus.PROCESSED,
        ItemStatus.APPROVED,
        ItemStatus.REJECTED,
        ItemStatus.DONE,
    ],
)
def test_reprocess_only_from_manual_review(
    session: Session, profile: Profile, status: ItemStatus
) -> None:
    item = _item(session, status)

    with pytest.raises(ReviewNotAllowedError):
        reprocess_item(session, item, profile, LLMClient(FakeProvider([])))
    assert item.id is not None
    assert _audit(session, item.id) == []


# --- reopen ---


def test_reopen_rejected_item_for_new_decision(session: Session) -> None:
    item = _item(session, ItemStatus.PROCESSED)
    review_item(session, item, ReviewDecision.REJECTED)

    reopen_item(session, item)

    assert item.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert item.id is not None
    entry = _audit(session, item.id)[-1]
    assert (entry.step, entry.model) == ("reopen", HUMAN_ACTOR_MODEL)
    assert entry.model_params["from_status"] == "rejected"
    review_item(session, item, ReviewDecision.APPROVED)
    assert item.status == ItemStatus.APPROVED


@pytest.mark.parametrize(
    "status", [ItemStatus.DONE, ItemStatus.NEW, ItemStatus.PROCESSED, ItemStatus.APPROVED]
)
def test_only_rejected_items_can_be_reopened(session: Session, status: ItemStatus) -> None:
    item = _item(session, status)

    with pytest.raises(ReviewNotAllowedError):
        reopen_item(session, item)
    assert item.status == status


# --- web ---


def _create(engine: Engine, status: ItemStatus) -> int:
    with Session(engine) as session:
        item = _item(session, status)
        assert item.id is not None
        return item.id


def _status(engine: Engine, item_id: int) -> ItemStatus:
    with Session(engine) as session:
        item = session.get(InboxItem, item_id)
        assert item is not None
        return item.status


def test_reprocess_and_reopen_buttons(client: TestClient, engine: Engine) -> None:
    review_id = _create(engine, ItemStatus.NEEDS_MANUAL_REVIEW)
    rejected_id = _create(engine, ItemStatus.REJECTED)
    done_id = _create(engine, ItemStatus.DONE)

    assert "Neu verarbeiten" in client.get(f"/items/{review_id}").text
    assert "Wieder öffnen" in client.get(f"/items/{rejected_id}").text
    assert "Wieder öffnen" not in client.get(f"/items/{done_id}").text


def test_reprocess_endpoint(client: TestClient, engine: Engine) -> None:
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(FakeProvider([HIGH, SONSTIGES]))
    item_id = _create(engine, ItemStatus.NEEDS_MANUAL_REVIEW)

    response = client.post(f"/items/{item_id}/reprocess", follow_redirects=False)

    assert response.status_code == 303
    assert _status(engine, item_id) == ItemStatus.PROCESSED
    assert "durch Mensch: demo" in client.get(f"/items/{item_id}/audit").text


def test_reopen_endpoint(client: TestClient, engine: Engine) -> None:
    rejected_id = _create(engine, ItemStatus.REJECTED)
    done_id = _create(engine, ItemStatus.DONE)

    assert client.post(f"/items/{rejected_id}/reopen", follow_redirects=False).status_code == 303
    assert _status(engine, rejected_id) == ItemStatus.NEEDS_MANUAL_REVIEW
    assert client.post(f"/items/{done_id}/reopen").status_code == 409
    assert _status(engine, done_id) == ItemStatus.DONE


# --- OCR ---


def _image_item(session: Session, raw_dir: Path, content: bytes = PNG) -> InboxItem:
    return ingest_file(session, "scan.png", content, ItemSource.UPLOAD, raw_dir).item


def test_ocr_text_is_processed_and_audited(
    session: Session, profile: Profile, raw_dir: Path
) -> None:
    item = _image_item(session, raw_dir)
    fake = FakeProvider(["Vielen Dank für das tolle Training!", HIGH, SONSTIGES])

    result = process_item(session, item, profile, LLMClient(fake))

    assert result.status == ItemStatus.PROCESSED
    assert item.text == "Vielen Dank für das tolle Training!"
    assert fake.visuals[0] == (VisualInput("image/png", PNG),)
    assert fake.visuals[1] == ()  # classification gets the text only
    assert item.id is not None
    ocr = _audit(session, item.id)[0]
    assert (ocr.step, ocr.prompt_version) == ("ocr", "ocr_v1")
    _, system, user = fake.calls[0]
    # Audit input = prompt + hash of the image; the image itself is never stored.
    image_hash = hashlib.sha256(PNG).hexdigest()
    assert ocr.input_hash == hash_input(f"{system}\n\n{user}\n\n[visual sha256={image_hash}]")


def test_ocr_without_text_goes_to_manual_review(
    session: Session, profile: Profile, raw_dir: Path
) -> None:
    item = _image_item(session, raw_dir)

    result = process_item(session, item, profile, LLMClient(FakeProvider([NO_TEXT_MARKER])))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert result.reason == "kein Text erkannt"
    assert item.text is None


def test_failed_ocr_is_retried_then_manual_review(
    session: Session, profile: Profile, raw_dir: Path
) -> None:
    item = _image_item(session, raw_dir)

    result = process_item(session, item, profile, LLMClient(FakeProvider(["", "  "])))

    assert result.reason == "Texterkennung fehlgeschlagen"
    assert item.id is not None
    assert [e.step for e in _audit(session, item.id)] == ["ocr", "ocr"]


def test_heuristic_fake_cannot_read_images(
    session: Session, profile: Profile, raw_dir: Path
) -> None:
    item = _image_item(session, raw_dir)

    result = process_item(session, item, profile, LLMClient(FakeProvider()))

    assert result.reason == "kein Text erkannt"


def test_scanned_pdf_is_sent_as_pdf(session: Session, profile: Profile, raw_dir: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    content = bytes(pdf.output())  # no text layer
    item = ingest_file(session, "scan.pdf", content, ItemSource.UPLOAD, raw_dir).item
    fake = FakeProvider([NO_TEXT_MARKER])

    process_item(session, item, profile, LLMClient(fake))

    assert fake.visuals[0][0].media_type == "application/pdf"


def test_ocr_hint_and_original_file_in_ui(
    client: TestClient, engine: Engine, raw_dir: Path
) -> None:
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(
        FakeProvider(["Erkannter Text", HIGH, SONSTIGES])
    )
    client.post("/items/upload", files={"file": ("scan.png", PNG)})
    client.post("/items/process")
    with Session(engine) as session:
        item_id = session.exec(select(InboxItem)).one().id

    detail = client.get(f"/items/{item_id}").text
    original = client.get(f"/items/{item_id}/original")

    assert "KI-Texterkennung" in detail
    assert original.status_code == 200
    assert original.headers["content-type"] == "image/png"
    assert original.content == PNG


def test_original_of_unknown_or_missing_file(client: TestClient, engine: Engine) -> None:
    item_id = _create(engine, ItemStatus.NEW)  # raw_content_ref "x" does not exist

    assert client.get(f"/items/{item_id}/original").status_code == 404
    assert client.get("/items/999/original").status_code == 404


def test_invalid_visual_media_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="media type"):
        VisualInput("image/gif", b"GIF89a")


# --- anthropic provider: fallback switch and visual blocks ---


class _Stub:
    def __init__(self, model: str = "claude-opus-5-5") -> None:
        self.kwargs: dict[str, object] = {}
        self.model = model

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            model=self.model,
        )


def test_fallback_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    stub = _Stub()

    completion = AnthropicProvider(messages_api=stub).complete(
        task="classify", system="s", user="u"
    )

    assert "fallbacks" not in stub.kwargs
    assert "betas" not in stub.kwargs
    assert completion.params["fallback_enabled"] is False


def test_fallback_enabled_records_actual_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK", "true")
    stub = _Stub(model="claude-opus-5")

    completion = AnthropicProvider(messages_api=stub).complete(task="extract", system="s", user="u")

    assert stub.kwargs["fallbacks"] == "default"
    assert stub.kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert completion.model == "claude-opus-5"  # the model that actually answered → audit
    assert completion.params["requested_model"] == "claude-opus-5-5"
    assert completion.params["fallback_used"] is True


def test_visual_inputs_become_image_and_document_blocks() -> None:
    stub = _Stub()
    visuals = (VisualInput("image/png", b"img"), VisualInput("application/pdf", b"pdf"))

    AnthropicProvider(messages_api=stub, fallback=False).complete(
        task="ocr", system="s", user="Übertrage", visuals=visuals
    )

    messages = stub.kwargs["messages"]
    assert isinstance(messages, list)
    content = messages[0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(b"img").decode(),
        },
    }
    assert content[1]["type"] == "document"
    assert content[2] == {"type": "text", "text": "Übertrage"}


def test_text_only_request_keeps_plain_content() -> None:
    stub = _Stub()

    AnthropicProvider(messages_api=stub, fallback=False).complete(
        task="draft", system="s", user="u"
    )

    assert stub.kwargs["messages"] == [{"role": "user", "content": "u"}]


def test_ocr_prompt_marker_matches_prompt_file() -> None:
    text = (Path(__file__).resolve().parent.parent / "prompts" / "ocr_v1.md").read_text()

    assert NO_TEXT_MARKER in text
    assert json.dumps(NO_TEXT_MARKER)  # plain ASCII-safe marker
