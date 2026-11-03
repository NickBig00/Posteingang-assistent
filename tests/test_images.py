"""Image interface (ROADMAP_social B1): switch, providers, cost brake, audit. Only fakes."""

import base64
import io
import json
from datetime import UTC, datetime, timedelta

import openai
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.audit import audit_outputs
from app.core.image_formats import FORMAT_SPECS, ImageFormat
from app.core.images.base import (
    ImageBatch,
    ImageBudgetExceededError,
    ImageGenerationDisabledError,
    ImageProvider,
    ImageProviderError,
)
from app.core.images.fake import FAKE_MODEL, FakeImageProvider, placeholder
from app.core.images.openai_provider import OpenAIImageConfigError, OpenAIImageProvider
from app.core.images.service import (
    budget_blocker,
    generate_images,
    get_image_provider,
    image_generation_enabled,
    image_limits,
)
from app.core.models import AuditEntry

STEP = "social.image_generate"
PROMPT = "Helle Trainingsfläche mit Pflanzen, ruhige Stimmung, keine Personen, kein Text"


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")


def _generate(
    session: Session, provider: ImageProvider | None = None, scope: str = "draft:1"
) -> ImageBatch:
    return generate_images(
        session,
        provider or FakeImageProvider(cost_per_image_eur=0.05),
        prompt=PROMPT,
        fmt=ImageFormat.PORTRAIT,
        step=STEP,
        scope=scope,
        user="demo",
        details={"briefing_id": 1, "draft_id": 1},
    )


def _entries(session: Session) -> list[AuditEntry]:
    return list(session.exec(select(AuditEntry).where(AuditEntry.step == STEP)).all())


# --- switch ---


def test_switched_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAGE_GENERATION_ENABLED", raising=False)

    assert image_generation_enabled() is False


def test_switched_off_generates_nothing(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "false")
    provider = FakeImageProvider()

    with pytest.raises(ImageGenerationDisabledError):
        _generate(session, provider)

    assert provider.calls == []
    assert _entries(session) == []


# --- generation and audit ---


def test_generation_returns_variants_and_is_audited(session: Session, enabled: None) -> None:
    batch = _generate(session)

    assert len(batch.images) == 3
    (entry,) = _entries(session)
    assert entry.model == FAKE_MODEL
    assert entry.model_params["variants"] == 3
    assert entry.model_params["format"] == "4:5"
    assert entry.model_params["cost_estimate_eur"] == pytest.approx(0.15)
    assert entry.model_params["briefing_id"] == 1
    assert entry.model_params["actor"] == "demo"
    # The prompt is not in the immutable entry, only its hash and the deletable output.
    assert PROMPT not in json.dumps(entry.model_params)
    output = json.loads(audit_outputs(session, [entry.id or 0])[entry.id or 0])
    assert output["prompt"] == PROMPT
    assert len(output["image_hashes"]) == 3


