"""Deterministic offline provider for tests and the demo without API key.

Scripted mode returns predefined answers in order. Heuristic mode reads the rendered
prompt like a model would (document types, fields, document) and answers with simple,
reproducible rules: keyword matching for classification, profile patterns and allowed
values for extraction, and the given template for drafts.
"""

import json
import re
from collections import deque
from collections.abc import Callable, Iterable, Sequence

from app.core.llm.base import Completion, LLMProvider, LLMProviderError, LLMTask, VisualInput
from app.core.llm.prompts import NO_TEXT_MARKER

Responder = Callable[[LLMTask, str, str], str]


def prompt_section(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", text, re.DOTALL)
    return match.group(1) if match else ""


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, responses: Iterable[str] | Responder | None = None) -> None:
        self._responder: Responder | None = None
        self._queue: deque[str] | None = None
        if callable(responses):
            self._responder = responses
        elif responses is not None:
            self._queue = deque(responses)
        self.calls: list[tuple[LLMTask, str, str]] = []
        self.visuals: list[tuple[VisualInput, ...]] = []

    @property
    def model(self) -> str:
        return (
            "fake-heuristic-v1"
            if self._queue is None and self._responder is None
            else "fake-scripted"
        )

    def complete(
        self,
        *,
        task: LLMTask,
        system: str,
        user: str,
        visuals: Sequence[VisualInput] = (),
    ) -> Completion:
        self.calls.append((task, system, user))
        self.visuals.append(tuple(visuals))
        if self._responder is not None:
            text = self._responder(task, system, user)
        elif self._queue is not None:
            if not self._queue:
                raise LLMProviderError("fake provider has no scripted response left")
            text = self._queue.popleft()
        else:
            heuristic = _HEURISTICS.get(task)
            if heuristic is None:
                raise LLMProviderError(f"fake provider has no heuristic for task {task!r}")
            text = heuristic(user)
        return Completion(text=text, model=self.model, params={"provider": self.name})


def _classify(user: str) -> str:
    document = prompt_section(user, "dokument").lower()
    fallback = prompt_section(user, "rueckfall").strip()
    best_type, best_score = fallback, 0
    for doc_type in json.loads(prompt_section(user, "dokumenttypen") or "[]"):
        score = sum(document.count(keyword.lower()) for keyword in doc_type["hinweise"])
        if score > best_score:
            best_type, best_score = doc_type["typ"], score
    confidence = 0.8 if best_score == 0 else min(0.95, 0.6 + 0.1 * best_score)
    return json.dumps({"document_type": best_type, "confidence": round(confidence, 2)})


def _extract(user: str) -> str:
    document = prompt_section(user, "dokument")
    result: dict[str, str | list[str] | None] = {}
    for field in json.loads(prompt_section(user, "felder") or "[]"):
        value: str | None = None
        if "muster" in field:
            match = re.search(field["muster"], document)
            value = match.group(0) if match else None
        elif "erlaubte_werte" in field:
            lowered = document.lower()
            value = next((v for v in field["erlaubte_werte"] if v.lower() in lowered), None)
        result[field["name"]] = value
    # The heuristic does not detect further requests.
    result["weitere_anliegen"] = []
    return json.dumps(result, ensure_ascii=False)


def _draft(user: str) -> str:
    return prompt_section(user, "vorlage")


def _ocr(user: str) -> str:
    # The offline fake cannot read images: it reports that no text was recognised.
    return NO_TEXT_MARKER


_HEURISTICS: dict[LLMTask, Callable[[str], str]] = {
    "classify": _classify,
    "extract": _extract,
    "draft": _draft,
    "ocr": _ocr,
}


def register_heuristic(task: LLMTask, heuristic: Callable[[str], str]) -> None:
    """Let a module add the offline answer for its own task (reads the rendered user prompt)."""
    _HEURISTICS[task] = heuristic
