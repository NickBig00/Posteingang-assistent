"""Building blocks for module LLM clients: timed call, outcome record, parsing, audit, retry."""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.core.audit import write_audit_entry
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


# One retry after an invalid or failed LLM answer, then manual review (CLAUDE.md).
MAX_ATTEMPTS = 2


def audit_llm_call(
    session: Session,
    call: LLMCall,
    *,
    attempt: int,
    provider: str,
    item_id: int | None = None,
    step: str | None = None,
    context: Mapping[str, JsonValue] | None = None,
) -> None:
    """Write the immutable audit entry of one LLM call (also for failed calls)."""
    completion = call.completion
    params: dict[str, JsonValue] = dict(completion.params) if completion else {"provider": provider}
    params["attempt"] = attempt
    params.update(context or {})
    write_audit_entry(
        session,
        item_id=item_id,
        step=step or call.task,
        model=completion.model if completion else provider,
        model_params=params,
        prompt_version=call.prompt_id,
        input_text=call.prompt_text,
        output=completion.text if completion else None,
        duration_ms=call.duration_ms,
        error=call.error,
    )


def call_with_retry[T](
    attempt_call: Callable[[], LLMCall[T]], record: Callable[[LLMCall[T], int], None]
) -> LLMCall[T]:
    """Run `attempt_call` and retry once if it fails; `record(call, attempt)` audits each try."""
    call = attempt_call()
    record(call, 1)
    for attempt in range(2, MAX_ATTEMPTS + 1):
        if call.ok:
            break
        call = attempt_call()
        record(call, attempt)
    return call
