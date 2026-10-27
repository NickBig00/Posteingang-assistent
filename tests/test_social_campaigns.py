"""Campaigns: one occasion, several posts, each checked and approved on its own (S7)."""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.outcomes import Outcome
from app.core.profiles import PROFILES_DIR, ProfileError
from app.main import app
from app.social.briefings import BriefingFormError
from app.social.campaigns import (
    CampaignInput,
    campaign_plan,
    create_campaign,
    load_demo_campaign,
    plan_dates,
    validate_campaign,
)
from app.social.facts import BriefingFacts
from app.social.llm.client import SocialLLMClient
from app.social.models import Briefing, Campaign, Channel, CheckResult, PostDraft, PostStatus
from app.social.profiles import load_industry_social
from app.social.review import approval_blockers, approve_draft
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
TODAY = date(2026, 10, 27)
EVENT = date(2026, 12, 1)


def _input(**overrides: object) -> CampaignInput:
    briefing = course_input(facts=BriefingFacts(uhrzeit=time(18, 30), ort="Kursraum 2"))
    return validate_campaign(
        CONFIG, "Rücken-Fit Winter", EVENT, replace(briefing, **overrides), TODAY
    )


def test_fitness_plan() -> None:
    steps = CONFIG.industry.campaign_steps

    assert [(s.key, s.offset_days) for s in steps] == [
        ("ankuendigung", -21),
        ("erinnerung", -7),
        ("letzter_aufruf", -1),
    ]
    assert [d for _, d in plan_dates(steps, EVENT)] == [
        date(2026, 11, 10),
        date(2026, 11, 24),
        date(2026, 11, 30),
    ]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"key": "a", "label": "A", "offset_days": 3, "instruction": "x"}], "offset_days"),
        (
            [
                {"key": "a", "label": "A", "offset_days": -3, "instruction": "x"},
                {"key": "a", "label": "B", "offset_days": -1, "instruction": "y"},
            ],
            "duplicate campaign step keys: a",
        ),
    ],
)
def test_invalid_campaign_steps(tmp_path: Path, steps: list, message: str) -> None:
    data = yaml.safe_load((PROFILES_DIR / "fitness.yaml").read_text(encoding="utf-8"))
    data["social"]["campaign_steps"] = steps
    path = tmp_path / "fitness.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ProfileError, match=message):
        load_industry_social(path)


def test_event_date_becomes_fact() -> None:
    assert _input().briefing.facts.datum == EVENT


def test_given_date_fact_is_kept() -> None:
    data = validate_campaign(
        CONFIG, "x", EVENT, course_input(facts=BriefingFacts(datum=date(2026, 12, 2))), TODAY
    )

    assert data.briefing.facts.datum == date(2026, 12, 2)


def test_too_short_notice() -> None:
    with pytest.raises(BriefingFormError) as excinfo:
        validate_campaign(CONFIG, "x", date(2026, 11, 10), course_input(), TODAY)

    assert "Zu kurzfristig" in excinfo.value.errors["event_date"]
    assert "Ankündigung" in excinfo.value.errors["event_date"]


def test_industry_without_plan() -> None:
    config = replace(CONFIG, industry=CONFIG.industry.model_copy(update={"campaign_steps": []}))

    with pytest.raises(BriefingFormError, match="kein Kampagnenplan"):
        validate_campaign(config, "x", EVENT, course_input(), TODAY)


def test_create_campaign(session: Session) -> None:
    provider = scripted()

    campaign = create_campaign(session, CONFIG, SocialLLMClient(provider), _input(), "demo")

    plan = campaign_plan(session, CONFIG, campaign)
    assert [s.step.key for s in plan] == ["ankuendigung", "erinnerung", "letzter_aufruf"]
    assert [s.briefing.desired_date for s in plan] == [
        date(2026, 11, 10),
        date(2026, 11, 24),
        date(2026, 11, 30),
    ]
    assert all(s.briefing.campaign_id == campaign.id for s in plan)
    drafts = [d for s in plan for d in s.drafts]
    assert len(drafts) == 6  # 3 steps x 2 channels
    assert all(d.status is PostStatus.IN_REVIEW for d in drafts)
    draft_prompts = [user for task, _system, user in provider.calls if task == "post_draft"]
    assert "<kampagnenschritt>Erinnerung (Erinnerung, die Anmeldung läuft noch)" in draft_prompts[2]
    assert all("Datum: 01.12.2026" in prompt for prompt in draft_prompts)
    assert drafts[2].text.startswith("Erinnerung: Kurs!")


