"""Human review of drafts: confirming warnings and approving (SPEC_social sections 5 and 6).

Nothing is approved while a check reports an error or a warning that requires confirmation
(health claims, persons) is not confirmed. Every human step is written to the audit trail.
"""

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.approval import ReviewDecision
from app.core.audit import write_human_audit_entry
from app.core.models import utcnow
from app.core.outcomes import Outcome
from app.social.models import CheckResult, PostDraft, PostReview, PostStatus

CONFIRM_STEP = "social.confirm_warning"
REVIEW_STEP = "social.review"


class ReviewNotAllowedError(ValueError):
    """The draft is not in a state that allows this action (message in German for the UI)."""


def check_results(session: Session, draft_id: int) -> list[CheckResult]:
    statement = (
        select(CheckResult).where(CheckResult.draft_id == draft_id).order_by(CheckResult.id)  # type: ignore[arg-type]
    )
    return list(session.exec(statement).all())


def approval_blockers(session: Session, draft: PostDraft) -> list[str]:
    """Reasons why the draft cannot be approved yet (empty list: approval possible)."""
    if draft.id is None:
        return ["Entwurf ist nicht gespeichert."]
    blockers: list[str] = []
    if draft.status is not PostStatus.IN_REVIEW:
        blockers.append("Nur Entwürfe im Status „In Prüfung“ können freigegeben werden.")
    results = check_results(session, draft.id)
    if not results:
        blockers.append("Der Entwurf wurde noch nicht geprüft.")
    for result in results:
        if result.outcome is Outcome.ERROR:
            blockers.append(f"Fehler: {result.reason}")
        elif result.requires_confirmation and result.confirmed_at is None:
            blockers.append(f"Warnung noch nicht bestätigt: {result.reason}")
    return blockers


def _audit(session: Session, draft: PostDraft, step: str, user: str, **details: JsonValue) -> None:
    write_human_audit_entry(
        session,
        item_id=None,
        step=step,
        actor=user,
        details={"briefing_id": draft.briefing_id, "draft_id": draft.id, **details},
    )


def confirm_warning(session: Session, result: CheckResult, user: str) -> CheckResult:
    """A person confirms a warning (e.g. health claim) after reading the marked passage."""
    draft = session.get(PostDraft, result.draft_id)
    if draft is None or draft.status not in (PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW):
        raise ReviewNotAllowedError("Warnungen können nur vor der Freigabe bestätigt werden.")
    if not result.requires_confirmation or result.outcome is not Outcome.WARNING:
        raise ReviewNotAllowedError("Dieses Prüfergebnis braucht keine Bestätigung.")
    if result.confirmed_at is not None:
        raise ReviewNotAllowedError("Die Warnung ist bereits bestätigt.")
    result.confirmed_by, result.confirmed_at = user, utcnow()
    session.add(result)
    session.commit()
    session.refresh(result)
    # The audit keeps which warning was confirmed, not the passage itself (data minimisation).
    _audit(
        session,
        draft,
        CONFIRM_STEP,
        user,
        check_result_id=result.id,
        check_key=result.check_key,
        is_ai_assessment=result.is_ai_assessment,
    )
    return result


def approve_draft(session: Session, draft: PostDraft, user: str) -> PostReview:
    blockers = approval_blockers(session, draft)
    if blockers:
        raise ReviewNotAllowedError("Freigabe nicht möglich: " + " ".join(blockers))
    if draft.id is None:
        raise ReviewNotAllowedError("Entwurf ist nicht gespeichert.")
    review = PostReview(draft_id=draft.id, decision=ReviewDecision.APPROVED, reviewer=user)
    draft.status = PostStatus.APPROVED
    session.add(review)
    session.add(draft)
    session.commit()
    session.refresh(review)
    session.refresh(draft)
    _audit(session, draft, REVIEW_STEP, user, decision=ReviewDecision.APPROVED.value)
    return review
