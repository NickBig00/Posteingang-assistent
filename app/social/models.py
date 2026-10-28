"""Data model of the social media module (docs/SPEC_social.md, section 7).

The brand profile is configuration (YAML, see `app.social.profiles`), not a table.
Approvals use the shared review decision and the immutable audit trail from `core`.
"""

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel

from app.core.approval import ReviewDecision
from app.core.models import timestamp_column, utcnow
from app.core.outcomes import Outcome


def _optional_timestamp_column() -> Column:
    return Column(DateTime(timezone=True), nullable=True)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; all stored timestamps are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Channel(StrEnum):
    INSTAGRAM_POST = "instagram_post"
    INSTAGRAM_STORY = "instagram_story"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"


CHANNEL_LABELS: dict[Channel, str] = {
    Channel.INSTAGRAM_POST: "Instagram-Beitrag",
    Channel.INSTAGRAM_STORY: "Instagram-Story",
    Channel.FACEBOOK: "Facebook-Beitrag",
    Channel.LINKEDIN: "LinkedIn-Beitrag",
}


class PostStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    # Invalid LLM output after one retry: a person has to write or fix the text.
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"


POST_STATUS_LABELS: dict[PostStatus, str] = {
    PostStatus.DRAFT: "Entwurf",
    PostStatus.IN_REVIEW: "In Prüfung",
    PostStatus.NEEDS_MANUAL_REVIEW: "Manuelle Bearbeitung",
    PostStatus.APPROVED: "Freigegeben",
    PostStatus.SCHEDULED: "Geplant",
    PostStatus.PUBLISHED: "Veröffentlicht",
    PostStatus.REJECTED: "Verworfen",
}


class Campaign(SQLModel, table=True):
    """Plan over several weeks for one occasion; every post is approved on its own."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    brand_key: str
    area: str
    occasion_type: str
    # Date of the occasion itself (e.g. start of the course).
    event_date: date
    created_by: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())


class Briefing(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
    created_by: str
    brand_key: str
    # Area of the brand profile (e.g. `physio`): decides address form, tone, channels.
    area: str
    # Occasion type of the industry profile (e.g. `kurs`).
    occasion_type: str
    # Key points written by a person (input for the LLM).
    notes: str
    # Hard facts, structured (see `app.social.facts.BriefingFacts`), e.g. {"preis": "19.00"}.
    facts: dict[str, JsonValue] = Field(default_factory=dict, sa_column=Column(JSON))
    channels: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    desired_date: date | None = None
    campaign_id: int | None = Field(default=None, foreign_key="campaign.id", index=True)
    # Step of the campaign plan (e.g. `erinnerung`); None outside campaigns.
    campaign_step: str | None = None


class PostDraft(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    briefing_id: int = Field(foreign_key="briefing.id", index=True)
    channel: Channel
    text: str = ""
    hashtags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: PostStatus = Field(default=PostStatus.DRAFT, index=True)
    scheduled_at: datetime | None = Field(default=None, sa_column=_optional_timestamp_column())
    published_at: datetime | None = Field(default=None, sa_column=_optional_timestamp_column())
    # Transparency requirement: the text was produced by the LLM (kept after approval as
    # provenance; the label is shown in the UI, never inside the post text).
    is_ai_suggestion: bool = True
    # True once a person changed the text.
    edited_by_human: bool = False
    created_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())

    @property
    def full_text(self) -> str:
        """Text as published: body plus hashtags."""
        if not self.hashtags:
            return self.text
        return f"{self.text}\n\n{' '.join(self.hashtags)}" if self.text else " ".join(self.hashtags)


class CheckResult(SQLModel, table=True):
    """Result of one check on one draft; marks the text passage it refers to."""

    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(foreign_key="postdraft.id", index=True)
    check_key: str
    outcome: Outcome
    reason: str
    # Character span in `PostDraft.full_text`; None if the finding has no single passage.
    span_start: int | None = None
    span_end: int | None = None
    excerpt: str | None = None
    # True for findings of the LLM assessment (AI result, labelled in the UI).
    is_ai_assessment: bool = False
    # Warnings that must be confirmed by a person before approval (health claims, persons).
    requires_confirmation: bool = False
    # Errors that only the confirmed consent of the named person lifts (members in a post);
    # the consent is recorded in `confirmed_by`/`confirmed_at`.
    consent_required: bool = False
    confirmed_by: str | None = None
    confirmed_at: datetime | None = Field(default=None, sa_column=_optional_timestamp_column())


class Asset(SQLModel, table=True):
    """Uploaded image of a briefing with the consent of the people shown on it."""

    id: int | None = Field(default=None, primary_key=True)
    briefing_id: int = Field(foreign_key="briefing.id", index=True)
    filename: str
    stored_path: str
    media_type: str
    content_hash: str
    uploaded_by: str
    uploaded_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
    shows_persons: bool = False
    consent_confirmed: bool = False
    consent_by: str | None = None
    consent_at: datetime | None = Field(default=None, sa_column=_optional_timestamp_column())


class PostReview(SQLModel, table=True):
    """Human decision on a draft (approve or reject)."""

    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(foreign_key="postdraft.id", index=True)
    decision: ReviewDecision
    reviewer: str
    reviewed_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