def test_number_of_variants_is_configurable_between_two_and_three(
    session: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_VARIANTS", "2")
    assert len(_generate(session).images) == 2
    monkeypatch.setenv("IMAGE_VARIANTS", "9")
    assert image_limits().variants == 3


class _BrokenProvider(ImageProvider):
    name = "kaputt"

    def generate(self, *, prompt: str, fmt: ImageFormat, count: int) -> ImageBatch:  # noqa: ARG002
        raise ImageProviderError("Zeitüberschreitung")


def test_provider_error_is_audited_and_not_counted(session: Session, enabled: None) -> None:
    with pytest.raises(ImageProviderError):
        _generate(session, _BrokenProvider())

    (entry,) = _entries(session)
    assert entry.error == "Zeitüberschreitung"
    assert budget_blocker(session, STEP, "draft:1") is None


# --- cost brake ---


def test_brake_per_post(session: Session, enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_MAX_PER_POST", "2")
    _generate(session)
    _generate(session)

    with pytest.raises(ImageBudgetExceededError, match="für diesen Beitrag"):
        _generate(session)
    _generate(session, scope="draft:2")  # another post still works


def test_brake_per_day(session: Session, enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_MAX_PER_DAY", "2")
    _generate(session, scope="draft:1")
    _generate(session, scope="draft:2")

    with pytest.raises(ImageBudgetExceededError, match="Tageslimit"):
        _generate(session, scope="draft:3")
    tomorrow = datetime.now(UTC) + timedelta(days=1, minutes=1)
    assert budget_blocker(session, STEP, "draft:3", now=tomorrow) is None


# --- providers ---


def test_factory_defaults_to_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)

    assert isinstance(get_image_provider(), FakeImageProvider)
    with pytest.raises(ValueError, match="unknown image provider"):
        get_image_provider("unbekannt")


def test_openai_needs_a_deliberate_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAGE_MODEL", raising=False)

    with pytest.raises(OpenAIImageConfigError, match="IMAGE_MODEL"):
        OpenAIImageProvider(images_api=_StubImagesAPI())


@pytest.mark.parametrize("fmt", list(ImageFormat))
def test_fake_placeholder_keeps_the_aspect_ratio(fmt: ImageFormat) -> None:
    image = Image.open(io.BytesIO(placeholder(PROMPT, fmt, 0)))
    spec = FORMAT_SPECS[fmt]

    assert image.width / image.height == pytest.approx(spec.width / spec.height, rel=0.01)
    assert placeholder(PROMPT, fmt, 0) == placeholder(PROMPT, fmt, 0)
    assert placeholder(PROMPT, fmt, 0) != placeholder(PROMPT, fmt, 1)


class _Item:
    def __init__(self, b64: str | None) -> None:
        self.b64_json = b64


class _Response:
    def __init__(self, items: list[_Item]) -> None:
        self.data = items


class _StubImagesAPI:
    """Stands in for `openai.OpenAI().images`; tests never call the real API."""

    def __init__(self, items: list[_Item] | None = None, error: Exception | None = None) -> None:
        self.items = items or []
        self.error = error
        self.requests: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> _Response:
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return _Response(self.items)


def test_openai_provider_request_and_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_COST_PER_IMAGE_EUR", "0,04")
    png = placeholder(PROMPT, ImageFormat.SQUARE, 0)
    api = _StubImagesAPI([_Item(base64.b64encode(png).decode())] * 2)

    batch = OpenAIImageProvider("bild-modell-test", images_api=api).generate(
        prompt=PROMPT, fmt=ImageFormat.STORY, count=2
    )

    assert api.requests[0]["size"] == "1024x1536"
    assert api.requests[0]["n"] == 2
    assert [image.content for image in batch.images] == [png, png]
    assert batch.cost_estimate_eur == pytest.approx(0.08)
    assert batch.model == "bild-modell-test"


def test_openai_provider_errors_become_provider_errors() -> None:
    api = _StubImagesAPI(error=openai.APIConnectionError(request=None))  # type: ignore[arg-type]

    with pytest.raises(ImageProviderError):
        OpenAIImageProvider("bild-modell-test", images_api=api).generate(
            prompt=PROMPT, fmt=ImageFormat.SQUARE, count=2
        )
    empty = _StubImagesAPI([_Item(None)])
    with pytest.raises(ImageProviderError, match="kein Bild"):
        OpenAIImageProvider("bild-modell-test", images_api=empty).generate(
            prompt=PROMPT, fmt=ImageFormat.SQUARE, count=2
        )


# --- settings page ---


def test_settings_show_the_switch(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAGE_GENERATION_ENABLED", raising=False)
    assert "IMAGE_GENERATION_ENABLED" in client.get("/settings").text
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")
    assert "Erzeugungen je Beitrag" in client.get("/settings").text


def test_provider_sdks_are_only_imported_in_the_core_interfaces() -> None:
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    allowed = {app_dir / "core" / "llm", app_dir / "core" / "images"}
    offenders = [
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+(openai|anthropic)\b", path.read_text(), re.M)
        and path.parent not in allowed
    ]
    assert offenders == []
