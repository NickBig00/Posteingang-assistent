"""Processing pipeline: classification and extraction (SPEC section 3, steps 3–4)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.llm.base import VisualInput
from app.core.llm.calls import LLMCall, audit_llm_call, call_with_retry
from app.inbox.llm.client import LLMClient
from app.inbox.llm.schemas import ClassificationOutput
from app.inbox.models import (
    AdditionalRequest,
    Classification,
    Extraction,
    InboxItem,
    ItemStatus,
    RuleOutcome,
    RuleResult,
    Suggestion,
    ValidationStatus,
)
from app.inbox.profiles import (
    ADDITIONAL_REQUESTS_KEY,
    FORBIDDEN_HEALTH_TERMS,
    DocumentType,
    Profile,
)
from app.inbox.rules import RuleCheck, RuleContext, run_rules
from app.inbox.rules.engine import FieldValue, find_member, typed_fields
from app.inbox.status import transition_status
from app.inbox.suggestions import (
    TemplateRenderError,
    draft_reply,
    label_draft,
    render_template,
    select_action,
    template_values,
)

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    item_id: int
    status: ItemStatus
    document_type: str | None = None
    reason: str | None = None


def _with_retry[T](
    session: Session, item_id: int, provider: str, attempt_call: Callable[[], LLMCall[T]]
) -> LLMCall[T]:
    def record(call: LLMCall[T], attempt: int) -> None:
        audit_llm_call(session, call, attempt=attempt, provider=provider, item_id=item_id)

    return call_with_retry(attempt_call, record)


_VISUAL_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}


def visual_input(item: InboxItem) -> VisualInput | None:
    """The stored raw file as OCR input, if it is an image or PDF that still exists."""
    path = Path(item.raw_content_ref)
    media_type = _VISUAL_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None or not path.is_file():
        return None
    return VisualInput(media_type=media_type, data=path.read_bytes())


def _manual_review(
    session: Session, item: InboxItem, item_id: int, reason: str, doc_type: str | None
) -> ProcessResult:
    transition_status(session, item, ItemStatus.NEEDS_MANUAL_REVIEW)
    return ProcessResult(item_id, item.status, doc_type, reason)


def process_item(
    session: Session, item: InboxItem, profile: Profile, client: LLMClient
) -> ProcessResult:
    """Classify an inbox item and extract the fields of its type.

    Only items in status `new` are processed. The result is `processed` or
    `needs_manual_review`; nothing is executed or sent here.
    """
    if item.id is None or item.status != ItemStatus.NEW:
        raise ValueError("only persisted items with status 'new' can be processed")
    item_id = item.id
    provider = client.provider.name
    if not item.text:
        visual = visual_input(item)
        if visual is None:
            return _manual_review(session, item, item_id, "kein Text extrahierbar", None)
        ocr = _with_retry(session, item_id, provider, lambda: client.ocr(visual))
        if not ocr.ok:
            return _manual_review(session, item, item_id, "Texterkennung fehlgeschlagen", None)
        if not ocr.output:
            return _manual_review(session, item, item_id, "kein Text erkannt", None)
        item.text = ocr.output
        session.add(item)
        session.commit()
    text = item.text

    classification = _with_retry(session, item_id, provider, lambda: client.classify(text, profile))
    if not classification.ok or classification.output is None:
        return _manual_review(session, item, item_id, "Klassifizierung fehlgeschlagen", None)
    result: ClassificationOutput = classification.output
    session.add(
        Classification(
            item_id=item_id, document_type=result.document_type, confidence=result.confidence
        )
    )
    session.commit()

    if result.confidence < profile.confidence_threshold:
        # Data minimisation: no extraction for an uncertain type; a human decides first.
        return _manual_review(
            session, item, item_id, "Konfidenz unter Schwellwert", result.document_type
        )

    doc_type = profile.document_type(result.document_type)
    extraction = _with_retry(session, item_id, provider, lambda: client.extract(text, doc_type))
    output: BaseModel | None = extraction.output if extraction.ok else None
    fields = output.model_dump(mode="json") if output else {}
    topics = fields.pop(ADDITIONAL_REQUESTS_KEY, [])
    session.add(
        Extraction(
            item_id=item_id,
            fields=fields,
            validation_status=ValidationStatus.VALID if output else ValidationStatus.INVALID,
        )
    )
    session.add_all(
        AdditionalRequest(item_id=item_id, topic=topic)
        for topic in additional_request_topics(topics)
    )
    session.commit()
    if output is None:
        return _manual_review(session, item, item_id, "Extraktion ungültig", result.document_type)

    typed = typed_fields(doc_type, fields)
    checks = check_rules(session, item, doc_type, typed)
    suggestion_error = suggest(session, item_id, text, profile, doc_type, typed, checks, client)
    if any(check.outcome == RuleOutcome.ERROR for check in checks):
        return _manual_review(session, item, item_id, "Regelprüfung mit Fehler", doc_type.key)
    if suggestion_error:
        return _manual_review(session, item, item_id, suggestion_error, doc_type.key)

    transition_status(session, item, ItemStatus.PROCESSED)
    return ProcessResult(item_id, item.status, result.document_type)


# Shown instead of a topic that touches health data: the reviewer reads the original.
HEALTH_TOPIC_PLACEHOLDER = "weiteres Anliegen – bitte im Original nachlesen"


def additional_request_topics(topics: object) -> list[str]:
    """Topics to store as reviewer hints; health-related wording is never stored."""
    if not isinstance(topics, list):
        return []
    result: list[str] = []
    for topic in topics:
        text = str(topic).strip()
        if any(term in text.lower() for term in FORBIDDEN_HEALTH_TERMS):
            text = HEALTH_TOPIC_PLACEHOLDER
        if text and text not in result:
            result.append(text)
    return result


def check_rules(
    session: Session, item: InboxItem, doc_type: DocumentType, fields: dict[str, FieldValue]
) -> list[RuleCheck]:
    """Run the deterministic rules of the document type and store their results."""
    if item.id is None:
        raise ValueError("item must be persisted")
    context = RuleContext(
        doc_type=doc_type,
        fields=fields,
        text=item.text or "",
        received_on=item.received_at.date(),
        member=find_member(session, fields.get("mitgliedsnummer")),
    )
    checks = run_rules(context)
    for check in checks:
        session.add(
            RuleResult(
                item_id=item.id,
                rule_name=check.rule,
                outcome=check.outcome,
                reason=check.reason,
                data=check.data,
            )
        )
    session.commit()
    return checks


def suggest(
    session: Session,
    item_id: int,
    text: str,
    profile: Profile,
    doc_type: DocumentType,
    fields: dict[str, FieldValue],
    checks: list[RuleCheck],
    client: LLMClient,
) -> str | None:
    """Store the suggested action and, if it has a template, an AI-labelled reply draft.

    Returns an error reason if no valid suggestion could be created.
    """
    values = template_values(fields, checks)
    action = select_action(doc_type, values, checks)
    if action is None:
        return "kein Aktionsvorschlag im Profil"
    draft: str | None = None
    if action.reply_template:
        try:
            template_text = render_template(action.reply_template, values)
        except TemplateRenderError:
            return "Antwortvorlage nicht anwendbar"
        provider = client.provider.name
        call = _with_retry(
            session,
            item_id,
            provider,
            lambda: draft_reply(client, profile, template_text, text),
        )
        if call.output is None:
            return "Antwortentwurf ungültig"
        draft = label_draft(call.output)
    session.add(
        Suggestion(item_id=item_id, action=action.key, draft_text=draft, is_ai_suggestion=True)
    )
    session.commit()
    return None


def process_new_items(session: Session, profile: Profile, client: LLMClient) -> list[ProcessResult]:
    """Process every inbox item that is still in status `new`."""
    statement = select(InboxItem).where(InboxItem.status == ItemStatus.NEW).order_by(InboxItem.id)  # type: ignore[arg-type]
    results = [
        process_item(session, item, profile, client) for item in session.exec(statement).all()
    ]
    logger.info("processed %d inbox items", len(results))
    return results
