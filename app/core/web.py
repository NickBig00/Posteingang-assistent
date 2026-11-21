"""Shared web building blocks: layout with sidebar, templates, static files, settings page."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import MODULES_VARIABLE, app_title
from app.core.db import get_session
from app.core.images.service import image_generation_enabled, image_limits
from app.core.models import utcnow
from app.core.overview import ModuleOverview, OverviewProvider
from app.core.profiles import active_profile_key
from app.core.provider_errors import ProviderConfigError, configuration_problems
from app.core.retention import retention_days
from app.core.theme import (
    BRAND_COLOR_VARIABLE,
    current_theme,
    resolve_brand_color,
)

CORE_TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
STATIC_PATH = "/static"


@dataclass(frozen=True)
class NavEntry:
    """An entry of the sidebar navigation (stored in `app.state.navigation`).

    `prefixes` are further URL prefixes that belong to the entry (e.g. detail pages), so the
    entry stays highlighted there. The entry with the longest matching path wins.
    """

    module: str
    label: str
    path: str
    icon: str = "dot"
    prefixes: tuple[str, ...] = field(default=())

    def match_length(self, url_path: str) -> int:
        """Length of the best matching path or prefix (0 = no match)."""
        best = len(self.path) if url_path == self.path else 0
        for prefix in (self.path, *self.prefixes):
            if prefix != "/" and (url_path == prefix or url_path.startswith(prefix + "/")):
                best = max(best, len(prefix))
        return best


def active_entry(navigation: list[NavEntry], url_path: str) -> NavEntry | None:
    """The navigation entry for the current page (longest match), if any."""
    scored = [(entry.match_length(url_path), entry) for entry in navigation]
    scored = [item for item in scored if item[0] > 0]
    return max(scored, key=lambda item: item[0])[1] if scored else None


OVERVIEW_ENTRY = NavEntry(module="core", label="Übersicht", path="/", icon="home")
SETTINGS_ENTRY = NavEntry(module="core", label="Einstellungen", path="/settings", icon="settings")


# A module may supply the brand colour of its profile (registered in `app/main.py`).
BrandColorProvider = Callable[[], str | None]


def profile_brand_color(application: object) -> str | None:
    """First brand colour offered by an enabled module, if any."""
    providers: list[BrandColorProvider] = getattr(
        getattr(application, "state", None), "brand_color_providers", []
    )
    for provider in providers:
        color = provider()
        if color:
            return color
    return None


def _app_context(request: Request) -> dict[str, object]:
    # Read per request, so title and brand colour apply without code changes.
    navigation: list[NavEntry] = getattr(request.app.state, "navigation", [])
    return {
        "app_title": app_title(),
        "theme": current_theme(profile_brand_color(request.app)),
        "navigation": navigation,
        "active_nav": active_entry(navigation, request.url.path),
    }


def module_templates(templates_dir: Path, module: str) -> Jinja2Templates:
    """Templates of a module; `base.html` and other shared templates come from core."""
    templates = Jinja2Templates(
        directory=[templates_dir, CORE_TEMPLATES_DIR], context_processors=[_app_context]
    )
    templates.env.globals["active_module"] = module
    return templates


def mount_static(application: FastAPI) -> None:
    """Serve CSS, JavaScript and fonts locally (no CDN, nothing loaded from third parties)."""
    application.mount(STATIC_PATH, StaticFiles(directory=STATIC_DIR), name="static")


router = APIRouter()
templates = module_templates(CORE_TEMPLATES_DIR, "core")


class HealthResponse(BaseModel):
    """Response body of the health endpoint."""

    status: str


@router.get("/health")
def health() -> HealthResponse:
    """Liveness check used by tests, CI and deployment."""
    return HealthResponse(status="ok")


# Local time zone of the demo operator (dates on the start page).
LOCAL_TZ = ZoneInfo("Europe/Berlin")
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


def german_date(day: date) -> str:
    """E.g. "Dienstag, 3. November 2026"."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day}. {MONTHS[day.month - 1]} {day.year}"


