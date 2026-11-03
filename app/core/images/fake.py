"""Deterministic offline image provider for tests and the demo: abstract placeholders.

The placeholder shows neither people nor text, like the rules require for AI images.
"""

import hashlib
import io

from PIL import Image, ImageDraw

from app.core.image_formats import FORMAT_SPECS, ImageFormat
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


class FakeImageProvider(ImageProvider):
    name = "fake"

    def __init__(self, cost_per_image_eur: float = 0.0) -> None:
        self.cost_per_image_eur = cost_per_image_eur
        self.calls: list[str] = []

    def generate(self, *, prompt: str, fmt: ImageFormat, count: int) -> ImageBatch:
        self.calls.append(prompt)
        images = [GeneratedImage(placeholder(prompt, fmt, n), "image/png") for n in range(count)]
        return ImageBatch(
            images=images,
            model=FAKE_MODEL,
            params={"provider": "fake", "format": fmt.value, "count": count},
            cost_estimate_eur=round(self.cost_per_image_eur * count, 4),
        )
