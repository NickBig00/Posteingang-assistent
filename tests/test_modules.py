"""Modular monolith: shared navigation, module switches and import boundaries."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import ModuleConfigError, app_title, enabled_modules
from app.main import MODULES, create_app

APP_DIR = Path(__file__).resolve().parent.parent / "app"
AVAILABLE = ["inbox", "social"]


# --- configuration ---


def test_all_modules_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODULES", raising=False)

    assert enabled_modules(AVAILABLE) == ["inbox", "social"]


def test_modules_from_environment_keep_navigation_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODULES", " Social , inbox ")

    assert enabled_modules(AVAILABLE) == ["inbox", "social"]


def test_single_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODULES", "inbox")

    assert enabled_modules(AVAILABLE) == ["inbox"]


def test_unknown_module_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODULES", "inbox,kalender")

    with pytest.raises(ModuleConfigError, match="kalender"):
        enabled_modules(AVAILABLE)


@pytest.mark.parametrize("value", ["", " , "])
def test_empty_module_list_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MODULES", value)

    with pytest.raises(ModuleConfigError, match="mindestens ein Modul"):
        enabled_modules(AVAILABLE)


def test_registry_lists_all_modules() -> None:
    assert list(MODULES) == AVAILABLE


# --- application with module switches ---


def _client(monkeypatch: pytest.MonkeyPatch, modules: str | None) -> TestClient:
    if modules is None:
        monkeypatch.delenv("MODULES", raising=False)
    else:
        monkeypatch.setenv("MODULES", modules)
    return TestClient(create_app())


def test_navigation_shows_all_modules(client: TestClient) -> None:
    html = client.get("/").text

    assert 'aria-label="Module"' in html
    assert re.search(r'<a href="/"\s+aria-current="page"', html)
    assert 'href="/social"' in html
    assert "Social Media" in html


def test_social_start_page(client: TestClient) -> None:
    response = client.get("/social")

    assert response.status_code == 200
    assert "Neues Briefing" in response.text
    assert re.search(r'<a href="/social"\s+aria-current="page"', response.text)


def test_health_is_part_of_core(monkeypatch: pytest.MonkeyPatch) -> None:
    for modules in ("inbox", "social"):
        assert _client(monkeypatch, modules).get("/health").json() == {"status": "ok"}


def test_inbox_only_hides_social(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "inbox")

    assert client.get("/social").status_code == 404
    page = client.get("/")
    assert page.status_code == 200
    assert "Posteingang" in page.text
    # A single module needs no navigation bar.
    assert 'aria-label="Module"' not in page.text


def test_social_only_redirects_start_page(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "social")

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/social"
    assert client.post("/items/process").status_code in {404, 405}
    assert client.get("/items/1").status_code == 404


def test_unknown_module_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODULES", "unbekannt")

    with pytest.raises(ModuleConfigError):
        create_app()


# --- architecture rules (docs/SPEC_social.md, section 3) ---


def _imports(package: str) -> dict[str, set[str]]:
    """Top-level `app.<x>` packages imported by each file of `app/<package>/`."""
    pattern = re.compile(r"^\s*(?:from|import)\s+app\.(\w+)", re.M)
    return {
        str(path.relative_to(APP_DIR)): set(pattern.findall(path.read_text(encoding="utf-8")))
        for path in (APP_DIR / package).rglob("*.py")
    }


def test_core_does_not_import_modules() -> None:
    offenders = {f: i for f, i in _imports("core").items() if i - {"core"}}

    assert offenders == {}


@pytest.mark.parametrize(("module", "other"), [("inbox", "social"), ("social", "inbox")])
def test_modules_do_not_import_each_other(module: str, other: str) -> None:
    offenders = {f: i for f, i in _imports(module).items() if other in i}

    assert offenders == {}


# --- application title from configuration ---


def test_default_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_TITLE", raising=False)

    assert app_title() == "Posteingangs-Assistent"
    monkeypatch.setenv("APP_TITLE", "   ")
    assert app_title() == "Posteingangs-Assistent"


def test_configured_title_on_every_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_TITLE", "Demo-Assistent Musterstadt")

    for path in ("/", "/social", "/social/campaigns"):
        html = client.get(path).text
        assert '<h1 class="text-xl font-semibold">Demo-Assistent Musterstadt</h1>' in html, path
        assert "Posteingangs-Assistent" not in html, path
    assert "<title>Social Media – Demo-Assistent Musterstadt</title>" in client.get("/social").text


def test_title_of_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TITLE", "Demo-Assistent")

    assert create_app().title == "Demo-Assistent"
