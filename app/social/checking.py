"""Running all checks of a draft and storing the results (deterministic + LLM assessment).

The status follows the result: a draft with text and a valid assessment is `in_review`;
without text or after an invalid assessment it needs manual review (CLAUDE.md).
"""

from collections.abc import Sequence

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.llm.calls import LLMCall, audit_llm_call, call_with_retry
from app.core.members import Member
from app.social.checks import Finding, check_input, deterministic_checks
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import AssessmentOutput
from app.social.models import Briefing, CheckResult, PostDraft, PostStatus
from app.social.profiles import SocialConfig
from app.social.sensitive import (
    assessment_failed,
    check_address_form,
    check_health_claims,
    check_persons,
    merge_assessment,
    ok_findings,
)
from app.social.status import transition

ASSESS_STEP = "social.post_assess"
# Statuses in which the checks decide between `in_review` and manual review.
OPEN_STATUSES = frozenset({PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW})


def member_names(session: Session) -> list[str]:
    return list(session.exec(select(Member.name)).all())


def store_findings(session: Session, draft: PostDraft, findings: Sequence[Finding]) -> None:
    """Replace the stored check results of a draft (results always belong to its current text)."""
    if draft.id is None:
        raise ValueError("draft must be persisted before it is checked")
    for old in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all():
        session.delete(old)
    session.flush()
    text = draft.full_text
    for finding in findings:
        start, end = finding.span if finding.span else (None, None)
        session.add(
            CheckResult(
                draft_id=draft.id,
                check_key=finding.check_key,
                outcome=finding.outcome,
                reason=finding.reason,
                span_start=start,
                span_end=end,
                excerpt=text[start:end] if start is not None else None,
                is_ai_assessment=finding.is_ai_assessment,
                requires_confirmation=finding.requires_confirmation,
            )
        )
    session.commit()


def _assess(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    draft: PostDraft,
) -> LLMCall[AssessmentOutput]:
    context: dict[str, JsonValue] = {
        "briefing_id": briefing.id,
        "draft_id": draft.id,
        "channel": draft.channel.value,
    }

    def record(call: LLMCall[AssessmentOutput], attempt: int) -> None:
        audit_llm_call(
            session,
            call,
            attempt=attempt,
            provider=client.provider.name,
            step=ASSESS_STEP,
            context=context,
        )

    return call_with_retry(lambda: client.assess_post(config, draft.full_text), record)


def run_checks(
    session: Session, config: SocialConfig, client: SocialLLMClient, draft: PostDraft
) -> list[Finding]:
    """Run all checks on the current text of the draft, store the results, set the status."""
    briefing = session.get(Briefing, draft.briefing_id)
    if briefing is None:
        raise ValueError(f"briefing {draft.briefing_id} not found")
    text = draft.full_text
    area = config.brand.area(briefing.area)
    sensitive = area.label if config.is_sensitive(briefing.area) else None
    findings = deterministic_checks(text, check_input(config, briefing, draft))
    code = [
        *check_health_claims(text, config.industry.health_claims, sensitive),
        *check_persons(text, member_names(session)),
        *check_address_form(text, area.address_form),
    ]
    ai: list[Finding] = []
    assessment_ok = True
    if text.strip():
        call = _assess(session, config, client, briefing, draft)
        if call.ok and call.output is not None:
            ai = merge_assessment(text, call.output, code, area.address_form)
        else:
            ai, assessment_ok = [assessment_failed()], False
    sensitive_findings = [*code, *ai]
    health_configured = config.industry.health_claims is not None
    findings += [*sensitive_findings, *ok_findings(sensitive_findings, health_configured)]
    store_findings(session, draft, findings)
    if draft.status in OPEN_STATUSES:
        ready = bool(text.strip()) and assessment_ok
        transition(
            session, draft, PostStatus.IN_REVIEW if ready else PostStatus.NEEDS_MANUAL_REVIEW
        )
    return findings
