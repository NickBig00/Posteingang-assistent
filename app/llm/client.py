"""High-level LLM operations: render prompt, call provider, validate output."""

import hashlib
import json
import time
from dataclasses import dataclass

from pydantic import BaseModel, JsonValue, ValidationError

from app.llm.base import Completion, LLMError, LLMProvider, LLMTask, VisualInput
from app.llm.prompts import NO_TEXT_MARKER, PromptTemplate, RenderedPrompt, load_prompt
from app.llm.schemas import ClassificationOutput, extraction_model
from app.profiles import DocumentType, Profile


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


def _parse_json_object(text: str) -> dict[str, JsonValue]:
    # Invalid output is not repaired (CLAUDE.md): it must be a bare JSON object.
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


class LLMClient:
    def __init__(
        self, provider: LLMProvider, prompts: dict[LLMTask, PromptTemplate] | None = None
    ) -> None:
        self.provider = provider
        self.prompts = prompts or {
            task: load_prompt(task) for task in ("classify", "extract", "draft", "ocr")
        }

    def _call(
        self, task: LLMTask, prompt: RenderedPrompt, visuals: tuple[VisualInput, ...] = ()
    ) -> tuple[Completion | None, str | None, int]:
        started = time.perf_counter()
        try:
            completion = self.provider.complete(
                task=task, system=prompt.system, user=prompt.user, visuals=visuals
            )
            error = None
        except LLMError as exc:
            completion, error = None, f"{type(exc).__name__}: {exc}"
        duration_ms = round((time.perf_counter() - started) * 1000)
        return completion, error, duration_ms

    def classify(self, text: str, profile: Profile) -> LLMCall[ClassificationOutput]:
        template = self.prompts["classify"]
        types = [
            {"typ": t.key, "beschreibung": t.description, "hinweise": t.keywords}
            for t in profile.document_types
        ]
        prompt = template.render(
            profile_name=profile.name,
            fallback_type=profile.fallback_type,
            document_types_json=json.dumps(types, ensure_ascii=False, indent=2),
            text=text,
        )
        completion, error, duration = self._call("classify", prompt)
        call: LLMCall[ClassificationOutput] = LLMCall(
            "classify", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is None:
            return call
        try:
            output = ClassificationOutput.model_validate(_parse_json_object(completion.text))
        except (ValueError, ValidationError) as exc:
            call.error = f"invalid output: {_short(exc)}"
            return call
        if output.document_type not in profile.type_keys:
            call.error = f"invalid output: unknown document type {output.document_type!r}"
            return call
        call.output = output
        return call

    def extract(self, text: str, doc_type: DocumentType) -> LLMCall[BaseModel]:
        template = self.prompts["extract"]
        fields = [
            {
                "name": spec.name,
                "typ": spec.type,
                "beschreibung": spec.description,
                **({"muster": spec.pattern} if spec.pattern else {}),
                **({"erlaubte_werte": spec.allowed_values} if spec.allowed_values else {}),
            }
            for spec in doc_type.fields
        ]
        prompt = template.render(
            type_label=doc_type.label,
            fields_json=json.dumps(fields, ensure_ascii=False, indent=2),
            text=text,
        )
        completion, error, duration = self._call("extract", prompt)
        call: LLMCall[BaseModel] = LLMCall(
            "extract", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is None:
            return call
        try:
            call.output = extraction_model(doc_type).model_validate(
                _parse_json_object(completion.text)
            )
        except (ValueError, ValidationError) as exc:
            call.error = f"invalid output: {_short(exc)}"
        return call

    def draft(self, template_text: str, text: str, profile: Profile) -> LLMCall[str]:
        template = self.prompts["draft"]
        prompt = template.render(profile_name=profile.name, template_text=template_text, text=text)
        completion, error, duration = self._call("draft", prompt)
        call: LLMCall[str] = LLMCall(
            "draft", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is not None:
            if completion.text.strip():
                call.output = completion.text.strip()
            else:
                call.error = "invalid output: empty draft"
        return call

    def ocr(self, visual: VisualInput) -> LLMCall[str]:
        """Transcribe the text of an image or scanned PDF (vision model).

        Output "" means: the model found no readable text. An empty answer is invalid.
        """
        template = self.prompts["ocr"]
        prompt = template.render()
        completion, error, duration = self._call("ocr", prompt, (visual,))
        call: LLMCall[str] = LLMCall(
            "ocr",
            template.prompt_id,
            prompt,
            duration,
            completion,
            error=error,
            visual_hash=hashlib.sha256(visual.data).hexdigest(),
        )
        if completion is not None:
            text = completion.text.strip()
            if text == NO_TEXT_MARKER:
                call.output = ""
            elif text:
                call.output = text
            else:
                call.error = "invalid output: empty transcription"
        return call


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
        )
    return str(exc)
