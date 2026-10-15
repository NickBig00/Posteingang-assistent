import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.llm.client import LLMClient
from app.llm.fake import FakeProvider
from app.main import app, get_llm_client
from app.models import (
    InboxItem,
    ItemSource,
    ItemStatus,
    Review,
    ReviewDecision,
    RuleOutcome,
    RuleResult,
    Suggestion,
)
from app.review import (
    ReviewNotAllowedError,
    execute_item,
    final_text,
    review_item,
    traffic_light,
)
from app.suggestions import AI_LABEL


def _item(session: Session, status: ItemStatus, draft: str | None = "Entwurf") -> InboxItem:
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text="Text", status=status)
    session.add(item)
    session.commit()
    session.refresh(item)
    assert item.id is not None
    session.add(Suggestion(item_id=item.id, action="a", draft_text=draft))
    session.commit()
    return item


# --- service ---


@pytest.mark.parametrize("start", [ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW])
def test_approve_and_execute(session: Session, start: ItemStatus) -> None:
    item = _item(session, start)

    review_item(session, item, ReviewDecision.APPROVED)
    text = execute_item(session, item)

    assert item.status == ItemStatus.DONE
    assert text == "Entwurf"


def test_edited_text_wins(session: Session) -> None:
    item = _item(session, ItemStatus.PROCESSED)

    review_item(session, item, ReviewDecision.APPROVED, "  Geänderter Text  ")

    assert final_text(session, item) == "Geänderter Text"


def test_reject(session: Session) -> None:
    item = _item(session, ItemStatus.PROCESSED)

    review_item(session, item, ReviewDecision.REJECTED)

    assert item.status == ItemStatus.REJECTED
    assert final_text(session, item) is None


@pytest.mark.parametrize(
    "status", [ItemStatus.NEW, ItemStatus.APPROVED, ItemStatus.REJECTED, ItemStatus.DONE]
)
def test_review_not_allowed_in_status(session: Session, status: ItemStatus) -> None:
    item = _item(session, status)

    with pytest.raises(ReviewNotAllowedError):
        review_item(session, item, ReviewDecision.APPROVED)


@pytest.mark.parametrize(
    "status",
    [ItemStatus.NEW, ItemStatus.PROCESSED, ItemStatus.NEEDS_MANUAL_REVIEW, ItemStatus.REJECTED],
)
def test_nothing_is_executed_without_approval(session: Session, status: ItemStatus) -> None:
    item = _item(session, status)

    with pytest.raises(ReviewNotAllowedError):
        execute_item(session, item)

    session.refresh(item)
    assert item.status == status
    assert final_text(session, item) is None


def test_traffic_light() -> None:
    def result(outcome: RuleOutcome) -> RuleResult:
        return RuleResult(item_id=1, rule_name="r", outcome=outcome, reason="-")

    assert traffic_light([]) is None
    assert traffic_light([result(RuleOutcome.OK)]) == "ok"
    assert traffic_light([result(RuleOutcome.OK), result(RuleOutcome.WARNING)]) == "warning"
    assert traffic_light([result(RuleOutcome.WARNING), result(RuleOutcome.ERROR)]) == "error"


# --- web ---


def _status(engine: Engine, item_id: int) -> ItemStatus:
    with Session(engine) as session:
        item = session.get(InboxItem, item_id)
        assert item is not None
        return item.status


def _create(engine: Engine, status: ItemStatus, draft: str | None = "Entwurf") -> int:
    with Session(engine) as session:
        item = _item(session, status, draft)
        assert item.id is not None
        return item.id


def test_detail_view_shows_ai_labels_and_buttons(client: TestClient, engine: Engine) -> None:
    item_id = _create(engine, ItemStatus.PROCESSED, f"{AI_LABEL}\n\nGuten Tag")

    response = client.get(f"/items/{item_id}")

    assert response.status_code == 200
    assert "KI-Vorschlag" in response.text
    assert "Hinweis zur KI-Nutzung" in response.text
    assert 'value="approved"' in response.text
    assert 'value="rejected"' in response.text
    assert "Guten Tag" in response.text


