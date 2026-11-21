"""B8: editing own photos with AI (fake provider only): label, consent, brake, deletion."""

import base64
import io
from collections.abc import Iterator
from pathlib import Path

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.image_formats import FORMAT_SPECS, ImageFormat
from app.core.images.base import ImageBudgetExceededError, ImageProviderError
from app.core.images.fake import FakeImageProvider
from app.core.images.openai_provider import OpenAIImageProvider
from app.core.llm.fake import FakeProvider
from app.core.models import AuditEntry
from app.main import app
from app.social.assets import consent_blockers, delete_asset, revoke_consent, save_asset
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import Asset, Briefing, Channel
from app.social.photo_edit import (
    EDIT_SAFETY_SUFFIX,
    EDIT_STEP,
    HINT_CONFIRM_STEP,
    PhotoEditError,
    confirm_edit_hint,
    edit_photo,
)
from app.social.retention import delete_briefing
from app.social.web import get_social_client, get_social_config, get_social_image_provider
from tests.social_helpers import course_input, demo_config

CONFIG = demo_config()


@pytest.fixture(autouse=True)
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")


def _png(colour: tuple[int, int, int] = (90, 120, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _briefing(session: Session) -> Briefing:
    briefing = create_briefing(
        session, CONFIG, course_input(channels=[Channel.INSTAGRAM_POST]), "demo"
    )
    generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider()), briefing)
    return briefing


def _photo(session: Session, briefing: Briefing, storage: Path, **kw: bool) -> Asset:
    return save_asset(
        session,
        briefing,
        "kursraum.png",
        _png(),
        shows_persons=kw.get("persons", False),
        consent_confirmed=kw.get("consent", False),
        user="demo",
        storage=storage,
    )


def _edit(
    session: Session,
    asset: Asset,
    storage: Path,
    instruction: str = "Hintergrund heller",
    provider: FakeImageProvider | None = None,
) -> Asset:
    return edit_photo(
        session,
        provider or FakeImageProvider(),
        asset,
        instruction=instruction,
        fmt=ImageFormat.STORY,
        user="demo",
        storage=storage,
    )


def test_edit_creates_a_new_labelled_image_and_keeps_the_original(
    session: Session, raw_dir: Path
) -> None:
    briefing = _briefing(session)
    original = _photo(session, briefing, raw_dir)
    original_bytes = Path(original.stored_path).read_bytes()
    provider = FakeImageProvider()

    edited = _edit(session, original, raw_dir, provider=provider)

    assert edited.id != original.id and edited.ai_edited and not edited.ai_generated
    assert edited.source_asset_id == original.id
    assert edited.filename.startswith("ki-bearbeitet-")
    assert Path(original.stored_path).read_bytes() == original_bytes
    result = Image.open(edited.stored_path)
    spec = FORMAT_SPECS[ImageFormat.STORY]
    assert result.width / result.height == pytest.approx(spec.width / spec.height, rel=0.02)
    instruction, media_type = provider.edits[0]
    assert instruction.startswith("Hintergrund heller") and EDIT_SAFETY_SUFFIX in instruction
    assert media_type == "image/png"


