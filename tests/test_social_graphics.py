"""Text on the image with only a headline (U6): replaces the former template graphic (B3).

The info graphic with a headline and the facts is what "Grafik aus Vorlage" used to be: the
same fact fidelity, formats, JPEG export, logo and readable brand colours.
"""

import io
from collections.abc import Iterator
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from app.core.image_formats import FORMAT_SPECS, ImageFormat
from app.core.profiles import PROFILES_DIR, ProfileError
from app.core.theme import WHITE, contrast_ratio, parse_hex
from app.main import app
from app.social.briefings import create_briefing
from app.social.facts import BriefingFacts
from app.social.infographic import InfoGraphicError, InfoTexts, render_info_graphic
from app.social.llm.client import SocialLLMClient
from app.social.models import Briefing, Channel
from app.social.profiles import DEFAULT_BRAND_PROFILES_DIR, SocialConfig, load_social_config
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
HANDWERK = load_social_config(
    PROFILES_DIR / "handwerk.yaml", DEFAULT_BRAND_PROFILES_DIR / "handwerk_demo.yaml"
)
COURSE = BriefingFacts(datum=date(2026, 11, 12), uhrzeit=time(18, 30), ort="Kursraum 2")
OFFER = BriefingFacts(
    zeitraum_von=date(2026, 11, 1), zeitraum_bis=date(2026, 11, 14), preis=Decimal("19.00")
)
ALL_FACTS = BriefingFacts(
    datum=date(2026, 11, 12),
    uhrzeit=time(18, 30),
    ort="Kursraum 2",
    preis=Decimal("19.00"),
    zeitraum_von=date(2026, 11, 1),
    zeitraum_bis=date(2026, 11, 14),
    telefon="01234 567890",
)


def _briefing(
    occasion: str = "kurs", facts: BriefingFacts = COURSE, area: str = "kurse"
) -> Briefing:
    return Briefing(
        id=1,
        brand_key="demo",
        area=area,
        occasion_type=occasion,
        notes="Stichpunkte",
        facts=facts.to_json(),
        channels=[],
        created_by="demo",
    )


