"""Web routes of the social media module (SPEC_social section 8)."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlmodel import Session, col, select

from app.core.db import get_session
from app.core.image_formats import (
    FORMAT_SPECS,
    ImageExportError,
    ImageFormat,
    export_jpeg,
)
from app.core.images.base import ImageError, ImageProvider
from app.core.images.service import get_image_provider, image_generation_enabled
from app.core.llm.factory import get_provider
from app.core.models import utcnow
from app.core.outcomes import worst_outcome
from app.core.retention import retention_days
from app.core.storage import get_storage_dir
from app.core.users import current_user
from app.core.web import NavEntry, module_templates
from app.social.assets import (
    MAX_IMAGE_BYTES,
    AssetError,
    Revocation,
    briefing_assets,
    confirm_consent,
    delete_asset,
    is_usable,
    revoke_consent,
    save_asset,
    usable_assets,
)
from app.social.audit import briefing_audit_trail
from app.social.briefings import BriefingFormError, create_briefing, parse_briefing_form
from app.social.calendar import (
    CalendarError,
    build_ics,
    calendar_entries,
    export_text,
    mark_scheduled_externally,
    month_view,
    publish_draft,
    schedule_draft,
    summary_line,
    to_local,
    unmark_scheduled_externally,
    unschedule_draft,
    week_start,
    week_view,
)
from app.social.campaigns import (
    campaign_plan,
    create_campaign,
    load_demo_campaign,
    parse_campaign_form,
    validate_campaign,
)
from app.social.checks import CHECK_LABELS
from app.social.drafts import generate_drafts
from app.social.facts import FACT_LABELS, BriefingFacts, formatted_facts
from app.social.graphics import (
    LAYOUT_LABELS,
    GraphicError,
    GraphicLayoutError,
    graphic_text,
    layout_for,
    parse_fact_keys,
    render_graphic,
    verify_graphic_text,
)
from app.social.highlight import highlight
from app.social.images import (
    AI_IMAGE_LABEL,
    HINT_LABELS,
    ImageSuggestionError,
    choose_candidate,
    confirm_prompt_hint,
    discard_candidates,
    generate_variants,
    image_state,
    suggest_prompt,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    CHANNEL_IMAGE_FORMATS,
    CHANNEL_LABELS,
    POST_STATUS_LABELS,
    POST_STATUS_TONES,
    Asset,
    Briefing,
    Campaign,
    Channel,
    CheckResult,
    ImageCandidate,
    PostDraft,
    PostStatus,
    PromptHint,
)
from app.social.profiles import ADDRESS_FORM_LABELS, SocialConfig, load_social_config
from app.social.retention import delete_briefing, delete_campaign, delete_draft
from app.social.review import (
    ReviewNotAllowedError,
    approval_blockers,
    approve_draft,
    confirm_person_consent,
    confirm_warning,
    edit_draft,
    recheck_draft,
    reject_draft,
    reopen_draft,
)
from app.social.sensitive import SENSITIVE_CHECK_LABELS
from app.social.status import latest_review

TEMPLATES_DIR = Path(__file__).parent / "templates"
NAV_ENTRY = NavEntry(module="social", label="Social Media", path="/social", icon="megaphone")
CALENDAR_NAV_ENTRY = NavEntry(
    module="social", label="Kalender", path="/social/calendar", icon="calendar"
)

router = APIRouter()
templates = module_templates(TEMPLATES_DIR, NAV_ENTRY.module)
templates.env.globals["channel_labels"] = CHANNEL_LABELS
templates.env.globals["post_status_labels"] = POST_STATUS_LABELS
templates.env.globals["post_status_tones"] = POST_STATUS_TONES
templates.env.globals["channel_formats"] = CHANNEL_IMAGE_FORMATS
templates.env.globals["format_specs"] = FORMAT_SPECS
templates.env.globals["ai_image_label"] = AI_IMAGE_LABEL
templates.env.globals["hint_labels"] = HINT_LABELS
templates.env.globals["fact_labels"] = FACT_LABELS
templates.env.globals["address_form_labels"] = ADDRESS_FORM_LABELS
templates.env.globals["light_ok_label"] = "Prüfungen ok"
templates.env.filters["highlight"] = highlight
templates.env.filters["local"] = to_local


def get_social_config() -> SocialConfig:
    return load_social_config()


def get_social_client() -> SocialLLMClient:
    return SocialLLMClient(get_provider())


def get_social_image_provider() -> ImageProvider:
    return get_image_provider()


def _labels(config: SocialConfig) -> dict[str, dict[str, str]]:
    rules = [*config.industry.term_rules, *config.industry.phrase_rules]
    return {
        "area_labels": {a.key: a.label for a in config.brand.areas},
        "occasion_labels": {o.key: o.label for o in config.industry.occasion_types},
        "check_labels": CHECK_LABELS | SENSITIVE_CHECK_LABELS | {r.key: r.label for r in rules},
    }


def _results_by_draft(session: Session, draft_ids: list[int]) -> dict[int, list[CheckResult]]:
    results: dict[int, list[CheckResult]] = {draft_id: [] for draft_id in draft_ids}
    statement = (
        select(CheckResult)
        .where(CheckResult.draft_id.in_(draft_ids))  # type: ignore[union-attr]
        .order_by(CheckResult.id)  # type: ignore[arg-type]
    )
    for result in session.exec(statement).all():
        results[result.draft_id].append(result)
    return results


def _light(results: list[CheckResult]) -> str | None:
    outcome = worst_outcome(r.outcome for r in results)
    return outcome.value if outcome else None


SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[SocialConfig, Depends(get_social_config)]
ClientDep = Annotated[SocialLLMClient, Depends(get_social_client)]
UserDep = Annotated[str, Depends(current_user)]
StorageDep = Annotated[Path, Depends(get_storage_dir)]


def _drafts_by_briefing(session: Session) -> dict[int, list[PostDraft]]:
    drafts: dict[int, list[PostDraft]] = {}
    for draft in session.exec(select(PostDraft).order_by(PostDraft.id)).all():  # type: ignore[arg-type]
        drafts.setdefault(draft.briefing_id, []).append(draft)
    return drafts


def parse_status_filter(raw: str | None) -> list[PostStatus]:
    """`?status=draft,in_review` → statuses; unknown values are ignored."""
    values = {v.strip() for v in (raw or "").split(",") if v.strip()}
    return [status for status in PostStatus if status.value in values]


@router.get("/social", response_class=HTMLResponse)
def social_index(
    request: Request, session: SessionDep, config: ConfigDep, status: str | None = None
) -> HTMLResponse:
    """Overview: briefings with their drafts and status, optionally filtered by status."""
    briefings = session.exec(select(Briefing).order_by(Briefing.id.desc())).all()  # type: ignore[union-attr]
    drafts = _drafts_by_briefing(session)
    statuses = parse_status_filter(status)
    if statuses:
        drafts = {b: [d for d in ds if d.status in statuses] for b, ds in drafts.items()}
        drafts = {b: ds for b, ds in drafts.items() if ds}
        briefings = [b for b in briefings if b.id in drafts]
    results = _results_by_draft(session, [d.id for ds in drafts.values() for d in ds if d.id])
    return templates.TemplateResponse(
        request,
        "social_index.html",
        {
            "briefings": briefings,
            "drafts": drafts,
            "status_filter": statuses,
            "retention_days": retention_days(),
            "lights": {draft_id: _light(r) for draft_id, r in results.items()},
            "config": config,
            **_labels(config),
        },
    )


def _form_context(
    config: SocialConfig,
    values: dict[str, str],
    channels: list[str] | None,
    errors: dict[str, str] | None = None,
) -> dict[str, object]:
    area_key = values.get("area") or config.brand.areas[0].key
    if area_key not in {a.key for a in config.brand.areas}:
        area_key = config.brand.areas[0].key
    occasion_key = values.get("occasion_type") or config.industry.occasion_types[0].key
    if occasion_key not in {o.key for o in config.industry.occasion_types}:
        occasion_key = config.industry.occasion_types[0].key
    occasion = config.industry.occasion_type(occasion_key)
    required = {"zeitraum_von" if k == "zeitraum" else k for k in occasion.required_facts}
    if "zeitraum_von" in required:
        required.add("zeitraum_bis")
    if channels is None:
        channels = [c.value for c in config.brand.channels(area_key)]
    return {
        "config": config,
        "values": values | {"area": area_key, "occasion_type": occasion_key},
        "area": config.brand.area(area_key),
        "occasion": occasion,
        "required": required,
        "selected_channels": channels,
        "channels": list(Channel),
        "errors": errors or {},
    }


@router.get("/social/briefings/new", response_class=HTMLResponse)
def briefing_new(request: Request, config: ConfigDep) -> HTMLResponse:
    """Form "Neues Briefing"."""
    return templates.TemplateResponse(
        request, "social_briefing_form.html", _form_context(config, {}, None)
    )


@router.get("/social/briefings/form", response_class=HTMLResponse)
def briefing_form_fields(request: Request, config: ConfigDep) -> HTMLResponse:
    """HTMX: re-render the form fields after area or occasion type changed.

    Changing the area preselects its channels; required facts follow the occasion type.
    """
    params = request.query_params
    values = {k: v for k, v in params.items() if k not in ("channels", "changed", "campaign")}
    channels = None if params.get("changed") == "area" else params.getlist("channels")
    context = _form_context(config, values, channels) | {"campaign": bool(params.get("campaign"))}
    return templates.TemplateResponse(request, "_social_briefing_fields.html", context)


@router.post("/social/briefings", response_model=None)
async def briefing_create(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
) -> HTMLResponse | RedirectResponse:
    """Validate the briefing, store it and generate one AI draft per channel."""
    form = await request.form()
    values = {k: v for k, v in form.items() if isinstance(v, str) and k != "channels"}
    channels = [v for v in form.getlist("channels") if isinstance(v, str)]
    try:
        data = parse_briefing_form(values, channels, config)
    except BriefingFormError as error:
        return templates.TemplateResponse(
            request,
            "social_briefing_form.html",
            _form_context(config, values, channels, error.errors),
            status_code=422,
        )
    briefing = create_briefing(session, config, data, user)
    generate_drafts(session, config, client, briefing)
    return RedirectResponse(f"/social/briefings/{briefing.id}", status_code=303)


RELEASED_STATUSES = (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED)


def _has_approved_post(session: Session, briefing_id: int) -> bool:
    """The graphic is publishing material: downloadable once a post of the briefing is released."""
    statement = (
        select(PostDraft.id)
        .where(PostDraft.briefing_id == briefing_id)
        .where(col(PostDraft.status).in_(RELEASED_STATUSES))
    )
    return session.exec(statement).first() is not None


def _graphic_context(
    request: Request,
    session: Session,
    config: SocialConfig,
    briefing: Briefing,
    usable: list[Asset],
) -> dict[str, object]:
    """Form values, layout and fact check result of the graphic from a template."""
    params = request.query_params
    fmt = params.get("graphic_format") or ImageFormat.PORTRAIT.value
    if fmt not in {f.value for f in ImageFormat}:
        fmt = ImageFormat.PORTRAIT.value
    background = params.get("graphic_background") or "color"
    if background != "color" and background not in {str(a.id) for a in usable}:
        background = "color"
    headline = (params.get("graphic_headline") or "")[:80]
    facts = BriefingFacts.from_json(briefing.facts)
    chosen = params.getlist("graphic_facts")
    keys = parse_fact_keys(chosen, facts) if chosen else tuple(facts.present_keys())
    text = graphic_text(config, briefing, headline, keys)
    try:
        verify_graphic_text(text, facts, config)
        problems: list[str] = []
    except GraphicError as error:
        problems = error.problems
    query = urlencode(
        [("format", fmt), ("background", background), ("headline", headline)]
        + [("facts", key) for key in keys]
    )
    ai_background = any(str(a.id) == background and a.ai_generated for a in usable)
    return {
        "format": fmt,
        "background": background,
        "headline": headline,
        "fact_keys": keys,
        "available_facts": [(k, FACT_LABELS[k]) for k in facts.present_keys()],
        "layout": LAYOUT_LABELS[layout_for(briefing.occasion_type)],
        "lines": text.lines,
        "problems": problems,
        "src": f"/social/briefings/{briefing.id}/graphic.jpg?{query}",
        "ai_background": ai_background,
        "downloadable": _has_approved_post(session, briefing.id or 0),
    }


def _render_briefing(
    request: Request,
    session: Session,
    config: SocialConfig,
    briefing_id: int,
    error: str | None = None,
    status_code: int = 200,
    revocation: Revocation | None = None,
) -> HTMLResponse:
    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        raise HTTPException(status_code=404, detail="Briefing nicht gefunden")
    drafts = session.exec(
        select(PostDraft).where(PostDraft.briefing_id == briefing_id).order_by(PostDraft.id)  # type: ignore[arg-type]
    ).all()
    facts = formatted_facts(BriefingFacts.from_json(briefing.facts))
    results = _results_by_draft(session, [d.id for d in drafts if d.id])
    usable = usable_assets(session, briefing_id)
    # Per draft: its own AI image first, then the uploaded images of the briefing.
    draft_images = {
        d.id: sorted(
            (a for a in usable if a.draft_id in (None, d.id)),
            key=lambda a: (not a.ai_generated, a.id or 0),
        )
        for d in drafts
    }
    images_enabled = image_generation_enabled()
    graphic = _graphic_context(request, session, config, briefing, usable)
    return templates.TemplateResponse(
        request,
        "social_briefing_detail.html",
        {
            "briefing": briefing,
            "drafts": drafts,
            "results": results,
            "lights": {draft_id: _light(r) for draft_id, r in results.items()},
            "blockers": {d.id: approval_blockers(session, d) for d in drafts},
            "reviews": {d.id: latest_review(session, d.id) for d in drafts if d.id},
            "assets": briefing_assets(session, briefing_id),
            "draft_images": draft_images,
            "usable_assets": usable,
            "images_enabled": images_enabled,
            "image_states": {d.id: image_state(session, config, d) for d in drafts}
            if images_enabled
            else {},
            "image_formats": list(ImageFormat),
            "graphic": graphic,
            "facts": facts,
            "config": config,
            "error": error,
            "revocation": revocation,
            **_labels(config),
        },
        status_code=status_code,
    )


@router.get("/social/briefings/{briefing_id}", response_class=HTMLResponse)
def briefing_detail(
    request: Request, session: SessionDep, config: ConfigDep, briefing_id: int
) -> HTMLResponse:
    """Briefing left, drafts per channel with check results right."""
    return _render_briefing(request, session, config, briefing_id)


def _draft_or_404(session: Session, draft_id: int) -> PostDraft:
    draft = session.get(PostDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Entwurf nicht gefunden")
    return draft


def _back(draft: PostDraft) -> RedirectResponse:
    return RedirectResponse(
        f"/social/briefings/{draft.briefing_id}#draft-{draft.id}", status_code=303
    )


@router.post("/social/checks/{result_id}/confirm", response_model=None)
def check_confirm(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, result_id: int
) -> HTMLResponse | RedirectResponse:
    """A person confirms a warning (health claim, person) – recorded in the audit trail."""
    result = session.get(CheckResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Prüfergebnis nicht gefunden")
    draft = _draft_or_404(session, result.draft_id)
    try:
        confirm_warning(session, result, user)
    except ReviewNotAllowedError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _back(draft)


@router.post("/social/checks/{result_id}/consent", response_model=None)
def check_person_consent(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, result_id: int
) -> HTMLResponse | RedirectResponse:
    """A person confirms the consent of a named member – recorded in the audit trail."""
    result = session.get(CheckResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Prüfergebnis nicht gefunden")
    draft = _draft_or_404(session, result.draft_id)
    try:
        confirm_person_consent(session, result, user)
    except ReviewNotAllowedError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _back(draft)


@router.post("/social/drafts/{draft_id}/approve", response_model=None)
def draft_approve(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Approve a draft – only without errors and with all warnings confirmed."""
    draft = _draft_or_404(session, draft_id)
    try:
        approve_draft(session, draft, user)
    except ReviewNotAllowedError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _back(draft)


