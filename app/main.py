"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

from app.audit import audit_outputs
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
from app.logging_config import setup_logging
from app.models import Classification, InboxItem, ItemSource, ItemStatus, ReviewDecision, RuleResult
from app.pipeline import process_new_items
from app.profiles import Profile, load_active_profile
from app.retention import delete_item, retention_days
from app.review import (
    ItemDetails,
    ReviewNotAllowedError,
    audit_trail,
    execute_item,
    final_text,
    load_details,
    review_item,
    traffic_light,
)
from app.rules import validate_profile_rules

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
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


def _inbox_items(
    session: Session,
) -> list[tuple[InboxItem, Classification | None, str | None]]:
    statement = (
        select(InboxItem, Classification)
        .join(Classification, isouter=True)
        .order_by(InboxItem.id.desc())  # type: ignore[union-attr]
    )
    rules: dict[int, list[RuleResult]] = {}
    for result in session.exec(select(RuleResult)).all():
        rules.setdefault(result.item_id, []).append(result)
    return [
        (item, classification, traffic_light(rules.get(item.id or 0, [])))
        for item, classification in session.exec(statement).all()
    ]


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
            "retention_days": retention_days(),
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


def _details_or_404(session: Session, item_id: int) -> ItemDetails:
    details = load_details(session, item_id)
    if details is None:
        raise HTTPException(status_code=404, detail="Eingang nicht gefunden")
    return details


def _render_detail(
    request: Request, session: Session, item_id: int, error: str | None = None, status: int = 200
) -> HTMLResponse:
    details = _details_or_404(session, item_id)
    return templates.TemplateResponse(
        request,
        "detail.html",
        {"d": details, "error": error, "final_text": final_text(session, details.item)},
        status_code=status,
    )


@app.get("/items/{item_id}", response_class=HTMLResponse)
def item_detail(request: Request, session: SessionDep, item_id: int) -> HTMLResponse:
    """Detail view: original left, fields, rule results and suggestion right."""
    return _render_detail(request, session, item_id)


@app.get("/items/{item_id}/audit", response_class=HTMLResponse)
def item_audit(request: Request, session: SessionDep, item_id: int) -> HTMLResponse:
    """Chronological trail of all AI steps for one item."""
    details = _details_or_404(session, item_id)
    entries = audit_trail(session, item_id)
    outputs = audit_outputs(session, [e.id for e in entries if e.id is not None])
    return templates.TemplateResponse(
        request, "audit.html", {"item": details.item, "entries": entries, "outputs": outputs}
    )


@app.post("/items/{item_id}/review", response_model=None)
def item_review(
    request: Request,
    session: SessionDep,
    item_id: int,
    decision: Annotated[ReviewDecision, Form()],
    edited_text: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """Human decision: approve (optionally with edited text) or reject."""
    item = _details_or_404(session, item_id).item
    try:
        review_item(session, item, decision, edited_text)
    except ReviewNotAllowedError as error:
        return _render_detail(request, session, item_id, str(error), status=409)
    return RedirectResponse(f"/items/{item_id}", status_code=303)


@app.post("/items/{item_id}/execute", response_model=None)
def item_execute(
    request: Request, session: SessionDep, item_id: int
) -> HTMLResponse | RedirectResponse:
    """Demo execution after approval: set status to done (reply becomes exportable)."""
    item = _details_or_404(session, item_id).item
    try:
        execute_item(session, item)
    except ReviewNotAllowedError as error:
        return _render_detail(request, session, item_id, str(error), status=409)
    return RedirectResponse(f"/items/{item_id}", status_code=303)


@app.get("/items/{item_id}/export", response_class=PlainTextResponse)
def item_export(session: SessionDep, item_id: int) -> PlainTextResponse:
    """Download the released reply text – only for executed (approved) items."""
    item = _details_or_404(session, item_id).item
    if item.status != ItemStatus.DONE:
        raise HTTPException(status_code=409, detail="Export erst nach Freigabe und Ausführung")
    text = final_text(session, item)
    if not text:
        raise HTTPException(status_code=404, detail="Kein Antworttext vorhanden")
    return PlainTextResponse(
        text, headers={"Content-Disposition": f'attachment; filename="antwort-{item_id}.txt"'}
    )


@app.post("/items/{item_id}/delete")
def item_delete(session: SessionDep, item_id: int) -> RedirectResponse:
    """Delete an item with all content; the audit trail keeps only hashes and metadata."""
    if not delete_item(session, item_id):
        raise HTTPException(status_code=404, detail="Eingang nicht gefunden")
    return RedirectResponse("/", status_code=303)
