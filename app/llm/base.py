"""Provider interface shared by all LLM implementations."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

LLMTask = Literal["classify", "extract", "draft", "ocr"]

# Media types a provider may receive as visual input (OCR of images and scanned PDFs).
VISUAL_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "application/pdf"})


@dataclass(frozen=True)
class VisualInput:
    """An image or PDF sent to the model alongside the prompt (only for OCR)."""

    media_type: str
    data: bytes

    def __post_init__(self) -> None:
        if self.media_type not in VISUAL_MEDIA_TYPES:
            raise ValueError(f"unsupported media type for visual input: {self.media_type}")


class Completion(BaseModel):
    """Raw text answer of a provider plus the metadata needed for the audit trail."""

    text: str
    model: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


class LLMError(Exception):
    """Base class for all errors raised by the LLM layer."""


class LLMProviderError(LLMError):
    """The provider could not be reached or returned an error."""


class LLMRefusalError(LLMError):
    """The model declined to answer."""


class LLMProvider(ABC):
    """A language model backend. Implementations only transport text, they never parse it."""

    name: str

    @abstractmethod
    def complete(
        self,
        *,
        task: LLMTask,
        system: str,
        user: str,
        visuals: Sequence[VisualInput] = (),
    ) -> Completion:
        """Send one system + user prompt (plus optional images/PDFs) and return the text answer."""
