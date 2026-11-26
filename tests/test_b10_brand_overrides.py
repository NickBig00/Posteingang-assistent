"""B10: occasion types, required facts and campaign plan per brand profile; text facts.

Uses a fictitious brand profile (copy of the demo profile with an `industry_overrides`
section); real customer profiles never go into the repository.
"""

from collections.abc import Iterator
from datetime import date, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.profiles import PROFILES_DIR, ProfileError
from app.main import app
from app.social.briefings import BriefingFormError, parse_briefing_form
from app.social.campaigns import plan_dates
from app.social.checks import check_facts, missing_facts
from app.social.facts import BriefingFacts, formatted_facts
from app.social.infographic import fact_lines
from app.social.llm.client import SocialLLMClient
from app.social.profiles import DEFAULT_BRAND_PROFILES_DIR, SocialConfig, load_social_config
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import scripted

OVERRIDES = """
industry_overrides:
  occasion_types:
    - key: kurs
      label: Kurs
      required_facts: [titel, datum, uhrzeit, kursleitung, anmeldung]
      optional_facts: [ort]
    - key: aktion
      label: Aktion
      required_facts: [zeitraum, vorteil, bedingungen]
      optional_facts: [preis]
    - {key: lauf, label: Gesundheitslauf, required_facts: [datum, uhrzeit, ort]}
  campaign_steps:
    - {key: ankuendigung, label: Ankündigung, offset_days: -14, instruction: ankündigen}
    - {key: erinnerung, label: Erinnerung, offset_days: -7, instruction: erinnern}
    - {key: letzter_aufruf, label: Letzter Aufruf, offset_days: -1, instruction: letzter Aufruf}
"""


def _brand(tmp_path: Path, extra: str = OVERRIDES) -> Path:
    source = (DEFAULT_BRAND_PROFILES_DIR / "demo.yaml").read_text(encoding="utf-8")
    path = tmp_path / "demo.yaml"
    path.write_text(source + extra, encoding="utf-8")
    return path


def _config(tmp_path: Path) -> SocialConfig:
    return load_social_config(PROFILES_DIR / "fitness.yaml", _brand(tmp_path))


# --- overrides ---


def test_brand_replaces_occasion_types_and_campaign_plan(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert [o.key for o in config.industry.occasion_types] == ["kurs", "aktion", "lauf"]
    assert [s.offset_days for s in config.industry.campaign_steps] == [-14, -7, -1]
    steps = plan_dates(config.industry.campaign_steps, date(2026, 12, 15))
    assert [d for _, d in steps] == [date(2026, 12, 1), date(2026, 12, 8), date(2026, 12, 14)]


def test_health_rules_and_sensitive_areas_stay_those_of_the_industry(tmp_path: Path) -> None:
    industry = load_social_config(PROFILES_DIR / "fitness.yaml").industry
    config = _config(tmp_path)

    assert config.industry.health_claims == industry.health_claims
    assert config.industry.sensitive_areas == industry.sensitive_areas
    assert config.industry.term_rules == industry.term_rules


def test_without_overrides_the_industry_applies(tmp_path: Path) -> None:
    config = load_social_config(PROFILES_DIR / "fitness.yaml", _brand(tmp_path, ""))

    assert "stellenangebot" in [o.key for o in config.industry.occasion_types]
    assert [s.offset_days for s in config.industry.campaign_steps] == [-21, -7, -1]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("\nindustry_overrides:\n  occasion_types: []\n", "occasion_types"),
        (
            "\nindustry_overrides:\n  occasion_types:\n"
            "    - {key: kurs, label: Kurs, required_facts: [unbekannt]}\n",
            "required_facts",
        ),
        (
            "\nindustry_overrides:\n  occasion_types:\n"
            "    - {key: kurs, label: Kurs}\n    - {key: kurs, label: Kurs 2}\n",
            "duplicate",
        ),
    ],
)
def test_invalid_overrides_are_rejected(tmp_path: Path, extra: str, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        load_social_config(PROFILES_DIR / "fitness.yaml", _brand(tmp_path, extra))


# --- text facts ---


COURSE = BriefingFacts(
    titel="Rücken-Fit",
    datum=date(2026, 12, 3),
    uhrzeit=time(18, 30),
    kursleitung="Trainerteam",
    anmeldung="über die App",
)


def test_required_text_facts_are_enforced_by_the_form(tmp_path: Path) -> None:
    config = _config(tmp_path)
    form = {
        "area": "kurse",
        "occasion_type": "kurs",
        "notes": "Rückenkurs",
        "datum": "2026-12-03",
        "uhrzeit": "18:30",
    }

    with pytest.raises(BriefingFormError) as error:
        parse_briefing_form(form, ["instagram_post"], config)
    assert {"titel", "kursleitung", "anmeldung"} <= set(error.value.errors)

    data = parse_briefing_form(
        form | {"titel": "Rücken-Fit", "kursleitung": "Trainerteam", "anmeldung": "über die App"},
        ["instagram_post"],
        config,
    )
    assert data.facts == COURSE


def test_text_facts_must_appear_word_for_word(tmp_path: Path) -> None:
    text = "Rücken-Fit am 03.12.2026 um 18:30 Uhr mit dem Trainerteam. Anmeldung über die App."

    assert missing_facts(text, COURSE) == []
    changed = text.replace("über die App", "an der Theke")
    assert missing_facts(changed, COURSE) == ["anmeldung"]
    errors = [f.reason for f in check_facts(changed, COURSE) if f.outcome == "error"]
    assert errors == ["Anmeldung „über die App“ fehlt im Text oder wurde verändert."]


def test_text_facts_on_the_graphic_and_in_the_prompt() -> None:
    assert formatted_facts(COURSE)["kursleitung"] == "Trainerteam"
    assert "Anmeldung: über die App" in fact_lines(COURSE, COURSE.present_keys())


# --- web ---


@pytest.fixture
def web(client: TestClient, tmp_path: Path) -> Iterator[TestClient]:
    config = _config(tmp_path)
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = lambda: config
    yield client


def test_form_shows_text_facts_only_where_an_occasion_uses_them(web: TestClient) -> None:
    course = web.get("/social/briefings/form?area=kurse&occasion_type=kurs").text
    run = web.get("/social/briefings/form?area=kurse&occasion_type=lauf").text
    new = web.get("/social/briefings/new").text

    assert 'name="kursleitung"' in course and 'name="anmeldung"' in course
    assert "data-text-facts" not in run
    assert "Stellenangebot" not in new and "Gesundheitslauf" in new


def test_briefing_with_text_facts_produces_a_checked_draft(
    web: TestClient, session: Session
) -> None:
    from sqlmodel import select

    from app.social.models import CheckResult, PostDraft

    response = web.post(
        "/social/briefings",
        data={
            "area": "kurse",
            "occasion_type": "kurs",
            "notes": "Rückenkurs für Einsteiger",
            "titel": "Rücken-Fit",
            "datum": "2026-12-03",
            "uhrzeit": "18:30",
            "kursleitung": "Trainerteam",
            "anmeldung": "über die App",
            "channels": ["instagram_post"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    draft = session.exec(select(PostDraft)).one()
    assert "Anmeldung: über die App" in draft.text
    facts = session.exec(
        select(CheckResult).where(
            CheckResult.draft_id == draft.id, CheckResult.check_key == "fakten"
        )
    ).all()
    assert facts and all(f.outcome == "ok" for f in facts)