def _png(size: tuple[int, int] = (640, 480)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 140, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


HEADLINE = InfoTexts(headline="Rücken-Fit")


def _render(
    config: SocialConfig = CONFIG,
    briefing: Briefing | None = None,
    fmt: ImageFormat = ImageFormat.PORTRAIT,
    texts: InfoTexts = HEADLINE,
    background: bytes | None = None,
) -> bytes:
    return render_info_graphic(config, briefing or _briefing(), texts, fmt, background=background)


# --- fact check ---


@pytest.mark.parametrize(
    "headline",
    ["Neu ab 01.12.2026", "Start um 19:00 Uhr", "Nur 9,99 €", "Ruf an: 0987 654321"],
)
def test_headline_must_not_add_facts(headline: str) -> None:
    with pytest.raises(InfoGraphicError):
        _render(texts=InfoTexts(headline=headline))


def test_headline_alone_is_enough() -> None:
    """Without list, intro or closing: the old template (headline plus facts)."""
    assert _render(texts=InfoTexts(headline="Kurs"))


# --- rendering ---


@pytest.mark.parametrize("fmt", list(ImageFormat))
@pytest.mark.parametrize("background", [None, "photo"])
def test_formats_and_jpeg_export(fmt: ImageFormat, background: str | None) -> None:
    jpeg = _render(fmt=fmt, background=_png() if background else None)

    image = Image.open(io.BytesIO(jpeg))
    spec = FORMAT_SPECS[fmt]
    assert image.format == "JPEG"
    assert image.size == (spec.width, spec.height)


def test_rendering_is_deterministic() -> None:
    assert _render() == _render()


def test_long_place_wraps_instead_of_being_cut() -> None:
    long_place = (
        "Kursraum 2 im Erdgeschoss des Nebengebäudes neben dem großen Parkplatz am Beispielweg"
    )
    facts = BriefingFacts(datum=date(2026, 11, 12), uhrzeit=time(18, 30), ort=long_place)

    assert _render(briefing=_briefing(facts=facts), fmt=ImageFormat.STORY)


def _facts_for(config: SocialConfig, occasion: str) -> BriefingFacts:
    required = set(config.industry.occasion_type(occasion).required_facts)
    values: dict[str, object] = {}
    if "datum" in required:
        values["datum"] = date(2026, 11, 12)
    if "uhrzeit" in required:
        values["uhrzeit"] = time(18, 30)
    if "ort" in required:
        values["ort"] = "Kursraum 2"
    if "preis" in required:
        values["preis"] = Decimal("19.00")
    if "zeitraum" in required:
        values |= {"zeitraum_von": date(2026, 11, 1), "zeitraum_bis": date(2026, 11, 14)}
    if "telefon" in required:
        values["telefon"] = "01234 567890"
    return BriefingFacts.model_validate(values)


@pytest.mark.parametrize("config", [CONFIG, HANDWERK], ids=["fitness", "handwerk"])
def test_every_occasion_type_of_both_industries_renders(config: SocialConfig) -> None:
    area = config.brand.areas[0].key
    for occasion in config.industry.occasion_types:
        briefing = _briefing(occasion.key, _facts_for(config, occasion.key), area)
        texts = InfoTexts(headline=occasion.label)
        assert _render(config, briefing, texts=texts), occasion.key


def test_all_facts_fit_on_every_format() -> None:
    for fmt in ImageFormat:
        assert _render(briefing=_briefing(facts=ALL_FACTS), fmt=fmt)


@pytest.mark.parametrize("config", [CONFIG, HANDWERK], ids=["fitness", "handwerk"])
def test_brand_colours_keep_text_readable(config: SocialConfig) -> None:
    assert config.brand.design is not None
    primary = parse_hex(config.brand.design.primary_color)

    assert contrast_ratio(primary, WHITE) >= 4.5


# --- web ---


@pytest.fixture
def graphic_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_template_graphic_route_is_gone(graphic_client: TestClient, session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")

    assert graphic_client.get(f"/social/briefings/{briefing.id}/graphic.jpg").status_code == 404
    page = graphic_client.get(f"/social/briefings/{briefing.id}").text
    assert "Grafik aus Vorlage" not in page


# --- logo from the brand profile ---


def _brand_with_logo(tmp_path: Path, logo: str | None = "logo.png", create: bool = True) -> Path:
    source = (DEFAULT_BRAND_PROFILES_DIR / "demo.yaml").read_text(encoding="utf-8")
    if logo is not None:
        source = source.replace("  font: inter\n", f"  font: inter\n  logo: {logo}\n", 1)
    path = tmp_path / "demo.yaml"
    path.write_text(source, encoding="utf-8")
    if create and logo is not None and "/" not in logo:
        image = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
        image.paste((250, 250, 250, 255), (0, 30, 400, 90))
        image.save(tmp_path / logo)
    return path


def test_logo_replaces_the_brand_name(tmp_path: Path) -> None:
    config = load_social_config(PROFILES_DIR / "fitness.yaml", _brand_with_logo(tmp_path))

    with_logo = _render(config, fmt=ImageFormat.SQUARE)
    without_logo = _render(CONFIG, fmt=ImageFormat.SQUARE)

    assert config.logo_path is not None
    assert with_logo != without_logo


def test_missing_logo_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="Logo-Datei"):
        load_social_config(PROFILES_DIR / "fitness.yaml", _brand_with_logo(tmp_path, create=False))


@pytest.mark.parametrize("logo", ["../logo.png", "/etc/logo.png", "logo.svg"])
def test_logo_must_be_a_plain_image_file_name(tmp_path: Path, logo: str) -> None:
    with pytest.raises(ProfileError):
        load_social_config(
            PROFILES_DIR / "fitness.yaml", _brand_with_logo(tmp_path, f'"{logo}"', create=False)
        )
