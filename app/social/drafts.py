"""Draft generation per channel via the LLM interface (SPEC_social section 5, step 2).

Every LLM call is audited. One retry: after invalid output, or – naming them explicitly –
after facts of the briefing were dropped or changed. Invalid output twice: the draft goes to
`needs_manual_review` with an empty text; facts still missing: `needs_manual_review` with the
text, which a person completes.
"""

import logging

from pydantic import JsonValue
from sqlmodel import Session

from app.core.llm.calls import LLMCall, audit_llm_call
from app.social.checking import run_checks
from app.social.checks import missing_facts
from app.social.facts import BriefingFacts, FactKey
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import DraftOutput
from app.social.models import Briefing, Channel, PostDraft, PostStatus
from app.social.profiles import SocialConfig
from app.social.status import transition

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

    def attempt(number: int, missing: list[FactKey] | None = None) -> LLMCall[DraftOutput]:
        call = client.draft_post(
            config,
            area_key=briefing.area,
            occasion_key=briefing.occasion_type,
            channel=channel,
            facts=facts,
            notes=briefing.notes,
            campaign_step=campaign_step_label,
            missing_facts=missing,
        )
        retry: dict[str, JsonValue] = {}
        if number > 1:
            retry = {"retry_reason": "missing_facts" if missing else "invalid_output"}
            if missing:
                # Only the keys: the audit holds no content.
                retry["missing_facts"] = list(missing)
        audit_llm_call(
            session,
            call,
            attempt=number,
            provider=client.provider.name,
            step=DRAFT_STEP,
            context=context | retry,
        )
        return call

    # One retry (CLAUDE.md): for invalid output, or for facts the draft dropped or changed.
    output = _valid(attempt(1))
    still_missing: list[FactKey] = []
    if output is None:
        output = _valid(attempt(2))
    else:
        missing = missing_facts(_full_text(channel, output), facts)
        if missing:
            output = _valid(attempt(2, missing)) or output
            still_missing = missing_facts(_full_text(channel, output), facts)
    if output is None:
        draft.status = PostStatus.NEEDS_MANUAL_REVIEW
    else:
        draft.text, draft.hashtags = output.text, list(output.hashtags)
        draft.status = PostStatus.IN_REVIEW
    session.add(draft)
    session.commit()
    session.refresh(draft)
    run_checks(session, config, client, draft)
    if still_missing and draft.status is PostStatus.IN_REVIEW:
        # Facts still missing after the retry: a person has to complete the text.
        transition(session, draft, PostStatus.NEEDS_MANUAL_REVIEW)
    logger.info("draft %s created (channel=%s, status=%s)", draft.id, channel, draft.status)
    return draft


def _valid(call: LLMCall[DraftOutput]) -> DraftOutput | None:
    return call.output if call.ok else None


def _full_text(channel: Channel, output: DraftOutput) -> str:
    return PostDraft(
        briefing_id=0, channel=channel, text=output.text, hashtags=output.hashtags
    ).full_text


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
