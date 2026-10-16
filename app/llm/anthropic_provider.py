"""LLM provider backed by the Anthropic Claude API.

The SDK is imported only here. The API key is read by the SDK from the environment
(`ANTHROPIC_API_KEY`); it never appears in code or configuration files.
"""

import base64
import os
from collections.abc import Sequence
from typing import Protocol

import anthropic

from app.llm.base import (
    Completion,
    LLMProvider,
    LLMProviderError,
    LLMRefusalError,
    LLMTask,
    VisualInput,
)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Classification/extraction answers are short JSON; drafts and transcriptions are longer.
MAX_TOKENS: dict[LLMTask, int] = {"classify": 2000, "extract": 4000, "draft": 8000, "ocr": 16000}


class _MessagesAPI(Protocol):
    def create(self, **kwargs: object) -> anthropic.types.beta.BetaMessage: ...


def fallback_enabled_from_env() -> bool:
    """Env `LLM_FALLBACK`: answer refusals with another model. Off unless explicitly enabled."""
    return os.environ.get("LLM_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _visual_block(visual: VisualInput) -> dict[str, object]:
    source = {
        "type": "base64",
        "media_type": visual.media_type,
        "data": base64.standard_b64encode(visual.data).decode("ascii"),
    }
    block_type = "document" if visual.media_type == "application/pdf" else "image"
    return {"type": block_type, "source": source}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        messages_api: _MessagesAPI | None = None,
        fallback: bool | None = None,
    ) -> None:
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("LLM_EFFORT", DEFAULT_EFFORT)
        self.fallback = fallback_enabled_from_env() if fallback is None else fallback
        self._messages = messages_api or anthropic.Anthropic().beta.messages

    def complete(
        self,
        *,
        task: LLMTask,
        system: str,
        user: str,
        visuals: Sequence[VisualInput] = (),
    ) -> Completion:
        max_tokens = MAX_TOKENS[task]
        content: str | list[dict[str, object]] = user
        if visuals:
            # Visual inputs first, then the instruction text.
            content = [*(_visual_block(v) for v in visuals), {"type": "text", "text": user}]
        request: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": self.effort},
        }
        if self.fallback:
            # On a safety decline the API answers with a fallback model in the same call.
            request |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        try:
            response = self._messages.create(**request)
        except anthropic.APIConnectionError as error:
            raise LLMProviderError("Anthropic API not reachable") from error
        except anthropic.APIStatusError as error:
            raise LLMProviderError(f"Anthropic API error (HTTP {error.status_code})") from error

        if response.stop_reason == "refusal":
            raise LLMRefusalError("model declined the request")
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            # The model that actually answered; differs from the requested one on a fallback.
            model=response.model,
            params={
                "provider": self.name,
                "requested_model": self.model,
                "fallback_enabled": self.fallback,
                "fallback_used": response.model != self.model,
                "effort": self.effort,
                "max_tokens": max_tokens,
                "stop_reason": response.stop_reason,
            },
        )
