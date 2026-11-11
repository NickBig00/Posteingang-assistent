"""B4: real provider errors, invalid API keys, model hints, template improvements.

Provider errors are built from the SDK's own exception classes with stub responses; no
request ever reaches a real API.
"""

import io
from collections.abc import Iterator
from datetime import date, time
from decimal import Decimal

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.cli import main as cli_main
from app.core.image_formats import ImageFormat
from app.core.images.base import ImageProviderError
from app.core.images.fake import FakeImageProvider
from app.core.images.openai_provider import (
    OpenAIImageProvider,
    list_image_models,
)
from app.core.images.service import generate_images
from app.core.llm.base import LLMProviderError
from app.core.llm.openai_provider import OpenAIProvider
from app.core.models import AuditEntry
from app.core.provider_errors import (
    ProviderConfigError,
    api_key_problem,
    describe_provider_error,
    mask_secrets,
)
from app.main import app
from app.social.briefings import create_briefing
from app.social.facts import BriefingFacts
from app.social.graphics import graphic_text, parse_fact_keys, render_graphic
from app.social.models import Briefing, Channel
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config

CONFIG = demo_config()
REQUEST = httpx.Request("POST", "https://api.example.invalid/v1/images/generations")


def _status_error(cls: type[openai.APIStatusError], status: int, message: str, code: str = ""):  # noqa: ANN202
    body = {"message": message, "code": code or None, "type": "invalid_request_error"}
    response = httpx.Response(status, request=REQUEST, json={"error": body})
    return cls(message, response=response, body=body)


class _FailingImages:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate(self, **kwargs: object) -> object:
        raise self.error


def _generate_error(error: Exception) -> str:
    provider = OpenAIImageProvider("bild-modell-test", images_api=_FailingImages(error))
    with pytest.raises(ImageProviderError) as caught:
        provider.generate(prompt="Matten im Licht", fmt=ImageFormat.SQUARE, count=2)
    return str(caught.value)


# --- error mapping ---


def test_unknown_model_shows_the_real_reason() -> None:
    error = _status_error(
        openai.BadRequestError, 400, "The model 'bild-alt' does not exist", "model_not_found"
    )

    message = _generate_error(error)

    assert "nicht verfügbar" in message and "HTTP 400" in message
    assert "does not exist" in message
    assert "nicht erreichbar" not in message


def test_billing_limit_and_content_filter_are_named() -> None:
    billing = _status_error(
        openai.BadRequestError,
        400,
        "Billing hard limit has been reached",
        "billing_hard_limit_reached",
    )
    quota = _status_error(
        openai.RateLimitError, 429, "You exceeded your quota", "insufficient_quota"
    )
    blocked = _status_error(
        openai.BadRequestError,
        400,
        "Request was rejected by the safety system",
        "moderation_blocked",
    )

    assert "Abrechnungs" in _generate_error(billing)
    assert "Abrechnungs" in _generate_error(quota)
    assert "Inhaltsfilter" in _generate_error(blocked)


def test_rejected_key_and_rate_limit() -> None:
    unauthorized = _status_error(openai.AuthenticationError, 401, "Incorrect API key provided")
    busy = _status_error(openai.RateLimitError, 429, "Rate limit reached")

    assert "OPENAI_API_KEY" in _generate_error(unauthorized)
    assert "Zu viele Anfragen" in _generate_error(busy)


def test_not_reachable_only_for_connection_errors() -> None:
    message = _generate_error(openai.APIConnectionError(request=REQUEST))

    assert "nicht erreichbar" in message


def test_keys_never_appear_in_messages() -> None:
    error = _status_error(
        openai.AuthenticationError, 401, "Incorrect API key provided: sk-proj-abc123DEF456ghi"
    )

    message = _generate_error(error)

    assert "abc123" not in message and "[Schlüssel entfernt]" in message
    assert mask_secrets("key sk-test_12345678 end") == "key [Schlüssel entfernt] end"


def test_long_provider_messages_are_shortened() -> None:
    error = _status_error(openai.BadRequestError, 400, "x " * 400)

    assert len(describe_provider_error(error, "Bildanbieter")) < 420