def test_checks_apply_to_every_post(session: Session) -> None:
    good = json.dumps({"text": "Rücken-Fit am 1.12. um 18:30 Uhr im Kursraum 2", "hashtags": []})
    wrong = json.dumps({"text": "Rücken-Fit am 2.12. um 18:30 Uhr im Kursraum 2", "hashtags": []})
    provider = scripted(post_draft=[good, wrong, good])
    data = _input(channels=[Channel.FACEBOOK])

    campaign = create_campaign(session, CONFIG, SocialLLMClient(provider), data, "demo")

    drafts = [d for s in campaign_plan(session, CONFIG, campaign) for d in s.drafts]
    assert len(drafts) == 3
    errors = [
        session.exec(
            select(CheckResult)
            .where(CheckResult.draft_id == d.id)
            .where(CheckResult.outcome == Outcome.ERROR)
        ).all()
        for d in drafts
    ]
    assert [len(e) for e in errors] == [0, 1, 0]
    assert "Datum weicht ab" in errors[1][0].reason
    # Every post is approved on its own: the faulty one stays blocked.
    approve_draft(session, drafts[0], "demo")
    approve_draft(session, drafts[2], "demo")
    assert approval_blockers(session, drafts[1])
    assert [d.status for d in drafts] == [
        PostStatus.APPROVED,
        PostStatus.IN_REVIEW,
        PostStatus.APPROVED,
    ]


def test_demo_campaign_has_at_least_three_posts(session: Session) -> None:
    data = load_demo_campaign(CONFIG, TODAY)

    campaign = create_campaign(session, CONFIG, SocialLLMClient(scripted()), data, "demo")

    assert campaign.event_date == date(2026, 11, 24)
    drafts = session.exec(select(PostDraft)).all()
    assert len(drafts) >= 3
    assert len(session.exec(select(Briefing)).all()) == 3


def test_demo_campaign_must_be_synthetic(tmp_path: Path) -> None:
    path = tmp_path / "demo.json"
    path.write_text(json.dumps({"synthetic": False}), encoding="utf-8")

    with pytest.raises(ValueError, match="synthetic"):
        load_demo_campaign(CONFIG, TODAY, path)


# --- web ---


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_campaign_form_hides_desired_date(social_client: TestClient) -> None:
    page = social_client.get("/social/campaigns/new").text

    assert 'name="event_date"' in page and 'name="name"' in page
    assert 'name="desired_date"' not in page
    assert "Ankündigung (21 Tage vorher)" in page
    fields = social_client.get(
        "/social/briefings/form", params={"area": "kurse", "occasion_type": "kurs", "campaign": "1"}
    ).text
    assert 'name="desired_date"' not in fields


def test_create_campaign_in_the_ui(social_client: TestClient, session: Session) -> None:
    response = social_client.post(
        "/social/campaigns",
        data={
            "name": "Rücken-Fit 2099",
            "event_date": "2099-03-01",
            "area": "kurse",
            "occasion_type": "kurs",
            "notes": "Neuer Kurs Rücken-Fit",
            "uhrzeit": "18:30",
            "ort": "Kursraum 2",
            "channels": ["facebook"],
        },
    )

    assert response.status_code == 200
    assert response.text.count('data-step="') == 3
    assert "Anlass am 01.03.2099" in response.text
    assert "Wunschtermin 08.02.2099" in response.text
    assert len(session.exec(select(PostDraft)).all()) == 3
    assert "Rücken-Fit 2099" in social_client.get("/social/campaigns").text
    overview = social_client.get("/social").text
    campaign = session.exec(select(Campaign)).one()
    assert f'href="/social/campaigns/{campaign.id}"' in overview


def test_campaign_form_errors(social_client: TestClient, session: Session) -> None:
    response = social_client.post(
        "/social/campaigns",
        data={"name": "", "event_date": "2020-01-01", "area": "kurse", "occasion_type": "kurs"},
    )

    assert response.status_code == 422
    assert "Bitte einen Namen angeben." in response.text
    assert "Bitte Stichpunkte angeben." in response.text
    assert session.exec(select(Campaign)).all() == []


def test_campaign_too_late_in_the_ui(social_client: TestClient) -> None:
    response = social_client.post(
        "/social/campaigns",
        data={
            "name": "Zu spät",
            "event_date": "2020-01-01",
            "area": "kurse",
            "occasion_type": "kurs",
            "notes": "x",
            "uhrzeit": "18:30",
            "ort": "Kursraum 2",
            "channels": ["facebook"],
        },
    )

    assert response.status_code == 422
    assert "Zu kurzfristig" in response.text


def test_demo_button(social_client: TestClient, session: Session) -> None:
    response = social_client.post("/social/campaigns/demo")

    assert response.status_code == 200
    assert response.text.count('data-step="') == 3
    drafts = session.exec(select(PostDraft).order_by(col(PostDraft.id))).all()
    assert len(drafts) == 6


def test_unknown_campaign(social_client: TestClient) -> None:
    assert social_client.get("/social/campaigns/999").status_code == 404
