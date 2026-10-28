"""Briefing form and AI drafts per channel (ROADMAP_social S2). Fake provider only."""

import json
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.llm.base import LLMTask
from app.core.llm.fake import FakeProvider
from app.core.llm.prompts import load_prompt
from app.core.models import AuditEntry
from app.main import app
from app.social.briefings import BriefingFormError, create_briefing, parse_briefing_form
from app.social.checking import ASSESS_STEP
from app.social.drafts import DRAFT_STEP, generate_drafts
from app.social.facts import format_price, formatted_facts, parse_price_input
from app.social.llm.client import SOCIAL_PROMPTS_DIR, SocialLLMClient
from app.social.models import Briefing, Channel, PostDraft, PostStatus
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, offer_facts, scripted

CONFIG = demo_config()
VALID_DRAFT = json.dumps({"text": "Neuer Kurs am 03.11.2026 um 18:30 Uhr.", "hashtags": ["#Kurse"]})


def _form(**overrides: str) -> dict[str, str]:
    return {
        "area": "kurse",
        "occasion_type": "kurs",
        "notes": "Neuer Kurs Rücken-Fit",
        "datum": "2026-11-03",
        "uhrzeit": "18:30",
        "ort": "Kursraum 2",
    } | overrides


# --- form validation (deterministic) ---


def test_parse_valid_form() -> None:
    data = parse_briefing_form(_form(preis="19,00"), ["instagram_post", "facebook"], CONFIG)

    assert data.area == "kurse"
    assert data.channels == [Channel.INSTAGRAM_POST, Channel.FACEBOOK]
    assert data.facts.preis == Decimal("19.00")
    assert data.facts.present_keys() == ["datum", "uhrzeit", "preis", "ort"]


def test_required_facts_of_occasion() -> None:
    with pytest.raises(BriefingFormError) as excinfo:
        parse_briefing_form(_form(uhrzeit="", ort=""), ["facebook"], CONFIG)

    assert excinfo.value.errors == {
        "uhrzeit": "Uhrzeit ist Pflichtangabe für den Anlass „Kurs“.",
        "ort": "Ort ist Pflichtangabe für den Anlass „Kurs“.",
    }


def test_offer_requires_period_and_price() -> None:
    with pytest.raises(BriefingFormError) as excinfo:
        parse_briefing_form(_form(occasion_type="aktion"), ["facebook"], CONFIG)

    assert set(excinfo.value.errors) == {"zeitraum_von", "preis"}


def test_occasion_without_required_facts() -> None:
    data = parse_briefing_form(
        {"area": "fitness", "occasion_type": "team", "notes": "Unser Team"}, ["facebook"], CONFIG
    )

    assert data.facts.present_keys() == []


@pytest.mark.parametrize(
    ("overrides", "channels", "field", "message"),
    [
        ({"area": "sauna"}, ["facebook"], "area", "Bitte einen Bereich wählen."),
        ({"occasion_type": "x"}, ["facebook"], "occasion_type", "Bitte einen Anlass wählen."),
        ({"notes": "  "}, ["facebook"], "notes", "Bitte Stichpunkte angeben."),
        ({"notes": "x" * 2001}, ["facebook"], "notes", "Höchstens 2000 Zeichen."),
        ({}, [], "channels", "Bitte mindestens einen Kanal wählen."),
        ({}, ["tiktok"], "channels", "Unbekannter Kanal: tiktok"),
        ({"datum": "3.11."}, ["facebook"], "datum", "Ungültiges Datum."),
        ({"uhrzeit": "halb sieben"}, ["facebook"], "uhrzeit", "Ungültige Uhrzeit."),
        ({"preis": "neunzehn"}, ["facebook"], "preis", "Ungültiger Preis."),
        ({"preis": "-5"}, ["facebook"], "preis", "Ungültiger Preis."),
        ({"telefon": "ruf an"}, ["facebook"], "telefon", "Ungültige Telefonnummer."),
        ({"desired_date": "morgen"}, ["facebook"], "desired_date", "Ungültiges Datum."),
        ({"zeitraum_von": "2026-11-01"}, ["facebook"], "zeitraum_von", "Zeitraum braucht"),
        (
            {"zeitraum_von": "2026-11-14", "zeitraum_bis": "2026-11-01"},
            ["facebook"],
            "zeitraum_von",
            "Beginn liegt nach dem Ende",
        ),
    ],
)
def test_invalid_form(
    overrides: dict[str, str], channels: list[str], field: str, message: str
) -> None:
    with pytest.raises(BriefingFormError) as excinfo:
        parse_briefing_form(_form(**overrides), channels, CONFIG)

    assert message in excinfo.value.errors[field]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("19", "19.00"),
        ("19,5", "19.50"),
        ("19.50", "19.50"),
        ("19,00 €", "19.00"),
        ("1.200,00", "1200.00"),
        ("1.200", "1200.00"),
        ("0", "0.00"),
    ],
)
def test_parse_price_input(text: str, expected: str) -> None:
    assert parse_price_input(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "19,00,00", "NaN", "Infinity"])
