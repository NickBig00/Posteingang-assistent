"""Further requests beyond the profile fields: extracted as reviewer hint, never answered."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.llm.client import LLMClient
from app.llm.fake import FakeProvider
from app.llm.prompts import load_prompt
from app.llm.schemas import MAX_ADDITIONAL_REQUESTS, extraction_model
from app.models import AdditionalRequest, Extraction, InboxItem, ItemSource, ItemStatus
from app.pipeline import HEALTH_TOPIC_PLACEHOLDER, additional_request_topics, process_item
from app.profiles import ADDITIONAL_REQUESTS_KEY, PROFILES_DIR, Profile, load_profile
from app.retention import delete_item
from app.review import load_details, reprocess_item

CLASSIFY = '{"document_type": "probetraining_anfrage", "confidence": 0.9}'
LOW = '{"document_type": "probetraining_anfrage", "confidence": 0.3}'
FIELDS = {
    "name": "Leon Lorem",
    "kontakt": "leon.lorem@example.net",
    "wunschtermin": "2026-10-15",
    "interesse": "Ausdauertraining",
}
DRAFT = "Guten Tag Leon Lorem, gerne reservieren wir Ihnen einen Termin am 15.10.2026."


@pytest.fixture
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


def _extraction(topics: list[str]) -> str:
    return json.dumps({**FIELDS, ADDITIONAL_REQUESTS_KEY: topics}, ensure_ascii=False)


def _item(session: Session) -> InboxItem:
    item = InboxItem(
        source=ItemSource.UPLOAD,
        raw_content_ref="x",
        text="Was kostet eine Mitgliedschaft? Probetraining am 15.10.2026?",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _topics(session: Session) -> list[str]:
    return [r.topic for r in session.exec(select(AdditionalRequest)).all()]


# --- schema and prompt ---


def test_schema_accepts_topics_and_defaults_to_none(profile: Profile) -> None:
    model = extraction_model(profile.document_type("probetraining_anfrage"))

    with_topics = model.model_validate({**FIELDS, ADDITIONAL_REQUESTS_KEY: [" Preise "]})
    without = model.model_validate(FIELDS)

    assert getattr(with_topics, ADDITIONAL_REQUESTS_KEY) == ["Preise"]
    assert getattr(without, ADDITIONAL_REQUESTS_KEY) == []


@pytest.mark.parametrize(
    "value",
    ["Preise", [""], [42], ["x" * 121], ["Thema"] * (MAX_ADDITIONAL_REQUESTS + 1)],
)
def test_schema_rejects_invalid_topics(profile: Profile, value: object) -> None:
    model = extraction_model(profile.document_type("probetraining_anfrage"))

    with pytest.raises(ValidationError):
        model.model_validate({**FIELDS, ADDITIONAL_REQUESTS_KEY: value})


def test_extract_prompt_asks_for_further_requests() -> None:
    prompt = load_prompt("extract").render(type_label="Kündigung", fields_json="[]", text="x")

    assert ADDITIONAL_REQUESTS_KEY in prompt.system
    assert "Gesundheitsangaben" in prompt.system


def test_fake_provider_reports_no_further_requests(profile: Profile) -> None:
    doc_type = profile.document_type("probetraining_anfrage")

    call = LLMClient(FakeProvider()).extract("Probetraining bitte", doc_type)

    assert call.ok and call.output is not None
    assert getattr(call.output, ADDITIONAL_REQUESTS_KEY) == []


def test_profile_field_must_not_use_reserved_name(profile: Profile) -> None:
    raw = profile.model_dump()
    raw["document_types"][0]["fields"].append(
        {"name": ADDITIONAL_REQUESTS_KEY, "type": "string", "description": "x"}
    )

    with pytest.raises(ValidationError, match="reserved"):
        Profile.model_validate(raw)


# --- topic filtering ---


def test_topics_are_trimmed_and_deduplicated() -> None:
    assert additional_request_topics([" Preise", "Preise ", "Öffnungszeiten"]) == [
        "Preise",
        "Öffnungszeiten",
    ]


def test_health_related_topics_are_not_stored() -> None:
    topics = additional_request_topics(["Training trotz Knieverletzung", "Preise"])

    assert topics == [HEALTH_TOPIC_PLACEHOLDER, "Preise"]


def test_non_list_topics_are_ignored() -> None:
    assert additional_request_topics(None) == []


# --- pipeline ---


def test_topics_are_stored_separately_from_fields(session: Session, profile: Profile) -> None:
    fake = FakeProvider([CLASSIFY, _extraction(["Preise der Mitgliedschaft"]), DRAFT])

    result = process_item(session, _item(session), profile, LLMClient(fake))

    assert result.status == ItemStatus.PROCESSED
    extraction = session.exec(select(Extraction)).one()
    assert ADDITIONAL_REQUESTS_KEY not in extraction.fields
    assert extraction.fields["name"] == "Leon Lorem"
    request = session.exec(select(AdditionalRequest)).one()
    assert request.topic == "Preise der Mitgliedschaft"
    assert request.is_ai_suggestion is True


def test_further_requests_do_not_change_the_draft(session: Session, profile: Profile) -> None:
    fake = FakeProvider([CLASSIFY, _extraction(["Preise"]), DRAFT])

    process_item(session, _item(session), profile, LLMClient(fake))

    # The draft prompt only gets the template: the request is left to the reviewer.
    assert "Preise" not in fake.calls[2][2].split("<dokument>")[0]


def test_reprocessing_replaces_topics(session: Session, profile: Profile) -> None:
    item = _item(session)
    process_item(session, item, profile, LLMClient(FakeProvider([LOW])))
    session.add(AdditionalRequest(item_id=item.id or 0, topic="alt"))
    session.commit()

    fake = FakeProvider([CLASSIFY, _extraction(["Preise"]), DRAFT])
    reprocess_item(session, item, profile, LLMClient(fake))

    assert _topics(session) == ["Preise"]


def test_deleting_item_removes_topics(session: Session, profile: Profile) -> None:
    item = _item(session)
    process_item(
        session, item, profile, LLMClient(FakeProvider([CLASSIFY, _extraction(["Preise"]), DRAFT]))
    )
    assert item.id is not None

    delete_item(session, item.id)

    assert _topics(session) == []


# --- user interface ---


def _processed(engine: Engine, profile: Profile, topics: list[str]) -> int:
    with Session(engine) as session:
        item = _item(session)
        fake = FakeProvider([CLASSIFY, _extraction(topics), DRAFT])
        process_item(session, item, profile, LLMClient(fake))
        assert item.id is not None
        details = load_details(session, item.id)
        assert details is not None
        assert [r.topic for r in details.additional_requests] == topics
        return item.id


def test_detail_view_shows_further_requests(
    client: TestClient, engine: Engine, profile: Profile
) -> None:
    item_id = _processed(engine, profile, ["Preise der Mitgliedschaft"])

    page = client.get(f"/items/{item_id}").text

    assert 'id="additional-requests"' in page
    assert "Weitere Anliegen im Original" in page
    assert "Preise der Mitgliedschaft" in page
    assert "Der Entwurf beantwortet diese Punkte nicht" in page


def test_detail_view_without_further_requests(
    client: TestClient, engine: Engine, profile: Profile
) -> None:
    item_id = _processed(engine, profile, [])

    assert 'id="additional-requests"' not in client.get(f"/items/{item_id}").text
