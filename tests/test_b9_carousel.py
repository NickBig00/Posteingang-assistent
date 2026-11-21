"""B9: carousel posts for Instagram – facts on all pages, order, checks, AI plan, ZIP export."""

import io
import zipfile
from collections.abc import Iterator
from datetime import date, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.image_formats import ImageFormat
from app.core.llm.fake import FakeProvider
from app.core.models import AuditEntry
from app.main import app
from app.social.assets import save_asset
from app.social.briefings import BriefingInput, create_briefing
from app.social.carousel import (
    CHANGE_STEP,
    PLAN_STEP,
    CarouselError,
    PageInput,
    add_page,
    carousel_blockers,
    carousel_checks,
    confirm_carousel_check,
    export_zip,
    move_page,
    pages,
    remove_page,
    suggest_plan,
    update_page,
)
from app.social.drafts import generate_drafts
from app.social.facts import BriefingFacts
from app.social.infographic import InfoTexts
from app.social.llm.client import SocialLLMClient
from app.social.models import CarouselCheck, CarouselPage, Channel, CheckResult, PostDraft
from app.social.retention import delete_draft
from app.social.review import approval_blockers, approve_draft, confirm_warning
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import demo_config, scripted

CONFIG = demo_config()
CLIENT = SocialLLMClient(FakeProvider())
FACTS = BriefingFacts(datum=date(2026, 12, 3), uhrzeit=time(19, 0), ort="Seminarraum 1")


def _drafts(session: Session) -> tuple[PostDraft, PostDraft]:
    data = BriefingInput(
        area="kurse",
        occasion_type="veranstaltung",
        notes="Vortrag Bewegung im Büroalltag, Übungen zum Mitmachen, Fragen willkommen",
        channels=[Channel.INSTAGRAM_POST, Channel.FACEBOOK],
        facts=FACTS,
    )
    briefing = create_briefing(session, CONFIG, data, "demo")
    instagram, facebook = generate_drafts(session, CONFIG, CLIENT, briefing)
    return instagram, facebook


def _info(
    headline: str, *, points: list[str] | None = None, facts: bool = False, intro: str = ""
) -> PageInput:
    return PageInput(
        "info", InfoTexts(headline, "", intro, points or [], ""), None, facts, "zentriert"
    )


def _three_pages(session: Session, draft: PostDraft) -> list[CarouselPage]:
    add_page(session, CONFIG, CLIENT, draft, _info("Vortrag: Bewegung im Büroalltag"), "demo")
    add_page(
        session,
        CONFIG,
        CLIENT,
        draft,
        _info("Das erwartet euch", points=["Übungen", "Pausen", "Fragen"]),
        "demo",
    )
    add_page(session, CONFIG, CLIENT, draft, _info("Termin und Anmeldung", facts=True), "demo")
    return pages(session, draft.id or 0)


def _release(session: Session, draft: PostDraft) -> None:
    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all():
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")
    for result in carousel_checks(session, draft.id or 0):
        if result.requires_confirmation and result.confirmed_at is None:
            confirm_carousel_check(session, result, "demo")
    approve_draft(session, draft, "demo")


def test_only_instagram_posts_have_a_carousel(session: Session) -> None:
    _, facebook = _drafts(session)

    with pytest.raises(CarouselError, match="Instagram"):
        add_page(session, CONFIG, CLIENT, facebook, _info("Titel"), "demo")


def test_pages_in_order_and_reordering_is_audited(session: Session) -> None:
    draft, _ = _drafts(session)
    rows = _three_pages(session, draft)
    assert [InfoTexts.from_json(r.texts).headline for r in rows][0].startswith("Vortrag")

    move_page(session, CONFIG, CLIENT, rows[2], -1, "demo")

    order = [InfoTexts.from_json(r.texts).headline for r in pages(session, draft.id or 0)]
    assert order == ["Vortrag: Bewegung im Büroalltag", "Termin und Anmeldung", "Das erwartet euch"]
    assert [r.position for r in pages(session, draft.id or 0)] == [1, 2, 3]
    with pytest.raises(CarouselError, match="Rand"):
        move_page(session, CONFIG, CLIENT, pages(session, draft.id or 0)[0], -1, "demo")
    moves = session.exec(select(AuditEntry).where(AuditEntry.step == CHANGE_STEP)).all()
    assert any(e.model_params.get("action") == "move" for e in moves)


