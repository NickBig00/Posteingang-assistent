"""Human review of drafts: confirming warnings and approving (SPEC_social sections 5 and 6).

Nothing is approved while a check reports an error or a warning that requires confirmation
(health claims, persons) is not confirmed. Every human step is written to the audit trail.
"""

import re

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.approval import ReviewDecision
from app.core.audit import write_human_audit_entry
from app.core.models import utcnow
from app.core.outcomes import Outcome
from app.social.assets import consent_blockers
from app.social.checking import run_checks
from app.social.llm.client import SocialLLMClient
from app.social.models import POST_STATUS_LABELS, CheckResult, PostDraft, PostReview, PostStatus
from app.social.profiles import HASHTAG_PATTERN, SocialConfig
from app.social.status import ALLOWED_TRANSITIONS, transition

CONFIRM_STEP = "social.confirm_warning"
REVIEW_STEP = "social.review"
EDIT_STEP = "social.edit"
RECHECK_STEP = "social.recheck"
REOPEN_STEP = "social.reopen"


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
    blockers += consent_blockers(session, draft.briefing_id)
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
    return _review(session, draft, ReviewDecision.APPROVED, PostStatus.APPROVED, user)


def _review(
    session: Session,
    draft: PostDraft,
    decision: ReviewDecision,
    target: PostStatus,
    user: str,
) -> PostReview:
    if draft.id is None:
        raise ReviewNotAllowedError("Entwurf ist nicht gespeichert.")
    if target not in ALLOWED_TRANSITIONS[draft.status]:
        raise ReviewNotAllowedError(
            f"Im Status „{POST_STATUS_LABELS[draft.status]}“ nicht möglich."
        )
    review = PostReview(draft_id=draft.id, decision=decision, reviewer=user)
    session.add(review)
    session.commit()
    session.refresh(review)
    transition(session, draft, target)
    _audit(session, draft, REVIEW_STEP, user, decision=decision.value)
    return review


def reject_draft(session: Session, draft: PostDraft, user: str) -> PostReview:
    """Reject (or withdraw before publishing) a draft."""
    return _review(session, draft, ReviewDecision.REJECTED, PostStatus.REJECTED, user)


EDITABLE = frozenset({PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW})
MAX_TEXT_LENGTH = 10000


def parse_hashtags(text: str) -> list[str]:
    """Hashtags typed in the edit form, separated by spaces or commas."""
    tags = [tag for tag in re.split(r"[\s,]+", text.strip()) if tag]
    invalid = [tag for tag in tags if not re.fullmatch(HASHTAG_PATTERN, tag)]
    if invalid:
        raise ReviewNotAllowedError(f"Ungültige Hashtags (erwartet „#Wort“): {', '.join(invalid)}")
    return list(dict.fromkeys(tags))


def edit_draft(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
    text: str,
    hashtags: str,
    user: str,
) -> PostDraft:
    """A person changes the text; all checks run again (confirmations start over)."""
    if draft.status not in EDITABLE:
        raise ReviewNotAllowedError("Freigegebene oder verworfene Entwürfe sind nicht bearbeitbar.")
    if len(text) > MAX_TEXT_LENGTH:
        raise ReviewNotAllowedError(f"Text ist zu lang (höchstens {MAX_TEXT_LENGTH} Zeichen).")
    draft.text = text.strip().replace("\r\n", "\n")
    draft.hashtags = parse_hashtags(hashtags)
    draft.edited_by_human = True
    session.add(draft)
    session.commit()
    session.refresh(draft)
    _audit(session, draft, EDIT_STEP, user)
    run_checks(session, config, client, draft)
    return draft


def recheck_draft(
    session: Session, config: SocialConfig, client: SocialLLMClient, draft: PostDraft, user: str
) -> PostDraft:
    """Run the checks again, e.g. after the AI assessment was not available."""
    if draft.status not in EDITABLE:
        raise ReviewNotAllowedError("Nur offene Entwürfe können erneut geprüft werden.")
    _audit(session, draft, RECHECK_STEP, user)
    run_checks(session, config, client, draft)
    return draft


def reopen_draft(
    session: Session, config: SocialConfig, client: SocialLLMClient, draft: PostDraft, user: str
) -> PostDraft:
    """A rejected draft goes back into review; the checks run again."""
    if draft.status is not PostStatus.REJECTED:
        raise ReviewNotAllowedError("Nur verworfene Entwürfe können wieder geöffnet werden.")
    transition(session, draft, PostStatus.IN_REVIEW)
    _audit(session, draft, REOPEN_STEP, user)
    run_checks(session, config, client, draft)
    return draft
