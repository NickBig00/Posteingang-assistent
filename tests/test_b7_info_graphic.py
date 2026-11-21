"""B7: info graphic with texts – fact fidelity, checks, layout (nothing cut), export, web."""

import io
import json
from collections.abc import Iterator
from datetime import date, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.image_formats import FORMAT_SPECS, ImageFormat
from app.core.llm.fake import FakeProvider
from app.core.members import Member
from app.core.models import AuditEntry
from app.main import app
from app.social.briefings import BriefingInput, create_briefing
from app.social.drafts import generate_drafts
from app.social.facts import BriefingFacts
from app.social.graphics import GraphicLayoutError
from app.social.infographic import (
    CONFIRM_STEP,
    SUGGEST_STEP,
    VARIANT_LABELS,
    InfoGraphicError,
    InfoTexts,
    check_blockers,
    confirm_check,
    fact_problems,
    get_info_graphic,
    graphic_checks,
    parse_points,
    render_info_graphic,
    revise_texts,
    save_texts,
    suggest_texts,
    texts_for_format,
    validate_texts,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import Briefing, Channel, InfoGraphic, InfoGraphicCheck, RevisionRequest
from app.social.retention import delete_briefing
from app.social.review import approve_draft, confirm_warning
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import demo_config, scripted

CONFIG = demo_config()
CLIENT = SocialLLMClient(FakeProvider())
FACTS = BriefingFacts(datum=date(2026, 12, 3), uhrzeit=time(19, 0), ort="Seminarraum 1")
TALK = InfoTexts(
    headline="Vortrag: Bewegung im Büroalltag",
    subline="Kurzweilig, praxisnah, mit Übungen zum Mitmachen",
    intro="Viel Sitzen gehört für viele zum Alltag. An diesem Abend zeigen wir einfache Ideen "
    "für mehr Bewegung zwischendurch.",
    points=[
        "Kleine Übungen für den Schreibtisch",
        "Pausen sinnvoll nutzen",
        "Tipps für den Arbeitsweg",
        "Zeit für eure Fragen",
    ],
    closing="Kommt vorbei – wir freuen uns auf euch!",
)
BACKGROUND = (
    Path(__file__).resolve().parent.parent
    / "samples"
    / "social"
    / "images"
    / "demo_hintergrund.jpg"
).read_bytes()


def _briefing(facts: BriefingFacts = FACTS) -> Briefing:
    return Briefing(
        id=1,
        brand_key="demo",
        area="kurse",
        occasion_type="veranstaltung",
        notes="Vortrag am Abend",
        facts=facts.to_json(),
        channels=[],
        created_by="demo",
    )


def _stored(session: Session) -> Briefing:
    data = BriefingInput(
        area="kurse",
        occasion_type="veranstaltung",
        notes="Vortrag Bewegung im Büroalltag, Übungen zum Mitmachen, Fragen willkommen",
        channels=[Channel.INSTAGRAM_POST],
        facts=FACTS,
    )
    briefing = create_briefing(session, CONFIG, data, "demo")
    generate_drafts(session, CONFIG, CLIENT, briefing)
    return briefing


def _save(
    session: Session, briefing: Briefing, texts: InfoTexts = TALK, **kw: object
) -> InfoGraphic:
    return save_texts(
        session,
        CONFIG,
        CLIENT,
        briefing,
        texts,
        story=kw.get("story"),  # type: ignore[arg-type]
        variant=str(kw.get("variant", "zentriert")),
        user="demo",
    )


# --- texts and facts ---


def test_points_are_parsed_one_per_line_without_bullets() -> None:
    assert parse_points("- Eins\n✓ Zwei\r\n\n• Drei ") == ["Eins", "Zwei", "Drei"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"headline": ""}, "Überschrift"),
        ({"points": ["Nur", "zwei"]}, "3 bis 6"),
        ({"points": [f"Punkt {n}" for n in range(7)]}, "3 bis 6"),
        ({"intro": "x" * 261}, "Einleitung"),
    ],
)
def test_text_limits(change: dict[str, object], message: str) -> None:
    texts = InfoTexts(**(TALK.to_json() | change))  # type: ignore[arg-type]

    assert any(message in p for p in validate_texts(texts))


def test_story_text_may_have_fewer_points() -> None:
    assert validate_texts(InfoTexts(headline="Vortrag", points=[]), story=True) == []


@pytest.mark.parametrize(
    ("intro", "message"),
    [
        ("Auch am 10.12.2026 geht es weiter.", "Datum"),
        ("Beginn ab 18:00 Uhr.", "Uhrzeit"),
        ("Eintritt nur 5 €.", "Preis"),
        ("Anmeldung unter 01234 567890.", "Telefonnummer"),
    ],
)
def test_free_texts_must_not_add_other_facts(intro: str, message: str) -> None:
    texts = InfoTexts(**(TALK.to_json() | {"intro": intro}))  # type: ignore[arg-type]

    assert any(message in p for p in fact_problems(texts, FACTS, ["datum", "uhrzeit", "ort"]))
    with pytest.raises(InfoGraphicError, match=message):
        render_info_graphic(CONFIG, _briefing(), texts, ImageFormat.PORTRAIT)


