"""Human review and execution (SPEC section 3, steps 7–8).

Nothing is executed without a review with decision `approved` (enforced by the
status machine). In the demo, "execution" means setting the item to `done` and
exporting the approved reply text.
"""

from dataclasses import dataclass, field

from sqlmodel import Session, SQLModel, delete, select

from app.core.approval import ReviewDecision
from app.core.audit import write_human_audit_entry
from app.core.models import AuditEntry
from app.inbox.llm.client import LLMClient
from app.inbox.models import (
    AdditionalRequest,
    Classification,
    Extraction,
    InboxItem,
    ItemStatus,
    Review,
    RuleOutcome,
    RuleResult,
    Suggestion,
)
from app.inbox.pipeline import ProcessResult, process_item
from app.inbox.profiles import Profile
from app.inbox.status import latest_review, transition_status
from app.inbox.suggestions import strip_ai_label

DEMO_REVIEWER = "demo"
REVIEWABLE = frozenset({ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW})
# Results of one pipeline run; replaced on reprocessing (audit entries and reviews stay).
_PROCESSING_RESULTS: tuple[type[SQLModel], ...] = (
    Suggestion,
    RuleResult,
    AdditionalRequest,
    Extraction,
    Classification,
)


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
    # Further requests in the document that the draft does not answer (AI hint).
    additional_requests: list[AdditionalRequest] = field(default_factory=list)
    # True if the item text was produced by AI text recognition (OCR) – a KI result.
    text_from_ocr: bool = False

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
    requests = session.exec(
        select(AdditionalRequest)
        .where(AdditionalRequest.item_id == item_id)
        .order_by(AdditionalRequest.id)  # type: ignore[arg-type]
    ).all()
    return ItemDetails(
        item=item,
        classification=_latest(session, Classification, item_id),
        extraction=_latest(session, Extraction, item_id),
        rule_results=list(rules),
        suggestion=_latest(session, Suggestion, item_id),
        reviews=list(reviews),
        additional_requests=list(requests),
        text_from_ocr=item.text is not None
        and any(e.step == "ocr" and e.error is None for e in audit_trail(session, item_id)),
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
    """Reply text released by the reviewer: the edited text, else the suggested draft.

    The internal "KI-Vorschlag" label is removed: it marks the draft for the reviewer
    and is not part of the reply that is sent.
    """
    if item.id is None:
        return None
    review = latest_review(session, item.id)
    if review is None or review.decision != ReviewDecision.APPROVED:
        return None
    text = review.edited_text
    if not text:
        suggestion = _latest(session, Suggestion, item.id)
        text = suggestion.draft_text if suggestion else None
    return (strip_ai_label(text) or None) if text else None


def execute_item(session: Session, item: InboxItem) -> str | None:
    """Demo execution: mark the approved item as done and return the reply to export.

    Raises if the item is not approved (status machine requires an approving review).
    """
    if item.status != ItemStatus.APPROVED:
        raise ReviewNotAllowedError("Ausführung nur nach Freigabe möglich")
    text = final_text(session, item)
    transition_status(session, item, ItemStatus.DONE)
    return text


def reprocess_item(
    session: Session,
    item: InboxItem,
    profile: Profile,
    client: LLMClient,
    actor: str = DEMO_REVIEWER,
) -> ProcessResult:
    """Run the AI pipeline again for an item in manual review (e.g. after a profile change).

    The previous results are replaced; their audit entries stay. The request itself is
    recorded in the audit trail.
    """
    if item.id is None or item.status != ItemStatus.NEEDS_MANUAL_REVIEW:
        raise ReviewNotAllowedError(
            "Neu verarbeiten ist nur für Eingänge in manueller Prüfung möglich"
        )
    item_id = item.id
    write_human_audit_entry(
        session,
        item_id=item_id,
        step="reprocess",
        actor=actor,
        details={"from_status": item.status.value, "to_status": ItemStatus.NEW.value},
    )
    for model in _PROCESSING_RESULTS:
        session.exec(delete(model).where(model.item_id == item_id))  # type: ignore[attr-defined,call-overload]
    session.commit()
    transition_status(session, item, ItemStatus.NEW)
    return process_item(session, item, profile, client)


def reopen_item(session: Session, item: InboxItem, actor: str = DEMO_REVIEWER) -> InboxItem:
    """Reopen a rejected item for a new human decision. Executed items (`done`) stay closed."""
    if item.id is None or item.status != ItemStatus.REJECTED:
        raise ReviewNotAllowedError("Nur verworfene Eingänge können wieder geöffnet werden")
    write_human_audit_entry(
        session,
        item_id=item.id,
        step="reopen",
        actor=actor,
        details={
            "from_status": item.status.value,
            "to_status": ItemStatus.NEEDS_MANUAL_REVIEW.value,
        },
    )
    return transition_status(session, item, ItemStatus.NEEDS_MANUAL_REVIEW)
