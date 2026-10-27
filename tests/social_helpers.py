"""Shared fixtures data for the social media tests (fictitious values only)."""

from collections import deque
from datetime import date, time
from decimal import Decimal

from app.core.llm.base import LLMProviderError, LLMTask
from app.core.llm.fake import FakeProvider
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


def scripted(**answers: list[str]) -> FakeProvider:
    """Fake provider with scripted answers per task; other tasks use the offline heuristic.

    Raises (like a provider error) once the answers of a task are used up.
    """
    queues = {task: deque(values) for task, values in answers.items()}
    heuristic = FakeProvider()

    def respond(task: LLMTask, system: str, user: str) -> str:
        if task not in queues:
            return heuristic.complete(task=task, system=system, user=user).text
        if not queues[task]:
            raise LLMProviderError(f"no scripted answer left for {task}")
        return queues[task].popleft()

    return FakeProvider(respond)
