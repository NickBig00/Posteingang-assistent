"""Status machine of post drafts (SPEC_social section 5).

draft -> in_review -> approved -> scheduled -> published, or rejected. `needs_manual_review`
holds drafts without a usable text or assessment until a person fixed them. Approval and
rejection need a human review; publishing happens by hand, the click only records it.
"""

from sqlmodel import Session, select

from app.core.approval import ReviewDecision, check_transition
from app.social.models import PostDraft, PostReview, PostStatus

S = PostStatus
ALLOWED_TRANSITIONS: dict[PostStatus, frozenset[PostStatus]] = {
    S.DRAFT: frozenset({S.IN_REVIEW, S.NEEDS_MANUAL_REVIEW, S.REJECTED}),
    S.IN_REVIEW: frozenset({S.IN_REVIEW, S.NEEDS_MANUAL_REVIEW, S.APPROVED, S.REJECTED}),
    S.NEEDS_MANUAL_REVIEW: frozenset({S.IN_REVIEW, S.NEEDS_MANUAL_REVIEW, S.REJECTED}),
    # -> rejected: an approved or planned post can still be withdrawn before publishing.
    S.APPROVED: frozenset({S.SCHEDULED, S.REJECTED}),
    # scheduled -> scheduled: moved to another date; -> approved: taken off the calendar.
    S.SCHEDULED: frozenset({S.SCHEDULED, S.APPROVED, S.PUBLISHED, S.REJECTED}),
    S.PUBLISHED: frozenset(),
    # -> in_review: a person reopens a rejected draft (checks run again).
    S.REJECTED: frozenset({S.IN_REVIEW}),
}
# Target status -> review decision that must be the latest one of the draft.
_REQUIRED_DECISION = {S.APPROVED: ReviewDecision.APPROVED, S.REJECTED: ReviewDecision.REJECTED}


class MissingReviewError(PermissionError):
    """The transition needs a human review that does not exist."""


def latest_review(session: Session, draft_id: int) -> PostReview | None:
    statement = (
        select(PostReview)
        .where(PostReview.draft_id == draft_id)
        .order_by(PostReview.reviewed_at.desc(), PostReview.id.desc())  # type: ignore[union-attr]
    )
    return session.exec(statement).first()


def transition(session: Session, draft: PostDraft, target: PostStatus) -> PostDraft:
    """Move the draft to `target`, checking the status machine and the human review."""
    check_transition(ALLOWED_TRANSITIONS, draft.status, target)
    required = _REQUIRED_DECISION.get(target)
    if required is not None and draft.status != target:
        review = latest_review(session, draft.id) if draft.id is not None else None
        if review is None or review.decision != required:
            raise MissingReviewError(f"transition to {target} requires a review ({required})")
    draft.status = target
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft
