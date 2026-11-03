"""Design tokens that depend on configuration: the brand colour and its derived shades.

The brand colour comes from env `BRAND_COLOR` (hex, e.g. `#0f766e`). Everything else in the
design system is static CSS (`app/core/static/src/app.css`, see `docs/DESIGN.md`). Derived
shades are computed deterministically so that text on and in the brand colour always meets
the WCAG AA contrast ratio of 4.5:1.
"""

import os
import re
from dataclasses import dataclass

BRAND_COLOR_VARIABLE = "BRAND_COLOR"
DEFAULT_BRAND_COLOR = "#0f766e"  # teal-700: calm, with good contrast on white
MIN_CONTRAST = 4.5  # WCAG 2.1 AA for normal text

_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

RGB = tuple[int, int, int]


class ThemeConfigError(ValueError):
    """Raised for a brand colour that is not a hex colour."""


def parse_hex(value: str) -> RGB:
    """Parse `#rrggbb` or `#rgb` (the `#` is optional)."""
    match = _HEX.match(value.strip())
    if match is None:
        raise ThemeConfigError(
            f"{BRAND_COLOR_VARIABLE} muss eine Hex-Farbe sein, z. B. #0f766e (war: {value!r})"
        )
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: RGB) -> float:
    """WCAG 2.1 relative luminance."""
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    """WCAG 2.1 contrast ratio between two colours (1 to 21)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: RGB, b: RGB, share_of_b: float) -> RGB:
    """Blend `a` towards `b` by `share_of_b` (0 = a, 1 = b)."""
    return (
        round(a[0] + (b[0] - a[0]) * share_of_b),
        round(a[1] + (b[1] - a[1]) * share_of_b),
        round(a[2] + (b[2] - a[2]) * share_of_b),
    )


WHITE: RGB = (255, 255, 255)
INK: RGB = (15, 23, 42)  # slate-900, the body text colour
BLACK: RGB = (0, 0, 0)


def _shift_until(color: RGB, toward: RGB, against: RGB, minimum: float) -> RGB:
    """Blend `color` towards `toward` until it has `minimum` contrast to `against`."""
    step = 0.0
    candidate = color
    while contrast_ratio(candidate, against) < minimum and step < 1:
        step = round(step + 0.05, 2)
        candidate = mix(color, toward, step)
    return candidate


@dataclass(frozen=True)
class BrandTheme:
    """Brand colour with derived, contrast-safe shades (all as hex)."""

    brand: str  # buttons, active navigation, focus ring
    on_brand: str  # text on `brand` (white or ink, whichever contrasts better)
    brand_strong: str  # hover state of `brand`
    brand_text: str  # brand-coloured text and links on white (≥ 4.5:1)
    brand_soft: str  # light tint for backgrounds (active navigation item)

    def css_variables(self) -> str:
        """`--brand-*` custom properties as space-separated RGB channels (Tailwind format)."""
        names = {
            "--brand": self.brand,
            "--brand-on": self.on_brand,
            "--brand-strong": self.brand_strong,
            "--brand-text": self.brand_text,
            "--brand-soft": self.brand_soft,
        }
        return " ".join(
            f"{name}: {' '.join(str(c) for c in parse_hex(value))};"
            for name, value in names.items()
        )


def build_theme(color: str) -> BrandTheme:
    """Derive the brand theme from one hex colour."""
    base = parse_hex(color)
    on_brand = WHITE if contrast_ratio(base, WHITE) >= contrast_ratio(base, INK) else INK
    # Medium tones: shift the button colour until the text on it is readable.
    toward = BLACK if on_brand == WHITE else WHITE
    base_for_buttons = _shift_until(base, toward, on_brand, MIN_CONTRAST)
    strong_target = BLACK if on_brand == WHITE else WHITE
    return BrandTheme(
        brand=to_hex(base_for_buttons),
        on_brand=to_hex(on_brand),
        brand_strong=to_hex(mix(base_for_buttons, strong_target, 0.18)),
        brand_text=to_hex(_shift_until(base, BLACK, WHITE, MIN_CONTRAST)),
        brand_soft=to_hex(mix(base, WHITE, 0.9)),
    )


def brand_color() -> str:
    """Configured brand colour (env `BRAND_COLOR`, default teal)."""
    return (os.environ.get(BRAND_COLOR_VARIABLE) or "").strip() or DEFAULT_BRAND_COLOR


def current_theme() -> BrandTheme:
    """Theme for the configured brand colour; an invalid value falls back to the default."""
    try:
        return build_theme(brand_color())
    except ThemeConfigError:
        return build_theme(DEFAULT_BRAND_COLOR)
