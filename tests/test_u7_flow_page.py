"""U7: the briefing page as a flow – tabs, steps, checklist, review list, live preview."""

import io
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.models import AuditEntry
from app.main import app
from app.social.assets import PERSONS_STEP, save_asset
from app.social.briefing_image import set_briefing_image
from app.social.briefings import create_briefing
from app.social.checks import CHECK_LABELS
from app.social.drafts import generate_drafts
from app.social.flow import draft_flow
from app.social.infographic import InfoTexts, save_texts
from app.social.llm.client import SocialLLMClient
from app.social.models import Asset, Briefing, CheckResult, ImageSource, PostDraft
from app.social.review import approve_draft, confirm_warning
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, png_bytes, scripted

CONFIG = demo_config()
CLIENT = SocialLLMClient(scripted())


@pytest.fixture
def web(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: CLIENT
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def _briefing(session: Session, notes: str | None = None) -> tuple[Briefing, list[PostDraft]]:
    data = course_input(notes=notes) if notes else course_input()
    briefing = create_briefing(session, CONFIG, data, "demo")
    drafts = generate_drafts(session, CONFIG, CLIENT, briefing)
    return briefing, drafts


def _confirm_post_warnings(session: Session, draft: PostDraft) -> None:
    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)):
        if result.requires_confirmation and result.confirmed_at is None:
            confirm_warning(session, result, "demo")


# --- review list and checklist (app.social.flow) ---


def test_review_list_gathers_text_image_and_consent_sorted(session: Session, raw_dir: Path) -> None:
    briefing, (draft, _) = _briefing(session, "Training, das Bluthochdruck vorbeugt")
    photo = save_asset(
        session, briefing, "gruppe.png", png_bytes(), shows_persons=True,
        consent_confirmed=False, user="demo", storage=raw_dir,
    )  # fmt: skip
    set_briefing_image(
        session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo", with_text=True
    )
    save_texts(
        session, CONFIG, CLIENT, briefing, InfoTexts(headline="Garantiert schmerzfrei"),
        story=None, variant="zentriert", user="demo", from_ai=True,
    )  # fmt: skip

    flow = draft_flow(session, CONFIG, draft, CHECK_LABELS)

    sources = {i.source for i in flow.items}
    assert {"Beitragstext", "Text auf dem Bild", "Bild"} <= sources
    order = [i.status for i in flow.items]
    rank = {"error": 0, "open": 1, "note": 2, "confirmed": 3, "ok": 4}
    assert order == sorted(order, key=rank.__getitem__)
    consent = next(i for i in flow.items if i.uid == f"asset-{photo.id}-consent")
    assert consent.status == "open" and consent.action_url == f"/social/assets/{photo.id}/consent"
    assert not flow.ready and flow.to_do >= 2
    labels = [e.label for e in flow.checklist]
    assert any("bestätigen" in label for label in labels)
    # Every checklist entry points to an item of the list (or to the image step).
    uids = {i.uid for i in flow.items}
    for entry in flow.checklist:
        assert entry.href[1:] in uids or entry.href in ("#bild",) or entry.href.startswith("#")


def test_checklist_offers_the_image_as_an_optional_hint(session: Session) -> None:
    _, (draft, _) = _briefing(session)
    _confirm_post_warnings(session, draft)

    flow = draft_flow(session, CONFIG, draft, CHECK_LABELS)

    assert flow.ready
    assert [(e.label, e.optional) for e in flow.checklist] == [("Bild wählen", True)]
    assert flow.tab_symbol == ("●", "warning")


def test_released_draft_has_no_checklist_and_a_tick(session: Session) -> None:
    _, (draft, _) = _briefing(session)
    _confirm_post_warnings(session, draft)
    approve_draft(session, draft, "demo", CONFIG)

    flow = draft_flow(session, CONFIG, draft, CHECK_LABELS)

    assert flow.released and flow.checklist == [] and flow.tab_symbol == ("✓", "ok")


# --- the page ---


def test_page_has_tabs_steps_preview_and_checklist(web: TestClient, session: Session) -> None:
    briefing, (instagram, facebook) = _briefing(session)

    page = web.get(f"/social/briefings/{briefing.id}").text

    assert 'role="tablist"' in page
    for channel in ("instagram_post", "facebook"):
        assert f'id="tab-{channel}"' in page and f'aria-controls="panel-{channel}"' in page
        assert f'id="panel-{channel}" role="tabpanel"' in page
    selected = re.search(r'id="tab-instagram_post"[^>]*aria-selected="(\w+)"', page)
    assert selected and selected.group(1) == "true"
    for title in ("Text", "Bild", "Prüfen und freigeben"):
        assert f">{title}</h2>" in page
    assert page.count('id="bild"') == 1  # step ② once, for all channels
    assert "Welches Bild?" in page and "Eigenes Foto" in page and "Farbfläche" in page
    assert f'id="draft-{instagram.id}"' in page and f'id="pruefen-{facebook.id}"' in page
    assert page.count("data-preview=") == 2 and "data-checklist" in page
    assert "Hinweis zur KI-Nutzung" in page  # the general notice stays on the page