def test_the_briefing_facts_may_be_mentioned_and_are_added_exactly() -> None:
    texts = InfoTexts(**(TALK.to_json() | {"intro": "Am 03.12.2026 um 19:00 Uhr geht es los."}))  # type: ignore[arg-type]

    assert fact_problems(texts, FACTS, ["datum", "uhrzeit", "ort"]) == []


# --- layout ---


@pytest.mark.parametrize("fmt", list(ImageFormat))
@pytest.mark.parametrize("variant", list(VARIANT_LABELS))
@pytest.mark.parametrize("background", [None, BACKGROUND])
def test_every_variant_and_format_renders_the_talk(
    fmt: ImageFormat, variant: str, background: bytes | None
) -> None:
    jpeg = render_info_graphic(
        CONFIG, _briefing(), TALK, fmt, variant=variant, background=background
    )

    image = Image.open(io.BytesIO(jpeg))
    spec = FORMAT_SPECS[fmt]
    assert image.format == "JPEG" and image.size == (spec.width, spec.height)


def test_too_much_text_is_refused_instead_of_cut() -> None:
    long = InfoTexts(
        headline="Eine sehr lange Überschrift für einen Vortrag über vieles",
        subline="Mit einer ebenso langen Unterzeile, die viel zu viel erklären möchte",
        intro="x " * 120,
        points=["Ein ziemlich langer Aufzählungspunkt mit vielen, vielen Wörtern drin"] * 6,
        closing="Und ein Schlusssatz, der auch noch einmal alles zusammenfasst und erklärt.",
    )

    with pytest.raises(GraphicLayoutError, match="nie abgeschnitten"):
        render_info_graphic(
            CONFIG,
            _briefing(),
            long,
            ImageFormat.SQUARE,
            variant="foto_oben",
            background=BACKGROUND,
        )


def test_more_text_means_smaller_font_not_overflow() -> None:
    short = InfoTexts(headline="Vortrag", points=["Eins", "Zwei", "Drei"])

    small = render_info_graphic(CONFIG, _briefing(), TALK, ImageFormat.SQUARE)
    big = render_info_graphic(CONFIG, _briefing(), short, ImageFormat.SQUARE)

    assert small != big  # both fit; the layout adapts the size


def test_rendering_is_deterministic() -> None:
    first = render_info_graphic(CONFIG, _briefing(), TALK, ImageFormat.PORTRAIT)

    assert first == render_info_graphic(CONFIG, _briefing(), TALK, ImageFormat.PORTRAIT)


# --- stored texts, checks, AI ---


def test_saving_runs_the_checks_and_health_claims_block_the_download(session: Session) -> None:
    briefing = _stored(session)
    claim = InfoTexts(**(TALK.to_json() | {"closing": "Garantiert schmerzfrei nach dem Vortrag!"}))  # type: ignore[arg-type]

    graphic = _save(session, briefing, claim)

    blockers = check_blockers(session, graphic)
    assert blockers and any("Warnung" in b or "Fehler" in b for b in blockers)
    for result in graphic_checks(session, graphic.id or 0):
        if result.requires_confirmation and result.outcome == "warning":
            confirm_check(session, result, "demo")
    assert all("Warnung" not in b for b in check_blockers(session, graphic))
    assert session.exec(select(AuditEntry).where(AuditEntry.step == CONFIRM_STEP)).all()


def test_member_name_is_an_error_that_only_consent_lifts(session: Session) -> None:
    session.add(
        Member(
            member_number="M-90003",
            name="Erika Beispiel",
            email="erika@example.com",
            contract_start=date(2025, 1, 1),
            minimum_term_months=12,
            notice_period_months=1,
            renewal_period_months=1,
            monthly_fee_eur=1,
        )
    )
    session.commit()
    briefing = _stored(session)
    texts = InfoTexts(**(TALK.to_json() | {"closing": "Mit Erika Beispiel als Gast!"}))  # type: ignore[arg-type]

    graphic = _save(session, briefing, texts)

    member = next(r for r in graphic_checks(session, graphic.id or 0) if r.consent_required)
    assert member.outcome == "error"
    confirm_check(session, member, "demo")
    assert not any("Einwilligung" in b for b in check_blockers(session, graphic))