def test_parse_price_input_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_price_input(text)


def test_formatted_facts() -> None:
    assert formatted_facts(course_input().facts) == {
        "datum": "03.11.2026",
        "uhrzeit": "18:30 Uhr",
        "ort": "Kursraum 2",
    }
    assert formatted_facts(offer_facts()) == {
        "preis": "19,00 €",
        "zeitraum": "01.11.2026 bis 14.11.2026",
    }
    assert format_price(Decimal("1200.5")) == "1.200,50 €"


# --- drafts ---


def _briefing(session: Session, **overrides: object) -> Briefing:
    return create_briefing(session, CONFIG, course_input(**overrides), "demo")


def test_prompt_file_is_versioned() -> None:
    template = load_prompt("post_draft", "v1", SOCIAL_PROMPTS_DIR)

    assert template.prompt_id == "post_draft_v1"


def test_one_draft_per_channel_with_heuristic(session: Session) -> None:
    briefing = _briefing(session)

    drafts = generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider()), briefing)

    assert [d.channel for d in drafts] == [Channel.INSTAGRAM_POST, Channel.FACEBOOK]
    for draft in drafts:
        assert draft.status is PostStatus.IN_REVIEW
        assert draft.is_ai_suggestion
        assert "03.11.2026" in draft.text and "18:30 Uhr" in draft.text
        assert "Kursraum 2" in draft.text
        assert "freuen uns auf dich" in draft.text
        assert draft.hashtags == ["#DemoGesundheitszentrum", "#Musterstadt", "#Kurse"]


def test_formal_area_uses_sie(session: Session) -> None:
    briefing = _briefing(session, area="physio", channels=[Channel.FACEBOOK])

    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider()), briefing)

    assert "freuen uns auf Sie" in draft.text


def test_story_respects_channel_length(session: Session) -> None:
    briefing = _briefing(
        session, area="fitness", channels=[Channel.INSTAGRAM_STORY], notes="Lang " * 200
    )

    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider()), briefing)

    assert len(draft.full_text) <= 250
    assert "03.11.2026" in draft.text


def test_every_call_is_audited(session: Session) -> None:
    briefing = _briefing(session)

    drafts = generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider()), briefing)

    entries = session.exec(select(AuditEntry).order_by(AuditEntry.id)).all()  # type: ignore[arg-type]
    # Per draft: the draft call, then the assessment of its checks.
    assert [e.step for e in entries] == [DRAFT_STEP, ASSESS_STEP] * 2
    for entry, draft in zip(entries[1::2], drafts, strict=True):
        assert entry.prompt_version == "post_assess_v1"
        assert entry.model_params["draft_id"] == draft.id
    for entry, draft in zip(entries[::2], drafts, strict=True):
        assert entry.prompt_version == "post_draft_v1"
        assert entry.item_id is None
        assert entry.model == "fake-heuristic-v1"
        assert entry.model_params["briefing_id"] == briefing.id
        assert entry.model_params["draft_id"] == draft.id
        assert entry.model_params["channel"] == draft.channel.value
        assert entry.model_params["attempt"] == 1
        assert entry.output_hash is not None
        assert entry.error is None


def test_prompt_contains_briefing_data(session: Session) -> None:
    provider = FakeProvider()
    briefing = _briefing(session, channels=[Channel.FACEBOOK])

    generate_drafts(session, CONFIG, SocialLLMClient(provider), briefing)

    task, system, user = provider.calls[0]
    assert task == "post_draft"
    assert "„du“" in system
    assert "Facebook-Beitrag" in system
    assert "Datum: 03.11.2026" in user
    assert "Uhrzeit: 18:30 Uhr" in user
    assert "<stichpunkte>\nNeuer Kurs Rücken-Fit" in user
    assert "#Kurse" in user
    # Data minimisation: no contact details of the brand are needed for a draft.
    assert CONFIG.brand.contact.email not in system + user