@router.get("/", response_class=HTMLResponse)
def overview_page(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> HTMLResponse:
    """Start page: what is to do today, figures per module (SPEC 10.2)."""
    providers: list[OverviewProvider] = getattr(request.app.state, "overview_providers", [])
    modules: list[ModuleOverview] = [provider(session) for provider in providers]
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "modules": modules,
            "today": german_date(utcnow().astimezone(LOCAL_TZ).date()),
            "open_total": sum(m.open_count for m in modules),
            "todo_total": sum(len(m.todos) for m in modules),
        },
    )


@dataclass(frozen=True)
class SettingRow:
    """One read-only configuration value on the settings page."""

    label: str
    value: str
    variable: str
    note: str = ""


COLOR_SOURCES = {
    "env": "aus BRAND_COLOR",
    "profile": "aus dem Markenprofil (design.primary_color)",
    "default": "Standardfarbe",
}


def settings_rows(profile_color: str | None = None) -> list[SettingRow]:
    """Effective configuration as shown on the settings page – never secrets."""
    choice = resolve_brand_color(profile_color)
    color = choice.color
    color_note = COLOR_SOURCES[choice.source]
    if choice.ignored:
        color_note += f" (BRAND_COLOR „{choice.ignored}“ ungültig, ignoriert)"
    llm = (os.environ.get("LLM_PROVIDER") or "fake").strip().lower()
    limits = image_limits()
    return [
        SettingRow("Anwendungstitel", app_title(), "APP_TITLE"),
        SettingRow("Markenfarbe", color, BRAND_COLOR_VARIABLE, color_note),
        SettingRow(
            "Aktive Module",
            os.environ.get(MODULES_VARIABLE) or "alle",
            MODULES_VARIABLE,
        ),
        SettingRow("Branchenprofil", active_profile_key(), "PROFILE"),
        SettingRow(
            "Markenprofil (Social Media)",
            os.environ.get("BRAND_PROFILE") or "demo",
            "BRAND_PROFILE",
        ),
        SettingRow(
            "KI-Anbieter (Text)",
            llm,
            "LLM_PROVIDER",
            "„fake“ arbeitet offline und deterministisch" if llm == "fake" else "",
        ),
        SettingRow(
            "KI-Bilder",
            "an" if image_generation_enabled() else "aus",
            "IMAGE_GENERATION_ENABLED",
            (
                f"Anbieter {os.environ.get('IMAGE_PROVIDER') or 'fake'} · höchstens "
                f"{limits.per_scope} Erzeugungen je Beitrag, {limits.per_day} pro Tag"
            )
            if image_generation_enabled()
            else "Standard: aus",
        ),
        SettingRow(
            "Aufbewahrung abgeschlossener Vorgänge", f"{retention_days()} Tage", "RETENTION_DAYS"
        ),
    ]


# API key variables checked at start-up and on the settings page (values are never shown).
KEY_VARIABLES = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
logger = logging.getLogger(__name__)


def log_configuration_problems() -> list[str]:
    """Warn at start-up about unusable API keys (only the variable name, never the value)."""
    problems = configuration_problems(KEY_VARIABLES)
    for problem in problems:
        logger.warning("configuration problem: %s", problem)
    return problems


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    """Read-only overview of the configuration (changed via environment variables)."""
    rows = settings_rows(profile_brand_color(request.app))
    problems = configuration_problems(KEY_VARIABLES)
    return templates.TemplateResponse(
        request, "settings.html", {"rows": rows, "problems": problems}
    )


def provider_config_error_page(request: Request, error: Exception) -> HTMLResponse:
    """Clear message instead of a server error when a provider is not configured usably."""
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "title": "KI-Anbieter nicht nutzbar",
            "message": str(error),
            "hint": "Die Konfiguration steht in Umgebungsvariablen bzw. der Datei .env. Nach "
            "einer Änderung die Anwendung neu starten. Ohne API-Schlüssel arbeitet die Demo "
            "mit LLM_PROVIDER=fake und IMAGE_PROVIDER=fake.",
        },
        status_code=503,
    )


def register_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(ProviderConfigError, provider_config_error_page)  # type: ignore[arg-type]


@router.get("/design", response_class=HTMLResponse)
def design_page(request: Request) -> HTMLResponse:
    """Living style guide: all shared components with their states (docs/DESIGN.md)."""
    return templates.TemplateResponse(request, "design.html", {})