def test_facts_are_checked_exactly_on_every_page(session: Session) -> None:
    draft, _ = _drafts(session)
    rows = _three_pages(session, draft)

    update_page(
        session,
        CONFIG,
        CLIENT,
        rows[1],
        _info("Das erwartet euch", points=["Übungen", "Pausen"], intro="Auch am 10.12.2026 dabei."),
        "demo",
    )

    errors = [r for r in carousel_checks(session, draft.id or 0) if r.outcome == "error"]
    assert errors and errors[0].page == 2 and "10.12.2026" in errors[0].reason
    assert any("Karussell" in b for b in approval_blockers(session, draft))


def test_facts_block_appears_only_on_chosen_pages(session: Session) -> None:
    draft, _ = _drafts(session)
    _three_pages(session, draft)
    _release(session, draft)

    archive = zipfile.ZipFile(io.BytesIO(export_zip(session, CONFIG, draft, ImageFormat.PORTRAIT)))

    assert archive.namelist() == ["01.jpg", "02.jpg", "03.jpg"]
    first, _, last = (archive.read(n) for n in archive.namelist())
    assert first != last
    for name in archive.namelist():
        image = Image.open(io.BytesIO(archive.read(name)))
        assert image.format == "JPEG" and image.size == (1080, 1350)
        assert not image.getexif()


def test_zip_only_after_approval_in_4x5_or_1x1(session: Session) -> None:
    draft, _ = _drafts(session)
    _three_pages(session, draft)

    with pytest.raises(CarouselError, match="Freigabe"):
        export_zip(session, CONFIG, draft, ImageFormat.PORTRAIT)
    _release(session, draft)
    with pytest.raises(CarouselError, match="4:5 oder 1:1"):
        export_zip(session, CONFIG, draft, ImageFormat.STORY)
    square = zipfile.ZipFile(io.BytesIO(export_zip(session, CONFIG, draft, ImageFormat.SQUARE)))
    assert Image.open(io.BytesIO(square.read("01.jpg"))).size == (1080, 1080)


def test_a_single_page_is_no_carousel(session: Session) -> None:
    draft, _ = _drafts(session)
    add_page(session, CONFIG, CLIENT, draft, _info("Nur eine Seite"), "demo")

    assert any("mindestens 2" in b for b in carousel_blockers(session, draft))


def test_image_pages_need_a_usable_image(session: Session, raw_dir: Path) -> None:
    draft, _ = _drafts(session)
    from app.social.models import Briefing

    briefing = session.get(Briefing, draft.briefing_id)
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), (40, 120, 90)).save(buffer, format="PNG")
    group = save_asset(
        session,
        briefing,  # type: ignore[arg-type]
        "gruppe.png",
        buffer.getvalue(),
        shows_persons=True,
        consent_confirmed=False,
        user="demo",
        storage=raw_dir,
    )
    add_page(session, CONFIG, CLIENT, draft, _info("Titel"), "demo")
    add_page(
        session,
        CONFIG,
        CLIENT,
        draft,
        PageInput("image", InfoTexts(), group.id, False, "zentriert"),
        "demo",
    )

    assert any("Einwilligung" in r.reason for r in carousel_checks(session, draft.id or 0))
    with pytest.raises(CarouselError, match="Bild"):
        add_page(
            session,
            CONFIG,
            CLIENT,
            draft,
            PageInput("image", InfoTexts(), None, False, "zentriert"),
            "demo",
        )


