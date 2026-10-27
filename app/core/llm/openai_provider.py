"""LLM provider backed by the OpenAI API (Chat Completions).

The SDK is imported only here. The API key is read by the SDK from the environment
(`OPENAI_API_KEY`); it never appears in code or configuration files. Like every
provider it only transports text: parsing, Pydantic validation and the single retry
happen in `LLMClient` / the pipeline, identically for all providers.
"""

import base64
import os
from collections.abc import Sequence
from typing import Protocol

import openai

from app.core.llm.base import (
    Completion,
    LLMProvider,
    LLMProviderError,
    LLMRefusalError,
    LLMTask,
    VisualInput,
)

# Tasks whose answer must be a JSON object: JSON mode guarantees syntactically valid
# JSON; the schema itself is still checked strictly against the Pydantic models.
JSON_TASKS: frozenset[LLMTask] = frozenset({"classify", "extract", "post_draft", "post_assess"})
MAX_TOKENS: dict[LLMTask, int] = {
    "classify": 2000,
    "extract": 4000,
    "draft": 8000,
    "ocr": 16000,
    "post_draft": 4000,
    "post_assess": 4000,
}


class OpenAIConfigError(ValueError):
    """The OpenAI provider is not configured completely."""


class _ChatCompletionsAPI(Protocol):
    def create(self, **kwargs: object) -> openai.types.chat.ChatCompletion: ...


def _visual_part(visual: VisualInput) -> dict[str, object]:
    data = base64.standard_b64encode(visual.data).decode("ascii")
    data_url = f"data:{visual.media_type};base64,{data}"
    if visual.media_type == "application/pdf":
        return {"type": "file", "file": {"filename": "dokument.pdf", "file_data": data_url}}
    return {"type": "image_url", "image_url": {"url": data_url}}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self, model: str | None = None, completions_api: _ChatCompletionsAPI | None = None
    ) -> None:
        model = model or os.environ.get("LLM_MODEL", "").strip()
        if not model:
            # No silent default: the model choice is a deliberate (data protection) decision.
            raise OpenAIConfigError("LLM_MODEL muss für LLM_PROVIDER=openai gesetzt sein")
        self.model = model
        self._completions = completions_api or openai.OpenAI().chat.completions

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
            content = [*(_visual_part(v) for v in visuals), {"type": "text", "text": user}]
        request: dict[str, object] = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if task in JSON_TASKS:
            request["response_format"] = {"type": "json_object"}
        try:
            response = self._completions.create(**request)
        except openai.APIConnectionError as error:
            raise LLMProviderError("OpenAI API not reachable") from error
        except openai.APIStatusError as error:
            raise LLMProviderError(f"OpenAI API error (HTTP {error.status_code})") from error

        if not response.choices:
            raise LLMProviderError("OpenAI API returned no choices")
        choice = response.choices[0]
        if choice.message.refusal or choice.finish_reason == "content_filter":
            raise LLMRefusalError("model declined the request")
        return Completion(
            text=choice.message.content or "",
            # The model that actually answered (may be a dated snapshot of the requested one).
            model=response.model,
            params={
                "provider": self.name,
                "requested_model": self.model,
                "max_completion_tokens": max_tokens,
                "response_format": "json_object" if task in JSON_TASKS else "text",
                "finish_reason": choice.finish_reason,
            },
        )
