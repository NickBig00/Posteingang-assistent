"""Deletion concept of the social media module: delete per briefing/campaign, retention."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, select

from app import cli
from app.core.models import AuditEntry, AuditOutput
from app.main import app
from app.social.assets import (
    REVOKE_STEP,
    AssetError,
    confirm_consent,
    delete_asset,
    revoke_consent,
    save_asset,
)
from app.social.briefings import create_briefing
from app.social.calendar import publish_draft, schedule_draft
from app.social.campaigns import create_campaign, load_demo_campaign
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    Asset,
    Briefing,
    Campaign,
    Channel,
    CheckResult,
    PostDraft,
    PostReview,
    PostStatus,
)
from app.social.retention import (
    DELETE_STEP,
    DRAFT_DELETE_STEP,
    SYSTEM_ACTOR,
    delete_briefing,
    delete_campaign,
    delete_draft,
    purge_expired,
)
from app.social.review import approval_blockers, approve_draft, confirm_warning, reject_draft
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
NOW = datetime(2027, 6, 1, 12, 0, tzinfo=UTC)


def _briefing(session: Session, **overrides: object) -> Briefing:
    briefing = create_briefing(
        session,
        CONFIG,
        course_input(channels=[Channel.FACEBOOK, Channel.INSTAGRAM_POST], **overrides),
        "demo",
    )
    generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    return briefing


def _drafts(session: Session, briefing: Briefing) -> list[PostDraft]:
    return list(session.exec(select(PostDraft).where(PostDraft.briefing_id == briefing.id)).all())


def _count(session: Session, model: type[SQLModel]) -> int:
    return len(session.exec(select(model)).all())


def _age(session: Session, briefing: Briefing, days: int) -> None:
    """Move the creation of a briefing into the past (test helper)."""
    briefing.created_at = NOW - timedelta(days=days)
    session.add(briefing)
    session.commit()


# --- delete per briefing ---


def test_delete_removes_all_content(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    keep = _briefing(session, notes="Anderes Briefing")
    approve_draft(session, _drafts(session, briefing)[0], "demo")
    asset = save_asset(
        session, briefing, "kurs.png", PNG, shows_persons=True, consent_confirmed=True,
        user="demo", storage=raw_dir,
    )  # fmt: skip
    image = Path(asset.stored_path)
    entries_before = _count(session, AuditEntry)
    kept_outputs = {o.id for o in session.exec(select(AuditOutput)).all()} - _outputs_of(
        session, briefing
    )

    assert delete_briefing(session, briefing.id or 0, "demo")

    assert session.get(Briefing, briefing.id) is None
    remaining_drafts = session.exec(select(PostDraft)).all()
    assert {d.briefing_id for d in remaining_drafts} == {keep.id}
    draft_ids = {d.id for d in remaining_drafts}
    assert {r.draft_id for r in session.exec(select(CheckResult)).all()} <= draft_ids
    assert session.exec(select(PostReview)).all() == []
    assert session.exec(select(Asset)).all() == []
    assert not image.exists()
    # Raw model outputs of the deleted briefing are gone, those of other briefings stay.
    assert {o.id for o in session.exec(select(AuditOutput)).all()} == kept_outputs
    # Audit entries stay (hashes and metadata) plus one entry for the deletion.
    assert _count(session, AuditEntry) == entries_before + 1
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == DELETE_STEP)).one()
    assert entry.model_params["actor"] == "demo"
    assert entry.model_params["drafts"] == 2 and entry.model_params["images"] == 1
    assert entry.model_params["reason"] == "manual"


def _outputs_of(session: Session, briefing: Briefing) -> set[int]:
    entries = session.exec(select(AuditEntry)).all()
    ids = {e.id for e in entries if e.model_params.get("briefing_id") == briefing.id}
    return {
        o.id
        for o in session.exec(select(AuditOutput)).all()
        if o.audit_entry_id in ids and o.id is not None
    }


def test_shared_image_file_stays_for_other_briefing(session: Session, raw_dir: Path) -> None:
    first, second = _briefing(session), _briefing(session)
    one = save_asset(
        session, first, "a.png", PNG, shows_persons=False, consent_confirmed=False,
        user="demo", storage=raw_dir,
    )  # fmt: skip
    save_asset(
        session, second, "b.png", PNG, shows_persons=False, consent_confirmed=False,
        user="demo", storage=raw_dir,
    )  # fmt: skip

    delete_briefing(session, first.id or 0, "demo")

    assert Path(one.stored_path).exists()


def test_delete_unknown_briefing(session: Session) -> None:
    assert delete_briefing(session, 999, "demo") is False
    assert delete_campaign(session, 999, "demo") is False


def test_campaign_is_removed_with_its_last_briefing(session: Session) -> None:
    data = load_demo_campaign(CONFIG, NOW.date())
    campaign = create_campaign(session, CONFIG, SocialLLMClient(scripted()), data, "demo")
    briefings = session.exec(select(Briefing).where(Briefing.campaign_id == campaign.id)).all()

    delete_briefing(session, briefings[0].id or 0, "demo")
    assert session.get(Campaign, campaign.id) is not None
    for briefing in briefings[1:]:
        delete_briefing(session, briefing.id or 0, "demo")

    assert session.get(Campaign, campaign.id) is None


def test_delete_campaign(session: Session) -> None:
    data = load_demo_campaign(CONFIG, NOW.date())
    campaign = create_campaign(session, CONFIG, SocialLLMClient(scripted()), data, "demo")
    other = _briefing(session)

    assert delete_campaign(session, campaign.id or 0, "demo")

    assert session.exec(select(Campaign)).all() == []
    assert [b.id for b in session.exec(select(Briefing)).all()] == [other.id]
    assert len(session.exec(select(AuditEntry).where(AuditEntry.step == DELETE_STEP)).all()) == 3


# --- retention ---


def _finished(session: Session, days_ago: int, *, publish: bool = False) -> Briefing:
    briefing = _briefing(session)
    for draft in _drafts(session, briefing):
        if publish:
            approve_draft(session, draft, "demo")
            schedule_draft(session, draft, datetime(2099, 1, 1, 9), "demo")
            publish_draft(session, draft, "demo", now=NOW - timedelta(days=days_ago))
        else:
            reject_draft(session, draft, "demo")
    draft_ids = [d.id for d in _drafts(session, briefing)]
    for review in session.exec(select(PostReview)).all():
        if review.draft_id not in draft_ids:
            continue
        review.reviewed_at = NOW - timedelta(days=days_ago)
        session.add(review)
    _age(session, briefing, days_ago)
    for draft in _drafts(session, briefing):
        if draft.scheduled_at:
            draft.scheduled_at = NOW - timedelta(days=days_ago)
            session.add(draft)
    session.commit()
    return briefing


def test_purge_deletes_only_finished_and_expired(session: Session) -> None:
    old_rejected = _finished(session, 120)
    old_open = _briefing(session)
    _age(session, old_open, 120)
    recent_rejected = _finished(session, 10)

    summary = purge_expired(session, now=NOW, days=90)

    assert summary.deleted_briefing_ids == [old_rejected.id]
    assert summary.cutoff == NOW - timedelta(days=90)
    remaining = {b.id for b in session.exec(select(Briefing)).all()}
    assert remaining == {old_open.id, recent_rejected.id}
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == DELETE_STEP)).one()
    assert entry.model_params["actor"] == SYSTEM_ACTOR
    assert entry.model_params["reason"] == "retention"


def test_published_posts_count_from_publication(session: Session) -> None:
    published = _finished(session, 120, publish=True)
    for draft in _drafts(session, published):
        draft.published_at = NOW - timedelta(days=30)
        session.add(draft)
    session.commit()

    assert purge_expired(session, now=NOW, days=90).deleted_briefing_ids == []
    assert purge_expired(session, now=NOW, days=20).deleted_briefing_ids == [published.id]


def test_planned_posts_are_never_purged(session: Session) -> None:
    briefing = _briefing(session)
    for draft in _drafts(session, briefing):
        approve_draft(session, draft, "demo")
    _age(session, briefing, 400)

    assert purge_expired(session, now=NOW, days=90).deleted_briefing_ids == []


def test_retention_days_from_environment(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    briefing = _finished(session, 40)
    monkeypatch.setenv("RETENTION_DAYS", "30")

    assert purge_expired(session, now=NOW).deleted_briefing_ids == [briefing.id]


def test_cli_social_purge(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr("app.social.cli.engine", engine)

    assert cli.main(["social-purge"]) == 0

    assert "0 Briefings gelöscht" in capsys.readouterr().out


# --- web ---


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_delete_in_the_ui(social_client: TestClient, session: Session) -> None:
    briefing = _briefing(session)
    page = social_client.get(f"/social/briefings/{briefing.id}").text
    assert "Briefing löschen" in page
    assert "Tage nach der letzten Aktivität gelöscht" in social_client.get("/social").text

    response = social_client.post(f"/social/briefings/{briefing.id}/delete", follow_redirects=False)

    assert response.headers["location"] == "/social"
    assert social_client.get(f"/social/briefings/{briefing.id}").status_code == 404
    assert social_client.post(f"/social/briefings/{briefing.id}/delete").status_code == 404


def test_delete_campaign_in_the_ui(social_client: TestClient, session: Session) -> None:
    created = social_client.post("/social/campaigns/demo", follow_redirects=False)
    campaign_url = created.headers["location"]
    assert "Kampagne löschen" in social_client.get(campaign_url).text
    first = session.exec(select(Briefing)).first()
    assert first is not None

    one = social_client.post(f"/social/briefings/{first.id}/delete", follow_redirects=False)
    assert one.headers["location"] == campaign_url
    deleted = social_client.post(f"{campaign_url}/delete", follow_redirects=False)

    assert deleted.headers["location"] == "/social/campaigns"
    assert session.exec(select(Briefing)).all() == []
    assert social_client.post(f"{campaign_url}/delete").status_code == 404


# --- delete per draft ---


def test_delete_draft_keeps_briefing_and_other_drafts(session: Session) -> None:
    briefing = _briefing(session)
    first, second = _drafts(session, briefing)
    approve_draft(session, first, "demo")
    outputs_before = {o.audit_entry_id for o in session.exec(select(AuditOutput)).all()}
    first_entries = {
        e.id
        for e in session.exec(select(AuditEntry)).all()
        if e.model_params.get("draft_id") == first.id
    }

    assert delete_draft(session, first.id or 0, "demo")

    assert session.get(Briefing, briefing.id) is not None
    assert [d.id for d in _drafts(session, briefing)] == [second.id]
    assert all(r.draft_id == second.id for r in session.exec(select(CheckResult)).all())
    assert session.exec(select(PostReview)).all() == []
    outputs_after = {o.audit_entry_id for o in session.exec(select(AuditOutput)).all()}
    assert outputs_after == outputs_before - first_entries
    assert outputs_after  # the other draft's outputs stay
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == DRAFT_DELETE_STEP)).one()
    assert entry.model_params["draft_id"] == first.id
    assert delete_draft(session, first.id or 0, "demo") is False


# --- withdrawn image consent ---


def _with_consented_image(session: Session, raw_dir: Path) -> tuple[Briefing, Asset]:
    briefing = _briefing(session)
    asset = save_asset(
        session, briefing, "team.png", PNG, shows_persons=True, consent_confirmed=True,
        user="demo", storage=raw_dir,
    )  # fmt: skip
    return briefing, asset


def test_revoke_consent_lists_affected_posts(session: Session, raw_dir: Path) -> None:
    briefing, asset = _with_consented_image(session, raw_dir)
    planned, published = _drafts(session, briefing)
    for draft in (planned, published):
        approve_draft(session, draft, "demo")
        schedule_draft(session, draft, datetime(2099, 1, 1, 9), "demo")
    publish_draft(session, published, "demo")
    image = Path(asset.stored_path)

    revocation = revoke_consent(session, asset, "demo")

    assert [d.id for d in revocation.back_to_review] == [planned.id]
    assert [d.id for d in revocation.published] == [published.id]
    assert planned.status is PostStatus.IN_REVIEW and planned.scheduled_at is None
    assert published.status is PostStatus.PUBLISHED
    assert not image.exists()
    assert asset.consent_revoked_by == "demo" and not asset.consent_confirmed
    assert any("widerrufen" in b for b in approval_blockers(session, planned))
    with pytest.raises(AssetError, match="widerrufen"):
        confirm_consent(session, asset, "demo")
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == REVOKE_STEP)).one()
    assert entry.model_params["back_to_review"] == [planned.id]
    assert entry.model_params["published"] == [published.id]

    # Removing the image entry lifts the block.
    delete_asset(session, asset, "demo")
    assert approval_blockers(session, planned) == []


def test_revoke_needs_confirmed_consent(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    asset = save_asset(
        session, briefing, "x.png", PNG, shows_persons=True, consent_confirmed=False,
        user="demo", storage=raw_dir,
    )  # fmt: skip

    with pytest.raises(AssetError, match="keine bestätigte Einwilligung"):
        revoke_consent(session, asset, "demo")


def test_revoked_file_stays_for_other_consented_entry(session: Session, raw_dir: Path) -> None:
    _, revoked = _with_consented_image(session, raw_dir)
    _, other = _with_consented_image(session, raw_dir)

    revoke_consent(session, revoked, "demo")

    assert Path(other.stored_path).exists()


# --- audit holds only hashes and metadata ---


def test_audit_trail_holds_no_content(session: Session, raw_dir: Path) -> None:
    notes = "Geheimtipp Zirkeltraining mit Trainerin Anna"
    briefing = _briefing(session, notes=notes)
    first, second = _drafts(session, briefing)
    asset = save_asset(
        session, briefing, "anna-im-kurs.png", PNG, shows_persons=True, consent_confirmed=True,
        user="demo", storage=raw_dir,
    )  # fmt: skip
    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == first.id)).all():
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")
    approve_draft(session, first, "demo")
    schedule_draft(session, first, datetime(2099, 1, 1, 9), "demo")
    revoke_consent(session, asset, "demo")
    delete_draft(session, second.id or 0, "demo")
    texts = [notes, first.text, "anna-im-kurs", "Zirkeltraining", "Anna"]

    delete_briefing(session, briefing.id or 0, "demo")

    entries = session.exec(select(AuditEntry)).all()
    assert len(entries) > 10
    for entry in entries:
        stored = json.dumps(
            [entry.step, entry.model, entry.model_params, entry.prompt_version, entry.error],
            ensure_ascii=False,
        )
        assert not any(text in stored for text in texts), entry.step
        assert len(entry.input_hash) == 64
    assert session.exec(select(AuditOutput)).all() == []


def test_revoke_and_delete_draft_in_the_ui(
    social_client: TestClient, session: Session, raw_dir: Path
) -> None:
    briefing, asset = _with_consented_image(session, raw_dir)
    first, second = _drafts(session, briefing)
    approve_draft(session, first, "demo")
    page = social_client.get(f"/social/briefings/{briefing.id}").text
    assert "Einwilligung widerrufen</button>" in page
    assert "Entwurf löschen" in page

    revoked = social_client.post(f"/social/assets/{asset.id}/revoke")

    assert revoked.status_code == 200
    assert "Zurück in die Prüfung" in revoked.text
    assert f"Entwurf {first.id} · Facebook-Beitrag" in revoked.text
    assert social_client.get(f"/social/assets/{asset.id}").status_code == 404
    again = social_client.post(f"/social/assets/{asset.id}/revoke")
    assert again.status_code == 409

    second_id, briefing_id = second.id, briefing.id
    deleted = social_client.post(f"/social/drafts/{second_id}/delete", follow_redirects=False)
    assert deleted.headers["location"] == f"/social/briefings/{briefing_id}"
    session.expire_all()
    assert session.get(PostDraft, second_id) is None
    assert social_client.post(f"/social/drafts/{second_id}/delete").status_code == 404
