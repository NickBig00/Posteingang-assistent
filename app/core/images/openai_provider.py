"""Image provider backed by the OpenAI Images API.

The SDK is imported only here (and in `app/core/llm/`). The API key comes from the
environment (`OPENAI_API_KEY`). The model is a deliberate choice: `IMAGE_MODEL` must be set.
"""

import base64
import os
from collections.abc import Iterable
from typing import Protocol

import openai

from app.core.image_formats import ImageFormat
from app.core.images.base import GeneratedImage, ImageBatch, ImageProvider, ImageProviderError
from app.core.provider_errors import (
    ProviderConfigError,
    describe_provider_error,
    require_valid_api_key,
)

SERVICE = "Bildanbieter (OpenAI)"
KEY_VARIABLE = "OPENAI_API_KEY"

# Closest supported sizes; the JPEG export crops to the exact format afterwards.
SIZES: dict[ImageFormat, str] = {
    ImageFormat.SQUARE: "1024x1024",
    ImageFormat.PORTRAIT: "1024x1536",
    ImageFormat.STORY: "1024x1536",
}
QUALITY = "medium"


class OpenAIImageConfigError(ProviderConfigError):
    """The OpenAI image provider is not configured completely."""


class _ImagesAPI(Protocol):
    def generate(self, **kwargs: object) -> object: ...

    def edit(self, **kwargs: object) -> object: ...


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
        if images_api is None:
            require_valid_api_key(KEY_VARIABLE)
            try:
                images_api = openai.OpenAI().images
            except openai.OpenAIError as error:
                raise OpenAIImageConfigError(
                    f"{KEY_VARIABLE} fehlt: für IMAGE_PROVIDER=openai einen API-Schlüssel setzen"
                ) from error
        self._images = images_api

    def generate(self, *, prompt: str, fmt: ImageFormat, count: int) -> ImageBatch:
        size = SIZES[fmt]
        try:
            response = self._images.generate(
                model=self.model, prompt=prompt, n=count, size=size, quality=QUALITY
            )
        except (openai.OpenAIError, UnicodeEncodeError) as error:
            # The real reason (unknown model, billing limit, content filter …), not just
            # "not reachable" – shown in the UI and stored in the audit trail.
            raise ImageProviderError(
                describe_provider_error(error, SERVICE, KEY_VARIABLE)
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

    def edit(
        self, *, image: bytes, media_type: str, instruction: str, fmt: ImageFormat
    ) -> ImageBatch:
        """`images.edit` with the configured model; the photo goes to the provider."""
        size = SIZES[fmt]
        suffix = "png" if media_type == "image/png" else "jpg"
        try:
            response = self._images.edit(
                model=self.model,
                image=(f"foto.{suffix}", image, media_type),
                prompt=instruction,
                n=1,
                size=size,
                quality=QUALITY,
            )
        except (openai.OpenAIError, UnicodeEncodeError) as error:
            raise ImageProviderError(
                describe_provider_error(error, SERVICE, KEY_VARIABLE)
            ) from error
        images = [
            GeneratedImage(base64.b64decode(data), "image/png")
            for item in getattr(response, "data", None) or []
            if (data := getattr(item, "b64_json", None))
        ]
        if not images:
            raise ImageProviderError("Der Bildanbieter hat kein bearbeitetes Bild geliefert.")
        return ImageBatch(
            images=images[:1],
            model=self.model,
            params={
                "provider": "openai",
                "size": size,
                "quality": QUALITY,
                "count": 1,
                "operation": "edit",
            },
            cost_estimate_eur=round(cost_per_image_eur(), 4),
        )


class _ModelsAPI(Protocol):
    def list(self) -> Iterable[object]: ...


def list_image_models(models_api: _ModelsAPI | None = None) -> list[str]:
    """Image models the configured OpenAI account can use (names change over time)."""
    if models_api is None:
        require_valid_api_key(KEY_VARIABLE)
        try:
            models_api = openai.OpenAI().models
        except openai.OpenAIError as error:
            raise OpenAIImageConfigError(f"{KEY_VARIABLE} fehlt") from error
    try:
        ids = [str(getattr(model, "id", "")) for model in models_api.list()]
    except (openai.OpenAIError, UnicodeEncodeError) as error:
        raise ImageProviderError(describe_provider_error(error, SERVICE, KEY_VARIABLE)) from error
    return sorted(i for i in ids if "image" in i.lower())