def test_the_real_reason_goes_into_the_audit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")
    error = _status_error(
        openai.BadRequestError, 400, "The model does not exist", "model_not_found"
    )
    provider = OpenAIImageProvider("bild-modell-test", images_api=_FailingImages(error))

    with pytest.raises(ImageProviderError):
        generate_images(
            session,
            provider,
            prompt="Matten",
            fmt=ImageFormat.SQUARE,
            step="test.image",
            scope="draft:1",
            user="demo",
        )

    (entry,) = session.exec(select(AuditEntry)).all()
    assert entry.error and "nicht verfügbar" in entry.error


def test_llm_status_errors_carry_the_reason() -> None:
    class _Failing:
        def create(self, **kwargs: object) -> object:
            raise _status_error(openai.NotFoundError, 404, "The model x does not exist")

    provider = OpenAIProvider("text-modell-test", completions_api=_Failing())

    with pytest.raises(LLMProviderError, match="HTTP 404.*nicht verfügbar"):
        provider.complete(task="post_draft", system="s", user="u")


# --- invalid API keys ---


@pytest.mark.parametrize("value", ["„dein-schlüssel“", "sk-abc def", "sk-ä123"])
def test_non_ascii_or_spaces_in_the_key_are_detected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", value)

    problem = api_key_problem("OPENAI_API_KEY")

    assert problem and "OPENAI_API_KEY" in problem
    assert value not in problem


def test_valid_or_missing_key_is_no_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    assert api_key_problem("OPENAI_API_KEY") is None
    monkeypatch.delenv("OPENAI_API_KEY")
    assert api_key_problem("OPENAI_API_KEY") is None


def test_providers_refuse_an_invalid_key_on_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "„Platzhalter“")
    monkeypatch.setenv("LLM_MODEL", "text-modell-test")

    with pytest.raises(ProviderConfigError, match="unzulässige Zeichen"):
        OpenAIProvider()
    with pytest.raises(ProviderConfigError, match="unzulässige Zeichen"):
        OpenAIImageProvider("bild-modell-test")


def test_unicode_error_during_a_request_becomes_a_provider_error() -> None:
    error = UnicodeEncodeError("ascii", "„", 0, 1, "ordinal not in range")

    assert "unzulässige Zeichen" in _generate_error(error)


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_briefing_with_invalid_key_shows_a_clear_page_instead_of_500(
    social_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "text-modell-test")
    monkeypatch.setenv("OPENAI_API_KEY", "„hier-schlüssel-eintragen“")
    app.dependency_overrides.pop(get_social_client, None)

    response = social_client.post(
        "/social/briefings",
        data={
            "area": "kurse",
            "occasion_type": "kurs",
            "notes": "Rückenkurs",
            "datum": "2026-11-12",
            "uhrzeit": "18:30",
            "ort": "Kursraum 2",
            "channels": "facebook",
        },
    )

    assert response.status_code == 503
    assert "data-config-error" in response.text
    assert "OPENAI_API_KEY" in response.text
    assert "hier-schlüssel" not in response.text


def test_settings_flag_an_invalid_key_without_showing_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant mit leerzeichen")

    page = client.get("/settings").text

    assert "data-config-problems" in page and "ANTHROPIC_API_KEY" in page
    assert "leerzeichen" not in page


# --- model list ---


class _Model:
    def __init__(self, model_id: str) -> None:
        self.id = model_id


class _Models:
    def list(self) -> list[_Model]:
        return [_Model("text-modell-a"), _Model("bild-image-b"), _Model("Bild-IMAGE-a")]


def test_image_models_are_listed_from_the_account() -> None:
    assert list_image_models(_Models()) == ["Bild-IMAGE-a", "bild-image-b"]


def test_image_models_command_reports_a_missing_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "„x“")

    assert cli_main(["image-models"]) == 1
    assert "unzulässige Zeichen" in capsys.readouterr().err


def test_env_example_names_no_fixed_image_model() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("IMAGE_MODEL="))
    assert line == "IMAGE_MODEL="
    assert "gpt-image-1" not in text and "app.cli image-models" in text


