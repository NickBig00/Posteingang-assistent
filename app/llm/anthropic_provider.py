"""LLM provider backed by the Anthropic Claude API.

The SDK is imported only here. The API key is read by the SDK from the environment
(`ANTHROPIC_API_KEY`); it never appears in code or configuration files.
"""

import os
from typing import Protocol

import anthropic

from app.llm.base import Completion, LLMProvider, LLMProviderError, LLMRefusalError, LLMTask

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Classification/extraction answers are short JSON; drafts are a few paragraphs.
MAX_TOKENS: dict[LLMTask, int] = {"classify": 2000, "extract": 4000, "draft": 8000}


class _MessagesAPI(Protocol):
    def create(self, **kwargs: object) -> anthropic.types.beta.BetaMessage: ...


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        messages_api: _MessagesAPI | None = None,
    ) -> None:
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("LLM_EFFORT", DEFAULT_EFFORT)
        self._messages = messages_api or anthropic.Anthropic().beta.messages

    def complete(self, *, task: LLMTask, system: str, user: str) -> Completion:
        max_tokens = MAX_TOKENS[task]
        try:
            response = self._messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
                # On a safety decline the API retries on a fallback model in the same call.
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as error:
            raise LLMProviderError("Anthropic API not reachable") from error
        except anthropic.APIStatusError as error:
            raise LLMProviderError(f"Anthropic API error (HTTP {error.status_code})") from error

        if response.stop_reason == "refusal":
            raise LLMRefusalError("model declined the request")
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            model=response.model,
            params={
                "provider": self.name,
                "requested_model": self.model,
                "effort": self.effort,
                "max_tokens": max_tokens,
                "stop_reason": response.stop_reason,
            },
        )
