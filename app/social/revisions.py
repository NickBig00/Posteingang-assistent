"""Revising AI results by instruction: "Was soll anders werden?" (SPEC_social 13.8).

A person writes in their own words what to change; the AI produces a new version, which is
again an AI suggestion and runs through all checks. One retry like every LLM call; invalid
output twice keeps the previous version. Every request is stored (`RevisionRequest`) and
audited: who, when, hash of the instruction; the instruction itself goes into the deletable
audit output. Image revisions are subject to the cost brake of the image generation.
"""

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue
from sqlmodel import Session, col, delete, func, select

from app.core.audit import HUMAN_ACTOR_MODEL, write_audit_entry
from app.core.image_formats import ImageFormat
from app.core.images.base import ImageProvider
from app.core.llm.calls import LLMCall, audit_llm_call, call_with_retry
from app.social.checking import run_checks
from app.social.facts import BriefingFacts
from app.social.images import EDITABLE_STATUSES, generate_variants, latest_proposal
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import DraftOutput, ImagePromptOutput
from app.social.models import (
    Briefing,
    ImageProposal,
    PostDraft,
    RevisionRequest,
    RevisionTarget,
)
from app.social.profiles import SocialConfig

REQUEST_STEP = "social.revision_request"
POST_REVISE_STEP = "social.post_revise"
PROMPT_REVISE_STEP = "social.image_prompt_revise"
MAX_INSTRUCTION_LENGTH = 500

# Quick picks shown below the field (the person can always write their own words).
POST_QUICK_PICKS = [
    "Kürzer fassen",
    "Neutraler formulieren, ohne Gesundheitsversprechen",
    "Freundlicher und persönlicher",
    "Weniger Emojis",
]
IMAGE_QUICK_PICKS = [
    "Heller und freundlicher",
    "Mehr Bewegung zeigen",
    "Ruhiger, weniger Details",
    "Wärmeres Licht",
]


class RevisionError(ValueError):
    """The revision is not possible or the AI delivered no valid version (German message)."""


def clean_instruction(instruction: str) -> str:
    text = " ".join(instruction.split())
    if not text:
        raise RevisionError("Bitte beschreiben, was anders werden soll.")
    if len(text) > MAX_INSTRUCTION_LENGTH:
        raise RevisionError(
            f"Die Anweisung ist zu lang (höchstens {MAX_INSTRUCTION_LENGTH} Zeichen)."
        )
    return text


def _next_version(session: Session, briefing_id: int, draft_id: int | None, target: str) -> int:
    statement = (
        select(func.count())
        .select_from(RevisionRequest)
        .where(RevisionRequest.briefing_id == briefing_id)
        .where(RevisionRequest.target == target)
    )
    if draft_id is not None:
        statement = statement.where(RevisionRequest.draft_id == draft_id)
    return int(session.exec(statement).one()) + 1


