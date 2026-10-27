"""Shared web building blocks: page layout with navigation, templates, health check."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

CORE_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class NavEntry:
    """A module in the shared navigation (stored in `app.state.navigation`)."""

    module: str
    label: str
    path: str


def module_templates(templates_dir: Path, module: str) -> Jinja2Templates:
    """Templates of a module; `base.html` and other shared templates come from core."""
    templates = Jinja2Templates(directory=[templates_dir, CORE_TEMPLATES_DIR])
    templates.env.globals["active_module"] = module
    return templates


router = APIRouter()


class HealthResponse(BaseModel):
    """Response body of the health endpoint."""

    status: str


@router.get("/health")
def health() -> HealthResponse:
    """Liveness check used by tests, CI and deployment."""
    return HealthResponse(status="ok")
