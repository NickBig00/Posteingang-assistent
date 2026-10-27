"""Selecting the LLM provider via environment variable `LLM_PROVIDER`."""

import os

from app.core.llm.base import LLMProvider
from app.core.llm.fake import FakeProvider

PROVIDERS = ("fake", "anthropic", "openai")


def get_provider(name: str | None = None) -> LLMProvider:
    """Return the configured provider. Default is the offline `fake` provider."""
    name = (name or os.environ.get("LLM_PROVIDER") or "fake").strip().lower()
    if name == "fake":
        return FakeProvider()
    if name == "anthropic":
        from app.core.llm.anthropic_provider import AnthropicProvider  # noqa: PLC0415 - optional backend

        return AnthropicProvider()
    if name == "openai":
        from app.core.llm.openai_provider import OpenAIProvider  # noqa: PLC0415 - optional backend

        return OpenAIProvider()
    raise ValueError(f"unknown LLM provider {name!r}; expected one of: {', '.join(PROVIDERS)}")
