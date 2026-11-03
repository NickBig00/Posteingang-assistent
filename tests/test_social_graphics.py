"""Graphics from templates (ROADMAP_social B3): fact fidelity, formats, JPEG export."""

import dataclasses
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
from app.social.assets import save_asset
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.facts import BriefingFacts, formatted_facts
from app.social.graphics import (
    GraphicError,
    graphic_text,
    layout_for,
    render_graphic,
    verify_graphic_text,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import Briefing, Channel
from app.social.profiles import DEFAULT_BRAND_PROFILES_DIR, SocialConfig, load_social_config
from app.social.review import approve_draft
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


# --- text and fact check ---


def test_every_fact_appears_exactly_in_display_spelling() -> None:
    text = graphic_text(CONFIG, _briefing(facts=ALL_FACTS))

    for key, value in formatted_facts(ALL_FACTS).items():
        assert sum(value in line for line in text.lines) >= 1, key
    verify_graphic_text(text, ALL_FACTS, CONFIG)  # does not raise


@pytest.mark.parametrize(
    ("occasion", "layout", "first"),
    [
        ("kurs", "ankuendigung", "Datum"),
        ("aktion", "angebot", "Preis"),
        ("oeffnungszeiten", "hinweis", "Telefonnummer"),
        ("team", "standard", "Datum"),
    ],
)
def test_template_per_occasion_type(occasion: str, layout: str, first: str) -> None:
    text = graphic_text(CONFIG, _briefing(occasion, ALL_FACTS, "fitness"))

    assert layout_for(occasion) == layout
    assert text.facts[0][0] == first


def test_headline_defaults_to_the_occasion() -> None:
    assert graphic_text(CONFIG, _briefing()).headline == "Kurs"
    assert graphic_text(CONFIG, _briefing(), "  Rücken-Fit   am Abend ").headline == (
        "Rücken-Fit am Abend"
    )


def _tampered(field: str, value: object) -> None:
    text = graphic_text(CONFIG, _briefing())
    verify_graphic_text(dataclasses.replace(text, **{field: value}), COURSE, CONFIG)


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        ([("Datum", "13.11.2026"), ("Uhrzeit", "18:30 Uhr"), ("Ort", "Kursraum 2")], "Datum"),
        ([("Datum", "12.11.2026"), ("Uhrzeit", "19:30 Uhr"), ("Ort", "Kursraum 2")], "Uhrzeit"),
        ([("Datum", "12.11.2026"), ("Uhrzeit", "18:30 Uhr")], "Ort"),
        (
            [
                ("Datum", "12.11.2026"),
                ("Datum", "12.11.2026"),
                ("Uhrzeit", "18:30 Uhr"),
                ("Ort", "Kursraum 2"),
            ],
            "genau einmal",
        ),
    ],
)
def test_changed_missing_or_doubled_facts_are_rejected(
    facts: list[tuple[str, str]], message: str
) -> None:
    with pytest.raises(GraphicError, match=message):
        _tampered("facts", facts)


@pytest.mark.parametrize(
    ("headline", "message"),
    [
        ("Auch am 19.11. dabei", "19.11."),
        ("Ab 20 Uhr geöffnet", "Uhrzeit"),
        ("Nur 9,90 € im Monat", "Preis"),
        ("Anrufen: 0987 654321", "Telefonnummer"),
        ("Schnäppchen-Kurs", "Schnäppchen"),
        ("Eine viel zu lange Überschrift für diese Grafik hier", "zu lang"),
    ],
)
def test_headline_must_not_add_facts_or_forbidden_terms(headline: str, message: str) -> None:
    text = graphic_text(CONFIG, _briefing(), headline)

    with pytest.raises(GraphicError, match=message):
        verify_graphic_text(text, COURSE, CONFIG)


def test_rendering_refuses_a_failing_fact_check() -> None:
    with pytest.raises(GraphicError):
        render_graphic(CONFIG, _briefing(), ImageFormat.SQUARE, headline="Neu ab 01.12.2026")


# --- rendering ---


