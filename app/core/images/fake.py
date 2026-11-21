"""Deterministic offline image provider for tests and the demo: abstract placeholders.

The placeholder shows neither people nor text, like the rules require for AI images.
"""

import hashlib
import io

from PIL import Image, ImageDraw, ImageEnhance

from app.core.image_formats import FORMAT_SPECS, ImageFormat, fit_to_format, open_image
from app.core.images.base import GeneratedImage, ImageBatch, ImageProvider

FAKE_MODEL = "fake-image-v1"
# Small size: the export scales to the exact format anyway.
_SCALE = 4


def _palette(seed: bytes) -> list[tuple[int, int, int]]:
    return [(seed[i], seed[i + 1], seed[i + 2]) for i in range(0, 12, 3)]


def placeholder(prompt: str, fmt: ImageFormat, variant: int) -> bytes:
    spec = FORMAT_SPECS[fmt]
    width, height = spec.width // _SCALE, spec.height // _SCALE
    seed = hashlib.sha256(f"{prompt}|{fmt.value}|{variant}".encode()).digest()
    colors = _palette(seed)
    image = Image.new("RGB", (width, height), colors[0])
    draw = ImageDraw.Draw(image, "RGBA")
    for index in range(1, 4):
        x = seed[12 + index] / 255 * width
        y = seed[16 + index] / 255 * height
        radius = (0.25 + seed[20 + index] / 255 * 0.5) * width
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*colors[index], 110))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def edited(image: bytes, instruction: str, fmt: ImageFormat) -> bytes:
    """Deterministic stand-in for an AI edit: brighter, slightly tinted, in the target format."""
    seed = hashlib.sha256(instruction.encode()).digest()
    picture = fit_to_format(open_image(image), fmt)
    picture = picture.resize((picture.width // _SCALE, picture.height // _SCALE))
    picture = ImageEnhance.Brightness(picture).enhance(1.12 + seed[0] / 255 * 0.1)
    tint = Image.new("RGB", picture.size, (255, 200 + seed[1] % 40, 150 + seed[2] % 60))
    picture = Image.blend(picture, tint, 0.12)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImageProvider(ImageProvider):
    name = "fake"

    def __init__(self, cost_per_image_eur: float = 0.0) -> None:
        self.cost_per_image_eur = cost_per_image_eur
        self.calls: list[str] = []
        self.edits: list[tuple[str, str]] = []

    def generate(self, *, prompt: str, fmt: ImageFormat, count: int) -> ImageBatch:
        self.calls.append(prompt)
        images = [GeneratedImage(placeholder(prompt, fmt, n), "image/png") for n in range(count)]
        return ImageBatch(
            images=images,
            model=FAKE_MODEL,
            params={"provider": "fake", "format": fmt.value, "count": count},
            cost_estimate_eur=round(self.cost_per_image_eur * count, 4),
        )

    def edit(
        self, *, image: bytes, media_type: str, instruction: str, fmt: ImageFormat
    ) -> ImageBatch:
        self.edits.append((instruction, media_type))
        return ImageBatch(
            images=[GeneratedImage(edited(image, instruction, fmt), "image/png")],
            model=FAKE_MODEL,
            params={"provider": "fake", "format": fmt.value, "count": 1, "operation": "edit"},
            cost_estimate_eur=round(self.cost_per_image_eur, 4),
        )
