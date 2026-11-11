"""Interface shared by all image generation backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import JsonValue

from app.core.image_formats import ImageFormat


class ImageError(Exception):
    """Base class for errors of the image layer."""


class ImageProviderError(ImageError):
    """The provider could not be reached, refused or returned no usable image."""


class ImageGenerationDisabledError(ImageError):
    """`IMAGE_GENERATION_ENABLED` is off: no image may be generated."""


class ImageBudgetExceededError(ImageError):
    """The cost brake (per post or per day) does not allow another generation."""


@dataclass(frozen=True)
class GeneratedImage:
    """One variant as returned by a provider (PNG or JPEG bytes)."""

    content: bytes
    media_type: str


@dataclass(frozen=True)
class ImageBatch:
    """All variants of one generation plus the metadata needed for the audit trail."""

    images: list[GeneratedImage]
    model: str
    params: dict[str, JsonValue] = field(default_factory=dict)
    # Estimated cost in EUR of this generation (all variants), for the audit and the brake.
    cost_estimate_eur: float = 0.0


class ImageProvider(ABC):
    """An image generation backend. It only transports prompt and bytes; checks live in
    the calling service (prompt rules, cost brake, audit)."""

    name: str

    @abstractmethod
    def generate(self, *, prompt: str, fmt: ImageFormat, count: int) -> ImageBatch:
        """Generate `count` variants for `prompt` in (roughly) the aspect ratio `fmt`."""