def test_edit_is_audited_with_hash_model_and_cost(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    original = _photo(session, briefing, raw_dir)

    _edit(session, original, raw_dir, "Kabel am Boden entfernen")

    entry = session.exec(select(AuditEntry).where(AuditEntry.step == EDIT_STEP)).one()
    assert entry.model == "fake-image-v1"
    assert entry.model_params["asset_id"] == original.id
    assert entry.model_params["briefing_id"] == briefing.id
    assert "cost_estimate_eur" in entry.model_params
    assert "Kabel" not in str(entry.model_params)


def test_photos_with_persons_need_confirmed_consent(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    without = _photo(session, briefing, raw_dir, persons=True, consent=False)
    provider = FakeImageProvider()

    with pytest.raises(PhotoEditError, match="Einwilligung"):
        _edit(session, without, raw_dir, provider=provider)
    assert provider.edits == []

    with_consent = _photo(session, briefing, raw_dir, persons=True, consent=True)
    edited = _edit(session, with_consent, raw_dir)
    assert edited.shows_persons and edited.consent_confirmed


def test_misleading_instruction_gives_a_hint_that_blocks_approval(
    session: Session, raw_dir: Path
) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir, persons=True, consent=True)

    edited = _edit(session, photo, raw_dir, "Person etwas schlanker machen")

    assert edited.edit_hint and "Körperform" in edited.edit_hint
    assert any("KI-bearbeiteten Bild" in b for b in consent_blockers(session, briefing.id or 0))
    confirm_edit_hint(session, edited, "demo")
    assert not any("KI-bearbeiteten" in b for b in consent_blockers(session, briefing.id or 0))
    assert session.exec(select(AuditEntry).where(AuditEntry.step == HINT_CONFIRM_STEP)).one()


@pytest.mark.parametrize("instruction", ["", "x" * 501, "Telefon 01234 567890 einfügen"])
def test_invalid_instructions_are_refused(
    session: Session, raw_dir: Path, instruction: str
) -> None:
    photo = _photo(session, _briefing(session), raw_dir)

    with pytest.raises(PhotoEditError):
        _edit(session, photo, raw_dir, instruction)


def test_ai_images_are_changed_by_prompt_not_edit(session: Session, raw_dir: Path) -> None:
    photo = _photo(session, _briefing(session), raw_dir)
    photo.ai_generated = True

    with pytest.raises(PhotoEditError, match="Bild-Prompt"):
        _edit(session, photo, raw_dir)


def test_switched_off_and_cost_brake(
    session: Session, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = _photo(session, _briefing(session), raw_dir)
    monkeypatch.setenv("IMAGE_MAX_PER_POST", "1")
    _edit(session, photo, raw_dir)

    with pytest.raises(ImageBudgetExceededError):
        _edit(session, photo, raw_dir)
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "false")
    with pytest.raises(PhotoEditError, match="ausgeschaltet"):
        _edit(session, photo, raw_dir)


def test_edits_count_for_the_daily_limit_of_all_image_calls(
    session: Session, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.images.service import usage

    photo = _photo(session, _briefing(session), raw_dir)
    _edit(session, photo, raw_dir)

    assert usage(session, "social.image_generate", "draft:1").today == 1


def test_revoking_the_consent_also_removes_edited_versions(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir, persons=True, consent=True)
    first = _edit(session, photo, raw_dir)
    second = _edit(session, first, raw_dir, "Wärmeres Licht")

    revoke_consent(session, photo, "demo")

    for asset in (first, second):
        session.refresh(asset)
        assert asset.consent_revoked_at is not None
        assert not Path(asset.stored_path).exists()


def test_deleting_the_original_keeps_the_edit_and_briefing_deletion_removes_all(
    session: Session, raw_dir: Path
) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir)
    edited = _edit(session, photo, raw_dir)

    delete_asset(session, photo, "demo")
    session.refresh(edited)
    assert Path(edited.stored_path).exists() and edited.source_asset_id is not None

    delete_briefing(session, briefing.id or 0, "demo")
    assert session.exec(select(Asset)).all() == []
    assert not Path(edited.stored_path).exists()


# --- OpenAI provider (stub, no real API) ---


class _Images:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> object:
        raise NotImplementedError

    def edit(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        item = type("Item", (), {"b64_json": base64.b64encode(_png()).decode()})()
        return type("Response", (), {"data": [item]})()


def test_openai_edit_request_and_errors() -> None:
    api = _Images()
    batch = OpenAIImageProvider("bild-modell-test", images_api=api).edit(
        image=_png(), media_type="image/png", instruction="Heller", fmt=ImageFormat.STORY
    )
    assert api.requests[0]["size"] == "1024x1536" and api.requests[0]["n"] == 1
    assert api.requests[0]["image"][0] == "foto.png"  # type: ignore[index]
    assert batch.params["operation"] == "edit" and len(batch.images) == 1

    body = {
        "message": "Your request was rejected by the safety system",
        "code": "moderation_blocked",
    }
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://x.invalid"), json={"error": body}
    )
    failing = _Images(openai.BadRequestError("x", response=response, body=body))
    with pytest.raises(ImageProviderError, match="Inhaltsfilter"):
        OpenAIImageProvider("bild-modell-test", images_api=failing).edit(
            image=_png(), media_type="image/png", instruction="Heller", fmt=ImageFormat.SQUARE
        )


# --- web ---


@pytest.fixture
def web(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(FakeProvider())
    app.dependency_overrides[get_social_config] = demo_config
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    yield client


def test_edit_in_the_ui_and_labelled_download(
    web: TestClient, session: Session, raw_dir: Path
) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir)
    url = f"/social/briefings/{briefing.id}"
    assert "Foto mit KI bearbeiten" in web.get(url).text

    page = web.post(
        f"/social/assets/{photo.id}/edit",
        data={"quick": "Wärmeres Licht", "image_format": "4:5"},
        follow_redirects=True,
    ).text
    assert "data-ai-edited" in page and "KI-bearbeitet" in page

    edited = session.exec(select(Asset).where(Asset.ai_edited)).one()
    graphic = web.get(f"{url}/graphic.jpg?background={edited.id}")
    assert graphic.status_code == 200
    assert "ki-bearbeitet" in graphic.headers["content-disposition"]


def test_edit_route_is_hidden_when_switched_off(
    web: TestClient, session: Session, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = _photo(session, _briefing(session), raw_dir)
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "false")

    assert web.post(f"/social/assets/{photo.id}/edit", data={"instruction": "x"}).status_code == 404
    assert "Foto mit KI bearbeiten" not in web.get(f"/social/briefings/{photo.briefing_id}").text
