"""Shared drawing helpers for graphics with text (SPEC_social 14).

The former "Grafik aus Vorlage" went into the info graphic (`app.social.infographic`, "Text aufs
Bild"); what is left here are fonts, line wrapping, text colour and the choice of facts.
"""

from collections.abc import Iterable

from PIL import ImageDraw, ImageFont

from app.core.image_formats import FONT_FILES
from app.core.theme import WHITE, contrast_ratio
from app.social.facts import FACT_KEYS, BriefingFacts, FactKey

DEFAULT_PRIMARY = "#0f766e"
DEFAULT_ACCENT = "#f59e0b"


class GraphicLayoutError(RuntimeError):
    """A line would not fit on the graphic (never cut silently)."""


def parse_fact_keys(values: Iterable[str], facts: BriefingFacts) -> tuple[FactKey, ...]:
    """Facts chosen for the graphic: known keys the briefing has, in canonical order.

    No valid choice at all means all facts (a graphic never loses its facts by accident).
    """
    chosen = {value for value in values}
    keys = tuple(k for k in FACT_KEYS if k in chosen and facts.has(k))
    return keys or tuple(facts.present_keys())


def select_facts(facts: BriefingFacts, keys: Iterable[FactKey]) -> BriefingFacts:
    """Only the chosen facts (the fact check then expects exactly these)."""
    wanted = set(keys)
    data = {
        name: value
        for name, value in facts.to_json().items()
        if (name.removesuffix("_von").removesuffix("_bis") if name.startswith("zeitraum") else name)
        in wanted
    }
    return BriefingFacts.from_json(data)


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILES[weight]), size)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _text_colour(background: tuple[int, int, int]) -> tuple[int, int, int]:
    ink = (15, 23, 42)
    return WHITE if contrast_ratio(background, WHITE) >= contrast_ratio(background, ink) else ink
