"""Shared fixtures data for the social media tests (fictitious values only)."""

from datetime import date, time
from decimal import Decimal

from app.core.profiles import PROFILES_DIR
from app.social.briefings import BriefingInput
from app.social.facts import BriefingFacts
from app.social.models import Channel
from app.social.profiles import DEFAULT_BRAND_PROFILES_DIR, SocialConfig, load_social_config


def demo_config() -> SocialConfig:
    return load_social_config(
        PROFILES_DIR / "fitness.yaml", DEFAULT_BRAND_PROFILES_DIR / "demo.yaml"
    )


def course_input(**overrides: object) -> BriefingInput:
    values: dict[str, object] = {
        "area": "kurse",
        "occasion_type": "kurs",
        "notes": "Neuer Kurs Rücken-Fit am Dienstagabend, für Einsteiger geeignet",
        "channels": [Channel.INSTAGRAM_POST, Channel.FACEBOOK],
        "facts": BriefingFacts(datum=date(2026, 11, 3), uhrzeit=time(18, 30), ort="Kursraum 2"),
    }
    return BriefingInput(**(values | overrides))  # type: ignore[arg-type]


def offer_facts() -> BriefingFacts:
    return BriefingFacts(
        zeitraum_von=date(2026, 11, 1), zeitraum_bis=date(2026, 11, 14), preis=Decimal("19.00")
    )
