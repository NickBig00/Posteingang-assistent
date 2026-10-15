"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Posteingangs-Assistent", version="0.1.0")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class HealthResponse(BaseModel):
    """Response body of the health endpoint."""

    status: str


@app.get("/health")
def health() -> HealthResponse:
    """Liveness check used by tests, CI and deployment."""
    return HealthResponse(status="ok")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Render the (still empty) inbox start page."""
    return templates.TemplateResponse(request, "index.html")
