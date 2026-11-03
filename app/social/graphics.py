"""Graphics from templates (SPEC_social 14): deterministic, rendered by code, no LLM.

Background (own photo, chosen AI image or colour fill) plus brand name, brand colours, font
and the facts of the briefing. The text of the graphic is built from the structured facts and
checked like the fact check of the posts before anything is rendered: every fact exactly once
in its display spelling, no other date, time, price or phone number.
"""

from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from app.core.image_formats import (
    FONT_FILES,
    FORMAT_SPECS,
    ImageFormat,
    fit_to_format,
    open_image,
    to_jpeg,
)
from app.core.outcomes import Outcome
from app.core.theme import WHITE, contrast_ratio, mix, parse_hex
from app.social.checks import check_facts, check_forbidden_terms
from app.social.facts import FACT_LABELS, BriefingFacts, FactKey, formatted_facts
from app.social.models import Briefing
from app.social.profiles import SocialConfig
from app.social.textscan import find_dates, find_phones, find_prices, find_times, phone_digits

Layout = Literal["ankuendigung", "angebot", "hinweis", "standard"]

# Template per occasion type; occasion types not listed use the neutral standard layout.
OCCASION_LAYOUTS: dict[str, Layout] = {
    "kurs": "ankuendigung",
    "veranstaltung": "ankuendigung",
    "probetraining": "ankuendigung",
    "aktion": "angebot",
    "oeffnungszeiten": "hinweis",
    "notdienst": "hinweis",
    "saisonhinweis": "hinweis",
}
LAYOUT_LABELS: dict[Layout, str] = {
    "ankuendigung": "Ankündigung",
    "angebot": "Aktion mit Preis",
    "hinweis": "Hinweis",
    "standard": "Standard",
}
# Order of the facts per layout: the most important fact first (shown largest).
FACT_ORDER: dict[Layout, tuple[FactKey, ...]] = {
    "ankuendigung": ("datum", "uhrzeit", "ort", "preis", "zeitraum", "telefon"),
    "angebot": ("preis", "zeitraum", "datum", "uhrzeit", "ort", "telefon"),
    "hinweis": ("telefon", "zeitraum", "datum", "uhrzeit", "ort", "preis"),
    "standard": ("datum", "uhrzeit", "zeitraum", "ort", "preis", "telefon"),
}
MAX_HEADLINE = 40
DEFAULT_PRIMARY = "#0f766e"
DEFAULT_ACCENT = "#f59e0b"