def _respond(
    request: Request,
    session: Session,
    config: SocialConfig,
    draft: PostDraft,
    action: Callable[[], object],
) -> HTMLResponse | RedirectResponse:
    try:
        action()
    except (ReviewNotAllowedError, AssetError, CalendarError) as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _back(draft)


@router.post("/social/drafts/{draft_id}/edit", response_model=None)
def draft_edit(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    draft_id: int,
    text: Annotated[str, Form()] = "",
    hashtags: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """A person edits text and hashtags; all checks run again."""
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request,
        session,
        config,
        draft,
        lambda: edit_draft(session, config, client, draft, text, hashtags, user),
    )


@router.post("/social/drafts/{draft_id}/reject", response_model=None)
def draft_reject(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Reject a draft or withdraw it before publishing."""
    draft = _draft_or_404(session, draft_id)
    return _respond(request, session, config, draft, lambda: reject_draft(session, draft, user))


@router.post("/social/drafts/{draft_id}/reopen", response_model=None)
def draft_reopen(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    draft_id: int,
) -> HTMLResponse | RedirectResponse:
    """Reopen a rejected draft (recorded in the audit trail)."""
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request, session, config, draft, lambda: reopen_draft(session, config, client, draft, user)
    )


@router.post("/social/drafts/{draft_id}/recheck", response_model=None)
def draft_recheck(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    draft_id: int,
) -> HTMLResponse | RedirectResponse:
    """Run all checks again (e.g. after the AI assessment was unavailable)."""
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request, session, config, draft, lambda: recheck_draft(session, config, client, draft, user)
    )


# --- images ---


def _briefing_or_404(session: Session, briefing_id: int) -> Briefing:
    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        raise HTTPException(status_code=404, detail="Briefing nicht gefunden")
    return briefing


def _asset_or_404(session: Session, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Bild nicht gefunden")
    return asset


@router.post("/social/briefings/{briefing_id}/assets", response_model=None)
async def asset_upload(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    storage: StorageDep,
    briefing_id: int,
    file: UploadFile,
    shows_persons: Annotated[bool, Form()] = False,
    consent_confirmed: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """Upload an image; if it shows persons, their consent must be confirmed."""
    briefing = _briefing_or_404(session, briefing_id)
    content = await file.read(MAX_IMAGE_BYTES + 1)
    try:
        save_asset(
            session,
            briefing,
            file.filename or "bild",
            content,
            shows_persons=shows_persons,
            consent_confirmed=consent_confirmed,
            user=user,
            storage=storage,
        )
    except AssetError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return RedirectResponse(f"/social/briefings/{briefing_id}#bilder", status_code=303)


@router.post("/social/assets/{asset_id}/consent", response_model=None)
def asset_consent(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, asset_id: int
) -> HTMLResponse | RedirectResponse:
    """Confirm the consent of everyone shown on the image."""
    asset = _asset_or_404(session, asset_id)
    try:
        confirm_consent(session, asset, user)
    except AssetError as error:
        return _render_briefing(request, session, config, asset.briefing_id, str(error), 409)
    return RedirectResponse(f"/social/briefings/{asset.briefing_id}#bilder", status_code=303)


@router.post("/social/assets/{asset_id}/delete")
def asset_delete(session: SessionDep, user: UserDep, asset_id: int) -> RedirectResponse:
    asset = _asset_or_404(session, asset_id)
    briefing_id = asset.briefing_id
    delete_asset(session, asset, user)
    return RedirectResponse(f"/social/briefings/{briefing_id}#bilder", status_code=303)


@router.get("/social/assets/{asset_id}", response_model=None)
def asset_file(session: SessionDep, asset_id: int) -> Response:
    asset = _asset_or_404(session, asset_id)
    path = Path(asset.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Bilddatei nicht vorhanden")
    return Response(
        path.read_bytes(),
        media_type=asset.media_type,
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )


# --- AI images (only with IMAGE_GENERATION_ENABLED) ---


ImageProviderDep = Annotated[ImageProvider, Depends(get_social_image_provider)]


def _images_draft(session: Session, draft_id: int) -> PostDraft:
    if not image_generation_enabled():
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return _draft_or_404(session, draft_id)


def _images_back(draft: PostDraft) -> RedirectResponse:
    return RedirectResponse(
        f"/social/briefings/{draft.briefing_id}#images-{draft.id}", status_code=303
    )


def _image_format(value: str, draft: PostDraft) -> ImageFormat:
    try:
        return ImageFormat(value)
    except ValueError:
        return CHANNEL_IMAGE_FORMATS[draft.channel]


@router.post("/social/drafts/{draft_id}/images/suggest", response_model=None)
def image_suggest(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    draft_id: int,
    image_format: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """The AI suggests an image prompt from briefing and image style (visible, editable)."""
    draft = _images_draft(session, draft_id)
    try:
        suggest_prompt(session, config, client, draft, _image_format(image_format, draft))
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _images_back(draft)


@router.post("/social/drafts/{draft_id}/images/generate", response_model=None)
def image_generate(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    provider: ImageProviderDep,
    user: UserDep,
    storage: StorageDep,
    draft_id: int,
    prompt: Annotated[str, Form()] = "",
    image_format: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Check the prompt and generate 2–3 variants (cost brake and audit in core)."""
    draft = _images_draft(session, draft_id)
    try:
        generate_variants(
            session,
            config,
            provider,
            draft,
            prompt=prompt,
            fmt=_image_format(image_format, draft),
            user=user,
            storage=storage,
        )
    except (ImageSuggestionError, ImageError) as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 422)
    return _images_back(draft)


