"""Status workflow, editing, images with consent and the full UI run (ROADMAP_social S5)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.approval import InvalidStatusTransitionError
from app.core.audit import HUMAN_ACTOR_MODEL
from app.core.models import AuditEntry
from app.core.outcomes import Outcome
from app.main import app
from app.social.assets import (
    AssetError,
    confirm_consent,
    consent_blockers,
    delete_asset,
    save_asset,
)
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import Asset, Briefing, Channel, CheckResult, PostDraft, PostStatus
from app.social.review import (
    EDIT_STEP,
    REOPEN_STEP,
    REVIEW_STEP,
    ReviewNotAllowedError,
    approval_blockers,
    approve_draft,
    edit_draft,
    parse_hashtags,
    recheck_draft,
    reject_draft,
    reopen_draft,
)
from app.social.status import ALLOWED_TRANSITIONS, MissingReviewError, transition
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _client() -> SocialLLMClient:
    return SocialLLMClient(scripted())


def _draft(session: Session, **overrides: object) -> PostDraft:
    briefing = create_briefing(
        session, CONFIG, course_input(channels=[Channel.FACEBOOK], **overrides), "demo"
    )
    (draft,) = generate_drafts(session, CONFIG, _client(), briefing)
    return draft


def _steps(session: Session) -> list[str]:
    entries = session.exec(select(AuditEntry).order_by(AuditEntry.id)).all()  # type: ignore[arg-type]
    return [e.step for e in entries if e.model == HUMAN_ACTOR_MODEL]


# --- status machine ---


def test_happy_path_statuses_are_allowed() -> None:
    path = [
        PostStatus.DRAFT,
        PostStatus.IN_REVIEW,
        PostStatus.APPROVED,
        PostStatus.SCHEDULED,
        PostStatus.PUBLISHED,
    ]
    for current, target in zip(path, path[1:], strict=False):
        assert target in ALLOWED_TRANSITIONS[current]
    assert ALLOWED_TRANSITIONS[PostStatus.PUBLISHED] == frozenset()


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PostStatus.DRAFT, PostStatus.APPROVED),
        (PostStatus.NEEDS_MANUAL_REVIEW, PostStatus.APPROVED),
        (PostStatus.IN_REVIEW, PostStatus.PUBLISHED),
        (PostStatus.APPROVED, PostStatus.PUBLISHED),
        (PostStatus.PUBLISHED, PostStatus.REJECTED),
        (PostStatus.REJECTED, PostStatus.APPROVED),
    ],
)
def test_forbidden_transitions(session: Session, current: PostStatus, target: PostStatus) -> None:
    draft = _draft(session)
    draft.status = current

    with pytest.raises(InvalidStatusTransitionError):
        transition(session, draft, target)


def test_approval_needs_a_review(session: Session) -> None:
    draft = _draft(session)

    with pytest.raises(MissingReviewError):
        transition(session, draft, PostStatus.APPROVED)


# --- approve / reject / reopen ---


def test_errors_block_the_approval(session: Session) -> None:
    draft = _draft(session)
    edit_draft(
        session, CONFIG, _client(), draft, "Kurs am 4.11. um 18:30 Uhr im Kursraum 2", "", "demo"
    )

    blockers = approval_blockers(session, draft)
    assert any("Datum weicht ab" in b for b in blockers)
    with pytest.raises(ReviewNotAllowedError, match="Freigabe nicht möglich"):
        approve_draft(session, draft, "demo")
    assert draft.status is PostStatus.IN_REVIEW


def test_reject_and_reopen(session: Session) -> None:
    draft = _draft(session)

    reject_draft(session, draft, "demo")
    assert draft.status is PostStatus.REJECTED
    with pytest.raises(ReviewNotAllowedError):
        approve_draft(session, draft, "demo")
    with pytest.raises(ReviewNotAllowedError, match="nicht bearbeitbar"):
        edit_draft(session, CONFIG, _client(), draft, "neu", "", "demo")

    reopen_draft(session, CONFIG, _client(), draft, "demo")
    assert draft.status is PostStatus.IN_REVIEW
    approve_draft(session, draft, "demo")
    assert draft.status is PostStatus.APPROVED
    assert _steps(session) == [REVIEW_STEP, REOPEN_STEP, REVIEW_STEP]


def test_withdraw_after_approval(session: Session) -> None:
    draft = _draft(session)
    approve_draft(session, draft, "demo")

    reject_draft(session, draft, "demo")

    assert draft.status is PostStatus.REJECTED


def test_reopen_only_rejected(session: Session) -> None:
    draft = _draft(session)

    with pytest.raises(ReviewNotAllowedError):
        reopen_draft(session, CONFIG, _client(), draft, "demo")


# --- editing ---


def test_edit_runs_checks_and_marks_human_edit(session: Session) -> None:
    draft = _draft(session)
    text = "Rücken-Fit am 03.11.2026 um 18:30 Uhr im Kursraum 2 – melde dich an!"

    edit_draft(session, CONFIG, _client(), draft, text, "#Kurse, #Musterstadt #Kurse", "demo")

    assert draft.text == text
    assert draft.hashtags == ["#Kurse", "#Musterstadt"]
    assert draft.edited_by_human and draft.is_ai_suggestion
    assert draft.status is PostStatus.IN_REVIEW
    results = session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all()
    assert {r.outcome for r in results} == {Outcome.OK}
    assert _steps(session) == [EDIT_STEP]


def test_manual_text_for_needs_manual_review(session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(
        session, CONFIG, SocialLLMClient(scripted(post_draft=["x", "y"])), briefing
    )
    assert draft.status is PostStatus.NEEDS_MANUAL_REVIEW

    edit_draft(
        session,
        CONFIG,
        _client(),
        draft,
        "Rücken-Fit am 3.11. um 18:30 Uhr im Kursraum 2",
        "",
        "demo",
    )

    assert draft.status is PostStatus.IN_REVIEW
    approve_draft(session, draft, "demo")


def test_recheck_after_failed_assessment(session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(
        session, CONFIG, SocialLLMClient(scripted(post_assess=["x", "y"])), briefing
    )
    assert draft.status is PostStatus.NEEDS_MANUAL_REVIEW

    recheck_draft(session, CONFIG, _client(), draft, "demo")

    assert draft.status is PostStatus.IN_REVIEW


@pytest.mark.parametrize("text", ["Kurse", "#", "#Kurs-Tag", "# Kurse"])
def test_invalid_hashtags(text: str) -> None:
    with pytest.raises(ReviewNotAllowedError, match="Ungültige Hashtags"):
        parse_hashtags(text)


def test_too_long_text(session: Session) -> None:
    draft = _draft(session)

    with pytest.raises(ReviewNotAllowedError, match="zu lang"):
        edit_draft(session, CONFIG, _client(), draft, "x" * 10001, "", "demo")


# --- images and consent ---


def _briefing(session: Session) -> Briefing:
    return create_briefing(session, CONFIG, course_input(), "demo")


def test_image_with_persons_needs_consent(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    briefing = session.get(Briefing, draft.briefing_id)
    assert briefing is not None
    asset = save_asset(
        session, briefing, "kurs.png", PNG, shows_persons=True, consent_confirmed=False,
        user="demo", storage=raw_dir,
    )  # fmt: skip

    assert consent_blockers(session, briefing.id or 0) == [
        "Bild „kurs.png“ zeigt Personen – die Einwilligung ist nicht bestätigt."
    ]
    with pytest.raises(ReviewNotAllowedError, match="Einwilligung ist nicht bestätigt"):
        approve_draft(session, draft, "demo")

    confirm_consent(session, asset, "demo")
    assert asset.consent_by == "demo" and asset.consent_at is not None
    approve_draft(session, draft, "demo")
    assert draft.status is PostStatus.APPROVED


def test_consent_given_at_upload(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)

    asset = save_asset(
        session, briefing, "team.jpg", JPG, shows_persons=True, consent_confirmed=True,
        user="demo", storage=raw_dir,
    )  # fmt: skip

    assert asset.consent_confirmed and asset.media_type == "image/jpeg"
    assert Path(asset.stored_path).read_bytes() == JPG
    assert consent_blockers(session, briefing.id or 0) == []


def test_image_without_persons(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)

    asset = save_asset(
        session, briefing, "geraete.png", PNG, shows_persons=False, consent_confirmed=True,
        user="demo", storage=raw_dir,
    )  # fmt: skip

    # Consent is only recorded where persons are shown.
    assert not asset.consent_confirmed
    with pytest.raises(AssetError, match="keine Personen"):
        confirm_consent(session, asset, "demo")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "leer"),
        (b"GIF89a....", "PNG oder JPG"),
        (b"<svg></svg>", "PNG oder JPG"),
        (PNG + b"\x00" * (10 * 1024 * 1024), "zu groß"),
    ],
)
def test_invalid_uploads(session: Session, raw_dir: Path, content: bytes, message: str) -> None:
    with pytest.raises(AssetError, match=message):
        save_asset(
            session, _briefing(session), "x.png", content, shows_persons=False,
            consent_confirmed=False, user="demo", storage=raw_dir,
        )  # fmt: skip


def test_delete_image(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    asset = save_asset(
        session, briefing, "kurs.png", PNG, shows_persons=True, consent_confirmed=False,
        user="demo", storage=raw_dir,
    )  # fmt: skip
    path = Path(asset.stored_path)

    delete_asset(session, asset, "demo")

    assert session.exec(select(Asset)).all() == []
    assert not path.exists()
    assert consent_blockers(session, briefing.id or 0) == []
    assert "social.asset_delete" in _steps(session)


# --- full run in the UI ---


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = _client
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_full_run_briefing_to_approval(social_client: TestClient, session: Session) -> None:
    created = social_client.post(
        "/social/briefings",
        data={
            "area": "kurse",
            "occasion_type": "kurs",
            "notes": "Neuer Kurs Rücken-Fit für Einsteiger",
            "datum": "2026-11-03",
            "uhrzeit": "18:30",
            "ort": "Kursraum 2",
            "channels": ["instagram_post", "facebook"],
        },
    )
    assert created.status_code == 200  # followed the redirect to the detail page
    page = created.text
    assert page.count("KI-Vorschlag</span>") == 2
    assert page.count("Prüfungen ok") >= 2
    assert "Text kopieren" not in page

    drafts = session.exec(select(PostDraft).order_by(PostDraft.id)).all()  # type: ignore[arg-type]
    first = drafts[0]
    approved = social_client.post(f"/social/drafts/{first.id}/approve")
    assert approved.status_code == 200
    assert "Freigegeben von demo" in approved.text
    assert "Text kopieren" in approved.text
    assert f'data-copy="{first.full_text.splitlines()[0]}' in approved.text

    rejected = social_client.post(f"/social/drafts/{drafts[1].id}/reject")
    assert "Wieder öffnen" in rejected.text
    session.expire_all()
    assert [d.status for d in session.exec(select(PostDraft).order_by(PostDraft.id)).all()] == [  # type: ignore[arg-type]
        PostStatus.APPROVED,
        PostStatus.REJECTED,
    ]

    audit = social_client.get(f"/social/briefings/{first.briefing_id}/audit").text
    assert "social.post_draft" in audit and "social.post_assess" in audit
    assert "social.review" in audit


def test_ui_blocks_approval_with_error(social_client: TestClient, session: Session) -> None:
    draft = _draft(session)

    edited = social_client.post(
        f"/social/drafts/{draft.id}/edit",
        data={"text": "Kurs am 3.11. um 19:30 Uhr im Kursraum 2", "hashtags": "#Kurse"},
    )
    assert "Uhrzeit weicht ab" in edited.text
    assert "19:30 Uhr</mark>" in edited.text

    response = social_client.post(f"/social/drafts/{draft.id}/approve")
    assert response.status_code == 409
    assert "Freigabe nicht möglich" in response.text
    session.refresh(draft)
    assert draft.status is PostStatus.IN_REVIEW


def test_ui_invalid_hashtags(social_client: TestClient, session: Session) -> None:
    draft = _draft(session)

    response = social_client.post(
        f"/social/drafts/{draft.id}/edit", data={"text": "x", "hashtags": "ohne Raute"}
    )

    assert response.status_code == 409
    assert "Ungültige Hashtags" in response.text


def test_ui_image_upload_and_consent(social_client: TestClient, session: Session) -> None:
    draft = _draft(session)

    uploaded = social_client.post(
        f"/social/briefings/{draft.briefing_id}/assets",
        files={"file": ("kurs.png", PNG, "image/png")},
        data={"shows_persons": "true"},
    )
    assert uploaded.status_code == 200
    assert "Einwilligung fehlt" in uploaded.text
    (asset,) = session.exec(select(Asset)).all()
    image = social_client.get(f"/social/assets/{asset.id}")
    assert image.headers["content-type"] == "image/png"
    assert image.content == PNG

    blocked = social_client.post(f"/social/drafts/{draft.id}/approve")
    assert blocked.status_code == 409
    social_client.post(f"/social/assets/{asset.id}/consent")
    assert social_client.post(f"/social/drafts/{draft.id}/approve").status_code == 200
    session.refresh(draft)
    assert draft.status is PostStatus.APPROVED


def test_ui_rejects_non_images(social_client: TestClient, session: Session) -> None:
    draft = _draft(session)

    response = social_client.post(
        f"/social/briefings/{draft.briefing_id}/assets",
        files={"file": ("bild.png", b"kein bild", "image/png")},
    )

    assert response.status_code == 422
    assert "PNG oder JPG" in response.text


def test_ui_not_found(social_client: TestClient) -> None:
    assert social_client.post("/social/drafts/999/approve").status_code == 404
    assert social_client.get("/social/assets/999").status_code == 404
    assert social_client.get("/social/briefings/999/audit").status_code == 404