def test_health_claim_on_a_page_needs_confirmation(session: Session) -> None:
    draft, _ = _drafts(session)
    add_page(session, CONFIG, CLIENT, draft, _info("Garantiert schmerzfrei"), "demo")
    add_page(session, CONFIG, CLIENT, draft, _info("Termin", facts=True), "demo")

    warnings = [r for r in carousel_checks(session, draft.id or 0) if r.requires_confirmation]
    assert warnings and warnings[0].page == 1
    assert any("Warnung" in b for b in carousel_blockers(session, draft))


def test_ai_plan_is_labelled_audited_and_contains_no_facts(session: Session) -> None:
    provider = FakeProvider()
    draft, _ = _drafts(session)

    rows = suggest_plan(session, CONFIG, SocialLLMClient(provider), draft, "demo")

    assert len(rows) == 3 and all(r.is_ai_suggestion for r in rows)
    assert rows[-1].show_facts and not rows[0].show_facts
    assert session.exec(select(AuditEntry).where(AuditEntry.step == PLAN_STEP)).all()
    _, _, user = next(c for c in provider.calls if c[0] == "carousel_plan")
    assert "03.12.2026" not in user and "Seminarraum" not in user


def test_invalid_plan_is_retried_once_then_reported(session: Session) -> None:
    draft, _ = _drafts(session)
    client = SocialLLMClient(scripted(carousel_plan=["kaputt", '{"pages": []}']))

    with pytest.raises(CarouselError, match="keine gültige"):
        suggest_plan(session, CONFIG, client, draft, "demo")
    assert pages(session, draft.id or 0) == []


def test_removing_pages_and_deleting_the_draft(session: Session) -> None:
    draft, _ = _drafts(session)
    rows = _three_pages(session, draft)
    remove_page(session, CONFIG, CLIENT, rows[0], "demo")
    assert [r.position for r in pages(session, draft.id or 0)] == [1, 2]

    delete_draft(session, draft.id or 0, "demo")

    assert session.exec(select(CarouselPage)).all() == []
    assert session.exec(select(CarouselCheck)).all() == []


def test_carousel_is_fixed_after_approval(session: Session) -> None:
    draft, _ = _drafts(session)
    _three_pages(session, draft)
    _release(session, draft)

    with pytest.raises(CarouselError, match="freigegeben"):
        add_page(session, CONFIG, CLIENT, draft, _info("Nachtrag"), "demo")


# --- web ---


@pytest.fixture
def web(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: CLIENT
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_carousel_in_the_ui(web: TestClient, session: Session) -> None:
    draft, facebook = _drafts(session)
    url = f"/social/briefings/{draft.briefing_id}"
    page = web.get(url).text
    assert f'id="carousel-{draft.id}"' in page and f'id="carousel-{facebook.id}"' not in page

    page = web.post(f"/social/drafts/{draft.id}/carousel/suggest", follow_redirects=True).text
    assert "data-carousel-preview" in page and page.count("Seite 3") >= 1
    rows = pages(session, draft.id or 0)
    assert web.get(f"/social/carousel-pages/{rows[0].id}.jpg?format=1:1").status_code == 200
    assert web.get(f"/social/carousel-pages/{rows[0].id}.jpg?format=9:16").status_code == 422

    web.post(f"/social/carousel-pages/{rows[2].id}/move", data={"direction": "-1"})
    session.expire_all()
    assert pages(session, draft.id or 0)[1].id == rows[2].id

    assert web.get(f"/social/drafts/{draft.id}/carousel.zip").status_code == 409
    session.expire_all()
    _release(session, session.get(PostDraft, draft.id))  # type: ignore[arg-type]
    response = web.get(f"/social/drafts/{draft.id}/carousel.zip?format=4:5")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == [
        "01.jpg",
        "02.jpg",
        "03.jpg",
    ]
    assert "data-carousel-download" in web.get(url).text


def test_add_page_validation_in_the_ui(web: TestClient, session: Session) -> None:
    draft, _ = _drafts(session)

    response = web.post(
        f"/social/drafts/{draft.id}/carousel/pages", data={"kind": "info", "headline": ""}
    )

    assert response.status_code == 422 and "Überschrift" in response.text
