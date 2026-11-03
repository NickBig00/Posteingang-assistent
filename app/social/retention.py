"""Deletion concept of the social media module (analogous to the inbox, SPEC section 7).

Deleting a single draft removes its text, check results, decisions and raw model outputs.
Deleting a briefing removes everything with content: the briefing (key points, facts),
its drafts, check results, review decisions, images (database entries and files) and the
raw model outputs of its LLM calls. Audit entries stay: they only hold hashes, ids and
metadata. A campaign without briefings is removed as well.

Retention: finished briefings – every post published or rejected – are deleted once their
last activity is older than `RETENTION_DAYS`. Briefings with open posts are kept.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session, col, delete, select

from app.core.audit import write_human_audit_entry
from app.core.models import AuditOutput, utcnow
from app.core.retention import retention_days
from app.social.audit import briefing_audit_trail
from app.social.models import (
    Asset,
    Briefing,
    Campaign,
    CheckResult,
    PostDraft,
    PostReview,
    PostStatus,
    as_utc,
)

logger = logging.getLogger(__name__)

DELETE_STEP = "social.briefing_delete"
DRAFT_DELETE_STEP = "social.draft_delete"
SYSTEM_ACTOR = "system"
FINISHED_STATUSES = frozenset({PostStatus.PUBLISHED, PostStatus.REJECTED})


@dataclass
class PurgeSummary:
    deleted_briefing_ids: list[int]
    cutoff: datetime


def _delete_files(paths: set[str], session: Session) -> None:
    for path in paths:
        still_used = session.exec(select(Asset).where(Asset.stored_path == path)).first()
        if still_used is None and Path(path).is_file():
            Path(path).unlink()


def delete_briefing(session: Session, briefing_id: int, user: str, reason: str = "manual") -> bool:
    """Delete one briefing with all content. Returns False if it does not exist."""
    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        return False
    campaign_id = briefing.campaign_id
    draft_ids = list(
        session.exec(select(PostDraft.id).where(PostDraft.briefing_id == briefing_id)).all()
    )
    entry_ids = [e.id for e in briefing_audit_trail(session, briefing_id) if e.id is not None]
    files = {
        a.stored_path
        for a in session.exec(select(Asset).where(Asset.briefing_id == briefing_id)).all()
    }
    statements = [
        delete(AuditOutput).where(col(AuditOutput.audit_entry_id).in_(entry_ids)),
        delete(CheckResult).where(col(CheckResult.draft_id).in_(draft_ids)),
        delete(PostReview).where(col(PostReview.draft_id).in_(draft_ids)),
        delete(PostDraft).where(col(PostDraft.briefing_id) == briefing_id),
        delete(Asset).where(col(Asset.briefing_id) == briefing_id),
    ]
    for statement in statements:
        session.exec(statement)  # type: ignore[call-overload]
    session.delete(briefing)
    session.commit()
    session.expire_all()
    _delete_files(files, session)
    if campaign_id is not None:
        remaining = session.exec(
            select(Briefing).where(Briefing.campaign_id == campaign_id)
        ).first()
        campaign = session.get(Campaign, campaign_id)
        if remaining is None and campaign is not None:
            session.delete(campaign)
            session.commit()
    write_human_audit_entry(
        session,
        item_id=None,
        step=DELETE_STEP,
        actor=user,
        details={
            "briefing_id": briefing_id,
            "campaign_id": campaign_id,
            "drafts": len(draft_ids),
            "images": len(files),
            "reason": reason,
        },
    )
    logger.info("social briefing %s deleted (%s)", briefing_id, reason)
    return True


def delete_draft(session: Session, draft_id: int, user: str) -> bool:
    """Delete one draft with its check results, decisions and raw model outputs.

    The briefing (and the other channels) stay. Returns False if the draft does not exist.
    """
    draft = session.get(PostDraft, draft_id)
    if draft is None:
        return False
    briefing_id = draft.briefing_id
    entry_ids = [
        e.id
        for e in briefing_audit_trail(session, briefing_id)
        if e.id is not None and e.model_params.get("draft_id") == draft_id
    ]
    statements = [
        delete(AuditOutput).where(col(AuditOutput.audit_entry_id).in_(entry_ids)),
        delete(CheckResult).where(col(CheckResult.draft_id) == draft_id),
        delete(PostReview).where(col(PostReview.draft_id) == draft_id),
    ]
    for statement in statements:
        session.exec(statement)  # type: ignore[call-overload]
    session.delete(draft)
    session.commit()
    write_human_audit_entry(
        session,
        item_id=None,
        step=DRAFT_DELETE_STEP,
        actor=user,
        details={"briefing_id": briefing_id, "draft_id": draft_id},
    )
    logger.info("social draft %s deleted", draft_id)
    return True


def delete_campaign(session: Session, campaign_id: int, user: str) -> bool:
    """Delete a campaign with all its briefings."""
    if session.get(Campaign, campaign_id) is None:
        return False
    briefing_ids = session.exec(
        select(Briefing.id).where(Briefing.campaign_id == campaign_id)
    ).all()
    for briefing_id in briefing_ids:
        if briefing_id is not None:
            delete_briefing(session, briefing_id, user)
    campaign = session.get(Campaign, campaign_id)
    if campaign is not None:  # a campaign without briefings
        session.delete(campaign)
        session.commit()
    return True


def last_activity(session: Session, briefing: Briefing) -> datetime:
    """Latest point in time something happened to the briefing or its posts."""
    moments = [as_utc(briefing.created_at)]
    drafts = session.exec(select(PostDraft).where(PostDraft.briefing_id == briefing.id)).all()
    draft_ids = [d.id for d in drafts]
    moments += [as_utc(d.published_at) for d in drafts if d.published_at]
    moments += [as_utc(d.scheduled_at) for d in drafts if d.scheduled_at]
    reviews = session.exec(select(PostReview).where(col(PostReview.draft_id).in_(draft_ids))).all()
    moments += [as_utc(r.reviewed_at) for r in reviews]
    assets = session.exec(select(Asset).where(Asset.briefing_id == briefing.id)).all()
    moments += [as_utc(a.uploaded_at) for a in assets]
    return max(moments)


def is_finished(session: Session, briefing: Briefing) -> bool:
    statuses = session.exec(
        select(PostDraft.status).where(PostDraft.briefing_id == briefing.id)
    ).all()
    return all(status in FINISHED_STATUSES for status in statuses)


def purge_expired(
    session: Session, now: datetime | None = None, days: int | None = None
) -> PurgeSummary:
    """Delete finished briefings whose last activity is older than the retention period."""
    cutoff = (now or utcnow()) - timedelta(days=days or retention_days())
    expired = [
        briefing.id
        for briefing in session.exec(select(Briefing)).all()
        if briefing.id is not None
        and is_finished(session, briefing)
        and last_activity(session, briefing) < cutoff
    ]
    for briefing_id in expired:
        delete_briefing(session, briefing_id, SYSTEM_ACTOR, reason="retention")
    logger.info("social retention purge deleted %d briefings", len(expired))
    return PurgeSummary(deleted_briefing_ids=expired, cutoff=cutoff)