class GraphicError(ValueError):
    """The graphic text failed the fact check or the input is invalid."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class GraphicText:
    """Everything written on the graphic, line by line (what the fact check sees)."""

    layout: Layout
    brand: str  # brand name as text; empty when the logo is shown instead
    tag: str
    headline: str
    facts: list[tuple[str, str]]  # (label, value in display spelling)

    logo: bool = False

    @property
    def lines(self) -> list[str]:
        head = [self.brand] if self.brand else []
        return [*head, self.tag, self.headline, *(f"{k}: {v}" for k, v in self.facts)]

    @property
    def full_text(self) -> str:
        return "\n".join(line for line in self.lines if line)


def layout_for(occasion_key: str) -> Layout:
    return OCCASION_LAYOUTS.get(occasion_key, "standard")


def graphic_text(config: SocialConfig, briefing: Briefing, headline: str = "") -> GraphicText:
    """Text of the graphic from the structured facts (never from the AI draft)."""
    layout = layout_for(briefing.occasion_type)
    facts = formatted_facts(BriefingFacts.from_json(briefing.facts))
    ordered = [(FACT_LABELS[k], facts[k]) for k in FACT_ORDER[layout] if k in facts]
    occasion = config.industry.occasion_type(briefing.occasion_type).label
    logo = config.logo_path is not None
    return GraphicText(
        layout=layout,
        brand="" if logo else config.brand.name,
        logo=logo,
        tag=occasion,
        headline=" ".join(headline.split()) or occasion,
        facts=ordered,
    )


def verify_graphic_text(text: GraphicText, facts: BriefingFacts, config: SocialConfig) -> None:
    """Fact check of the graphic text (analogous to the posts); raises `GraphicError`."""
    problems: list[str] = []
    if len(text.headline) > MAX_HEADLINE:
        problems.append(f"Die Überschrift ist zu lang (höchstens {MAX_HEADLINE} Zeichen).")
    full = text.full_text
    problems += [f.reason for f in check_facts(full, facts) if f.outcome is Outcome.ERROR]
    problems += [
        f.reason
        for f in check_forbidden_terms(full, config.brand.forbidden_terms)
        if f.outcome is Outcome.ERROR
    ]
    dates = [d for d in (facts.datum, facts.zeitraum_von, facts.zeitraum_bis) if d]
    for mention in find_dates(full):
        if not any(mention.matches(d) for d in dates):
            problems.append(f"Datum {mention.label} steht nicht im Briefing.")
    for mention in find_times(full):
        if mention.value != facts.uhrzeit:
            problems.append(f"Uhrzeit {mention.value:%H:%M} steht nicht im Briefing.")
    for mention in find_prices(full):
        if mention.value != facts.preis:
            problems.append(f"Preis {mention.value} € steht nicht im Briefing.")
    allowed_phone = phone_digits(facts.telefon) if facts.telefon else None
    for mention in find_phones(full):
        if mention.digits != allowed_phone:
            problems.append("Telefonnummer steht nicht im Briefing.")
    for key, value in formatted_facts(facts).items():
        if text.lines.count(f"{FACT_LABELS[key]}: {value}") != 1:
            problems.append(f"{FACT_LABELS[key]} muss genau einmal in der Grafik stehen.")
    if problems:
        raise GraphicError(list(dict.fromkeys(problems)))


# --- rendering ---


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


def _fit(
    draw: ImageDraw.ImageDraw, text: str, weight: str, size: int, width: int, max_lines: int
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size (down from `size`) at which `text` fits into `max_lines` lines."""
    while size > 18:
        font = _font(weight, size)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines and all(draw.textlength(x, font=font) <= width for x in lines):
            return font, lines
        size -= 4
    font = _font(weight, size)
    return font, _wrap(draw, text, font, width)


class GraphicLayoutError(RuntimeError):
    """A line would not fit on the graphic (never cut silently)."""


def _ensure_inside(
    lines: list[tuple[str, ImageFont.FreeTypeFont]], draw: ImageDraw.ImageDraw, width: int
) -> None:
    for line, font in lines:
        if draw.textlength(line, font=font) > width:
            raise GraphicLayoutError(f"Zeile passt nicht auf die Grafik: {line}")


def _text_colour(background: tuple[int, int, int]) -> tuple[int, int, int]:
    ink = (15, 23, 42)
    return WHITE if contrast_ratio(background, WHITE) >= contrast_ratio(background, ink) else ink


