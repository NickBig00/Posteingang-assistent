"""OpenAI provider – only with a stubbed Chat Completions API, never real API calls.

Model names below are fictitious placeholders.
"""

import base64
import json
from types import SimpleNamespace

import httpx2
import openai
import pytest
from sqlmodel import Session, select

from app.llm.base import LLMProviderError, LLMRefusalError, VisualInput
from app.llm.client import LLMClient
from app.llm.factory import get_provider
from app.llm.openai_provider import OpenAIConfigError, OpenAIProvider
from app.models import AuditEntry, InboxItem, ItemSource, ItemStatus
from app.pipeline import process_item
from app.profiles import PROFILES_DIR, Profile, load_profile

MODEL = "gpt-fiktiv"
SNAPSHOT = "gpt-fiktiv-2026-01-01"  # what the API reports as the model that answered


def _response(
    content: str | None = "ok",
    finish_reason: str = "stop",
    refusal: str | None = None,
    model: str = SNAPSHOT,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(
        model=model, choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


class _StubCompletions:
    """Records requests and replays prepared responses (or raises prepared errors)."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


# --- configuration and factory ---


def test_factory_selects_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", MODEL)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    provider = get_provider()

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == MODEL


def test_model_must_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(OpenAIConfigError, match="LLM_MODEL"):
        OpenAIProvider(completions_api=_StubCompletions())


# --- request building ---


@pytest.mark.parametrize(
    ("task", "json_mode"), [("classify", True), ("extract", True), ("draft", False), ("ocr", False)]
)
def test_request_structure(task: str, json_mode: bool) -> None:
    stub = _StubCompletions(_response())

    OpenAIProvider(MODEL, stub).complete(task=task, system="SYS", user="USER")  # type: ignore[arg-type]

    request = stub.requests[0]
    assert request["model"] == MODEL
    assert request["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert isinstance(request["max_completion_tokens"], int)
    assert ("response_format" in request) is json_mode
    if json_mode:
        assert request["response_format"] == {"type": "json_object"}


def test_visual_inputs_become_image_and_file_parts() -> None:
    stub = _StubCompletions(_response())
    visuals = (VisualInput("image/png", b"img"), VisualInput("application/pdf", b"pdf"))

    OpenAIProvider(MODEL, stub).complete(task="ocr", system="s", user="Übertrage", visuals=visuals)

    content = stub.requests[0]["messages"][1]["content"]  # type: ignore[index]
    png = base64.b64encode(b"img").decode()
    pdf = base64.b64encode(b"pdf").decode()
    assert content[0] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}}
    assert content[1] == {
        "type": "file",
        "file": {"filename": "dokument.pdf", "file_data": f"data:application/pdf;base64,{pdf}"},
    }
    assert content[2] == {"type": "text", "text": "Übertrage"}


# --- responses ---


def test_completion_reports_model_that_actually_answered() -> None:
    stub = _StubCompletions(_response('{"a": 1}'))

    completion = OpenAIProvider(MODEL, stub).complete(task="extract", system="s", user="u")

    assert completion.text == '{"a": 1}'
    assert completion.model == SNAPSHOT
    assert completion.params["requested_model"] == MODEL
    assert completion.params["provider"] == "openai"
    assert completion.params["response_format"] == "json_object"


def test_empty_content_becomes_empty_text() -> None:
    stub = _StubCompletions(_response(content=None))

    assert OpenAIProvider(MODEL, stub).complete(task="draft", system="s", user="u").text == ""


@pytest.mark.parametrize(
    "response",
    [_response(content=None, refusal="Kann ich nicht."), _response(finish_reason="content_filter")],
)
def test_refusals_raise(response: SimpleNamespace) -> None:
    with pytest.raises(LLMRefusalError):
        OpenAIProvider(MODEL, _StubCompletions(response)).complete(
            task="draft", system="s", user="u"
        )


def test_no_choices_is_provider_error() -> None:
    stub = _StubCompletions(SimpleNamespace(model=SNAPSHOT, choices=[]))

    with pytest.raises(LLMProviderError, match="no choices"):
        OpenAIProvider(MODEL, stub).complete(task="draft", system="s", user="u")


def test_api_errors_are_wrapped() -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    stub = _StubCompletions(
        openai.APIConnectionError(request=request),
        openai.APIStatusError(
            "rate limited", response=httpx2.Response(429, request=request), body=None
        ),
    )
    provider = OpenAIProvider(MODEL, stub)

    with pytest.raises(LLMProviderError, match="not reachable"):
        provider.complete(task="draft", system="s", user="u")
    with pytest.raises(LLMProviderError, match="HTTP 429"):
        provider.complete(task="draft", system="s", user="u")


# --- end to end through client and pipeline (validation, retry, audit) ---


def test_pipeline_with_openai_validates_retries_and_audits(
    session: Session, profile: Profile
) -> None:
    fields = {
        "name": "Erika Beispiel",
        "mitgliedsnummer": "M-10001",
        "eingangsdatum": "2026-09-14",
        "gewuenschtes_kuendigungsdatum": None,
    }
    stub = _StubCompletions(
        _response('{"document_type": "kuendigung", "confidence": 1.7}'),  # invalid → retry
        _response('{"document_type": "kuendigung", "confidence": 0.9}'),
        _response(json.dumps(fields)),
    )
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text="Ich kündige.")
    session.add(item)
    session.commit()
    session.refresh(item)

    result = process_item(session, item, profile, LLMClient(OpenAIProvider(MODEL, stub)))

    # No member data seeded → no reply template → no draft call; item processed.
    assert result.status == ItemStatus.PROCESSED
    entries = session.exec(select(AuditEntry).order_by(AuditEntry.id)).all()  # type: ignore[arg-type]
    assert [(e.step, e.model_params["attempt"]) for e in entries] == [
        ("classify", 1),
        ("classify", 2),
        ("extract", 1),
    ]
    assert all(e.model == SNAPSHOT for e in entries)
    assert all(e.model_params["requested_model"] == MODEL for e in entries)
    assert entries[0].error is not None and "confidence" in entries[0].error


def test_pipeline_with_openai_twice_invalid_needs_manual_review(
    session: Session, profile: Profile
) -> None:
    stub = _StubCompletions(_response("kein JSON"), _response("[]"))
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text="Text")
    session.add(item)
    session.commit()
    session.refresh(item)

    result = process_item(session, item, profile, LLMClient(OpenAIProvider(MODEL, stub)))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert len(stub.requests) == 2
