"""Human review and execution (SPEC section 3, steps 7–8).

Nothing is executed without a review with decision `approved` (enforced by the
status machine). In the demo, "execution" means setting the item to `done` and
exporting the approved reply text.
"""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import (
    AuditEntry,
    Classification,
    Extraction,
    InboxItem,
    ItemStatus,
    Review,
    ReviewDecision,
    RuleOutcome,
    RuleResult,
    Suggestion,
)
from app.status import latest_review, transition_status

DEMO_REVIEWER = "demo"
REVIEWABLE = frozenset({ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW})


class ReviewNotAllowedError(ValueError):
    """The item is not in a state that allows this review action."""


@dataclass
class ItemDetails:
    item: InboxItem
    classification: Classification | None
    extraction: Extraction | None
    rule_results: list[RuleResult]
    suggestion: Suggestion | None
    reviews: list[Review]

    @property
    def traffic_light(self) -> str | None:
        return traffic_light(self.rule_results)


def traffic_light(results: list[RuleResult]) -> str | None:
    """Worst rule outcome: `error` > `warning` > `ok`; None if no rule ran."""
    outcomes = {result.outcome for result in results}
    for outcome in (RuleOutcome.ERROR, RuleOutcome.WARNING, RuleOutcome.OK):
        if outcome in outcomes:
            return outcome.value
    return None


def _latest[T](session: Session, model: type[T], item_id: int) -> T | None:
    statement = select(model).where(model.item_id == item_id).order_by(model.id.desc())  # type: ignore[attr-defined]
    return session.exec(statement).first()


def load_details(session: Session, item_id: int) -> ItemDetails | None:
    item = session.get(InboxItem, item_id)
    if item is None:
        return None
    rules = session.exec(
        select(RuleResult).where(RuleResult.item_id == item_id).order_by(RuleResult.id)  # type: ignore[arg-type]
    ).all()
    reviews = session.exec(
        select(Review).where(Review.item_id == item_id).order_by(Review.id)  # type: ignore[arg-type]
    ).all()
    return ItemDetails(
        item=item,
        classification=_latest(session, Classification, item_id),
        extraction=_latest(session, Extraction, item_id),
        rule_results=list(rules),
        suggestion=_latest(session, Suggestion, item_id),
        reviews=list(reviews),
    )


def audit_trail(session: Session, item_id: int) -> list[AuditEntry]:
    statement = (
        select(AuditEntry)
        .where(AuditEntry.item_id == item_id)
        .order_by(AuditEntry.created_at, AuditEntry.id)  # type: ignore[arg-type]
    )
    return list(session.exec(statement).all())


def review_item(
    session: Session,
    item: InboxItem,
    decision: ReviewDecision,
    edited_text: str | None = None,
    reviewer: str = DEMO_REVIEWER,
) -> Review:
    """Record a human decision and move the item to `approved` or `rejected`."""
    if item.id is None or item.status not in REVIEWABLE:
        raise ReviewNotAllowedError(f"Eingang im Status '{item.status}' kann nicht geprüft werden")
    text = edited_text.strip() if edited_text and edited_text.strip() else None
    review = Review(item_id=item.id, decision=decision, edited_text=text, reviewer=reviewer)
    session.add(review)
    session.commit()
    target = ItemStatus.APPROVED if decision == ReviewDecision.APPROVED else ItemStatus.REJECTED
    transition_status(session, item, target)
    return review


def final_text(session: Session, item: InboxItem) -> str | None:
    """Reply text released by the reviewer: the edited text, else the suggested draft."""
    if item.id is None:
        return None
    review = latest_review(session, item.id)
    if review is None or review.decision != ReviewDecision.APPROVED:
        return None
    if review.edited_text:
        return review.edited_text
    suggestion = _latest(session, Suggestion, item.id)
    return suggestion.draft_text if suggestion else None


def execute_item(session: Session, item: InboxItem) -> str | None:
    """Demo execution: mark the approved item as done and return the reply to export.

    Raises if the item is not approved (status machine requires an approving review).
    """
    if item.status != ItemStatus.APPROVED:
        raise ReviewNotAllowedError("Ausführung nur nach Freigabe möglich")
    text = final_text(session, item)
    transition_status(session, item, ItemStatus.DONE)
    return text
