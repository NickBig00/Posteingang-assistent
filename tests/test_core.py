"""Core building blocks used by all modules: profile loader and LLM call helpers."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.llm.base import Completion, LLMProviderError
from app.core.llm.calls import parse_json_object, timed_complete
from app.core.llm.fake import FakeProvider
from app.core.llm.prompts import RenderedPrompt
from app.core.profiles import (
    PROFILES_DIR,
    ProfileError,
    active_profile_path,
    load_yaml_profile,
)


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    limit: int


def test_load_yaml_profile_with_module_schema(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text("name: Demo-Studio\nlimit: 3\n", encoding="utf-8")

    assert load_yaml_profile(path, _Schema) == _Schema(name="Demo-Studio", limit=3)


def test_load_yaml_profile_reports_schema_errors(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text("name: Demo-Studio\nlimit: viele\nfarbe: blau\n", encoding="utf-8")

    with pytest.raises(ProfileError) as excinfo:
        load_yaml_profile(path, _Schema)

    message = str(excinfo.value)
    assert "ist ungültig" in message
    assert "limit:" in message
    assert "farbe:" in message


@pytest.mark.parametrize(
    ("content", "expected"),
    [("name: [unclosed", "kein gültiges YAML"), ("- a\n- b\n", "muss ein YAML-Objekt sein")],
)
def test_load_yaml_profile_rejects_malformed_files(
    tmp_path: Path, content: str, expected: str
) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ProfileError, match=expected):
        load_yaml_profile(path, _Schema)


def test_load_yaml_profile_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="nicht gefunden"):
        load_yaml_profile(tmp_path / "fehlt.yaml", _Schema)


def test_active_profile_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROFILE", "handwerk")

    assert active_profile_path() == PROFILES_DIR / "handwerk.yaml"


def test_active_profile_path_rejects_path_tricks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROFILE", "../geheim")

    with pytest.raises(ProfileError, match="Ungültiger Profilname"):
        active_profile_path()


def test_timed_complete_returns_completion() -> None:
    provider = FakeProvider(["antwort"])

    completion, error, duration = timed_complete(
        provider, "draft", RenderedPrompt(system="s", user="u")
    )

    assert isinstance(completion, Completion)
    assert completion.text == "antwort"
    assert error is None
    assert duration >= 0
    assert provider.calls == [("draft", "s", "u")]


def test_timed_complete_captures_provider_errors() -> None:
    completion, error, _ = timed_complete(
        FakeProvider([]), "draft", RenderedPrompt(system="s", user="u")
    )

    assert completion is None
    assert error is not None
    assert error.startswith(LLMProviderError.__name__)


@pytest.mark.parametrize("text", ["[1, 2]", "kein json", '{"a": 1} extra'])
def test_parse_json_object_is_strict(text: str) -> None:
    with pytest.raises(ValueError):
        parse_json_object(text)