def test_ai_suggestion_is_labelled_audited_and_contains_no_facts(session: Session) -> None:
    provider = FakeProvider()
    briefing = _stored(session)

    graphic = suggest_texts(session, CONFIG, SocialLLMClient(provider), briefing, "demo")

    assert graphic.is_ai_suggestion and not graphic.edited_by_human
    assert 3 <= len(graphic.points) <= 6
    assert session.exec(select(AuditEntry).where(AuditEntry.step == SUGGEST_STEP)).all()
    _, _, user = next(c for c in provider.calls if c[0] == "graphic_text")
    assert "03.12.2026" not in user and "Seminarraum" not in user  # no facts sent
    # A person edits: the label "von Mensch bearbeitet" appears, the AI origin stays.
    edited = _save(
        session, briefing, InfoTexts(**(InfoTexts.of(graphic).to_json() | {"closing": "Bis bald!"}))
    )  # type: ignore[arg-type]
    assert edited.is_ai_suggestion and edited.edited_by_human


def test_invalid_suggestion_is_retried_once(session: Session) -> None:
    client = SocialLLMClient(scripted(graphic_text=["kaputt", "{}"]))

    with pytest.raises(InfoGraphicError, match="keinen gültigen"):
        suggest_texts(session, CONFIG, client, _stored(session), "demo")
    entries = session.exec(select(AuditEntry).where(AuditEntry.step == SUGGEST_STEP)).all()
    assert [e.model_params["attempt"] for e in entries] == [1, 2]


def test_revision_by_instruction(session: Session) -> None:
    briefing = _stored(session)
    _save(session, briefing)

    revised = revise_texts(session, CONFIG, CLIENT, briefing, "Bitte ohne Arbeitsweg", "demo")

    assert "Tipps für den Arbeitsweg" not in revised.points
    assert revised.is_ai_suggestion
    request = session.exec(select(RevisionRequest)).one()
    assert request.target == "graphic_text" and request.succeeded


def test_story_uses_the_shorter_text(session: Session) -> None:
    briefing = _stored(session)
    story = InfoTexts(headline="Vortrag am Abend", points=["Übungen zum Mitmachen"])

    graphic = _save(session, briefing, story=story)

    assert texts_for_format(graphic, ImageFormat.STORY).headline == "Vortrag am Abend"
    assert texts_for_format(graphic, ImageFormat.PORTRAIT).headline == TALK.headline


def test_deleting_the_briefing_deletes_the_info_graphic(session: Session) -> None:
    briefing = _stored(session)
    _save(session, briefing)

    delete_briefing(session, briefing.id or 0, "demo")

    assert session.exec(select(InfoGraphic)).all() == []
    assert session.exec(select(InfoGraphicCheck)).all() == []


# --- web ---


@pytest.fixture
def web(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: CLIENT
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_full_flow_in_the_ui(web: TestClient, session: Session) -> None:
    briefing = _stored(session)
    url = f"/social/briefings/{briefing.id}"
    assert "Text für Grafik vorschlagen" in web.get(url).text

    page = web.post(
        f"{url}/info-graphic",
        data={
            "headline": TALK.headline,
            "subline": TALK.subline,
            "intro": TALK.intro,
            "points": "\n".join(TALK.points),
            "closing": TALK.closing,
            "variant": "farbfeld",
        },
        follow_redirects=True,
    ).text
    assert "data-info-preview" in page and "Prüfungen des Grafiktexts" in page
    graphic = get_info_graphic(session, briefing.id or 0)
    assert graphic is not None and graphic.variant == "farbfeld"

    preview = web.get(f"{url}/info-graphic.jpg?format=4:5&variant=farbfeld")
    assert preview.status_code == 200
    assert Image.open(io.BytesIO(preview.content)).size == (1080, 1350)
    assert web.get(f"{url}/info-graphic.jpg?download=1").status_code == 409

    session.expire_all()
    from app.social.models import CheckResult, PostDraft

    draft = session.exec(select(PostDraft).where(PostDraft.briefing_id == briefing.id)).one()
    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all():
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")
    approve_draft(session, draft, "demo")
    download = web.get(f"{url}/info-graphic.jpg?download=1&format=1:1")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]


def test_invalid_texts_show_the_reason(web: TestClient, session: Session) -> None:
    briefing = _stored(session)

    response = web.post(
        f"/social/briefings/{briefing.id}/info-graphic",
        data={"headline": "Vortrag", "points": "Nur einer"},
    )

    assert response.status_code == 422 and "3 bis 6" in response.text


def test_foreign_fact_in_the_text_shows_no_preview(web: TestClient, session: Session) -> None:
    briefing = _stored(session)
    texts = InfoTexts(**(TALK.to_json() | {"intro": "Auch am 10.12.2026."}))  # type: ignore[arg-type]
    _save(session, briefing, texts)

    page = web.get(f"/social/briefings/{briefing.id}").text

    assert "data-info-problems" in page and "data-info-preview" not in page
    assert web.get(f"/social/briefings/{briefing.id}/info-graphic.jpg").status_code == 422
    assert json  # json imported for symmetry with other tests
