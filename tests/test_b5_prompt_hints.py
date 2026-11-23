"""Hints on image prompts (B5): listed only since November 2026, no confirmation.

Product owner decision: hints on people, text in the image, real places or contact data are
shown to the person but need no confirmation – neither before generating nor before the
approval. A member named in the prompt stays the exception (CLAUDE.md): only the confirmed
consent of that person lifts it, before anything goes to the image provider.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.image_formats import ImageFormat
from app.core.images.fake import FakeImageProvider
from app.core.members import Member
from app.core.models import AuditEntry
from app.main import app
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.images import (
    GENERATE_STEP,
    HINT_MEMBER,
    HINT_PERSONS,
    HINT_TEXT,
    MEMBER_CONSENT_STEP,
    ImageHintsPendingError,
    ImageSuggestionError,
    choose_candidate,
    confirm_prompt_hint,
    generate_variants,
    prompt_hints,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import Briefing, Channel, CheckResult, PostDraft, PromptHint
from app.social.retention import delete_briefing
from app.social.review import approval_blockers, approve_draft
from app.social.web import get_social_client, get_social_config, get_social_image_provider
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
PEOPLE_AND_TEXT = "Frau beim Rückentraining auf der Matte, Schriftzug „Rückenfit“ an der Wand"


@pytest.fixture(autouse=True)
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")


def _draft(session: Session) -> PostDraft:
    briefing = create_briefing(
        session, CONFIG, course_input(channels=[Channel.INSTAGRAM_POST]), "demo"
    )
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    return draft


def _generate(
    session: Session,
    draft: PostDraft,
    storage: Path,
    prompt: str,
    provider: FakeImageProvider | None = None,
) -> None:
    generate_variants(
        session,
        CONFIG,
        provider or FakeImageProvider(),
        _b(session, draft),
        prompt=prompt,
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=storage,
    )


def _b(session: Session, draft: PostDraft) -> Briefing:
    briefing = session.get(Briefing, draft.briefing_id)
    assert briefing is not None
    return briefing


def _choose_first(session: Session, draft: PostDraft) -> None:
    from app.social.images import candidates

    choose_candidate(
        session, _b(session, draft), candidates(session, draft.briefing_id)[0].id or 0, "demo"
    )


def test_hints_are_listed_and_the_images_are_generated_at_once(
    session: Session, raw_dir: Path
) -> None:
    draft = _draft(session)
    provider = FakeImageProvider()

    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT, provider)

    assert len(provider.calls) == 1
    hints = prompt_hints(session, draft.briefing_id)
    assert {h.key for h in hints} == {HINT_PERSONS, HINT_TEXT}
    assert "Info-Grafik" in next(h for h in hints if h.key == HINT_TEXT).reason
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == GENERATE_STEP)).one()
    assert set(entry.model_params["hints"]) == {HINT_PERSONS, HINT_TEXT}  # type: ignore[arg-type]


def test_hints_do_not_block_the_approval(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)
    _choose_first(session, draft)

    assert not any("KI-Bild" in b or "Bild-Prompt" in b for b in approval_blockers(session, draft))
    approve_draft(session, draft, "demo")


def test_clean_prompt_has_no_hints(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)

    _generate(session, draft, raw_dir, "Heller Kursraum mit Matten, weiches Morgenlicht")

    assert prompt_hints(session, draft.briefing_id) == []


def test_a_changed_prompt_lists_its_own_hints(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)

    _generate(session, draft, raw_dir, "Mann beim Dehnen am Fenster")

    assert [h.key for h in prompt_hints(session, draft.briefing_id)] == [HINT_PERSONS]


def test_ordinary_hints_cannot_be_confirmed(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, "Frau auf der Matte")
    (hint,) = prompt_hints(session, draft.briefing_id)

    with pytest.raises(ImageSuggestionError, match="Mitglied"):
        confirm_prompt_hint(session, hint, "demo")


def _member(session: Session) -> None:
    session.add(
        Member(
            member_number="M-90002",
            name="Erika Beispiel",
            email="erika@example.com",
            contract_start=date(2025, 1, 1),
            minimum_term_months=12,
            notice_period_months=1,
            renewal_period_months=1,
            monthly_fee_eur=Decimal("29.90"),
        )
    )
    session.commit()


def test_member_name_needs_the_consent_of_the_person(session: Session, raw_dir: Path) -> None:
    _member(session)
    draft = _draft(session)
    provider = FakeImageProvider()
    prompt = "Hantelbank mit einer Widmung für Erika Beispiel"

    with pytest.raises(ImageHintsPendingError, match="Mitgliederliste"):
        _generate(session, draft, raw_dir, prompt, provider)
    assert provider.calls == []
    (member,) = [h for h in prompt_hints(session, draft.briefing_id) if h.key == HINT_MEMBER]
    assert member.consent_required

    confirm_prompt_hint(session, member, "demo")
    _generate(session, draft, raw_dir, prompt, provider)

    assert len(provider.calls) == 1
    consent = session.exec(select(AuditEntry).where(AuditEntry.step == MEMBER_CONSENT_STEP)).one()
    assert consent.model_params["actor"] == "demo"
    assert "Erika" not in str(consent.model_params)
    with pytest.raises(ImageSuggestionError, match="bereits"):
        confirm_prompt_hint(session, member, "demo")


def test_post_errors_still_block(session: Session) -> None:
    draft = _draft(session)
    session.add(
        CheckResult(draft_id=draft.id or 0, check_key="fakten", outcome="error", reason="x")
    )
    session.commit()

    assert any(b.startswith("Fehler") for b in approval_blockers(session, draft))


def test_deleting_the_briefing_removes_the_hints(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)

    delete_briefing(session, draft.briefing_id, "demo")

    assert session.exec(select(PromptHint)).all() == []


@pytest.fixture
def image_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    yield client


def test_hints_are_listed_in_the_ui_without_buttons(
    image_client: TestClient, session: Session
) -> None:
    draft = _draft(session)
    data = {"prompt": PEOPLE_AND_TEXT}

    page = image_client.post(
        f"/social/briefings/{draft.briefing_id}/images/generate", data=data, follow_redirects=True
    ).text

    assert "data-image-hints" in page and "data-hint-note" in page
    assert "keine echten Mitglieder" in page and page.count("data-candidate=") == 3
    assert "/social/image-hints/" not in page and "Verstanden" not in page