def test_error_page_opens_the_tab_of_the_action(web: TestClient, session: Session) -> None:
    briefing, (_, facebook) = _briefing(session)

    response = web.post(f"/social/drafts/{facebook.id}/edit", data={"text": "x" * 10001})

    assert response.status_code == 409
    selected = re.search(r'id="tab-facebook"[^>]*aria-selected="(\w+)"', response.text)
    assert selected and selected.group(1) == "true"
    assert "data-flash-error" in response.text


def test_released_tab_shows_publishing_and_hides_the_image_step(
    web: TestClient, session: Session
) -> None:
    briefing, (instagram, _) = _briefing(session)
    _confirm_post_warnings(session, instagram)
    approve_draft(session, instagram, "demo", CONFIG)

    page = web.get(f"/social/briefings/{briefing.id}").text

    assert ">Veröffentlichen</h2>" in page and "Text kopieren" in page
    assert 'data-hide-for="instagram_post"' in page
    assert f'id="pruefen-{instagram.id}"' not in page


def test_hashtags_from_chips_and_the_add_field(web: TestClient, session: Session) -> None:
    _, (draft, _) = _briefing(session)

    web.post(
        f"/social/drafts/{draft.id}/edit",
        data={"text": draft.text, "hashtags": "#Kurse", "hashtags_add": "#Rücken"},
    )

    session.refresh(draft)
    assert draft.hashtags == ["#Kurse", "#Rücken"]


# --- image step: persons, prompt check, live preview ---


def test_persons_question_after_the_upload(
    web: TestClient, session: Session, raw_dir: Path
) -> None:
    briefing, _ = _briefing(session)
    upload = web.post(
        f"/social/briefings/{briefing.id}/assets",
        files={"file": ("kurs.png", png_bytes(), "image/png")},
        follow_redirects=True,
    )
    assert "Personen zu sehen?" in upload.text
    (asset,) = session.exec(select(Asset)).all()

    web.post(f"/social/assets/{asset.id}/persons", data={"shows_persons": "true"})
    session.refresh(asset)
    assert asset.shows_persons and not asset.consent_confirmed
    assert "Einwilligung liegt vor?" in web.get(f"/social/briefings/{briefing.id}").text
    web.post(f"/social/assets/{asset.id}/consent")
    web.post(f"/social/assets/{asset.id}/persons", data={"shows_persons": "false"})
    session.refresh(asset)
    assert not asset.shows_persons and not asset.consent_confirmed
    steps = [e.step for e in session.exec(select(AuditEntry)).all()]
    assert steps.count(PERSONS_STEP) == 2


def test_prompt_check_lists_hints_and_generating_stays_possible(
    web: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")
    briefing, _ = _briefing(session)

    page = web.post(
        f"/social/briefings/{briefing.id}/images/check",
        data={"prompt": "Frau auf einer Matte"},
        follow_redirects=True,
    ).text

    assert "data-image-hints" in page and "data-hint-note" in page and "Verstanden" not in page
    assert not re.search(r"<button[^>]*disabled[^>]*data-generate", page)


def test_live_preview_while_typing(web: TestClient, session: Session) -> None:
    briefing, drafts = _briefing(session)
    set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")
    url = f"/social/briefings/{briefing.id}/info-graphic/live"

    fragment = web.post(url, data={"headline": "Starker Rücken"}).text

    for draft in drafts:
        assert f'id="post-image-{draft.id}" hx-swap-oob="outerHTML"' in fragment
    src = re.search(r'src="([^"]+)"', fragment)
    assert src is not None
    image = web.get(src.group(1).replace("&amp;", "&"))
    assert image.status_code == 200 and "inline" in image.headers["content-disposition"]
    assert Image.open(io.BytesIO(image.content)).size == (1080, 1350)
    # Not saved: the stored text is unchanged, the download needs the approval.
    assert web.get(f"/social/drafts/{drafts[0].id}/preview.jpg").status_code == 422

    # Own text is not checked for facts, but it must fit (never cut).
    invalid = web.post(url, data={"headline": "Starker Rücken " * 6}).text
    assert "letzten gültigen Stand" in invalid and "post-image-" not in invalid


def test_design_page_shows_the_flow_components(web: TestClient) -> None:
    page = web.get("/design").text

    markers = ['role="tablist"', "step-marker", "choice-card", "segmented", "data-popover"]
    markers += ["checklist", "data-chips", "data-dropzone", "thumb", "sticky_footer"]
    for marker in markers:
        assert marker in page, marker


def test_provider_error_is_shown_at_the_empty_text(web: TestClient, session: Session) -> None:
    """E.g. an expired API key: the reason appears in step ①, not only in the audit trail."""
    failing = SocialLLMClient(scripted(post_draft=[]))
    briefing = create_briefing(session, CONFIG, course_input(), "demo")
    draft, _ = generate_drafts(session, CONFIG, failing, briefing)

    flow = draft_flow(session, CONFIG, draft, CHECK_LABELS)
    page = web.get(f"/social/briefings/{briefing.id}").text

    assert not draft.text and flow.ai_error and "no scripted answer" in flow.ai_error
    assert "data-ai-error" in page and "Die KI konnte keinen Text liefern." in page
    # Only "Text schreiben": the missing facts of the empty text are no extra errors.
    assert [e.label for e in flow.checklist if not e.optional] == ["Text schreiben"]
