"""Deterministic offline provider for tests and the demo without API key.

Scripted mode returns predefined answers in order. Heuristic mode reads the rendered
prompt like a model would (document types, fields, document) and answers with simple,
reproducible rules: keyword matching for classification, profile patterns and allowed
values for extraction, and the given template for drafts.
"""

import json
import re
from collections import deque
from collections.abc import Callable, Iterable

from app.llm.base import Completion, LLMProvider, LLMProviderError, LLMTask

Responder = Callable[[LLMTask, str, str], str]


def _section(text: str, tag: str) -> str:
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

    @property
    def model(self) -> str:
        return (
            "fake-heuristic-v1"
            if self._queue is None and self._responder is None
            else "fake-scripted"
        )

    def complete(self, *, task: LLMTask, system: str, user: str) -> Completion:
        self.calls.append((task, system, user))
        if self._responder is not None:
            text = self._responder(task, system, user)
        elif self._queue is not None:
            if not self._queue:
                raise LLMProviderError("fake provider has no scripted response left")
            text = self._queue.popleft()
        else:
            text = _HEURISTICS[task](user)
        return Completion(text=text, model=self.model, params={"provider": self.name})


def _classify(user: str) -> str:
    document = _section(user, "dokument").lower()
    fallback = _section(user, "rueckfall").strip()
    best_type, best_score = fallback, 0
    for doc_type in json.loads(_section(user, "dokumenttypen") or "[]"):
        score = sum(document.count(keyword.lower()) for keyword in doc_type["hinweise"])
        if score > best_score:
            best_type, best_score = doc_type["typ"], score
    confidence = 0.8 if best_score == 0 else min(0.95, 0.6 + 0.1 * best_score)
    return json.dumps({"document_type": best_type, "confidence": round(confidence, 2)})


def _extract(user: str) -> str:
    document = _section(user, "dokument")
    result: dict[str, str | None] = {}
    for field in json.loads(_section(user, "felder") or "[]"):
        value: str | None = None
        if "muster" in field:
            match = re.search(field["muster"], document)
            value = match.group(0) if match else None
        elif "erlaubte_werte" in field:
            lowered = document.lower()
            value = next((v for v in field["erlaubte_werte"] if v.lower() in lowered), None)
        result[field["name"]] = value
    return json.dumps(result, ensure_ascii=False)


def _draft(user: str) -> str:
    return _section(user, "vorlage")


_HEURISTICS: dict[LLMTask, Callable[[str], str]] = {
    "classify": _classify,
    "extract": _extract,
    "draft": _draft,
}