# --- templates ---


COURSE = BriefingFacts(datum=date(2026, 11, 12), uhrzeit=time(18, 30), ort="Kursraum 2")
ALL = BriefingFacts(
    datum=date(2026, 11, 12),
    uhrzeit=time(18, 30),
    ort="Kursraum 2",
    preis=Decimal("19.00"),
)


def _briefing(facts: BriefingFacts = COURSE) -> Briefing:
    return Briefing(
        id=1,
        brand_key="demo",
        area="kurse",
        occasion_type="kurs",
        notes="Stichpunkte",
        facts=facts.to_json(),
        channels=[],
        created_by="demo",
    )


def test_occasion_appears_once_without_own_headline() -> None:
    text = graphic_text(CONFIG, _briefing())

    assert text.tag == "" and text.headline == "Kurs"
    assert [line.casefold() for line in text.lines].count("kurs") == 1


def test_tag_stays_with_an_own_headline() -> None:
    text = graphic_text(CONFIG, _briefing(), "Rücken-Fit")

    assert text.tag == "Kurs" and text.headline == "Rücken-Fit"


def test_only_chosen_facts_are_shown_and_checked() -> None:
    text = graphic_text(CONFIG, _briefing(ALL), fact_keys=["datum", "preis"])

    assert [label for label, _ in text.facts] == ["Datum", "Preis"]
    jpeg, rendered = render_graphic(CONFIG, _briefing(ALL), ImageFormat.SQUARE, fact_keys=["preis"])
    assert jpeg and [label for label, _ in rendered.facts] == ["Preis"]


def test_unknown_or_empty_choice_means_all_facts() -> None:
    assert parse_fact_keys(["telefon", "unbekannt"], COURSE) == ("datum", "uhrzeit", "ort")
    assert parse_fact_keys([], COURSE) == ("datum", "uhrzeit", "ort")


def _text_rows(jpeg: bytes, primary: tuple[int, int, int]) -> list[int]:
    """Rows of the image that contain pixels clearly different from the background."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    width, height = image.size
    pixels = image.load()
    rows = []
    for y in range(0, height, 6):
        hits = sum(
            1
            for x in range(round(width * 0.075), round(width * 0.5), 6)
            if sum(abs(a - b) for a, b in zip(pixels[x, y], primary, strict=True)) > 150
        )
        if hits > 2:
            rows.append(y)
    return rows


def test_colour_fill_uses_the_height_of_portrait_formats() -> None:
    from app.core.theme import parse_hex

    assert CONFIG.brand.design is not None
    primary = parse_hex(CONFIG.brand.design.primary_color)
    jpeg, _ = render_graphic(CONFIG, _briefing(), ImageFormat.STORY)

    rows = [y for y in _text_rows(jpeg, primary) if y > 1920 * 0.15]
    # The text block sits in the middle of the free area, not squeezed to the bottom edge.
    assert rows and min(rows) < 1920 * 0.55
    assert max(rows) < 1920 * 0.85


def test_wunschtermin_is_explained_in_the_form(social_client: TestClient) -> None:
    page = social_client.get("/social/briefings/new").text

    assert "kein Kalendertermin" in page


def test_graphic_has_fact_choice_and_background_links(
    social_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.social.drafts import generate_drafts
    from app.social.llm.client import SocialLLMClient
    from tests.social_helpers import scripted

    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)

    page = social_client.get(f"/social/briefings/{briefing.id}?graphic_facts=datum").text

    assert 'name="graphic_facts" value="datum" checked' in page
    assert 'name="graphic_facts" value="ort" >' in page or 'value="ort" ' in page
    assert f'href="#images-{draft.id}"' in page and "KI-Bild erzeugen" in page
    assert "facts=datum" in page and "facts=ort" not in page
    response = social_client.get(
        f"/social/briefings/{briefing.id}/graphic.jpg?format=1:1&facts=datum"
    )
    assert response.status_code == 200
    assert FakeImageProvider  # image provider not involved in graphics
