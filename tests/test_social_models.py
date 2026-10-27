"""Tables and value objects of the social media module (SPEC_social section 7)."""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.approval import ReviewDecision
from app.core.outcomes import Outcome, worst_outcome
from app.social.channels import CHANNEL_SPECS
from app.social.facts import FACT_KEYS, FACT_LABELS, BriefingFacts
from app.social.models import (
    CHANNEL_LABELS,
    POST_STATUS_LABELS,
    Asset,
    Briefing,
    Campaign,
    Channel,
    CheckResult,
    PostDraft,
    PostReview,
    PostStatus,
    as_utc,
)


def _briefing(session: Session, **overrides: object) -> Briefing:
    briefing = Briefing(
        created_by="demo",
        brand_key="demo",
        area="kurse",
        occasion_type="kurs",
        notes="Neuer Kurs Rücken-Fit",
        facts=BriefingFacts(
            datum=date(2026, 11, 3), uhrzeit=time(18, 30), ort="Kursraum 2"
        ).to_json(),
        channels=["instagram_post", "facebook"],
        **overrides,
    )
    session.add(briefing)
    session.commit()
    session.refresh(briefing)
    return briefing


def test_briefing_round_trip(session: Session) -> None:
    briefing = _briefing(session, desired_date=date(2026, 10, 30))

    stored = session.get(Briefing, briefing.id)
    assert stored is not None
    assert stored.facts == {"datum": "2026-11-03", "uhrzeit": "18:30:00", "ort": "Kursraum 2"}
    assert stored.channels == ["instagram_post", "facebook"]
    facts = BriefingFacts.from_json(stored.facts)
    assert facts.uhrzeit == time(18, 30)
    assert facts.present_keys() == ["datum", "uhrzeit", "ort"]


def test_draft_with_checks_review_and_asset(session: Session) -> None:
    briefing = _briefing(session)
    draft = PostDraft(
        briefing_id=briefing.id, channel=Channel.FACEBOOK, text="Hallo", hashtags=["#Kurse"]
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    assert draft.status is PostStatus.DRAFT
    assert draft.is_ai_suggestion
    assert not draft.edited_by_human
    assert draft.full_text == "Hallo\n\n#Kurse"

    session.add(
        CheckResult(
            draft_id=draft.id,
            check_key="laenge",
            outcome=Outcome.OK,
            reason="ok",
            span_start=0,
            span_end=5,
        )
    )
    session.add(PostReview(draft_id=draft.id, decision=ReviewDecision.APPROVED, reviewer="demo"))
    session.add(
        Asset(
            briefing_id=briefing.id,
            filename="bild.png",
            stored_path="data/raw/x.png",
            media_type="image/png",
            content_hash="abc",
            uploaded_by="demo",
        )
    )
    session.commit()

    result = session.exec(select(CheckResult)).one()
    assert result.outcome is Outcome.OK
    assert not result.requires_confirmation
    asset = session.exec(select(Asset)).one()
    assert not asset.shows_persons and not asset.consent_confirmed


def test_scheduled_at_is_utc(session: Session) -> None:
    briefing = _briefing(session)
    when = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    draft = PostDraft(briefing_id=briefing.id, channel=Channel.LINKEDIN, scheduled_at=when)
    session.add(draft)
    session.commit()
    session.expire_all()

    stored = session.get(PostDraft, draft.id)
    assert stored is not None and stored.scheduled_at is not None
    assert as_utc(stored.scheduled_at) == when


def test_draft_requires_existing_briefing(session: Session) -> None:
    session.add(PostDraft(briefing_id=999, channel=Channel.FACEBOOK))

    with pytest.raises(IntegrityError):
        session.commit()


def test_campaign_links_briefings(session: Session) -> None:
    campaign = Campaign(
        name="Rücken-Fit Herbst",
        brand_key="demo",
        area="kurse",
        occasion_type="kurs",
        event_date=date(2026, 11, 3),
        created_by="demo",
    )
    session.add(campaign)
    session.commit()
    _briefing(session, campaign_id=campaign.id, campaign_step="erinnerung")

    linked = session.exec(select(Briefing).where(Briefing.campaign_id == campaign.id)).all()
    assert [b.campaign_step for b in linked] == ["erinnerung"]


def test_full_text_without_body_or_hashtags() -> None:
    assert (
        PostDraft(briefing_id=1, channel=Channel.FACEBOOK, text="Nur Text").full_text == "Nur Text"
    )
    assert (
        PostDraft(briefing_id=1, channel=Channel.FACEBOOK, hashtags=["#a", "#b"]).full_text
        == "#a #b"
    )


def test_labels_are_complete() -> None:
    assert set(CHANNEL_LABELS) == set(Channel)
    assert set(CHANNEL_SPECS) == set(Channel)
    assert set(POST_STATUS_LABELS) == set(PostStatus)
    assert set(FACT_LABELS) == set(FACT_KEYS)


def test_instagram_limits() -> None:
    spec = CHANNEL_SPECS[Channel.INSTAGRAM_POST]
    assert (spec.max_length, spec.max_hashtags) == (2200, 30)


def test_worst_outcome() -> None:
    assert worst_outcome([Outcome.OK, Outcome.WARNING]) is Outcome.WARNING
    assert worst_outcome([Outcome.WARNING, Outcome.ERROR, Outcome.OK]) is Outcome.ERROR
    assert worst_outcome([]) is None


# --- facts ---


def test_facts_missing_and_period() -> None:
    facts = BriefingFacts(zeitraum_von=date(2026, 11, 1), zeitraum_bis=date(2026, 11, 14))

    assert facts.has("zeitraum")
    assert facts.missing(["zeitraum", "preis"]) == ["preis"]
    assert facts.to_json() == {"zeitraum_von": "2026-11-01", "zeitraum_bis": "2026-11-14"}


@pytest.mark.parametrize(
    "data",
    [
        {"zeitraum_von": "2026-11-01"},
        {"zeitraum_von": "2026-11-14", "zeitraum_bis": "2026-11-01"},
        {"preis": "-1"},
        {"preis": "19.999"},
        {"telefon": "keine Nummer"},
        {"ort": ""},
        {"farbe": "blau"},
    ],
)
def test_invalid_facts(data: dict) -> None:
    with pytest.raises(ValidationError):
        BriefingFacts.model_validate(data)


def test_price_keeps_cents() -> None:
    assert BriefingFacts(preis=Decimal("19.00")).to_json() == {"preis": "19.00"}
