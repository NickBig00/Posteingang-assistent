"""U8: confirmations (toasts), focus targets and loading placeholders on the briefing page."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import CheckResult, PostDraft
from app.social.review import confirm_warning
from app.social.web import TOASTS, get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
CLIENT = SocialLLMClient(scripted())


@pytest.fixture
def web(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: CLIENT
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def _draft(session: Session) -> PostDraft:
    briefing = create_briefing(session, CONFIG, course_input(), "demo")
    draft, _ = generate_drafts(session, CONFIG, CLIENT, briefing)
    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)):
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")
    return draft


def test_approval_redirects_with_a_toast_to_the_publishing_step(
    web: TestClient, session: Session
) -> None:
    draft = _draft(session)

    response = web.post(f"/social/drafts/{draft.id}/approve", follow_redirects=False)

    location = response.headers["location"]
    assert location == f"/social/briefings/{draft.briefing_id}?toast=freigegeben#draft-{draft.id}"
    page = web.get(location.split("#")[0]).text
    assert "data-toast" in page and TOASTS["freigegeben"] in page


def test_unknown_toast_keys_are_ignored_and_never_echoed(web: TestClient, session: Session) -> None:
    draft = _draft(session)

    page = web.get(f"/social/briefings/{draft.briefing_id}?toast=<script>x</script>").text

    assert "data-toast" not in page and "<script>x" not in page


def test_saving_the_text_confirms_with_a_toast(web: TestClient, session: Session) -> None:
    draft = _draft(session)

    response = web.post(
        f"/social/drafts/{draft.id}/edit",
        data={"text": draft.text, "hashtags": "#Kurse"},
        follow_redirects=False,
    )

    assert "toast=gespeichert" in response.headers["location"]


def test_errors_show_no_toast(web: TestClient, session: Session) -> None:
    draft = _draft(session)

    response = web.post(
        f"/social/drafts/{draft.id}/edit?toast=gespeichert", data={"text": "x" * 10001}
    )

    assert response.status_code == 409 and "data-toast" not in response.text


def test_generating_jumps_to_the_new_variants_with_placeholders(
    web: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.images.fake import FakeImageProvider
    from app.social.web import get_social_image_provider

    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    draft = _draft(session)
    url = f"/social/briefings/{draft.briefing_id}"
    assert 'data-skeleton-for="ki-bild-form"' in web.get(url).text

    response = web.post(
        f"{url}/images/generate",
        data={"prompt": "Heller Kursraum mit Matten, Morgenlicht"},
        follow_redirects=False,
    )

    assert response.headers["location"].endswith("?toast=erzeugt#ki-varianten")
    page = web.get(url).text
    assert 'id="ki-varianten"' in page and "data-skeleton-hide" in page