def test_invalid_output_is_retried_once(session: Session) -> None:
    briefing = _briefing(session, channels=[Channel.FACEBOOK])
    provider = scripted(post_draft=['{"text": ""}', VALID_DRAFT])

    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(provider), briefing)

    assert draft.status is PostStatus.IN_REVIEW
    assert draft.hashtags == ["#Kurse"]
    entries = session.exec(
        select(AuditEntry).where(AuditEntry.step == DRAFT_STEP).order_by(AuditEntry.id)  # type: ignore[arg-type]
    ).all()
    assert [e.model_params["attempt"] for e in entries] == [1, 2]
    assert entries[0].error is not None and entries[0].error.startswith("invalid output")


@pytest.mark.parametrize(
    "answers",
    [
        ["kein json", "auch kein json"],
        ['{"text": "x", "hashtags": ["ohne Raute"]}', '{"text": "x", "extra": 1}'],
        ["[]", '{"hashtags": []}'],
    ],
)
def test_needs_manual_review_after_second_failure(session: Session, answers: list[str]) -> None:
    briefing = _briefing(session, channels=[Channel.FACEBOOK])

    (draft,) = generate_drafts(
        session, CONFIG, SocialLLMClient(scripted(post_draft=answers)), briefing
    )

    assert draft.status is PostStatus.NEEDS_MANUAL_REVIEW
    assert draft.text == ""
    assert len(session.exec(select(AuditEntry).where(AuditEntry.step == DRAFT_STEP)).all()) == 2


def test_provider_error_is_audited(session: Session) -> None:
    briefing = _briefing(session, channels=[Channel.FACEBOOK])

    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted(post_draft=[])), briefing)

    assert draft.status is PostStatus.NEEDS_MANUAL_REVIEW
    entries = session.exec(select(AuditEntry)).all()
    # Two failed draft calls; an empty draft is not assessed.
    assert len(entries) == 2
    assert all(e.error and e.error.startswith("LLMProviderError") for e in entries)
    assert all(e.model == "fake" for e in entries)


# --- web ---


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(FakeProvider())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_form_page(social_client: TestClient) -> None:
    html = social_client.get("/social/briefings/new").text

    assert "Neues Briefing" in html
    assert 'name="area"' in html and 'name="occasion_type"' in html
    # Default area "fitness": its channels are preselected.
    assert 'value="instagram_story" checked' in html
    assert 'value="linkedin" checked' not in html


def test_area_change_preselects_channels(social_client: TestClient) -> None:
    html = social_client.get(
        "/social/briefings/form",
        params={"area": "firmenfitness", "occasion_type": "kurs", "changed": "area"},
    ).text

    assert 'value="linkedin" checked' in html
    assert 'value="facebook" checked' not in html
    assert "Anrede: Sie" in html
    assert 'title="Pflichtangabe"' in html


def test_occasion_change_keeps_channels(social_client: TestClient) -> None:
    html = social_client.get(
        "/social/briefings/form",
        params={"area": "fitness", "occasion_type": "team", "channels": ["linkedin"]},
    ).text

    assert 'value="linkedin" checked' in html
    assert 'value="facebook" checked' not in html
    assert 'title="Pflichtangabe"' not in html


def test_submit_invalid_form(social_client: TestClient, session: Session) -> None:
    response = social_client.post(
        "/social/briefings", data=_form(ort="") | {"channels": ["facebook"]}
    )

    assert response.status_code == 422
    assert "Ort ist Pflichtangabe" in response.text
    assert session.exec(select(Briefing)).all() == []


def test_submit_creates_drafts(social_client: TestClient, session: Session) -> None:
    response = social_client.post(
        "/social/briefings",
        data=_form() | {"channels": ["instagram_post", "facebook"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    detail = social_client.get(response.headers["location"])
    assert detail.status_code == 200
    assert detail.text.count("KI-Vorschlag</span>") == 2
    assert "Instagram-Beitrag" in detail.text and "Facebook-Beitrag" in detail.text
    assert len(session.exec(select(PostDraft)).all()) == 2
    overview = social_client.get("/social").text
    assert "In Prüfung" in overview


def test_unknown_briefing(social_client: TestClient) -> None:
    assert social_client.get("/social/briefings/999").status_code == 404


def test_tasks_have_fake_heuristics() -> None:
    tasks: list[LLMTask] = ["post_draft"]
    provider = FakeProvider()
    for task in tasks:
        assert provider.complete(task=task, system="", user="").text
