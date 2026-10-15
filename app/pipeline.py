"""Processing pipeline: classification and extraction (SPEC section 3, steps 3–4)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlmodel import Session, select

from app.audit import write_audit_entry
from app.llm.client import LLMCall, LLMClient
from app.llm.schemas import ClassificationOutput
from app.models import Classification, Extraction, InboxItem, ItemStatus, ValidationStatus
from app.profiles import Profile
from app.status import transition_status

logger = logging.getLogger(__name__)

# One retry after an invalid or failed LLM answer, then manual review (CLAUDE.md).
MAX_ATTEMPTS = 2


@dataclass
class ProcessResult:
    item_id: int
    status: ItemStatus
    document_type: str | None = None
    reason: str | None = None


def _audit(session: Session, item_id: int, call: LLMCall, attempt: int, provider: str) -> None:
    completion = call.completion
    params = dict(completion.params) if completion else {"provider": provider}
    params["attempt"] = attempt
    write_audit_entry(
        session,
        item_id=item_id,
        step=call.task,
        model=completion.model if completion else provider,
        model_params=params,
        prompt_version=call.prompt_id,
        input_text=call.prompt_text,
        output=completion.text if completion else None,
        duration_ms=call.duration_ms,
        error=call.error,
    )


def _with_retry[T](
    session: Session, item_id: int, provider: str, attempt_call: Callable[[], LLMCall[T]]
) -> LLMCall[T]:
    call = attempt_call()
    _audit(session, item_id, call, 1, provider)
    for attempt in range(2, MAX_ATTEMPTS + 1):
        if call.ok:
            break
        call = attempt_call()
        _audit(session, item_id, call, attempt, provider)
    return call


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
        return _manual_review(session, item, item_id, "kein Text extrahierbar", None)
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
    fields: BaseModel | None = extraction.output if extraction.ok else None
    session.add(
        Extraction(
            item_id=item_id,
            fields=fields.model_dump(mode="json") if fields else {},
            validation_status=ValidationStatus.VALID if fields else ValidationStatus.INVALID,
        )
    )
    session.commit()
    if fields is None:
        return _manual_review(session, item, item_id, "Extraktion ungültig", result.document_type)

    transition_status(session, item, ItemStatus.PROCESSED)
    return ProcessResult(item_id, item.status, result.document_type)


def process_new_items(session: Session, profile: Profile, client: LLMClient) -> list[ProcessResult]:
    """Process every inbox item that is still in status `new`."""
    statement = select(InboxItem).where(InboxItem.status == ItemStatus.NEW).order_by(InboxItem.id)  # type: ignore[arg-type]
    results = [
        process_item(session, item, profile, client) for item in session.exec(statement).all()
    ]
    logger.info("processed %d inbox items", len(results))
    return results
