"""Shared web building blocks: layout with sidebar, templates, static files, settings page."""

import os
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
from app.core.models import utcnow
from app.core.overview import ModuleOverview, OverviewProvider
from app.core.profiles import active_profile_key
from app.core.retention import retention_days
from app.core.theme import (
    BRAND_COLOR_VARIABLE,
    ThemeConfigError,
    brand_color,
    build_theme,
    current_theme,
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


def _app_context(request: Request) -> dict[str, object]:
    # Read per request, so title and brand colour apply without code changes.
    navigation: list[NavEntry] = getattr(request.app.state, "navigation", [])
    return {
        "app_title": app_title(),
        "theme": current_theme(),
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


def settings_rows() -> list[SettingRow]:
    """Effective configuration as shown on the settings page – never secrets."""
    color = brand_color()
    try:
        build_theme(color)
        color_note = ""
    except ThemeConfigError:
        color_note = "ungültig – Standardfarbe wird verwendet"
    llm = (os.environ.get("LLM_PROVIDER") or "fake").strip().lower()
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
            "Aufbewahrung abgeschlossener Vorgänge", f"{retention_days()} Tage", "RETENTION_DAYS"
        ),
    ]


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    """Read-only overview of the configuration (changed via environment variables)."""
    return templates.TemplateResponse(request, "settings.html", {"rows": settings_rows()})


@router.get("/design", response_class=HTMLResponse)
def design_page(request: Request) -> HTMLResponse:
    """Living style guide: all shared components with their states (docs/DESIGN.md)."""
    return templates.TemplateResponse(request, "design.html", {})