@pytest.mark.parametrize("fmt", list(ImageFormat))
@pytest.mark.parametrize("background", [None, "photo"])
def test_formats_and_jpeg_export(fmt: ImageFormat, background: str | None) -> None:
    content = _png() if background else None

    jpeg, _ = render_graphic(CONFIG, _briefing(), fmt, background=content)

    image = Image.open(io.BytesIO(jpeg))
    spec = FORMAT_SPECS[fmt]
    assert image.format == "JPEG"
    assert image.size == (spec.width, spec.height)


def test_rendering_is_deterministic() -> None:
    first, _ = render_graphic(CONFIG, _briefing(), ImageFormat.PORTRAIT)
    second, _ = render_graphic(CONFIG, _briefing(), ImageFormat.PORTRAIT)

    assert first == second


def test_long_place_wraps_instead_of_being_cut() -> None:
    long_place = (
        "Kursraum 2 im Erdgeschoss des Nebengebäudes neben dem großen Parkplatz am Beispielweg"
    )
    facts = BriefingFacts(datum=date(2026, 11, 12), uhrzeit=time(18, 30), ort=long_place)

    jpeg, text = render_graphic(CONFIG, _briefing(facts=facts), ImageFormat.STORY)

    assert f"Ort: {long_place}" in text.lines
    assert jpeg


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
        jpeg, _ = render_graphic(config, briefing, ImageFormat.PORTRAIT)
        assert jpeg, occasion.key


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


def _stored(session: Session) -> Briefing:
    return create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")


def test_graphic_route_and_page(
    graphic_client: TestClient, session: Session, raw_dir: Path
) -> None:
    briefing = _stored(session)
    asset = save_asset(
        session,
        briefing,
        "foto.png",
        _png(),
        shows_persons=False,
        consent_confirmed=False,
        user="demo",
        storage=raw_dir,
    )

    page = graphic_client.get(f"/social/briefings/{briefing.id}").text
    assert "Grafik aus Vorlage" in page and "data-graphic-preview" in page
    assert "Datum: 03.11.2026" in page

    url = (
        f"/social/briefings/{briefing.id}/graphic.jpg?format=9:16&background={asset.id}&download=1"
    )
    # Publishing material: the download needs a released post of the briefing.
    assert graphic_client.get(url).status_code == 409
    assert "data-graphic-locked" in page
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    approve_draft(session, draft, "demo")
    assert "data-graphic-download" in graphic_client.get(f"/social/briefings/{briefing.id}").text
    response = graphic_client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert "attachment" in response.headers["content-disposition"]
    assert Image.open(io.BytesIO(response.content)).size == (1080, 1920)


def test_graphic_route_rejects_invented_facts(graphic_client: TestClient, session: Session) -> None:
    briefing = _stored(session)

    response = graphic_client.get(
        f"/social/briefings/{briefing.id}/graphic.jpg?headline=Neu%20ab%2001.12.2026"
    )
    page = graphic_client.get(
        f"/social/briefings/{briefing.id}?graphic_headline=Neu%20ab%2001.12.2026"
    ).text

    assert response.status_code == 422
    assert "data-graphic-problems" in page and "data-graphic-preview" not in page


def test_background_of_another_briefing_or_without_consent_is_refused(
    graphic_client: TestClient, session: Session, raw_dir: Path
) -> None:
    briefing, other = _stored(session), _stored(session)
    foreign = save_asset(
        session,
        other,
        "foto.png",
        _png(),
        shows_persons=False,
        consent_confirmed=False,
        user="demo",
        storage=raw_dir,
    )
    persons = save_asset(
        session,
        briefing,
        "gruppe.png",
        _png((600, 400)),
        shows_persons=True,
        consent_confirmed=False,
        user="demo",
        storage=raw_dir,
    )

    for asset in (foreign, persons):
        url = f"/social/briefings/{briefing.id}/graphic.jpg?background={asset.id}"
        assert graphic_client.get(url).status_code == 404


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

    text = graphic_text(config, _briefing())
    with_logo, _ = render_graphic(config, _briefing(), ImageFormat.SQUARE)
    without_logo, _ = render_graphic(CONFIG, _briefing(), ImageFormat.SQUARE)

    assert text.logo and text.brand == ""
    assert CONFIG.brand.name not in text.lines
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
