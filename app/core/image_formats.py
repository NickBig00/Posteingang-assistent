"""Social media image formats and the JPEG export (deterministic, Pillow).

Shared by uploaded photos, AI images and graphics from templates (SPEC_social 12–14).
"""

import io
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PIL import Image, ImageOps

# Fonts shipped with the app (Inter, SIL Open Font License 1.1); used by the UI and graphics.
FONTS_DIR = Path(__file__).parent / "static" / "fonts"
FONT_FILES: dict[str, Path] = {
    "regular": FONTS_DIR / "Inter_400Regular.ttf",
    "semibold": FONTS_DIR / "Inter_600SemiBold.ttf",
    "bold": FONTS_DIR / "Inter_700Bold.ttf",
}

JPEG_QUALITY = 88
# Upper bound against decompression bombs (a 10 MB upload never needs more).
MAX_PIXELS = 40_000_000


class ImageFormat(StrEnum):
    SQUARE = "1:1"
    PORTRAIT = "4:5"
    STORY = "9:16"


@dataclass(frozen=True)
class FormatSpec:
    label: str
    width: int
    height: int


FORMAT_SPECS: dict[ImageFormat, FormatSpec] = {
    ImageFormat.SQUARE: FormatSpec("Quadratisch 1:1", 1080, 1080),
    ImageFormat.PORTRAIT: FormatSpec("Hochformat 4:5", 1080, 1350),
    ImageFormat.STORY: FormatSpec("Story 9:16", 1080, 1920),
}


class ImageExportError(ValueError):
    """Raised for content that is not a readable image."""


def open_image(content: bytes) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (OSError, Image.DecompressionBombError) as error:
        raise ImageExportError("Die Bilddatei ist nicht lesbar.") from error
    return image


def fit_to_format(image: Image.Image, fmt: ImageFormat) -> Image.Image:
    """Scale and centre-crop to the exact pixel size of `fmt` (orientation from EXIF)."""
    spec = FORMAT_SPECS[fmt]
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    return ImageOps.fit(image.convert("RGB"), (spec.width, spec.height), Image.Resampling.LANCZOS)


def to_jpeg(image: Image.Image) -> bytes:
    """Encode as JPEG without any metadata (no EXIF, no GPS, no camera data)."""
    buffer = io.BytesIO()
    clean = Image.new("RGB", image.size)
    clean.paste(image.convert("RGB"))
    clean.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buffer.getvalue()


def export_jpeg(content: bytes, fmt: ImageFormat) -> bytes:
    """Uploaded PNG/JPG → JPEG in the exact size of `fmt`, metadata removed."""
    return to_jpeg(fit_to_format(open_image(content), fmt))
