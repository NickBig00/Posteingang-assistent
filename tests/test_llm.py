import json
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import (
    Completion,
    LLMProvider,
    LLMProviderError,
    LLMRefusalError,
    LLMTask,
    VisualInput,
)
from app.llm.client import LLMClient
from app.llm.factory import get_provider
from app.llm.fake import FakeProvider
from app.llm.prompts import PROMPTS_DIR, PromptError, load_prompt
from app.llm.schemas import extraction_model
from app.profiles import PROFILES_DIR, Profile, load_profile

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


# --- prompts ---


@pytest.mark.parametrize(
    ("task", "version"), [("classify", "v1"), ("extract", "v2"), ("draft", "v1"), ("ocr", "v1")]
)
def test_prompt_files_exist_with_version(task: LLMTask, version: str) -> None:
    template = load_prompt(task)

    assert template.prompt_id == f"{task}_{version}"
    assert (PROMPTS_DIR / f"{task}_{version}.md").is_file()


def test_previous_extract_prompt_stays_loadable() -> None:
    # Older prompt versions stay in the repository: audit entries refer to them.
    assert load_prompt("extract", "v1").prompt_id == "extract_v1"


def test_prompt_render_splits_system_and_user() -> None:
    rendered = load_prompt("draft").render(
        profile_name="Fitnessstudio", template_text="Hallo {{ name }}", text="Dokument"
    )

    assert "Fitnessstudio" in rendered.system
    assert "<vorlage>\nHallo {{ name }}\n</vorlage>" in rendered.user
    assert "{#" not in rendered.system


def test_prompt_render_requires_all_variables() -> None:
    with pytest.raises(Exception, match="undefined"):
        load_prompt("draft").render(profile_name="x")


def test_unknown_prompt_version_is_rejected() -> None:
    with pytest.raises(PromptError, match="not found"):
        load_prompt("classify", "v99")


def test_malformed_prompt_version_is_rejected() -> None:
    with pytest.raises(PromptError, match="invalid prompt version"):
        load_prompt("classify", "../x")


def test_prompt_without_separator_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "classify_v1.md").write_text("nur System", encoding="utf-8")

    with pytest.raises(PromptError, match="separator"):
        load_prompt("classify", prompts_dir=tmp_path)


# --- schemas ---


def test_extraction_model_accepts_valid_output(profile: Profile) -> None:
    model = extraction_model(profile.document_type("rechnung"))

    result = model.model_validate(
        {
            "lieferant": "Fiktiv GmbH",
            "rechnungsnummer": "RE-1",
            "rechnungsdatum": "2026-09-10",
            "betrag": "499.80",
            "e_rechnung": False,
        }
    )

    assert result.rechnungsdatum == date(2026, 9, 10)
    assert result.betrag == Decimal("499.80")


@pytest.mark.parametrize(
    ("type_key", "field", "value"),
    [
        ("rechnung", "betrag", 499.8),  # float instead of string
        ("rechnung", "betrag", "499.805"),  # more than two decimals
        ("rechnung", "rechnungsdatum", 1789000000),  # timestamp instead of date string
        ("rechnung", "rechnungsdatum", "10.09.2026"),  # wrong date format
        ("rechnung", "e_rechnung", "ja"),  # not a boolean
        ("kuendigung", "mitgliedsnummer", "10001"),  # pattern mismatch
        ("vertragspause", "grund_kategorie", "urlaub"),  # not an allowed value
    ],
)
def test_extraction_model_rejects_invalid_values(
    profile: Profile, type_key: str, field: str, value: object
) -> None:
    doc_type = profile.document_type(type_key)
    data: dict[str, object] = {spec.name: None for spec in doc_type.fields}
    data[field] = value

    with pytest.raises(ValueError, match=field):
        extraction_model(doc_type).model_validate(data)


def test_extraction_model_requires_every_key_and_no_extras(profile: Profile) -> None:
    model = extraction_model(profile.document_type("sonstiges"))

    with pytest.raises(ValueError, match="kurzbeschreibung"):
        model.model_validate({})
    with pytest.raises(ValueError, match="extra"):
        model.model_validate({"kurzbeschreibung": None, "diagnose": "x"})


# --- client with scripted fake ---


def test_classify_valid_output(profile: Profile) -> None:
    fake = FakeProvider(['{"document_type": "kuendigung", "confidence": 0.91}'])

    call = LLMClient(fake).classify("Ich kündige.", profile)

    assert call.ok
    assert call.output is not None and call.output.document_type == "kuendigung"
    assert call.prompt_id == "classify_v1"
    assert call.completion is not None and call.completion.model == "fake-scripted"
    assert "Ich kündige." in call.prompt_text
    assert call.duration_ms >= 0


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("Das ist eine Kündigung.", "invalid output"),
        ('```json\n{"document_type": "kuendigung", "confidence": 0.9}\n```', "invalid output"),
        ('["kuendigung"]', "expected a JSON object"),
        ('{"document_type": "kuendigung", "confidence": 1.4}', "confidence"),
        ('{"document_type": "mahnung", "confidence": 0.9}', "unknown document type"),
        ('{"document_type": "kuendigung", "confidence": 0.9, "x": 1}', "Extra inputs"),
    ],
)
def test_classify_invalid_output_is_reported_not_repaired(
    profile: Profile, answer: str, message: str
) -> None:
    call = LLMClient(FakeProvider([answer])).classify("Text", profile)

    assert not call.ok
    assert call.output is None
    assert call.error is not None and message in call.error


def test_provider_error_is_captured(profile: Profile) -> None:
    call = LLMClient(FakeProvider([])).classify("Text", profile)

    assert call.completion is None
    assert call.error is not None and "LLMProviderError" in call.error


