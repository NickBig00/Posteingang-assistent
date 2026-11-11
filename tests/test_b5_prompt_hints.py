"""B5: checks on image prompts give hints that block approval until a person confirms them."""

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
    HINT_CONFIRM_STEP,
    HINT_PERSONS,
    HINT_TEXT,
    ImagePromptRejectedError,
    ImageSuggestionError,
    choose_candidate,
    confirm_prompt_hint,
    generate_variants,
    prompt_hints,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import Channel, PostDraft, PromptHint
from app.social.retention import delete_draft
from app.social.review import ReviewNotAllowedError, approval_blockers, approve_draft
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


def _generate(session: Session, draft: PostDraft, storage: Path, prompt: str) -> None:
    rows = generate_variants(
        session,
        CONFIG,
        FakeImageProvider(),
        draft,
        prompt=prompt,
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=storage,
    )
    choose_candidate(session, draft, rows[0].id or 0, "demo")


def test_people_and_text_are_generated_and_recorded_as_hints(
    session: Session, raw_dir: Path
) -> None:
    draft = _draft(session)

    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)

    hints = prompt_hints(session, draft.id or 0)
    assert {h.key for h in hints} == {HINT_PERSONS, HINT_TEXT}
    assert all(h.confirmed_at is None for h in hints)
    text_hint = next(h for h in hints if h.key == HINT_TEXT)
    assert "Info-Grafik" in text_hint.reason
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == "social.image_generate")).one()
    assert set(entry.model_params["hints"]) == {HINT_PERSONS, HINT_TEXT}  # type: ignore[arg-type]


def test_hints_block_approval_until_confirmed_and_are_audited(
    session: Session, raw_dir: Path
) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)

    assert any("Hinweis zum KI-Bild" in b for b in approval_blockers(session, draft))
    with pytest.raises(ReviewNotAllowedError):
        approve_draft(session, draft, "demo")

    for hint in prompt_hints(session, draft.id or 0):
        confirm_prompt_hint(session, hint, "demo")

    assert not any("Hinweis zum KI-Bild" in b for b in approval_blockers(session, draft))
    approve_draft(session, draft, "demo")
    entries = session.exec(select(AuditEntry).where(AuditEntry.step == HINT_CONFIRM_STEP)).all()
    assert len(entries) == 2
    assert {e.model_params["hint"] for e in entries} == {HINT_PERSONS, HINT_TEXT}
    assert all(e.model_params["actor"] == "demo" for e in entries)
    # The audit does not contain the prompt itself.
    assert all("Rückenfit" not in str(e.model_params) for e in entries)


def test_hints_do_not_block_without_a_chosen_ai_image(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    generate_variants(
        session,
        CONFIG,
        FakeImageProvider(),
        draft,
        prompt=PEOPLE_AND_TEXT,
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=raw_dir,
    )

    assert not any("Hinweis zum KI-Bild" in b for b in approval_blockers(session, draft))


def test_a_changed_prompt_needs_a_new_confirmation(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)
    for hint in prompt_hints(session, draft.id or 0):
        confirm_prompt_hint(session, hint, "demo")

    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)  # same prompt: confirmations kept
    assert all(h.confirmed_at for h in prompt_hints(session, draft.id or 0))

    _generate(session, draft, raw_dir, "Mann beim Dehnen am Fenster")
    hints = prompt_hints(session, draft.id or 0)
    assert [h.key for h in hints] == [HINT_PERSONS] and hints[0].confirmed_at is None


def test_confirming_twice_or_after_approval_is_refused(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, "Frau auf der Matte")
    (hint,) = prompt_hints(session, draft.id or 0)
    confirm_prompt_hint(session, hint, "demo")

    with pytest.raises(ImageSuggestionError, match="bereits"):
        confirm_prompt_hint(session, hint, "demo")
    approve_draft(session, draft, "demo")
    with pytest.raises(ImageSuggestionError, match="vor der Freigabe"):
        confirm_prompt_hint(session, hint, "demo")


def test_member_names_still_block_generation(session: Session, raw_dir: Path) -> None:
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
    draft = _draft(session)
    provider = FakeImageProvider()

    with pytest.raises(ImagePromptRejectedError, match="Mitgliederliste"):
        generate_variants(
            session,
            CONFIG,
            provider,
            draft,
            prompt="Erika Beispiel beim Training",
            fmt=ImageFormat.SQUARE,
            user="demo",
            storage=raw_dir,
        )
    assert provider.calls == []


def test_post_errors_still_block(session: Session) -> None:
    from app.social.models import CheckResult

    draft = _draft(session)
    session.add(
        CheckResult(draft_id=draft.id or 0, check_key="fakten", outcome="error", reason="x")
    )
    session.commit()

    assert any(b.startswith("Fehler") for b in approval_blockers(session, draft))


def test_deleting_the_draft_removes_the_hints(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    _generate(session, draft, raw_dir, PEOPLE_AND_TEXT)

    delete_draft(session, draft.id or 0, "demo")

    assert session.exec(select(PromptHint)).all() == []


@pytest.fixture
def image_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    yield client


def test_hints_in_the_ui(image_client: TestClient, session: Session) -> None:
    draft = _draft(session)
    image_client.post(f"/social/drafts/{draft.id}/images/suggest")

    page = image_client.post(
        f"/social/drafts/{draft.id}/images/generate",
        data={"prompt": PEOPLE_AND_TEXT, "image_format": "4:5"},
        follow_redirects=True,
    ).text
    assert "data-image-hints" in page and "Geprüft – bestätigen" in page
    assert "keine echten Mitglieder" in page

    hint = prompt_hints(session, draft.id or 0)[0]
    page = image_client.post(f"/social/image-hints/{hint.id}/confirm", follow_redirects=True).text
    assert "✓ bestätigt von" in page
