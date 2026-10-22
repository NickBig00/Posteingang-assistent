import os
from pathlib import Path

import pytest

from app.config import DISABLE_VARIABLE, ENV_FILE, load_env_file


def test_test_suite_never_loads_local_env_file() -> None:
    assert os.environ.get(DISABLE_VARIABLE) == "1"


def test_env_file_lives_in_project_root() -> None:
    assert Path(__file__).resolve().parent.parent / ".env" == ENV_FILE


def test_env_file_is_git_ignored() -> None:
    gitignore = (ENV_FILE.parent / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in gitignore


def test_loads_values_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DISABLE_VARIABLE)
    monkeypatch.delenv("POSTEINGANG_TEST_VALUE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("POSTEINGANG_TEST_VALUE=aus-datei\n", encoding="utf-8")

    assert load_env_file(env_file) is True
    assert os.environ["POSTEINGANG_TEST_VALUE"] == "aus-datei"
    monkeypatch.delenv("POSTEINGANG_TEST_VALUE")


def test_existing_environment_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DISABLE_VARIABLE)
    monkeypatch.setenv("POSTEINGANG_TEST_VALUE", "aus-umgebung")
    env_file = tmp_path / ".env"
    env_file.write_text("POSTEINGANG_TEST_VALUE=aus-datei\n", encoding="utf-8")

    load_env_file(env_file)

    assert os.environ["POSTEINGANG_TEST_VALUE"] == "aus-umgebung"


def test_missing_file_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DISABLE_VARIABLE)

    assert load_env_file(tmp_path / ".env") is False


def test_disable_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTEINGANG_TEST_VALUE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("POSTEINGANG_TEST_VALUE=aus-datei\n", encoding="utf-8")

    assert load_env_file(env_file) is False
    assert "POSTEINGANG_TEST_VALUE" not in os.environ
