"""Status transitions of inbox items, enforcing human-in-the-loop approval."""

from sqlmodel import Session, select

from app.models import InboxItem, ItemStatus, Review, ReviewDecision

ALLOWED_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.NEW: frozenset({ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW}),
    ItemStatus.PROCESSED: frozenset(
        {ItemStatus.NEEDS_MANUAL_REVIEW, ItemStatus.APPROVED, ItemStatus.REJECTED}
    ),
    ItemStatus.NEEDS_MANUAL_REVIEW: frozenset({ItemStatus.APPROVED, ItemStatus.REJECTED}),
    ItemStatus.APPROVED: frozenset({ItemStatus.DONE}),
    ItemStatus.REJECTED: frozenset(),
    ItemStatus.DONE: frozenset(),
}

# Target status -> review decision that must exist before the transition is allowed.
_REQUIRED_DECISION: dict[ItemStatus, ReviewDecision] = {
    ItemStatus.APPROVED: ReviewDecision.APPROVED,
    ItemStatus.REJECTED: ReviewDecision.REJECTED,
    ItemStatus.DONE: ReviewDecision.APPROVED,
}


class InvalidStatusTransitionError(ValueError):
    """Raised for a transition that is not allowed by the status machine."""


class MissingReviewError(PermissionError):
    """Raised when a transition requires a human review that does not exist."""


def latest_review(session: Session, item_id: int) -> Review | None:
    """Return the most recent review of an item, if any."""
    statement = (
        select(Review)
        .where(Review.item_id == item_id)
        .order_by(Review.reviewed_at.desc(), Review.id.desc())  # type: ignore[union-attr]
    )
    return session.exec(statement).first()


def transition_status(session: Session, item: InboxItem, target: ItemStatus) -> InboxItem:
    """Move `item` to `target`, checking allowed transitions and required human review."""
    if target not in ALLOWED_TRANSITIONS[item.status]:
        raise InvalidStatusTransitionError(f"transition {item.status} -> {target} is not allowed")
    required = _REQUIRED_DECISION.get(target)
    if required is not None:
        if item.id is None:
            raise MissingReviewError("item has not been persisted and cannot have a review")
        review = latest_review(session, item.id)
        if review is None or review.decision != required:
            raise MissingReviewError(
                f"transition to {target} requires a human review with decision {required}"
            )
    item.status = target
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