def test_extract_valid_output(profile: Profile) -> None:
    answer = json.dumps(
        {
            "name": "Erika Beispiel",
            "mitgliedsnummer": "M-10001",
            "eingangsdatum": "2026-09-14",
            "gewuenschtes_kuendigungsdatum": None,
        }
    )

    call = LLMClient(FakeProvider([answer])).extract("Text", profile.document_type("kuendigung"))

    assert call.ok
    assert call.output is not None
    assert call.output.model_dump()["eingangsdatum"] == date(2026, 9, 14)


def test_extract_invalid_output(profile: Profile) -> None:
    call = LLMClient(FakeProvider(['{"name": "x"}'])).extract(
        "Text", profile.document_type("kuendigung")
    )

    assert not call.ok
    assert call.error is not None and "mitgliedsnummer" in call.error


def test_extract_prompt_lists_only_type_fields(profile: Profile) -> None:
    fake = FakeProvider()
    LLMClient(fake).extract("Text", profile.document_type("vertragspause"))

    _, _, user = fake.calls[0]
    assert '"grund_kategorie"' in user
    assert '"rechnungsnummer"' not in user


def test_draft_returns_text_and_rejects_empty(profile: Profile) -> None:
    client = LLMClient(FakeProvider(["Guten Tag", "   "]))

    assert client.draft("Vorlage", "Text", profile).output == "Guten Tag"
    assert client.draft("Vorlage", "Text", profile).error == "invalid output: empty draft"


# --- heuristic fake ---


def test_heuristic_fake_is_deterministic_and_valid(profile: Profile) -> None:
    text = "Hiermit kündige ich meine Mitgliedschaft M-10001 zum 31.10.2026."
    first = LLMClient(FakeProvider()).classify(text, profile)
    second = LLMClient(FakeProvider()).classify(text, profile)

    assert first.ok and second.ok
    assert first.output == second.output
    assert first.output is not None and first.output.document_type == "kuendigung"


def test_heuristic_fake_falls_back_without_keywords(profile: Profile) -> None:
    call = LLMClient(FakeProvider()).classify("Der Kursraum ist toll geworden.", profile)

    assert call.output is not None and call.output.document_type == "sonstiges"


def test_heuristic_fake_extracts_patterns_and_allowed_values(profile: Profile) -> None:
    text = "Bitte Vertrag M-10008 pausieren, ich bin beruflich im Ausland."

    call = LLMClient(FakeProvider()).extract(text, profile.document_type("vertragspause"))

    assert call.ok and call.output is not None
    assert call.output.model_dump()["mitgliedsnummer"] == "M-10008"
    assert call.output.model_dump()["grund_kategorie"] == "beruflich"


def test_heuristic_fake_draft_returns_template(profile: Profile) -> None:
    call = LLMClient(FakeProvider()).draft("Guten Tag Erika", "Text", profile)

    assert call.output == "Guten Tag Erika"


def test_fake_with_responder_function(profile: Profile) -> None:
    fake = FakeProvider(lambda task, system, user: '{"document_type": "rechnung", "confidence": 1}')

    assert LLMClient(fake).classify("x", profile).ok


# --- factory ---


def test_factory_defaults_to_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    assert isinstance(get_provider(), FakeProvider)


def test_factory_selects_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    provider = get_provider()

    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-opus-5-5"


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown LLM provider"):
        get_provider("mistral")


# --- anthropic provider (no network: stubbed messages API) ---


class _StubMessages:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def _response(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        model="claude-opus-5-5",
    )


def test_anthropic_provider_sends_request_and_returns_text() -> None:
    stub = _StubMessages(_response('{"document_type": "sonstiges", "confidence": 0.9}'))
    provider = AnthropicProvider(messages_api=stub)

    completion = provider.complete(task="classify", system="SYS", user="USER")

    assert completion.text == '{"document_type": "sonstiges", "confidence": 0.9}'
    assert completion.model == "claude-opus-5-5"
    assert completion.params["effort"] == "medium"
    assert stub.kwargs["system"] == "SYS"
    assert stub.kwargs["messages"] == [{"role": "user", "content": "USER"}]
    assert stub.kwargs["model"] == "claude-opus-5-5"


def test_anthropic_provider_model_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5-5")

    assert AnthropicProvider(messages_api=_StubMessages()).model == "claude-sonnet-5-5"


def test_anthropic_provider_refusal() -> None:
    provider = AnthropicProvider(messages_api=_StubMessages(_response("", "refusal")))

    with pytest.raises(LLMRefusalError):
        provider.complete(task="extract", system="s", user="u")


def test_anthropic_provider_api_errors_are_wrapped() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    provider = AnthropicProvider(messages_api=_StubMessages(error=error))

    with pytest.raises(LLMProviderError, match="not reachable"):
        provider.complete(task="draft", system="s", user="u")


def test_custom_provider_can_implement_interface(profile: Profile) -> None:
    class EchoProvider(LLMProvider):
        name = "echo"

        def complete(
            self,
            *,
            task: LLMTask,
            system: str,
            user: str,
            visuals: Sequence[VisualInput] = (),
        ) -> Completion:
            return Completion(text='{"document_type": "sonstiges", "confidence": 0.5}', model="e")

    assert LLMClient(EchoProvider()).classify("x", profile).ok


# --- architecture rule from CLAUDE.md ---


def test_provider_sdks_are_only_imported_in_llm_package() -> None:
    pattern = re.compile(r"^\s*(import|from)\s+(anthropic|openai|mistralai|google\.genai)\b", re.M)
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if path.parent.name != "llm" and pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