def record_request(
    session: Session,
    *,
    briefing_id: int,
    draft_id: int | None,
    target: RevisionTarget,
    instruction: str,
    user: str,
    succeeded: bool,
    details: dict[str, JsonValue] | None = None,
) -> RevisionRequest:
    """Store the request and write the human audit entry (instruction as hash + output)."""
    request = RevisionRequest(
        briefing_id=briefing_id,
        draft_id=draft_id,
        target=target,
        instruction=instruction,
        version=_next_version(session, briefing_id, draft_id, target.value),
        succeeded=succeeded,
        created_by=user,
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    write_audit_entry(
        session,
        step=REQUEST_STEP,
        model=HUMAN_ACTOR_MODEL,
        input_text=instruction,
        duration_ms=0,
        model_params={
            "actor": user,
            "briefing_id": briefing_id,
            "draft_id": draft_id,
            "target": target.value,
            "version": request.version,
            "succeeded": succeeded,
            "revision_id": request.id,
            **(details or {}),
        },
        # Plain text only in the deletable audit output (removed with the briefing).
        output=json.dumps({"instruction": instruction}, ensure_ascii=False),
    )
    return request


def revisions(session: Session, briefing_id: int) -> list[RevisionRequest]:
    statement = (
        select(RevisionRequest)
        .where(RevisionRequest.briefing_id == briefing_id)
        .order_by(col(RevisionRequest.id))
    )
    return list(session.exec(statement).all())


def _briefing(session: Session, briefing_id: int) -> Briefing:
    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        raise RevisionError("Briefing nicht gefunden.")
    return briefing


def _audited[T](
    session: Session,
    client: SocialLLMClient,
    step: str,
    context: dict[str, JsonValue],
    attempt: Callable[[], LLMCall[T]],
) -> LLMCall[T]:
    def record(call: LLMCall[T], number: int) -> None:
        audit_llm_call(
            session, call, attempt=number, provider=client.provider.name, step=step, context=context
        )

    return call_with_retry(attempt, record)


def revise_post(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
    instruction: str,
    user: str,
) -> PostDraft:
    """New AI version of the post text; all checks run again (confirmations start over)."""
    if draft.status not in EDITABLE_STATUSES:
        raise RevisionError("Nur offene Entwürfe lassen sich überarbeiten.")
    instruction = clean_instruction(instruction)
    briefing = _briefing(session, draft.briefing_id)
    context: dict[str, JsonValue] = {
        "briefing_id": draft.briefing_id,
        "draft_id": draft.id,
        "channel": draft.channel.value,
    }
    call = _audited(
        session,
        client,
        POST_REVISE_STEP,
        context,
        lambda: client.revise_post(
            config,
            area_key=briefing.area,
            channel=draft.channel,
            facts=BriefingFacts.from_json(briefing.facts),
            text=draft.text,
            hashtags=draft.hashtags,
            instruction=instruction,
        ),
    )
    output = call.output if call.ok else None
    record_request(
        session,
        briefing_id=draft.briefing_id,
        draft_id=draft.id,
        target=RevisionTarget.POST_TEXT,
        instruction=instruction,
        user=user,
        succeeded=isinstance(output, DraftOutput),
    )
    if not isinstance(output, DraftOutput):
        raise RevisionError(
            "Die KI hat keine gültige Überarbeitung geliefert (auch nicht beim zweiten "
            "Versuch). Der bisherige Text bleibt; bitte selbst bearbeiten oder anders formulieren."
        )
    draft.text = output.text.strip()
    draft.hashtags = list(dict.fromkeys(output.hashtags))
    # The new version is an AI suggestion again (labelled until a person approves it).
    draft.is_ai_suggestion = True
    session.add(draft)
    session.commit()
    session.refresh(draft)
    run_checks(session, config, client, draft)
    return draft


def revise_image_prompt(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
    instruction: str,
    user: str,
    *,
    target: RevisionTarget = RevisionTarget.IMAGE_PROMPT,
) -> ImageProposal:
    """New version of the image prompt (a new proposal row; the previous ones stay)."""
    if draft.status not in EDITABLE_STATUSES:
        raise RevisionError("Das Bild lässt sich nur vor der Freigabe ändern.")
    instruction = clean_instruction(instruction)
    briefing = _briefing(session, draft.briefing_id)
    current = latest_proposal(session, draft.id or 0)
    if current is None or not current.prompt.strip():
        raise RevisionError("Es gibt noch keinen Bild-Prompt zum Überarbeiten.")
    context: dict[str, JsonValue] = {
        "briefing_id": draft.briefing_id,
        "draft_id": draft.id,
        "channel": draft.channel.value,
    }
    call = _audited(
        session,
        client,
        PROMPT_REVISE_STEP,
        context,
        lambda: client.revise_image_prompt(
            config, area_key=briefing.area, prompt=current.prompt, instruction=instruction
        ),
    )
    output = call.output if call.ok else None
    record_request(
        session,
        briefing_id=draft.briefing_id,
        draft_id=draft.id,
        target=target,
        instruction=instruction,
        user=user,
        succeeded=isinstance(output, ImagePromptOutput),
    )
    if not isinstance(output, ImagePromptOutput):
        raise RevisionError(
            "Die KI hat keinen gültigen Bild-Prompt geliefert (auch nicht beim zweiten "
            "Versuch). Der bisherige Prompt bleibt; bitte selbst anpassen."
        )
    proposal = ImageProposal(
        draft_id=draft.id or 0,
        briefing_id=draft.briefing_id,
        prompt=output.prompt.strip(),
        prompt_from_ai=True,
        image_format=current.image_format,
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal


def revise_image(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    provider: ImageProvider,
    draft: PostDraft,
    instruction: str,
    user: str,
    storage: Path,
) -> ImageProposal:
    """Revise the prompt by instruction and generate new variants right away.

    The generation goes through the image service: switch, cost brake, prompt check, audit.
    """
    proposal = revise_image_prompt(
        session, config, client, draft, instruction, user, target=RevisionTarget.IMAGE
    )
    try:
        fmt = ImageFormat(proposal.image_format)
    except ValueError:
        fmt = ImageFormat.SQUARE
    generate_variants(
        session,
        config,
        provider,
        draft,
        prompt=proposal.prompt,
        fmt=fmt,
        user=user,
        storage=storage,
    )
    return proposal


def delete_revisions(
    session: Session, *, briefing_id: int | None = None, draft_ids: list[int] | None = None
) -> None:
    """Deletion concept: instructions are content and go with their briefing or draft."""
    if briefing_id is not None:
        session.exec(delete(RevisionRequest).where(col(RevisionRequest.briefing_id) == briefing_id))  # type: ignore[call-overload]
    if draft_ids:
        session.exec(delete(RevisionRequest).where(col(RevisionRequest.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
