"""B5: hints on image prompts – first confirm, then generate, then approve.

A member named in the prompt is not a hint but an error that only the confirmed consent of
that person lifts (CLAUDE.md); afterwards the generation is possible.
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
    HINT_CONFIRM_STEP,
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
from app.social.models import Channel, CheckResult, PostDraft, PromptHint
from app.social.retention import delete_draft
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
        draft,
        prompt=prompt,
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=storage,
    )


def _confirm_all(session: Session, draft: PostDraft) -> None:
    for hint in prompt_hints(session, draft.id or 0):
        if hint.confirmed_at is None:
            confirm_prompt_hint(session, hint, "demo")


def _confirmed_generation(session: Session, draft: PostDraft, storage: Path, prompt: str) -> None:
    """Try, confirm the hints, generate, choose the first variant."""
    with pytest.raises(ImageHintsPendingError):
        _generate(session, draft, storage, prompt)
    _confirm_all(session, draft)
    _generate(session, draft, storage, prompt)
    from app.social.images import candidates

    choose_candidate(session, draft, candidates(session, draft.id or 0)[0].id or 0, "demo")


def test_hints_are_recorded_and_nothing_is_generated_before_confirmation(
    session: Session, raw_dir: Path
) -> None:
    draft = _draft(session)
    provider = FakeImageProvider()

    with pytest.raises(ImageHintsPendingError, match="erst|zuerst"):
        _generate(session, draft, raw_dir, PEOPLE_AND_TEXT, provider)

    assert provider.calls == []
    assert GENERATE_STEP not in [e.step for e in session.exec(select(AuditEntry)).all()]
    hints = prompt_hints(session, draft.id or 0)
    assert {h.key for h in hints} == {HINT_PERSONS, HINT_TEXT}
    assert all(h.confirmed_at is None for h in hints)
    assert "Info-Grafik" in next(h for h in hints if h.key == HINT_TEXT).reason


def test_confirm_then_generate_then_approve_with_audit(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    provider = FakeImageProvider()
    with pytest.raises(ImageHintsPendingError):
        _generate(session, draft, raw_dir, PEOPLE_AND_TEXT, provider)

    _confirm_all(session, draft)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT, provider)

    assert len(provider.calls) == 1
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == GENERATE_STEP)).one()
    assert set(entry.model_params["hints"]) == {HINT_PERSONS, HINT_TEXT}  # type: ignore[arg-type]
    confirmations = session.exec(
        select(AuditEntry).where(AuditEntry.step == HINT_CONFIRM_STEP)
    ).all()
    assert {e.model_params["hint"] for e in confirmations} == {HINT_PERSONS, HINT_TEXT}
    assert all("Rückenfit" not in str(e.model_params) for e in confirmations)
    from app.social.images import candidates

    choose_candidate(session, draft, candidates(session, draft.id or 0)[0].id or 0, "demo")
    assert not any("Hinweis zum KI-Bild" in b for b in approval_blockers(session, draft))
    approve_draft(session, draft, "demo")


def test_clean_prompt_generates_at_once(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)

    _generate(session, draft, raw_dir, "Heller Kursraum mit Matten, weiches Morgenlicht")

    assert prompt_hints(session, draft.id or 0) == []


def test_a_changed_prompt_needs_a_new_confirmation(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _confirmed_generation(session, draft, raw_dir, PEOPLE_AND_TEXT)

    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)  # same prompt: confirmations kept
    assert all(h.confirmed_at for h in prompt_hints(session, draft.id or 0))

    with pytest.raises(ImageHintsPendingError):
        _generate(session, draft, raw_dir, "Mann beim Dehnen am Fenster")
    hints = prompt_hints(session, draft.id or 0)
    assert [h.key for h in hints] == [HINT_PERSONS] and hints[0].confirmed_at is None


def test_confirming_twice_or_after_approval_is_refused(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _confirmed_generation(session, draft, raw_dir, "Frau auf der Matte")
    (hint,) = prompt_hints(session, draft.id or 0)

    with pytest.raises(ImageSuggestionError, match="bereits"):
        confirm_prompt_hint(session, hint, "demo")
    approve_draft(session, draft, "demo")
    with pytest.raises(ImageSuggestionError, match="vor der Freigabe"):
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

    with pytest.raises(ImageHintsPendingError, match="Mitglied genannt"):
        _generate(session, draft, raw_dir, prompt, provider)
    assert provider.calls == []
    (member,) = [h for h in prompt_hints(session, draft.id or 0) if h.key == HINT_MEMBER]
    assert member.consent_required

    confirm_prompt_hint(session, member, "demo")
    _confirm_all(session, draft)
    _generate(session, draft, raw_dir, prompt, provider)

    assert len(provider.calls) == 1
    consent = session.exec(select(AuditEntry).where(AuditEntry.step == MEMBER_CONSENT_STEP)).one()
    assert consent.model_params["actor"] == "demo"
    assert "Erika" not in str(consent.model_params)


def test_post_errors_still_block(session: Session) -> None:
    draft = _draft(session)
    session.add(
        CheckResult(draft_id=draft.id or 0, check_key="fakten", outcome="error", reason="x")
    )
    session.commit()

    assert any(b.startswith("Fehler") for b in approval_blockers(session, draft))


def test_deleting_the_draft_removes_the_hints(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    with pytest.raises(ImageHintsPendingError):
        _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)

    delete_draft(session, draft.id or 0, "demo")

    assert session.exec(select(PromptHint)).all() == []


@pytest.fixture
def image_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    yield client


def test_confirm_then_generate_in_the_ui(image_client: TestClient, session: Session) -> None:
    draft = _draft(session)
    image_client.post(f"/social/drafts/{draft.id}/images/suggest")
    data = {"prompt": PEOPLE_AND_TEXT, "image_format": "4:5"}

    first = image_client.post(f"/social/drafts/{draft.id}/images/generate", data=data)
    assert first.status_code == 422
    assert "data-image-hints" in first.text and "erst bestätigen, dann erzeugen" in first.text
    assert "keine echten Mitglieder" in first.text and "data-candidate=" not in first.text

    for hint in prompt_hints(session, draft.id or 0):
        image_client.post(f"/social/image-hints/{hint.id}/confirm")
    page = image_client.post(
        f"/social/drafts/{draft.id}/images/generate", data=data, follow_redirects=True
    ).text
    assert page.count("data-candidate=") == 3 and "✓ bestätigt von" in page
