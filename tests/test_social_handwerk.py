"""Second industry for social media: switching by configuration only (ROADMAP_social S9)."""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.llm.fake import FakeProvider
from app.core.outcomes import Outcome
from app.core.profiles import PROFILES_DIR, ProfileError
from app.inbox.profiles import load_profile
from app.main import app
from app.social.briefings import BriefingInput, create_briefing
from app.social.checks import check_phrase_rules
from app.social.drafts import generate_drafts
from app.social.evaluation import evaluate_social, samples_dir_for
from app.social.facts import BriefingFacts
from app.social.llm.client import SocialLLMClient
from app.social.models import Channel, CheckResult, PostDraft
from app.social.profiles import (
    DEFAULT_BRAND_PROFILES_DIR,
    AddressForm,
    SocialConfig,
    load_social_config,
)
from app.social.web import get_social_client
from tests.social_helpers import scripted

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def handwerk(monkeypatch: pytest.MonkeyPatch) -> SocialConfig:
    monkeypatch.setenv("PROFILE", "handwerk")
    monkeypatch.setenv("BRAND_PROFILE", "handwerk_demo")
    monkeypatch.delenv("BRAND_PROFILES_DIR", raising=False)
    return load_social_config()


def test_switch_by_environment(handwerk: SocialConfig) -> None:
    assert handwerk.industry_key == "handwerk"
    assert handwerk.brand.name == "Demo-Haustechnik Musterstadt"
    assert [o.key for o in handwerk.industry.occasion_types] == [
        "referenzprojekt",
        "saisonhinweis",
        "notdienst",
        "aktion",
        "team",
        "stellenangebot",
        "ausbildung",
    ]
    assert handwerk.industry.occasion_type("notdienst").required_facts == ["telefon", "zeitraum"]
    assert handwerk.industry.sensitive_areas == []
    assert handwerk.brand.address_form("karriere") is AddressForm.DU
    assert handwerk.brand.channels("notdienst") == [Channel.FACEBOOK, Channel.INSTAGRAM_STORY]


def test_brand_of_other_industry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROFILE", "handwerk")
    monkeypatch.setenv("BRAND_PROFILE", "demo")

    with pytest.raises(ProfileError, match="gehört zur Branche 'fitness'"):
        load_social_config()


def test_inbox_handwerk_profile_is_unchanged() -> None:
    profile = load_profile(PROFILES_DIR / "handwerk.yaml")

    assert profile.key == "handwerk"
    assert "social" not in profile.model_dump()


def test_handwerk_demo_profile_is_tracked() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "!profiles/brand/handwerk_demo.yaml" in lines
    assert (DEFAULT_BRAND_PROFILES_DIR / "handwerk_demo.yaml").is_file()


def test_price_needs_vat_note(handwerk: SocialConfig) -> None:
    rules = handwerk.industry.phrase_rules
    facts = BriefingFacts(preis=Decimal("129"))

    assert (
        check_phrase_rules("Heizungs-Check für 129 €", rules, facts)[0].outcome is Outcome.WARNING
    )
    ok = check_phrase_rules("Heizungs-Check für 129 € inkl. MwSt.", rules, facts)[0]
    assert ok.outcome is Outcome.OK


def test_emergency_service_drafts(session: Session, handwerk: SocialConfig) -> None:
    data = BriefingInput(
        area="notdienst",
        occasion_type="notdienst",
        notes="Notdienst über die Feiertage",
        channels=[Channel.FACEBOOK],
        facts=BriefingFacts(
            zeitraum_von=date(2026, 12, 24), zeitraum_bis=date(2026, 12, 26), telefon="01234 98765"
        ),
    )
    briefing = create_briefing(session, handwerk, data, "demo")

    (draft,) = generate_drafts(session, handwerk, SocialLLMClient(scripted()), briefing)

    assert "freuen uns auf Sie" in draft.text
    results = session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all()
    facts = [r for r in results if r.check_key == "fakten"]
    assert len(facts) == 3 and all(r.outcome is Outcome.OK for r in facts)


def test_evaluation_runs_for_both_industries(handwerk: SocialConfig) -> None:
    fitness = load_social_config(
        PROFILES_DIR / "fitness.yaml", DEFAULT_BRAND_PROFILES_DIR / "demo.yaml"
    )

    for config in (fitness, handwerk):
        report = evaluate_social(config, SocialLLMClient(FakeProvider()))
        assert report.industry == config.industry_key
        assert report.facts_rate == 1.0
        assert report.length_rate == 1.0
        assert report.to_json()["targets"]["met"] is True  # type: ignore[index]


def test_committed_handwerk_report(handwerk: SocialConfig) -> None:
    path = ROOT / "docs/evaluation/social_handwerk_fake.json"
    committed = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate_social(handwerk, SocialLLMClient(FakeProvider())).to_json()

    committed.pop("created_at")
    report.pop("created_at")
    assert committed == json.loads(json.dumps(report))


def test_handwerk_samples_are_synthetic_and_consistent(handwerk: SocialConfig) -> None:
    folder = samples_dir_for("handwerk")
    briefings = json.loads((folder / "briefings.json").read_text(encoding="utf-8"))
    cases = json.loads((folder / "assessment_cases.json").read_text(encoding="utf-8"))

    assert briefings["synthetic"] is True and cases["synthetic"] is True
    assert {b["area"] for b in briefings["briefings"]} == {a.key for a in handwerk.brand.areas}
    for sample in briefings["briefings"]:
        occasion = handwerk.industry.occasion_type(sample["occasion_type"])
        facts = BriefingFacts.model_validate(sample["facts"])
        assert facts.missing(occasion.required_facts) == [], sample["id"]


# --- web with the handwerk configuration (no override of the profiles) ---


@pytest.fixture
def handwerk_client(client: TestClient, handwerk: SocialConfig) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    yield client


def test_ui_with_handwerk(handwerk_client: TestClient, session: Session) -> None:
    page = handwerk_client.get("/social").text
    assert "Demo-Haustechnik Musterstadt" in page
    form = handwerk_client.get("/social/briefings/new").text
    assert "Notdienst" in form and "Karriere und Ausbildung" in form

    response = handwerk_client.post(
        "/social/briefings",
        data={
            "area": "heizung",
            "occasion_type": "aktion",
            "notes": "Herbstaktion Heizungs-Check",
            "zeitraum_von": "2026-11-02",
            "zeitraum_bis": "2026-11-30",
            "preis": "129",
            "channels": ["facebook"],
        },
    )
    assert response.status_code == 200
    assert "Preisangabe" in response.text
    (draft,) = session.exec(select(PostDraft)).all()
    warnings = session.exec(
        select(CheckResult)
        .where(CheckResult.draft_id == draft.id)
        .where(CheckResult.check_key == "preisangabe")
    ).all()
    assert [w.outcome for w in warnings] == [Outcome.WARNING]


def test_handwerk_demo_campaign(handwerk_client: TestClient, session: Session) -> None:
    response = handwerk_client.post("/social/campaigns/demo")

    assert response.status_code == 200
    assert response.text.count('data-step="') == 2
    assert len(session.exec(select(PostDraft)).all()) == 4
