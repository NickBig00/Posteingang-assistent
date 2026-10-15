"""Provider interface shared by all LLM implementations."""

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

LLMTask = Literal["classify", "extract", "draft"]


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
    def complete(self, *, task: LLMTask, system: str, user: str) -> Completion:
        """Send one system + user prompt and return the model's text answer."""
