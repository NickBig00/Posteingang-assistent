"""Inbox LLM operations: render prompt, call provider, validate output."""

import hashlib
import json

from pydantic import BaseModel, ValidationError

from app.core.llm.base import LLMProvider, LLMTask, VisualInput
from app.core.llm.calls import LLMCall, parse_json_object, short_error, timed_complete
from app.core.llm.prompts import NO_TEXT_MARKER, PromptTemplate, load_prompt
from app.inbox.llm.schemas import ClassificationOutput, extraction_model
from app.inbox.profiles import DocumentType, Profile


class LLMClient:
    def __init__(
        self, provider: LLMProvider, prompts: dict[LLMTask, PromptTemplate] | None = None
    ) -> None:
        self.provider = provider
        self.prompts = prompts or {
            task: load_prompt(task) for task in ("classify", "extract", "draft", "ocr")
        }

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
        completion, error, duration = timed_complete(self.provider, "classify", prompt)
        call: LLMCall[ClassificationOutput] = LLMCall(
            "classify", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is None:
            return call
        try:
            output = ClassificationOutput.model_validate(parse_json_object(completion.text))
        except (ValueError, ValidationError) as exc:
            call.error = f"invalid output: {short_error(exc)}"
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
        completion, error, duration = timed_complete(self.provider, "extract", prompt)
        call: LLMCall[BaseModel] = LLMCall(
            "extract", template.prompt_id, prompt, duration, completion, error=error
        )
        if completion is None:
            return call
        try:
            call.output = extraction_model(doc_type).model_validate(
                parse_json_object(completion.text)
            )
        except (ValueError, ValidationError) as exc:
            call.error = f"invalid output: {short_error(exc)}"
        return call

    def draft(self, template_text: str, text: str, profile: Profile) -> LLMCall[str]:
        template = self.prompts["draft"]
        prompt = template.render(profile_name=profile.name, template_text=template_text, text=text)
        completion, error, duration = timed_complete(self.provider, "draft", prompt)
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
        completion, error, duration = timed_complete(self.provider, "ocr", prompt, (visual,))
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