def test_unknown_item_is_404(client: TestClient) -> None:
    assert client.get("/items/999").status_code == 404
    assert client.get("/items/999/audit").status_code == 404


def test_execute_without_approval_is_refused(client: TestClient, engine: Engine) -> None:
    item_id = _create(engine, ItemStatus.PROCESSED)

    response = client.post(f"/items/{item_id}/execute")

    assert response.status_code == 409
    assert "nur nach Freigabe" in response.text
    assert _status(engine, item_id) == ItemStatus.PROCESSED
    assert client.get(f"/items/{item_id}/export").status_code == 409


def test_export_after_approval_but_before_execution_is_refused(
    client: TestClient, engine: Engine
) -> None:
    item_id = _create(engine, ItemStatus.PROCESSED)
    client.post(f"/items/{item_id}/review", data={"decision": "approved"})

    assert client.get(f"/items/{item_id}/export").status_code == 409


def test_rejected_item_cannot_be_executed(client: TestClient, engine: Engine) -> None:
    item_id = _create(engine, ItemStatus.PROCESSED)
    client.post(f"/items/{item_id}/review", data={"decision": "rejected"})

    response = client.post(f"/items/{item_id}/execute")

    assert response.status_code == 409
    assert _status(engine, item_id) == ItemStatus.REJECTED


def test_second_review_is_refused(client: TestClient, engine: Engine) -> None:
    item_id = _create(engine, ItemStatus.PROCESSED)
    client.post(f"/items/{item_id}/review", data={"decision": "rejected"})

    response = client.post(f"/items/{item_id}/review", data={"decision": "approved"})

    assert response.status_code == 409
    assert _status(engine, item_id) == ItemStatus.REJECTED


def test_invalid_decision_is_rejected(client: TestClient, engine: Engine) -> None:
    item_id = _create(engine, ItemStatus.PROCESSED)

    response = client.post(f"/items/{item_id}/review", data={"decision": "done"})

    assert response.status_code == 422
    assert _status(engine, item_id) == ItemStatus.PROCESSED


@pytest.fixture
def heuristic_llm() -> Iterator[None]:
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(FakeProvider())
    yield


def test_full_ui_flow_load_review_approve_execute(
    client: TestClient, engine: Engine, heuristic_llm: None
) -> None:
    """Acceptance M8: load sample data → process → review → approve → execute → export."""
    assert "27 Beispiel-Eingänge importiert" in client.post("/samples/import").text
    inbox = client.post("/items/process").text
    assert "27 Eingänge verarbeitet" in inbox
    assert "bg-red-600" in inbox  # traffic light for rule errors

    with Session(engine) as session:
        suggestion = session.exec(
            select(Suggestion).where(Suggestion.draft_text.is_not(None))  # type: ignore[union-attr]
        ).first()
    assert suggestion is not None
    item_id = suggestion.item_id

    detail = client.get(f"/items/{item_id}")
    assert AI_LABEL in detail.text
    audit = client.get(f"/items/{item_id}/audit")
    assert audit.status_code == 200
    assert "classify_v1" in audit.text and "extract_v1" in audit.text and "draft_v1" in audit.text
    assert re.search(r"Eingabe-Hash: [0-9a-f]{64}", audit.text)

    edited = "Guten Tag,\n\nwir bestätigen Ihre Kündigung.\n\nIhr FitDemo-Team"
    response = client.post(
        f"/items/{item_id}/review",
        data={"decision": "approved", "edited_text": edited},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _status(engine, item_id) == ItemStatus.APPROVED
    assert "Ausführen (Demo)" in client.get(f"/items/{item_id}").text

    client.post(f"/items/{item_id}/execute")
    assert _status(engine, item_id) == ItemStatus.DONE

    export = client.get(f"/items/{item_id}/export")
    assert export.status_code == 200
    assert export.text == edited
    assert "attachment" in export.headers["content-disposition"]
    with Session(engine) as session:
        review = session.exec(select(Review).where(Review.item_id == item_id)).one()
    assert review.reviewer == "demo"
    assert review.edited_text == edited
