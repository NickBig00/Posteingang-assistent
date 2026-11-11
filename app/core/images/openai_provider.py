"""Image provider backed by the OpenAI Images API.

The SDK is imported only here (and in `app/core/llm/`). The API key comes from the
environment (`OPENAI_API_KEY`). The model is a deliberate choice: `IMAGE_MODEL` must be set.
"""

import base64
import os
from typing import Protocol

import openai

from app.core.image_formats import ImageFormat
from app.core.images.base import GeneratedImage, ImageBatch, ImageProvider, ImageProviderError

# Closest supported sizes; the JPEG export crops to the exact format afterwards.
SIZES: dict[ImageFormat, str] = {
    ImageFormat.SQUARE: "1024x1024",
    ImageFormat.PORTRAIT: "1024x1536",
    ImageFormat.STORY: "1024x1536",
}
QUALITY = "medium"


class OpenAIImageConfigError(ValueError):
    """The OpenAI image provider is not configured completely."""


class _ImagesAPI(Protocol):
    def generate(self, **kwargs: object) -> object: ...


def cost_per_image_eur() -> float:
    """Estimated cost of one image in EUR (env `IMAGE_COST_PER_IMAGE_EUR`, default 0.05)."""
    raw = (os.environ.get("IMAGE_COST_PER_IMAGE_EUR") or "").strip()
    try:
        return float(raw.replace(",", ".")) if raw else 0.05
    except ValueError:
        return 0.05


class OpenAIImageProvider(ImageProvider):
    name = "openai"

    def __init__(self, model: str | None = None, images_api: _ImagesAPI | None = None) -> None:
        model = model or (os.environ.get("IMAGE_MODEL") or "").strip()
        if not model:
            raise OpenAIImageConfigError("IMAGE_MODEL muss für IMAGE_PROVIDER=openai gesetzt sein")
        self.model = model
        self._images = images_api or openai.OpenAI().images

    def generate(self, *, prompt: str, fmt: ImageFormat, count: int) -> ImageBatch:
        size = SIZES[fmt]
        try:
            response = self._images.generate(
                model=self.model, prompt=prompt, n=count, size=size, quality=QUALITY
            )
        except openai.OpenAIError as error:
            raise ImageProviderError(
                f"Bildanbieter nicht erreichbar: {type(error).__name__}"
            ) from error
        images = []
        for item in getattr(response, "data", None) or []:
            data = getattr(item, "b64_json", None)
            if data:
                images.append(GeneratedImage(base64.b64decode(data), "image/png"))
        if not images:
            raise ImageProviderError("Der Bildanbieter hat kein Bild geliefert.")
        return ImageBatch(
            images=images,
            model=self.model,
            params={"provider": "openai", "size": size, "quality": QUALITY, "count": count},
            cost_estimate_eur=round(cost_per_image_eur() * len(images), 4),
        )
