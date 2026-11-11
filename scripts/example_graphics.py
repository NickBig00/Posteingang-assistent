"""Example info graphics and carousels for pull requests (ROADMAP_social B7, B9).

    uv run python scripts/example_graphics.py   → docs/screenshots/B7/beispiele/

Renders with the fictitious demo brand profile and synthetic texts only (no LLM, no API).
"""

import argparse
import sys
from datetime import date, time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.image_formats import ImageFormat  # noqa: E402
from app.core.profiles import PROFILES_DIR  # noqa: E402
from app.social.facts import BriefingFacts  # noqa: E402
from app.social.infographic import InfoTexts, render_info_graphic  # noqa: E402
from app.social.models import Briefing  # noqa: E402
from app.social.profiles import DEFAULT_BRAND_PROFILES_DIR, load_social_config  # noqa: E402

BACKGROUND = ROOT / "samples" / "social" / "images" / "demo_hintergrund.jpg"
TALK_FACTS = BriefingFacts(datum=date(2026, 12, 3), uhrzeit=time(19, 0), ort="Seminarraum 1")
TALK = InfoTexts(
    headline="Vortrag: Bewegung im Büroalltag",
    subline="Kurzweilig, praxisnah, mit Übungen zum Mitmachen",
    intro="Viel Sitzen gehört für viele zum Alltag. An diesem Abend zeigen wir einfache Ideen "
    "für mehr Bewegung zwischendurch.",
    points=[
        "Kleine Übungen für den Schreibtisch",
        "Pausen sinnvoll nutzen",
        "Tipps für den Arbeitsweg",
        "Zeit für eure Fragen",
    ],
    closing="Kommt vorbei – wir freuen uns auf euch!",
)
OFFER_FACTS = BriefingFacts(
    zeitraum_von=date(2026, 12, 1), zeitraum_bis=date(2026, 12, 14), preis=Decimal("19.00")
)
OFFER = InfoTexts(
    headline="Winter-Schnupperwochen",
    subline="Zwei Wochen alle Kurse ausprobieren",
    intro="Neugierig auf unsere Kurse? In den Schnupperwochen kannst du alles testen.",
    points=["Alle Kurse inklusive", "Ohne Vertragsbindung", "Persönliche Einweisung"],
    closing="Melde dich einfach am Empfang!",
)


def _briefing(occasion: str, facts: BriefingFacts, area: str = "kurse") -> Briefing:
    return Briefing(
        id=1,
        brand_key="demo",
        area=area,
        occasion_type=occasion,
        notes="Beispiel",
        facts=facts.to_json(),
        channels=[],
        created_by="demo",
    )


def info_examples(target: Path) -> list[Path]:
    config = load_social_config(
        PROFILES_DIR / "fitness.yaml", DEFAULT_BRAND_PROFILES_DIR / "demo.yaml"
    )
    photo = BACKGROUND.read_bytes()
    talk = _briefing("veranstaltung", TALK_FACTS)
    offer = _briefing("aktion", OFFER_FACTS, "fitness")
    jobs = [
        ("vortrag-zentriert-ueber-bild-4x5", talk, TALK, ImageFormat.PORTRAIT, "zentriert", photo),
        ("vortrag-farbfeld-1x1", talk, TALK, ImageFormat.SQUARE, "farbfeld", photo),
        ("vortrag-foto-oben-story-9x16", talk, TALK, ImageFormat.STORY, "foto_oben", photo),
        ("vortrag-farbflaeche-4x5", talk, TALK, ImageFormat.PORTRAIT, "zentriert", None),
        ("aktion-farbfeld-4x5", offer, OFFER, ImageFormat.PORTRAIT, "farbfeld", photo),
    ]
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, briefing, texts, fmt, variant, background in jobs:
        path = target / f"{name}.jpg"
        jpeg = render_info_graphic(
            config, briefing, texts, fmt, variant=variant, background=background
        )
        path.write_bytes(jpeg)
        written.append(path)
    return written


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    for path in info_examples(ROOT / "docs" / "screenshots" / "B7" / "beispiele"):
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
