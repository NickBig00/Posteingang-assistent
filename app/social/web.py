"""Web routes of the social media module. Only a placeholder page so far."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.web import NavEntry, module_templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
NAV_ENTRY = NavEntry(module="social", label="Social Media", path="/social")

router = APIRouter()
templates = module_templates(TEMPLATES_DIR, NAV_ENTRY.module)


@router.get("/social", response_class=HTMLResponse)
def social_index(request: Request) -> HTMLResponse:
    """Placeholder start page of the social media module."""
    return templates.TemplateResponse(request, "social_index.html")
