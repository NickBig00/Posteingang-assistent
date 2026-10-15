import pytest
from sqlmodel import Session

from app.models import InboxItem, ItemSource, ItemStatus, Review, ReviewDecision
from app.status import (
    ALLOWED_TRANSITIONS,
    InvalidStatusTransitionError,
    MissingReviewError,
    transition_status,
)


def _item(session: Session, status: ItemStatus = ItemStatus.NEW) -> InboxItem:
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="upload/1.eml", status=status)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _review(session: Session, item: InboxItem, decision: ReviewDecision) -> None:
    assert item.id is not None
    session.add(Review(item_id=item.id, decision=decision, reviewer="demo"))
    session.commit()


@pytest.mark.parametrize("target", [ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW])
def test_new_item_can_be_processed_or_flagged(session: Session, target: ItemStatus) -> None:
    item = transition_status(session, _item(session), target)

    assert item.status == target


@pytest.mark.parametrize("target", [ItemStatus.APPROVED, ItemStatus.REJECTED, ItemStatus.DONE])
def test_new_item_cannot_skip_processing(session: Session, target: ItemStatus) -> None:
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(session, _item(session), target)


@pytest.mark.parametrize(
    ("start", "decision", "target"),
    [
        (ItemStatus.PROCESSED, ReviewDecision.APPROVED, ItemStatus.APPROVED),
        (ItemStatus.PROCESSED, ReviewDecision.REJECTED, ItemStatus.REJECTED),
        (ItemStatus.NEEDS_MANUAL_REVIEW, ReviewDecision.APPROVED, ItemStatus.APPROVED),
        (ItemStatus.NEEDS_MANUAL_REVIEW, ReviewDecision.REJECTED, ItemStatus.REJECTED),
    ],
)
def test_review_decision_allows_transition(
    session: Session, start: ItemStatus, decision: ReviewDecision, target: ItemStatus
) -> None:
    item = _item(session, start)
    _review(session, item, decision)

    assert transition_status(session, item, target).status == target


@pytest.mark.parametrize("target", [ItemStatus.APPROVED, ItemStatus.REJECTED])
def test_approval_or_rejection_without_review_is_blocked(
    session: Session, target: ItemStatus
) -> None:
    item = _item(session, ItemStatus.PROCESSED)

    with pytest.raises(MissingReviewError):
        transition_status(session, item, target)
    session.refresh(item)
    assert item.status == ItemStatus.PROCESSED


def test_approval_with_rejecting_review_is_blocked(session: Session) -> None:
    item = _item(session, ItemStatus.PROCESSED)
    _review(session, item, ReviewDecision.REJECTED)

    with pytest.raises(MissingReviewError):
        transition_status(session, item, ItemStatus.APPROVED)


def test_latest_review_wins(session: Session) -> None:
    item = _item(session, ItemStatus.PROCESSED)
    _review(session, item, ReviewDecision.APPROVED)
    _review(session, item, ReviewDecision.REJECTED)

    with pytest.raises(MissingReviewError):
        transition_status(session, item, ItemStatus.APPROVED)
    assert transition_status(session, item, ItemStatus.REJECTED).status == ItemStatus.REJECTED


def test_full_happy_path_to_done(session: Session) -> None:
    item = _item(session)
    transition_status(session, item, ItemStatus.PROCESSED)
    _review(session, item, ReviewDecision.APPROVED)
    transition_status(session, item, ItemStatus.APPROVED)

    assert transition_status(session, item, ItemStatus.DONE).status == ItemStatus.DONE


def test_done_requires_approving_review(session: Session) -> None:
    item = _item(session, ItemStatus.APPROVED)

    with pytest.raises(MissingReviewError):
        transition_status(session, item, ItemStatus.DONE)


@pytest.mark.parametrize("start", [ItemStatus.REJECTED, ItemStatus.DONE])
@pytest.mark.parametrize("target", list(ItemStatus))
def test_terminal_states_allow_no_transition(
    session: Session, start: ItemStatus, target: ItemStatus
) -> None:
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(session, _item(session, start), target)


def test_every_status_has_transition_rules() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(ItemStatus)