def render_graphic(
    config: SocialConfig,
    briefing: Briefing,
    fmt: ImageFormat,
    *,
    headline: str = "",
    background: bytes | None = None,
) -> tuple[bytes, GraphicText]:
    """Check the text, then render the graphic as JPEG in the exact size of `fmt`."""
    text = graphic_text(config, briefing, headline)
    verify_graphic_text(text, BriefingFacts.from_json(briefing.facts), config)
    design = config.brand.design
    primary = parse_hex(design.primary_color if design else DEFAULT_PRIMARY)
    accent = parse_hex(design.accent_color if design else DEFAULT_ACCENT)
    spec = FORMAT_SPECS[fmt]
    width, height = spec.width, spec.height
    margin = round(width * 0.075)

    if background is not None:
        canvas = fit_to_format(open_image(background), fmt).convert("RGBA")
        shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(shade)
        start = height * 0.35
        for y in range(int(start), height):
            alpha = int(205 * (y - start) / (height - start))
            shade_draw.line([(0, y), (width, y)], fill=(*mix(primary, (0, 0, 0), 0.55), alpha))
        canvas = Image.alpha_composite(canvas, shade)
        panel = mix(primary, (0, 0, 0), 0.55)
    else:
        canvas = Image.new("RGBA", (width, height), (*primary, 255))
        # Decoration only in the upper half, so the text area keeps a plain background.
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        deco = ImageDraw.Draw(layer)
        r = width * 0.5
        deco.ellipse((width - r * 1.1, -r * 0.7, width + r * 0.6, r * 0.9), fill=(*WHITE, 26))
        deco.ellipse(
            (width - r * 0.75, r * 0.25, width - r * 0.25, r * 0.75),
            outline=(*accent, 200),
            width=max(4, round(width * 0.012)),
        )
        canvas = Image.alpha_composite(canvas, layer)
        panel = primary
    colour = _text_colour(panel)
    draw = ImageDraw.Draw(canvas)
    inner = width - 2 * margin

    # Top: the logo from the brand profile, otherwise the brand name with an accent bar.
    if text.logo and config.logo_path is not None:
        logo = open_image(config.logo_path.read_bytes()).convert("RGBA")
        logo.thumbnail((round(inner * 0.5), round(width * 0.09)), Image.Resampling.LANCZOS)
        canvas.alpha_composite(logo, (margin, margin))
        draw = ImageDraw.Draw(canvas)
    else:
        brand_font = _font("semibold", round(width * 0.036))
        draw.rectangle(
            (margin, margin, margin + round(width * 0.012), margin + brand_font.size), fill=accent
        )
        brand_colour = colour if background is None else WHITE
        draw.text(
            (margin + round(width * 0.03), margin - 2),
            text.brand,
            font=brand_font,
            fill=brand_colour,
        )

    # Bottom block: tag, headline, facts – laid out from the bottom up.
    fact_font = _font("semibold", round(width * 0.042))
    big_font = _font("bold", round(width * 0.075))
    tag_font = _font("bold", round(width * 0.03))
    head_font, head_lines = _fit(draw, text.headline, "bold", round(width * 0.1), inner, 3)

    # Facts are never cut: a line shrinks to fit, and wraps if it is still too wide.
    blocks: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for index, (label, value) in enumerate(text.facts):
        prominent = index == 0 and text.layout in ("angebot", "hinweis")
        size = big_font.size if prominent else fact_font.size
        font, lines = _fit(
            draw,
            f"{label}: {value}",
            "bold" if prominent else "semibold",
            size,
            inner,
            1 if prominent else 2,
        )
        if prominent and len(lines) > 1:
            font, lines = _fit(draw, f"{label}: {value}", "semibold", fact_font.size, inner, 3)
        blocks += [(line, font) for line in lines]
    _ensure_inside([(line, font) for line, font in blocks], draw, inner)
    _ensure_inside([(line, head_font) for line in head_lines], draw, inner)
    gap = round(width * 0.018)
    # Stories: keep clear of the platform's reply bar at the bottom.
    y = height - margin - (round(height * 0.12) if fmt is ImageFormat.STORY else 0)
    for line, font in reversed(blocks):
        y -= font.size + gap
        draw.text((margin, y), line, font=font, fill=colour if background is None else WHITE)
    y -= gap * 2
    for line in reversed(head_lines):
        y -= head_font.size + gap // 2
        draw.text((margin, y), line, font=head_font, fill=colour if background is None else WHITE)
    y -= gap * 2 + tag_font.size + gap
    tag_width = draw.textlength(text.tag.upper(), font=tag_font)
    pad = round(width * 0.015)
    draw.rounded_rectangle(
        (margin, y - pad, margin + tag_width + 2 * pad, y + tag_font.size + pad),
        radius=pad,
        fill=accent,
    )
    draw.text((margin + pad, y - 2), text.tag.upper(), font=tag_font, fill=_text_colour(accent))
    return to_jpeg(canvas.convert("RGB")), text
