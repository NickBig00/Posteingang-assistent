"""Building blocks for module LLM clients: timed provider call, outcome record, strict parsing."""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import JsonValue, ValidationError

from app.core.llm.base import Completion, LLMError, LLMProvider, LLMTask, VisualInput
from app.core.llm.prompts import RenderedPrompt


@dataclass
class LLMCall[T]:
    """Outcome of one LLM call. Errors are captured, not raised, so callers can retry/audit."""

    task: LLMTask
    prompt_id: str
    prompt: RenderedPrompt
    duration_ms: int
    completion: Completion | None = None
    output: T | None = None
    error: str | None = None
    # SHA-256 of an image/PDF sent along (OCR); part of the hashed audit input.
    visual_hash: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.output is not None

    @property
    def prompt_text(self) -> str:
        """Full input sent to the model (hashed for the audit trail, never stored)."""
        text = f"{self.prompt.system}\n\n{self.prompt.user}"
        return f"{text}\n\n[visual sha256={self.visual_hash}]" if self.visual_hash else text


def parse_json_object(text: str) -> dict[str, JsonValue]:
    # Invalid output is not repaired (CLAUDE.md): it must be a bare JSON object.
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def timed_complete(
    provider: LLMProvider,
    task: LLMTask,
    prompt: RenderedPrompt,
    visuals: Sequence[VisualInput] = (),
) -> tuple[Completion | None, str | None, int]:
    """Call the provider once; return completion, error text and duration in milliseconds."""
    started = time.perf_counter()
    try:
        completion = provider.complete(
            task=task, system=prompt.system, user=prompt.user, visuals=visuals
        )
        error = None
    except LLMError as exc:
        completion, error = None, f"{type(exc).__name__}: {exc}"
    duration_ms = round((time.perf_counter() - started) * 1000)
    return completion, error, duration_ms


def short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
        )
    return str(exc)
