"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import engine, get_session, init_db
from app.ingest.normalize import SUPPORTED_SUFFIXES, UnsupportedFileTypeError
from app.ingest.service import (
    DEFAULT_SAMPLES_DIR,
    MAX_UPLOAD_BYTES,
    import_samples,
    ingest_file,
    storage_dir,
)
from app.llm.client import LLMClient
from app.llm.factory import get_provider
from app.models import Classification, InboxItem, ItemSource
from app.pipeline import process_new_items
from app.profiles import Profile, load_active_profile
from app.rules import validate_profile_rules

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db(engine)
    yield


app = FastAPI(title="Posteingangs-Assistent", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_storage_dir() -> Path:
    return storage_dir()


def get_samples_dir() -> Path:
    return DEFAULT_SAMPLES_DIR


def get_llm_client() -> LLMClient:
    return LLMClient(get_provider())


def get_profile() -> Profile:
    profile = load_active_profile()
    validate_profile_rules(profile)
    return profile


SessionDep = Annotated[Session, Depends(get_session)]
ClientDep = Annotated[LLMClient, Depends(get_llm_client)]
ProfileDep = Annotated[Profile, Depends(get_profile)]
StorageDep = Annotated[Path, Depends(get_storage_dir)]
SamplesDep = Annotated[Path, Depends(get_samples_dir)]


class HealthResponse(BaseModel):
    """Response body of the health endpoint."""

    status: str


@app.get("/health")
def health() -> HealthResponse:
    """Liveness check used by tests, CI and deployment."""
    return HealthResponse(status="ok")


def _inbox_items(session: Session) -> list[tuple[InboxItem, Classification | None]]:
    statement = (
        select(InboxItem, Classification)
        .join(Classification, isouter=True)
        .order_by(InboxItem.id.desc())  # type: ignore[union-attr]
    )
    return [(item, classification) for item, classification in session.exec(statement).all()]


def _render_inbox(
    request: Request, session: Session, message: str | None = None, error: bool = False
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_inbox.html",
        {"items": _inbox_items(session), "message": message, "error": error},
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: SessionDep) -> HTMLResponse:
    """Render the inbox start page."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "items": _inbox_items(session),
            "message": None,
            "error": False,
            "accepted_suffixes": ",".join(sorted(SUPPORTED_SUFFIXES)),
        },
    )


@app.post("/samples/import", response_class=HTMLResponse)
def import_sample_data(
    request: Request, session: SessionDep, samples_dir: SamplesDep, raw_dir: StorageDep
) -> HTMLResponse:
    """Demo mode: import all synthetic sample files."""
    summary = import_samples(session, samples_dir, raw_dir)
    message = (
        f"{summary.imported} Beispiel-Eingänge importiert, {summary.skipped} bereits vorhanden."
    )
    return _render_inbox(request, session, message)


@app.post("/items/upload", response_class=HTMLResponse)
async def upload_item(
    request: Request, session: SessionDep, raw_dir: StorageDep, file: UploadFile
) -> HTMLResponse:
    """Upload a single .eml, PDF, PNG or JPG file."""
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _render_inbox(request, session, "Datei ist zu groß (max. 10 MB).", error=True)
    try:
        result = ingest_file(
            session, file.filename or "upload", content, ItemSource.UPLOAD, raw_dir
        )
    except UnsupportedFileTypeError:
        message = "Dateityp nicht unterstützt. Erlaubt: .eml, .pdf, .png, .jpg"
        return _render_inbox(request, session, message, error=True)
    message = "Datei hochgeladen." if result.created else "Datei ist bereits im Posteingang."
    return _render_inbox(request, session, message)


@app.post("/items/process", response_class=HTMLResponse)
def process_items(
    request: Request, session: SessionDep, client: ClientDep, profile: ProfileDep
) -> HTMLResponse:
    """Classify and extract all new items. Nothing is executed or sent."""
    results = process_new_items(session, profile, client)
    review = sum(r.status == "needs_manual_review" for r in results)
    message = f"{len(results)} Eingänge verarbeitet, davon {review} zur manuellen Prüfung."
    return _render_inbox(request, session, message)
