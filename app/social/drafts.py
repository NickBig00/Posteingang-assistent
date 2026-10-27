"""Draft generation per channel via the LLM interface (SPEC_social section 5, step 2).

Every LLM call is audited; invalid output is retried once, then the draft goes to
`needs_manual_review` with an empty text that a person has to write.
"""

import logging

from pydantic import JsonValue
from sqlmodel import Session

from app.core.llm.calls import LLMCall, audit_llm_call, call_with_retry
from app.social.checking import run_checks
from app.social.facts import BriefingFacts
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import DraftOutput
from app.social.models import Briefing, Channel, PostDraft, PostStatus
from app.social.profiles import SocialConfig

logger = logging.getLogger(__name__)

DRAFT_STEP = "social.post_draft"


def generate_draft(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    channel: Channel,
    campaign_step_label: str | None = None,
) -> PostDraft:
    if briefing.id is None:
        raise ValueError("briefing must be persisted before drafts are generated")
    draft = PostDraft(briefing_id=briefing.id, channel=channel)
    session.add(draft)
    session.commit()
    session.refresh(draft)
    facts = BriefingFacts.from_json(briefing.facts)
    context: dict[str, JsonValue] = {
        "briefing_id": briefing.id,
        "draft_id": draft.id,
        "channel": channel.value,
    }

    def record(call: LLMCall[DraftOutput], attempt: int) -> None:
        audit_llm_call(
            session,
            call,
            attempt=attempt,
            provider=client.provider.name,
            step=DRAFT_STEP,
            context=context,
        )

    call = call_with_retry(
        lambda: client.draft_post(
            config,
            area_key=briefing.area,
            occasion_key=briefing.occasion_type,
            channel=channel,
            facts=facts,
            notes=briefing.notes,
            campaign_step=campaign_step_label,
        ),
        record,
    )
    output: DraftOutput | None = call.output if call.ok else None
    if output is None:
        draft.status = PostStatus.NEEDS_MANUAL_REVIEW
    else:
        draft.text, draft.hashtags = output.text, list(output.hashtags)
        draft.status = PostStatus.IN_REVIEW
    session.add(draft)
    session.commit()
    session.refresh(draft)
    run_checks(session, config, client, draft)
    logger.info("draft %s created (channel=%s, status=%s)", draft.id, channel, draft.status)
    return draft


def generate_drafts(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    campaign_step_label: str | None = None,
) -> list[PostDraft]:
    """One draft per channel of the briefing."""
    return [
        generate_draft(session, config, client, briefing, Channel(c), campaign_step_label)
        for c in briefing.channels
    ]