@router.post("/social/drafts/{draft_id}/images/{candidate_id}/choose", response_model=None)
def image_choose(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    draft_id: int,
    candidate_id: int,
) -> HTMLResponse | RedirectResponse:
    draft = _images_draft(session, draft_id)
    try:
        choose_candidate(session, draft, candidate_id, user)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _images_back(draft)


@router.post("/social/drafts/{draft_id}/images/discard", response_model=None)
def image_discard(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    draft = _images_draft(session, draft_id)
    try:
        discard_candidates(session, draft, user)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _images_back(draft)


@router.post("/social/image-hints/{hint_id}/confirm", response_model=None)
def image_hint_confirm(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, hint_id: int
) -> HTMLResponse | RedirectResponse:
    """A person confirms a hint on the image prompt – recorded in the audit trail."""
    hint = session.get(PromptHint, hint_id)
    if hint is None:
        raise HTTPException(status_code=404, detail="Hinweis nicht gefunden")
    draft = _images_draft(session, hint.draft_id)
    try:
        confirm_prompt_hint(session, hint, user)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _images_back(draft)


@router.get("/social/images/candidates/{candidate_id}", response_model=None)
def image_candidate_file(session: SessionDep, candidate_id: int) -> Response:
    if not image_generation_enabled():
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    candidate = session.get(ImageCandidate, candidate_id)
    if candidate is None or not Path(candidate.stored_path).is_file():
        raise HTTPException(status_code=404, detail="Variante nicht gefunden")
    return Response(
        Path(candidate.stored_path).read_bytes(),
        media_type=candidate.media_type,
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )


# --- audit ---


@router.get("/social/briefings/{briefing_id}/audit", response_class=HTMLResponse)
def briefing_audit(
    request: Request, session: SessionDep, config: ConfigDep, briefing_id: int
) -> HTMLResponse:
    """Chronological trail of all AI steps and human actions of a briefing."""
    briefing = _briefing_or_404(session, briefing_id)
    entries = briefing_audit_trail(session, briefing_id)
    return templates.TemplateResponse(
        request,
        "social_audit.html",
        {"briefing": briefing, "entries": entries, "config": config, **_labels(config)},
    )


# --- calendar and export ---


@router.post("/social/drafts/{draft_id}/schedule", response_model=None)
def draft_schedule(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    draft_id: int,
    when: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Set or move the planned date (local time) of an approved post."""
    draft = _draft_or_404(session, draft_id)
    try:
        when_local = datetime.fromisoformat(when)
    except ValueError:
        return _render_briefing(
            request, session, config, draft.briefing_id, "Bitte Datum und Uhrzeit angeben.", 422
        )
    response = _respond(
        request, session, config, draft, lambda: schedule_draft(session, draft, when_local, user)
    )
    if back.startswith("/social/calendar") and isinstance(response, RedirectResponse):
        return RedirectResponse(back, status_code=303)
    return response


@router.post("/social/drafts/{draft_id}/unschedule", response_model=None)
def draft_unschedule(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    draft = _draft_or_404(session, draft_id)
    return _respond(request, session, config, draft, lambda: unschedule_draft(session, draft, user))


@router.post("/social/drafts/{draft_id}/publish", response_model=None)
def draft_publish(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Record that the post was published by hand (the system posts nothing)."""
    draft = _draft_or_404(session, draft_id)
    return _respond(request, session, config, draft, lambda: publish_draft(session, draft, user))


@router.post("/social/drafts/{draft_id}/external", response_model=None)
def draft_mark_external(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Record that a person planned the post in the platform's tool ("eingeplant (extern)")."""
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request, session, config, draft, lambda: mark_scheduled_externally(session, draft, user)
    )


@router.post("/social/drafts/{draft_id}/external/undo", response_model=None)
def draft_unmark_external(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request, session, config, draft, lambda: unmark_scheduled_externally(session, draft, user)
    )


@router.get("/social/drafts/{draft_id}/image.jpg", response_model=None)
def draft_image_jpeg(session: SessionDep, draft_id: int, asset: int) -> Response:
    """Image of an approved post as JPEG in the channel's format (metadata removed)."""
    draft = _draft_or_404(session, draft_id)
    if draft.status not in (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED):
        raise HTTPException(status_code=409, detail="Download erst nach der Freigabe")
    image = session.get(Asset, asset)
    if (
        image is None
        or image.briefing_id != draft.briefing_id
        or image.draft_id not in (None, draft.id)
    ):
        raise HTTPException(status_code=404, detail="Bild nicht gefunden")
    path = Path(image.stored_path)
    if not is_usable(image) or not path.is_file():
        raise HTTPException(status_code=409, detail="Bild darf nicht verwendet werden")
    fmt = CHANNEL_IMAGE_FORMATS[draft.channel]
    try:
        body = export_jpeg(path.read_bytes(), fmt)
    except ImageExportError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    label = "-ki-generiert" if image.ai_generated else ""
    name = f"beitrag-{draft.id}-{draft.channel.value}-{fmt.value.replace(':', 'x')}{label}.jpg"
    return Response(
        body,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/social/briefings/{briefing_id}/graphic.jpg", response_model=None)
def briefing_graphic(
    session: SessionDep,
    config: ConfigDep,
    briefing_id: int,
    format: str = ImageFormat.PORTRAIT.value,  # noqa: A002 - query parameter name
    background: str = "color",
    headline: str = "",
    download: bool = False,
    facts: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """Graphic from the template of the occasion type: rendered by code, facts checked."""
    briefing = _briefing_or_404(session, briefing_id)
    if download and not _has_approved_post(session, briefing_id):
        raise HTTPException(
            status_code=409, detail="Download erst nach der Freigabe eines Beitrags"
        )
    try:
        fmt = ImageFormat(format)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Unbekanntes Format") from error
    image: Asset | None = None
    if background != "color":
        image = session.get(Asset, int(background)) if background.isdigit() else None
        if image is None or image.briefing_id != briefing_id or not is_usable(image):
            raise HTTPException(status_code=404, detail="Hintergrundbild nicht verfügbar")
    content = Path(image.stored_path).read_bytes() if image else None
    try:
        keys = parse_fact_keys(facts or [], BriefingFacts.from_json(briefing.facts))
        body, _ = render_graphic(
            config, briefing, fmt, headline=headline, background=content, fact_keys=keys
        )
    except GraphicError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (GraphicLayoutError, ImageExportError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    label = "-ki-generiert" if image is not None and image.ai_generated else ""
    name = f"grafik-briefing-{briefing_id}-{fmt.value.replace(':', 'x')}{label}.jpg"
    disposition = "attachment" if download else "inline"
    return Response(
        body,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/social/drafts/{draft_id}/export", response_class=PlainTextResponse)
def draft_export(session: SessionDep, draft_id: int) -> PlainTextResponse:
    """The approved post as a text file."""
    draft = _draft_or_404(session, draft_id)
    try:
        text = export_text(draft)
    except CalendarError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return PlainTextResponse(
        text, headers={"Content-Disposition": f'attachment; filename="beitrag-{draft_id}.txt"'}
    )


@router.get("/social/calendar.ics")
def calendar_ics(session: SessionDep, config: ConfigDep) -> Response:
    """All planned and published posts as iCalendar file."""
    labels = _labels(config)
    entries = calendar_entries(session, date(1970, 1, 1), date(9999, 1, 1))
    body = build_ics([(e.draft, summary_line(e.draft, e.briefing, labels)) for e in entries])
    return Response(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="redaktionskalender.ics"'},
    )


@router.get("/social/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    view: str = "week",
    day: Annotated[str | None, Query(alias="date")] = None,
) -> HTMLResponse:
    """Week or month view of the planned posts."""
    try:
        current = date.fromisoformat(day) if day else to_local(utcnow()).date()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Ungültiges Datum") from error
    if view == "month":
        weeks = month_view(session, current)
        first = current.replace(day=1)
        previous = (first - timedelta(days=1)).replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
    else:
        view = "week"
        weeks = [week_view(session, current)]
        previous = week_start(current) - timedelta(days=7)
        following = week_start(current) + timedelta(days=7)
    unplanned = session.exec(
        select(PostDraft).where(PostDraft.status == PostStatus.APPROVED).order_by(PostDraft.id)  # type: ignore[arg-type]
    ).all()
    return templates.TemplateResponse(
        request,
        "social_calendar.html",
        {
            "view": view,
            "current": current,
            "today": to_local(utcnow()).date(),
            "weeks": weeks,
            "previous": previous,
            "following": following,
            "unplanned": unplanned,
            "config": config,
            **_labels(config),
        },
    )


# --- campaigns ---


def _campaign_or_404(session: Session, campaign_id: int) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Kampagne nicht gefunden")
    return campaign


@router.get("/social/campaigns", response_class=HTMLResponse)
def campaign_list(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    campaigns = session.exec(select(Campaign).order_by(col(Campaign.id).desc())).all()
    return templates.TemplateResponse(
        request,
        "social_campaigns.html",
        {"campaigns": campaigns, "config": config, "error": None, **_labels(config)},
    )


def _campaign_form(
    request: Request,
    config: SocialConfig,
    values: dict[str, str],
    channels: list[str] | None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = _form_context(config, values, channels, errors) | {
        "campaign": True,
        "steps": config.industry.campaign_steps,
    }
    return templates.TemplateResponse(
        request, "social_campaign_form.html", context, status_code=status_code
    )


@router.get("/social/campaigns/new", response_class=HTMLResponse)
def campaign_new(request: Request, config: ConfigDep) -> HTMLResponse:
    return _campaign_form(request, config, {}, None)


@router.post("/social/campaigns", response_model=None)
async def campaign_create(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
) -> HTMLResponse | RedirectResponse:
    """Create the plan: one briefing per step with drafts for all chosen channels."""
    form = await request.form()
    values = {k: v for k, v in form.items() if isinstance(v, str) and k not in ("channels",)}
    channels = [v for v in form.getlist("channels") if isinstance(v, str)]
    errors: dict[str, str] = {}
    name, event_date = parse_campaign_form(values, errors)
    briefing_values = dict(values)
    if event_date and not briefing_values.get("datum"):
        briefing_values["datum"] = event_date.isoformat()
    try:
        briefing = parse_briefing_form(briefing_values, channels, config)
    except BriefingFormError as error:
        errors = error.errors | errors
        briefing = None
    if errors or briefing is None or event_date is None:
        return _campaign_form(request, config, values, channels, errors, 422)
    try:
        data = validate_campaign(config, name, event_date, briefing, to_local(utcnow()).date())
    except BriefingFormError as error:
        return _campaign_form(request, config, values, channels, error.errors, 422)
    campaign = create_campaign(session, config, client, data, user)
    return RedirectResponse(f"/social/campaigns/{campaign.id}", status_code=303)


@router.post("/social/campaigns/demo", response_model=None)
def campaign_demo(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
) -> HTMLResponse | RedirectResponse:
    """Create the fictitious demo campaign of the active industry."""
    try:
        data = load_demo_campaign(config, to_local(utcnow()).date())
    except (FileNotFoundError, BriefingFormError) as error:
        message = "Für dieses Branchenprofil gibt es keine Demo-Kampagne."
        if isinstance(error, BriefingFormError):
            message = " ".join(error.errors.values())
        campaigns = session.exec(select(Campaign).order_by(col(Campaign.id).desc())).all()
        return templates.TemplateResponse(
            request,
            "social_campaigns.html",
            {"campaigns": campaigns, "config": config, "error": message, **_labels(config)},
            status_code=409,
        )
    campaign = create_campaign(session, config, client, data, user)
    return RedirectResponse(f"/social/campaigns/{campaign.id}", status_code=303)


@router.get("/social/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    request: Request, session: SessionDep, config: ConfigDep, campaign_id: int
) -> HTMLResponse:
    """The plan of a campaign: steps with date, drafts, status and checks."""
    campaign = _campaign_or_404(session, campaign_id)
    plan = campaign_plan(session, config, campaign)
    draft_ids = [d.id for step in plan for d in step.drafts if d.id]
    results = _results_by_draft(session, draft_ids)
    return templates.TemplateResponse(
        request,
        "social_campaign_detail.html",
        {
            "campaign": campaign,
            "plan": plan,
            "lights": {draft_id: _light(r) for draft_id, r in results.items()},
            "config": config,
            **_labels(config),
        },
    )


# --- deletion concept ---


@router.post("/social/briefings/{briefing_id}/delete")
def briefing_delete(session: SessionDep, user: UserDep, briefing_id: int) -> RedirectResponse:
    """Delete a briefing with drafts, checks, images and model outputs (audit stays)."""
    briefing = _briefing_or_404(session, briefing_id)
    campaign_id = briefing.campaign_id
    delete_briefing(session, briefing_id, user)
    if campaign_id is not None and session.get(Campaign, campaign_id) is not None:
        return RedirectResponse(f"/social/campaigns/{campaign_id}", status_code=303)
    return RedirectResponse("/social", status_code=303)


@router.post("/social/drafts/{draft_id}/delete")
def draft_delete(session: SessionDep, user: UserDep, draft_id: int) -> RedirectResponse:
    """Delete one draft (the briefing and its other drafts stay)."""
    draft = _draft_or_404(session, draft_id)
    briefing_id = draft.briefing_id
    delete_draft(session, draft_id, user)
    return RedirectResponse(f"/social/briefings/{briefing_id}", status_code=303)


@router.post("/social/assets/{asset_id}/revoke", response_model=None)
def asset_revoke(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, asset_id: int
) -> HTMLResponse:
    """Consent withdrawn: delete the image and show the affected posts."""
    asset = _asset_or_404(session, asset_id)
    try:
        revocation = revoke_consent(session, asset, user)
    except AssetError as error:
        return _render_briefing(request, session, config, asset.briefing_id, str(error), 409)
    return _render_briefing(request, session, config, asset.briefing_id, revocation=revocation)


@router.post("/social/campaigns/{campaign_id}/delete")
def campaign_delete(session: SessionDep, user: UserDep, campaign_id: int) -> RedirectResponse:
    """Delete a campaign with all its briefings."""
    _campaign_or_404(session, campaign_id)
    delete_campaign(session, campaign_id, user)
    return RedirectResponse("/social/campaigns", status_code=303)
