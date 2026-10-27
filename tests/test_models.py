import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.approval import ReviewDecision
from app.inbox.models import (
    Classification,
    Extraction,
    InboxItem,
    ItemSource,
    ItemStatus,
    Review,
    RuleOutcome,
    RuleResult,
    Suggestion,
    ValidationStatus,
)


def _item(session: Session) -> InboxItem:
    item = InboxItem(source=ItemSource.SAMPLE, raw_content_ref="samples/fitness/beispiel.eml")
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_inbox_item_defaults(session: Session) -> None:
    item = _item(session)

    assert item.id is not None
    assert item.status == ItemStatus.NEW
    assert item.received_at is not None


def test_related_records_can_be_created(session: Session) -> None:
    item = _item(session)
    assert item.id is not None
    session.add_all(
        [
            Classification(item_id=item.id, document_type="kuendigung", confidence=0.93),
            Extraction(
                item_id=item.id,
                fields={"mitgliedsnummer": "M-0001", "name": "Erika Beispiel"},
                validation_status=ValidationStatus.VALID,
            ),
            RuleResult(
                item_id=item.id,
                rule_name="kuendigungsfrist",
                outcome=RuleOutcome.OK,
                reason="Frist eingehalten",
            ),
            Suggestion(item_id=item.id, action="kuendigung_bestaetigen", draft_text="Entwurf"),
            Review(item_id=item.id, decision=ReviewDecision.APPROVED, reviewer="demo"),
        ]
    )
    session.commit()

    extraction = session.exec(select(Extraction)).one()
    assert extraction.fields == {"mitgliedsnummer": "M-0001", "name": "Erika Beispiel"}
    assert session.exec(select(Suggestion)).one().is_ai_suggestion is True
    assert session.exec(select(Classification)).one().confidence == pytest.approx(0.93)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_must_be_between_zero_and_one(session: Session, confidence: float) -> None:
    item = _item(session)
    assert item.id is not None
    session.add(Classification(item_id=item.id, document_type="sonstiges", confidence=confidence))

    with pytest.raises(IntegrityError):
        session.commit()


def test_foreign_key_to_inbox_item_is_enforced(session: Session) -> None:
    session.add(RuleResult(item_id=999, rule_name="x", outcome=RuleOutcome.OK, reason="-"))

    with pytest.raises(IntegrityError):
        session.commit()
