"""Web routes of the social media module (SPEC_social section 8)."""

import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlmodel import Session, col, select

from app.core.audit import audit_outputs
from app.core.db import get_session
from app.core.image_formats import (
    FORMAT_SPECS,
    ImageExportError,
    ImageFormat,
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
    revoke_consent,
    save_asset,
    set_shows_persons,
    usable_assets,
)
from app.social.audit import briefing_audit_trail
from app.social.briefing_image import (
    BriefingImageError,
    briefing_image,
    clear_briefing_image,
    image_locked,
    set_briefing_image,
    set_with_text,
)
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
from app.social.carousel import (
    CAROUSEL_CHANNELS,
    CAROUSEL_FORMATS,
    MAX_PAGES,
    CarouselError,
    PageInput,
    add_page,
    ai_pages,
    carousel_checks,
    confirm_carousel_check,
    export_zip,
    move_page,
    page_problems,
    pages,
    remove_page,
    render_page,
    set_carousel,
    suggest_plan,
    update_page,
)
from app.social.checks import CHECK_LABELS
from app.social.drafts import generate_drafts
from app.social.facts import FACT_LABELS, TEXT_FACT_KEYS, BriefingFacts, formatted_facts
from app.social.flow import draft_flow
from app.social.graphics import GraphicLayoutError
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
    pending_hints,
    save_prompt,
    suggest_prompt,
)
from app.social.infographic import (
    VARIANT_LABELS,
    InfoGraphicError,
    InfoTexts,
    check_blockers,
    clean_texts,
    confirm_check,
    get_info_graphic,
    graphic_checks,
    graphic_fact_keys,
    parse_points,
    render_info_graphic,
    revise_texts,
    save_layout,
    save_texts,
    story_texts,
    suggest_texts,
    text_errors,
    texts_for_format,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    CHANNEL_IMAGE_FORMATS,
    CHANNEL_LABELS,
    IMAGE_SOURCE_LABELS,
    POST_STATUS_LABELS,
    POST_STATUS_TONES,
    Asset,
    Briefing,
    Campaign,
    CarouselCheck,
    CarouselPage,
    Channel,
    CheckResult,
    ImageCandidate,
    ImageSource,
    InfoGraphic,
    InfoGraphicCheck,
    PostDraft,
    PostStatus,
    PromptHint,
)
from app.social.photo_edit import (
    AI_EDITED_LABEL,
    PhotoEditError,
    confirm_edit_hint,
    edit_photo,
)
from app.social.photo_edit import (
    QUICK_PICKS as PHOTO_QUICK_PICKS,
)
from app.social.post_image import (
    PostImageError,
    copy_from_first_step,
    copy_source,
    post_image_problems,
    remove_asset_choice,
    render_post_image,
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
from app.social.revisions import (
    IMAGE_QUICK_PICKS,
    POST_QUICK_PICKS,
    REQUEST_STEP,
    RevisionError,
    revise_image,
    revise_image_prompt,
    revise_post,
)
from app.social.sensitive import SENSITIVE_CHECK_LABELS
from app.social.status import latest_review

TEXT_FACT_PLACEHOLDERS = {
    "titel": "z. B. Rücken-Fit",
    "kursleitung": "z. B. Name der Trainerin oder des Trainers",
    "anmeldung": "z. B. über die App",
    "vorteil": "z. B. erster Monat gratis",
    "bedingungen": "z. B. nur für Neumitglieder, Laufzeit 12 Monate",
}
TEXT_FACT_MAX = {
    "titel": 120,
    "kursleitung": 120,
    "anmeldung": 200,
    "vorteil": 200,
    "bedingungen": 300,
}

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
templates.env.globals["image_source_labels"] = IMAGE_SOURCE_LABELS
templates.env.globals["hint_labels"] = HINT_LABELS
templates.env.globals["ai_edited_label"] = AI_EDITED_LABEL
templates.env.globals["photo_quick_picks"] = PHOTO_QUICK_PICKS
templates.env.globals["post_quick_picks"] = POST_QUICK_PICKS
templates.env.globals["image_quick_picks"] = IMAGE_QUICK_PICKS
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
        "check_labels": CHECK_LABELS
        | SENSITIVE_CHECK_LABELS
        | {"seiten": "Karussell"}
        | {r.key: r.label for r in rules},
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
    text_facts = [
        (key, FACT_LABELS[key], TEXT_FACT_PLACEHOLDERS[key], TEXT_FACT_MAX[key])
        for key in occasion.form_facts
        if key in TEXT_FACT_KEYS
    ]
    return {
        "config": config,
        "text_facts": text_facts,
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


def _info_context(
    request: Request,
    session: Session,
    config: SocialConfig,
    briefing: Briefing,
    usable: list[Asset],
) -> dict[str, object]:
    """Info graphic ("Text aufs Bild", SPEC_social 14.1): texts, checks, stored layout."""
    params = request.query_params
    graphic = get_info_graphic(session, briefing.id or 0)
    fmt = params.get("info_format") or ImageFormat.PORTRAIT.value
    if fmt not in {f.value for f in ImageFormat}:
        fmt = ImageFormat.PORTRAIT.value
    facts = BriefingFacts.from_json(briefing.facts)
    keys = graphic_fact_keys(graphic, briefing) if graphic else tuple(facts.present_keys())
    variant = (graphic.variant if graphic else None) or "zentriert"
    image = briefing_image(session, briefing.id or 0)
    background = str(image.asset_id) if image and image.asset_id else "color"
    query = urlencode([("format", fmt)])
    results = graphic_checks(session, graphic.id or 0) if graphic else []
    errors = text_errors(session, graphic) if graphic else []
    problems: list[str] = []
    if graphic is not None:
        try:
            # Preflight of facts and layout, so the page shows the reason instead of a
            # broken image.
            render_info_graphic(
                config,
                briefing,
                texts_for_format(graphic, ImageFormat(fmt)),
                ImageFormat(fmt),
                variant=variant,
                background=None,
                fact_keys=keys,
                check_text_facts=graphic.is_ai_suggestion,
            )
        except InfoGraphicError as error:
            problems = error.problems
        except GraphicLayoutError as error:
            problems = [str(error)]
    return {
        "graphic": graphic,
        "texts": InfoTexts.of(graphic) if graphic else InfoTexts(),
        "story": story_texts(graphic) if graphic else None,
        "format": fmt,
        "background": background,
        "fact_keys": keys,
        "available_facts": [(k, FACT_LABELS[k]) for k in facts.present_keys()],
        "variant": variant,
        "variants": VARIANT_LABELS,
        "results": results,
        "light": _light(results) if results else None,
        "errors": errors,
        "problems": problems,
        "blockers": check_blockers(session, graphic) if graphic else [],
        "src": f"/social/briefings/{briefing.id}/info-graphic.jpg?{query}",
        "ai_background": any(
            str(a.id) == background and (a.ai_generated or a.ai_edited) for a in usable
        ),
    }


def _carousel_context(
    session: Session, config: SocialConfig, briefing: Briefing
) -> dict[str, object]:
    rows = pages(session, briefing.id or 0)
    results = carousel_checks(session, briefing.id or 0)
    return {
        "pages": [
            {
                "page": page,
                "texts": InfoTexts.from_json(page.texts),
                "problems": page_problems(session, config, briefing, page, number),
            }
            for number, page in enumerate(rows, start=1)
        ],
        "results": results,
        "light": _light(list(results)) if results else None,  # type: ignore[arg-type]
        "ai_pages": ai_pages(session, rows),
        "max_pages": MAX_PAGES,
        "formats": CAROUSEL_FORMATS,
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
    images_enabled = image_generation_enabled()
    info = _info_context(request, session, config, briefing, usable)
    image = briefing_image(session, briefing_id)
    post_images = {
        d.id: {
            "problems": post_image_problems(
                session, config, briefing, CHANNEL_IMAGE_FORMATS[d.channel]
            ),
            "src": f"/social/drafts/{d.id}/preview.jpg",
        }
        for d in drafts
    }
    labels = _labels(config)
    flows = {d.id: draft_flow(session, config, d, labels["check_labels"]) for d in drafts}
    tabs = [
        {
            "id": d.channel.value,
            "label": CHANNEL_LABELS[d.channel],
            "symbol": flows[d.id].tab_symbol[0],
            "tone": flows[d.id].tab_symbol[1],
            "struck": d.status is PostStatus.REJECTED,
            "status_label": POST_STATUS_LABELS[d.status],
        }
        for d in drafts
    ]
    image_state_value = image_state(session, config, briefing) if images_enabled else None
    return templates.TemplateResponse(
        request,
        "social_briefing_detail.html",
        {
            "briefing": briefing,
            "drafts": drafts,
            "results": results,
            "lights": {draft_id: _light(r) for draft_id, r in results.items()},
            "blockers": {d.id: approval_blockers(session, d, config) for d in drafts},
            "reviews": {d.id: latest_review(session, d.id) for d in drafts if d.id},
            "assets": briefing_assets(session, briefing_id),
            "usable_assets": usable,
            "images_enabled": images_enabled,
            "image_state": image_state_value,
            "pending_hints": pending_hints(session, briefing_id) if images_enabled else [],
            "flows": flows,
            "tabs": tabs,
            "active_tab": _active_tab(request, session, list(drafts)),
            "released_channels": [d.channel.value for d in drafts if flows[d.id].released],
            "photos": [a for a in briefing_assets(session, briefing_id) if not a.ai_generated],
            "image_problems": sorted(
                {p for item in post_images.values() for p in item["problems"]}
            ),
            "carousel_drafts": [d for d in drafts if d.channel in CAROUSEL_CHANNELS],
            "carousel_open": any(d.carousel_enabled for d in drafts),
            "has_story": any(d.channel is Channel.INSTAGRAM_STORY for d in drafts),
            "image_formats": list(ImageFormat),
            "briefing_image": image,
            "chosen_asset": session.get(Asset, image.asset_id)
            if image and image.asset_id
            else None,
            "image_locked": image_locked(session, briefing_id),
            "copy_source": copy_source(session, briefing),
            "image_sources": list(ImageSource),
            "post_images": post_images,
            "info": info,
            "carousel": _carousel_context(session, config, briefing),
            "carousel_channels": CAROUSEL_CHANNELS,
            "facts": facts,
            "config": config,
            "error": error,
            "revocation": revocation,
            "toast": TOASTS.get(request.query_params.get("toast", "")) if not error else None,
            **labels,
        },
        status_code=status_code,
    )


_DRAFT_PATH = re.compile(r"^/social/drafts/(\d+)/")


def _active_tab(request: Request, session: Session, drafts: list[PostDraft]) -> str:
    """Tab shown first: the draft an action was about (error pages are rendered at the action's
    URL), else the first open draft. On normal pages the URL fragment decides (JavaScript)."""
    if not drafts:
        return ""
    match = _DRAFT_PATH.match(request.url.path)
    if match:
        for draft in drafts:
            if draft.id == int(match.group(1)):
                return draft.channel.value
    open_drafts = [d for d in drafts if d.status in EDITABLE_POST_STATUSES]
    return (open_drafts or drafts)[0].channel.value


EDITABLE_POST_STATUSES = frozenset(
    {PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW}
)


@router.get("/social/briefings/{briefing_id}", response_class=HTMLResponse)
def briefing_detail(
    request: Request, session: SessionDep, config: ConfigDep, briefing_id: int
) -> HTMLResponse:
    """The briefing as a flow: tabs per channel, steps text → image → check → publish."""
    return _render_briefing(request, session, config, briefing_id)


def _draft_or_404(session: Session, draft_id: int) -> PostDraft:
    draft = session.get(PostDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Entwurf nicht gefunden")
    return draft


# Short confirmations after an action (U8). Only these fixed texts are shown: the query
# parameter selects one, it is never echoed into the page.
TOASTS: dict[str, str] = {
    "gespeichert": "Gespeichert und geprüft",
    "ueberarbeitet": "Neuer KI-Vorschlag – bitte prüfen",
    "bestaetigt": "Bestätigt",
    "freigegeben": "Freigegeben – jetzt veröffentlichen",
    "verworfen": "Verworfen",
    "geoeffnet": "Wieder geöffnet",
    "geprueft": "Erneut geprüft",
    "bild": "Bild gewählt",
    "text_bild": "Text aufs Bild übernommen und geprüft",
    "erzeugt": "Bilder erzeugt – eine Variante wählen",
    "hinweise": "Beschreibung geprüft – Hinweise bestätigen, dann erzeugen",
    "uebernommen": "Von Schritt 1 übernommen",
    "geplant": "Termin gespeichert",
    "veroeffentlicht": "Als veröffentlicht markiert",
    "extern": "Als „eingeplant (extern)“ markiert",
    "karussell": "Karussell aktualisiert",
}


def _to_briefing(briefing_id: int, anchor: str, toast: str = "") -> RedirectResponse:
    query = f"?toast={toast}" if toast in TOASTS else ""
    return RedirectResponse(f"/social/briefings/{briefing_id}{query}#{anchor}", status_code=303)


def _back(draft: PostDraft, toast: str = "") -> RedirectResponse:
    return _to_briefing(draft.briefing_id, f"draft-{draft.id}", toast)


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
    return _back(draft, "bestaetigt")


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
    return _back(draft, "bestaetigt")


@router.post("/social/drafts/{draft_id}/approve", response_model=None)
def draft_approve(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Approve a draft – only without errors and with all warnings confirmed."""
    draft = _draft_or_404(session, draft_id)
    try:
        approve_draft(session, draft, user, config)
    except ReviewNotAllowedError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _back(draft, "freigegeben")


def _respond(
    request: Request,
    session: Session,
    config: SocialConfig,
    draft: PostDraft,
    action: Callable[[], object],
    toast: str = "",
) -> HTMLResponse | RedirectResponse:
    try:
        action()
    except (ReviewNotAllowedError, AssetError, CalendarError, BriefingImageError) as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _back(draft, toast)


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
    hashtags_add: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """A person edits text and hashtags (chips plus a field for new ones); checks run again."""
    draft = _draft_or_404(session, draft_id)
    hashtags = f"{hashtags} {hashtags_add}".strip()
    return _respond(
        request,
        session,
        config,
        draft,
        lambda: edit_draft(session, config, client, draft, text, hashtags, user),
        "gespeichert",
    )


@router.post("/social/drafts/{draft_id}/revise", response_model=None)
def draft_revise(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    draft_id: int,
    instruction: Annotated[str, Form()] = "",
    quick: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """ "Was soll anders werden?": the AI revises the post text; all checks run again."""
    draft = _draft_or_404(session, draft_id)
    try:
        revise_post(session, config, client, draft, quick or instruction, user)
    except RevisionError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 422)
    return _back(draft, "ueberarbeitet")


@router.post("/social/drafts/{draft_id}/reject", response_model=None)
def draft_reject(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Reject a draft or withdraw it before publishing."""
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request, session, config, draft, lambda: reject_draft(session, draft, user), "verworfen"
    )


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
        request,
        session,
        config,
        draft,
        lambda: reopen_draft(session, config, client, draft, user),
        "geoeffnet",
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
        request,
        session,
        config,
        draft,
        lambda: recheck_draft(session, config, client, draft, user),
        "geprueft",
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
        asset = save_asset(
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
    # Uploading means "this photo": it becomes the image of the briefing (while not locked).
    if not image_locked(session, briefing_id):
        set_briefing_image(session, briefing_id, ImageSource.UPLOAD, asset.id, user)
    return _image_back(briefing_id)


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
    return _image_back(asset.briefing_id, "bestaetigt")


@router.post("/social/assets/{asset_id}/persons", response_model=None)
def asset_persons(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    asset_id: int,
    shows_persons: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """ "Personen zu sehen?" after the upload; yes means the consent must be confirmed."""
    asset = _asset_or_404(session, asset_id)
    if image_locked(session, asset.briefing_id):
        return _render_briefing(
            request, session, config, asset.briefing_id, "Das Bild ist freigegeben.", 409
        )
    try:
        set_shows_persons(session, asset, shows_persons, user)
    except AssetError as error:
        return _render_briefing(request, session, config, asset.briefing_id, str(error), 409)
    return _image_back(asset.briefing_id, "")


@router.post("/social/assets/{asset_id}/delete", response_model=None)
def asset_delete(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, asset_id: int
) -> HTMLResponse | RedirectResponse:
    asset = _asset_or_404(session, asset_id)
    briefing_id = asset.briefing_id
    try:
        remove_asset_choice(session, asset)
    except BriefingImageError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    delete_asset(session, asset, user)
    return _image_back(briefing_id, "")


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


# --- the image of the briefing (one for all channels, SPEC_social 14.3) ---


def _image_back(briefing_id: int, toast: str = "bild") -> RedirectResponse:
    return _to_briefing(briefing_id, "bild", toast)


@router.post("/social/briefings/{briefing_id}/image", response_model=None)
def briefing_image_choose(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    briefing_id: int,
    source: Annotated[str, Form()] = "",
    asset: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """ "Welches Bild?": own photo, AI image or colour fill for all channels."""
    _briefing_or_404(session, briefing_id)
    try:
        choice = ImageSource(source)
    except ValueError:
        return _render_briefing(
            request, session, config, briefing_id, "Unbekannte Bildquelle.", 422
        )
    try:
        set_briefing_image(
            session, briefing_id, choice, int(asset) if asset.isdigit() else None, user
        )
    except BriefingImageError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _image_back(briefing_id)


@router.post("/social/briefings/{briefing_id}/image/text", response_model=None)
def briefing_image_text(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    briefing_id: int,
    with_text: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """ "Text aufs Bild?" yes/no."""
    _briefing_or_404(session, briefing_id)
    try:
        set_with_text(session, briefing_id, with_text, user)
    except BriefingImageError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _image_back(briefing_id, "")


@router.post("/social/briefings/{briefing_id}/image/clear", response_model=None)
def briefing_image_clear(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, briefing_id: int
) -> HTMLResponse | RedirectResponse:
    _briefing_or_404(session, briefing_id)
    try:
        clear_briefing_image(session, briefing_id, user)
    except BriefingImageError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _image_back(briefing_id)


@router.post("/social/briefings/{briefing_id}/image/copy", response_model=None)
def briefing_image_copy(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    briefing_id: int,
) -> HTMLResponse | RedirectResponse:
    """Campaigns: "Wie Schritt 1" – image and text on the image as a copy."""
    briefing = _briefing_or_404(session, briefing_id)
    try:
        copy_from_first_step(session, config, client, briefing, user)
    except BriefingImageError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _image_back(briefing_id, "uebernommen")


# --- AI images (only with IMAGE_GENERATION_ENABLED) ---


ImageProviderDep = Annotated[ImageProvider, Depends(get_social_image_provider)]


def _images_briefing(session: Session, briefing_id: int) -> Briefing:
    if not image_generation_enabled():
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return _briefing_or_404(session, briefing_id)


def _images_back(briefing_id: int, toast: str = "", anchor: str = "ki-bild") -> RedirectResponse:
    return _to_briefing(briefing_id, anchor, toast)


@router.post("/social/briefings/{briefing_id}/images/suggest", response_model=None)
def image_suggest(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    briefing_id: int,
) -> HTMLResponse | RedirectResponse:
    """The AI suggests an image prompt from briefing and image style (visible, editable)."""
    briefing = _images_briefing(session, briefing_id)
    try:
        suggest_prompt(session, config, client, briefing)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _images_back(briefing_id)


@router.post("/social/briefings/{briefing_id}/images/generate", response_model=None)
def image_generate(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    provider: ImageProviderDep,
    user: UserDep,
    storage: StorageDep,
    briefing_id: int,
    prompt: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Check the prompt and generate 2–3 variants in 4:5 (cost brake per briefing, audit)."""
    briefing = _images_briefing(session, briefing_id)
    try:
        generate_variants(
            session, config, provider, briefing, prompt=prompt, user=user, storage=storage
        )
    except (ImageSuggestionError, ImageError) as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _images_back(briefing_id, "erzeugt", "ki-varianten")


@router.post("/social/briefings/{briefing_id}/images/check", response_model=None)
def image_check(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    briefing_id: int,
    prompt: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """ "Prüfen": store the description and show its hints before anything is generated."""
    briefing = _images_briefing(session, briefing_id)
    try:
        save_prompt(session, config, briefing, prompt)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _images_back(briefing_id, "hinweise")


@router.post("/social/briefings/{briefing_id}/images/{candidate_id}/choose", response_model=None)
def image_choose(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    briefing_id: int,
    candidate_id: int,
) -> HTMLResponse | RedirectResponse:
    briefing = _images_briefing(session, briefing_id)
    try:
        choose_candidate(session, briefing, candidate_id, user)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _images_back(briefing_id, "bild")


@router.post("/social/briefings/{briefing_id}/images/discard", response_model=None)
def image_discard(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, briefing_id: int
) -> HTMLResponse | RedirectResponse:
    briefing = _images_briefing(session, briefing_id)
    try:
        discard_candidates(session, briefing, user)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 409)
    return _images_back(briefing_id)


@router.post("/social/briefings/{briefing_id}/images/revise", response_model=None)
def image_revise(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    provider: ImageProviderDep,
    user: UserDep,
    storage: StorageDep,
    briefing_id: int,
    instruction: Annotated[str, Form()] = "",
    quick: Annotated[str, Form()] = "",
    regenerate: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """Revise the image prompt by instruction, optionally generating new variants at once."""
    briefing = _images_briefing(session, briefing_id)
    text = quick or instruction
    try:
        if regenerate:
            revise_image(session, config, client, provider, briefing, text, user, storage)
        else:
            revise_image_prompt(session, config, client, briefing, text, user)
    except (RevisionError, ImageSuggestionError, ImageError) as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _images_back(briefing_id, "ueberarbeitet", "ki-varianten")


@router.post("/social/assets/{asset_id}/edit", response_model=None)
def asset_edit(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    provider: ImageProviderDep,
    user: UserDep,
    storage: StorageDep,
    asset_id: int,
    instruction: Annotated[str, Form()] = "",
    quick: Annotated[str, Form()] = "",
    image_format: Annotated[str, Form()] = ImageFormat.PORTRAIT.value,
) -> HTMLResponse | RedirectResponse:
    """Edit an own photo with AI; the result is a new image labelled "KI-bearbeitet" and
    becomes the image of the briefing at once."""
    if not image_generation_enabled():
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    asset = _asset_or_404(session, asset_id)
    try:
        fmt = ImageFormat(image_format)
    except ValueError:
        fmt = ImageFormat.PORTRAIT
    try:
        edited = edit_photo(
            session,
            provider,
            asset,
            instruction=quick or instruction,
            fmt=fmt,
            user=user,
            storage=storage,
        )
        set_briefing_image(session, asset.briefing_id, ImageSource.UPLOAD, edited.id, user)
    except (PhotoEditError, ImageError, BriefingImageError) as error:
        return _render_briefing(request, session, config, asset.briefing_id, str(error), 422)
    return RedirectResponse(
        f"/social/briefings/{asset.briefing_id}#asset-{edited.id}", status_code=303
    )


@router.post("/social/assets/{asset_id}/edit-hint/confirm", response_model=None)
def asset_edit_hint_confirm(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, asset_id: int
) -> HTMLResponse | RedirectResponse:
    asset = _asset_or_404(session, asset_id)
    try:
        confirm_edit_hint(session, asset, user)
    except PhotoEditError as error:
        return _render_briefing(request, session, config, asset.briefing_id, str(error), 409)
    return RedirectResponse(
        f"/social/briefings/{asset.briefing_id}#asset-{asset.id}", status_code=303
    )


@router.post("/social/image-hints/{hint_id}/confirm", response_model=None)
def image_hint_confirm(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, hint_id: int
) -> HTMLResponse | RedirectResponse:
    """A person confirms a hint on the image prompt – recorded in the audit trail."""
    hint = session.get(PromptHint, hint_id)
    if hint is None or hint.briefing_id is None:
        raise HTTPException(status_code=404, detail="Hinweis nicht gefunden")
    _images_briefing(session, hint.briefing_id)
    try:
        confirm_prompt_hint(session, hint, user)
    except ImageSuggestionError as error:
        return _render_briefing(request, session, config, hint.briefing_id, str(error), 409)
    return _images_back(hint.briefing_id, "bestaetigt")


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
    # Instructions of revisions in plain text (deletable audit output; gone after deletion).
    outputs = audit_outputs(session, [e.id for e in entries if e.id and e.step == REQUEST_STEP])
    instructions = {
        entry_id: json.loads(text).get("instruction", "") for entry_id, text in outputs.items()
    }
    return templates.TemplateResponse(
        request,
        "social_audit.html",
        {
            "briefing": briefing,
            "entries": entries,
            "instructions": instructions,
            "config": config,
            **_labels(config),
        },
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
        request,
        session,
        config,
        draft,
        lambda: schedule_draft(session, draft, when_local, user),
        "geplant",
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
    return _respond(
        request,
        session,
        config,
        draft,
        lambda: publish_draft(session, draft, user),
        "veroeffentlicht",
    )


@router.post("/social/drafts/{draft_id}/external", response_model=None)
def draft_mark_external(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    """Record that a person planned the post in the platform's tool ("eingeplant (extern)")."""
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request,
        session,
        config,
        draft,
        lambda: mark_scheduled_externally(session, draft, user),
        "extern",
    )


@router.post("/social/drafts/{draft_id}/external/undo", response_model=None)
def draft_unmark_external(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, draft_id: int
) -> HTMLResponse | RedirectResponse:
    draft = _draft_or_404(session, draft_id)
    return _respond(
        request, session, config, draft, lambda: unmark_scheduled_externally(session, draft, user)
    )


def _post_image_or_error(
    session: Session, config: SocialConfig, draft: PostDraft
) -> tuple[bytes, str]:
    """The rendered post image of the draft and its download name (HTTPException otherwise)."""
    briefing = _briefing_or_404(session, draft.briefing_id)
    fmt = CHANNEL_IMAGE_FORMATS[draft.channel]
    try:
        image = render_post_image(session, config, briefing, fmt)
    except PostImageError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if image is None:
        raise HTTPException(status_code=404, detail="Kein Bild gewählt")
    name = (
        f"beitrag-{draft.id}-{draft.channel.value}-{fmt.value.replace(':', 'x')}"
        f"{image.ai_label}.jpg"
    )
    return image.content, name


def _jpeg(body: bytes, name: str, *, download: bool) -> Response:
    return Response(
        body,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


LIVE_FIELDS = ("headline", "subline", "intro", "points", "closing")


def _live_texts(values: dict[str, str]) -> InfoTexts:
    return clean_texts(
        InfoTexts(
            values.get("headline", "")[:200],
            values.get("subline", "")[:200],
            values.get("intro", "")[:400],
            parse_points(values.get("points", "")[:1000]),
            values.get("closing", "")[:200],
        )
    )


@router.get("/social/drafts/{draft_id}/preview.jpg", response_model=None)
def draft_image_preview(
    request: Request, session: SessionDep, config: ConfigDep, draft_id: int
) -> Response:
    """The post image exactly as it will be posted (always visible, never a download).

    With `live=1` the text on the image comes from the query (typing, not saved yet).
    """
    draft = _draft_or_404(session, draft_id)
    params = request.query_params
    if params.get("live") != "1":
        body, name = _post_image_or_error(session, config, draft)
        return _jpeg(body, name, download=False)
    briefing = _briefing_or_404(session, draft.briefing_id)
    texts = _live_texts({k: params.get(k, "") for k in LIVE_FIELDS})
    try:
        image = render_post_image(
            session, config, briefing, CHANNEL_IMAGE_FORMATS[draft.channel], texts=texts
        )
    except PostImageError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if image is None:
        raise HTTPException(status_code=404, detail="Kein Bild gewählt")
    return _jpeg(image.content, f"vorschau-{draft.id}.jpg", download=False)


@router.post("/social/briefings/{briefing_id}/info-graphic/live", response_class=HTMLResponse)
async def info_graphic_live(
    request: Request, session: SessionDep, config: ConfigDep, briefing_id: int
) -> HTMLResponse:
    """HTMX while typing: new preview images for every tab (out-of-band swaps) or, if the text
    does not fit or adds facts, a short note – the preview keeps the last valid state."""
    briefing = _briefing_or_404(session, briefing_id)
    form = await request.form()
    values = {k: str(form.get(k) or "") for k in LIVE_FIELDS}
    texts = _live_texts(values)
    drafts = session.exec(
        select(PostDraft).where(PostDraft.briefing_id == briefing_id).order_by(col(PostDraft.id))
    ).all()
    problems: list[str] = []
    try:
        render_post_image(session, config, briefing, ImageFormat.PORTRAIT, texts=texts)
    except PostImageError as error:
        problems = error.problems
    query = urlencode({"live": "1", **values})
    return templates.TemplateResponse(
        request,
        "_flow_live.html",
        {"drafts": drafts, "problems": problems, "query": query},
    )


@router.get("/social/drafts/{draft_id}/image.jpg", response_model=None)
def draft_image_jpeg(session: SessionDep, config: ConfigDep, draft_id: int) -> Response:
    """The rendered post image in the channel's format, only after the approval."""
    draft = _draft_or_404(session, draft_id)
    if draft.status not in (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED):
        raise HTTPException(status_code=409, detail="Download erst nach der Freigabe")
    body, name = _post_image_or_error(session, config, draft)
    return _jpeg(body, name, download=True)


# --- info graphic ---


def _info_back(briefing_id: int, toast: str = "text_bild") -> RedirectResponse:
    return _to_briefing(briefing_id, "info-grafik", toast)


@router.post("/social/briefings/{briefing_id}/info-graphic/layout", response_model=None)
def info_graphic_layout(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    user: UserDep,
    briefing_id: int,
    variant: Annotated[str, Form()] = "zentriert",
    facts: Annotated[list[str] | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """Design variant and facts on the image (stored, used by every channel)."""
    briefing = _briefing_or_404(session, briefing_id)
    try:
        save_layout(session, briefing, variant=variant, fact_keys=facts, user=user)
    except InfoGraphicError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _info_back(briefing_id, "")


@router.post("/social/briefings/{briefing_id}/info-graphic", response_model=None)
def info_graphic_save(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    briefing_id: int,
    headline: Annotated[str, Form()] = "",
    subline: Annotated[str, Form()] = "",
    intro: Annotated[str, Form()] = "",
    points: Annotated[str, Form()] = "",
    closing: Annotated[str, Form()] = "",
    story_headline: Annotated[str, Form()] = "",
    story_intro: Annotated[str, Form()] = "",
    story_points: Annotated[str, Form()] = "",
    story_closing: Annotated[str, Form()] = "",
    variant: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Texts written or edited by a person; all checks run again when they changed."""
    briefing = _briefing_or_404(session, briefing_id)
    current = get_info_graphic(session, briefing_id)
    variant = variant or (current.variant if current else "zentriert")
    texts = InfoTexts(headline, subline, intro, parse_points(points), closing)
    story = InfoTexts(story_headline, "", story_intro, parse_points(story_points), story_closing)
    try:
        save_texts(
            session, config, client, briefing, texts, story=story, variant=variant, user=user
        )
    except InfoGraphicError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _info_back(briefing_id)


@router.post("/social/briefings/{briefing_id}/info-graphic/suggest", response_model=None)
def info_graphic_suggest(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    briefing_id: int,
) -> HTMLResponse | RedirectResponse:
    """ "Text für Grafik vorschlagen" – AI suggestion, labelled and freely editable."""
    briefing = _briefing_or_404(session, briefing_id)
    try:
        suggest_texts(session, config, client, briefing, user)
    except InfoGraphicError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _info_back(briefing_id, "ueberarbeitet")


@router.post("/social/briefings/{briefing_id}/info-graphic/revise", response_model=None)
def info_graphic_revise(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    briefing_id: int,
    instruction: Annotated[str, Form()] = "",
    quick: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    briefing = _briefing_or_404(session, briefing_id)
    try:
        revise_texts(session, config, client, briefing, quick or instruction, user)
    except InfoGraphicError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _info_back(briefing_id, "ueberarbeitet")


@router.post("/social/info-graphic-checks/{result_id}/confirm", response_model=None)
def info_graphic_confirm(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, result_id: int
) -> HTMLResponse | RedirectResponse:
    result = session.get(InfoGraphicCheck, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Prüfergebnis nicht gefunden")
    graphic = session.get(InfoGraphic, result.graphic_id)
    if graphic is None:
        raise HTTPException(status_code=404, detail="Grafik nicht gefunden")
    try:
        confirm_check(session, result, user)
    except InfoGraphicError as error:
        return _render_briefing(request, session, config, graphic.briefing_id, str(error), 409)
    return _info_back(graphic.briefing_id, "bestaetigt")


@router.get("/social/briefings/{briefing_id}/info-graphic.jpg", response_model=None)
def info_graphic_file(
    session: SessionDep,
    config: ConfigDep,
    briefing_id: int,
    format: str = ImageFormat.PORTRAIT.value,  # noqa: A002 - query parameter name
) -> Response:
    """Preview of the text on the image over a colour fill (the post image itself is
    `/social/drafts/{id}/preview.jpg`; downloads only per approved post)."""
    briefing = _briefing_or_404(session, briefing_id)
    try:
        fmt = ImageFormat(format)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Unbekanntes Format") from error
    graphic = get_info_graphic(session, briefing_id)
    if graphic is None:
        raise HTTPException(status_code=404, detail="Noch kein Grafiktext")
    errors = text_errors(session, graphic)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    try:
        body = render_info_graphic(
            config,
            briefing,
            texts_for_format(graphic, fmt),
            fmt,
            variant=graphic.variant,
            fact_keys=graphic_fact_keys(graphic, briefing),
            check_text_facts=graphic.is_ai_suggestion,
        )
    except InfoGraphicError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (GraphicLayoutError, ImageExportError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _jpeg(body, f"info-grafik-briefing-{briefing_id}.jpg", download=False)


# --- carousel (Instagram) ---


def _carousel_back(briefing_id: int) -> RedirectResponse:
    return _to_briefing(briefing_id, "mehrere-bilder", "karussell")


def _page_or_404(session: Session, page_id: int) -> tuple[CarouselPage, Briefing]:
    page = session.get(CarouselPage, page_id)
    if page is None or page.briefing_id is None:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden")
    return page, _briefing_or_404(session, page.briefing_id)


def _page_input(
    kind: str,
    headline: str,
    subline: str,
    intro: str,
    points: str,
    closing: str,
    asset: str,
    show_facts: bool,
    variant: str,
) -> PageInput:
    return PageInput(
        kind=kind,
        texts=InfoTexts(headline, subline, intro, parse_points(points), closing),
        asset_id=int(asset) if asset.isdigit() else None,
        show_facts=show_facts,
        variant=variant,
    )


PageForm = Annotated[str, Form()]


@router.post("/social/drafts/{draft_id}/carousel", response_model=None)
def carousel_toggle(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    draft_id: int,
    enabled: Annotated[bool, Form()] = False,
) -> HTMLResponse | RedirectResponse:
    """ "Mehrere Bilder" on or off for one Instagram or Facebook post."""
    draft = _draft_or_404(session, draft_id)
    try:
        set_carousel(session, config, client, draft, enabled, user)
    except CarouselError as error:
        return _render_briefing(request, session, config, draft.briefing_id, str(error), 409)
    return _carousel_back(draft.briefing_id)


@router.post("/social/briefings/{briefing_id}/carousel/suggest", response_model=None)
def carousel_suggest(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    briefing_id: int,
    draft: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """AI suggestion for the split into pages (labelled, editable)."""
    briefing = _briefing_or_404(session, briefing_id)
    post = session.get(PostDraft, int(draft)) if draft.isdigit() else None
    text = post.text if post is not None and post.briefing_id == briefing_id else ""
    try:
        suggest_plan(session, config, client, briefing, user, text)
    except CarouselError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _carousel_back(briefing_id)


@router.post("/social/briefings/{briefing_id}/carousel/pages", response_model=None)
def carousel_add(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    briefing_id: int,
    kind: PageForm = "info",
    headline: PageForm = "",
    subline: PageForm = "",
    intro: PageForm = "",
    points: PageForm = "",
    closing: PageForm = "",
    asset: PageForm = "",
    show_facts: Annotated[bool, Form()] = False,
    variant: PageForm = "zentriert",
) -> HTMLResponse | RedirectResponse:
    briefing = _briefing_or_404(session, briefing_id)
    data = _page_input(kind, headline, subline, intro, points, closing, asset, show_facts, variant)
    try:
        add_page(session, config, client, briefing, data, user)
    except CarouselError as error:
        return _render_briefing(request, session, config, briefing_id, str(error), 422)
    return _carousel_back(briefing_id)


@router.post("/social/carousel-pages/{page_id}", response_model=None)
def carousel_update(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    page_id: int,
    headline: PageForm = "",
    subline: PageForm = "",
    intro: PageForm = "",
    points: PageForm = "",
    closing: PageForm = "",
    asset: PageForm = "",
    show_facts: Annotated[bool, Form()] = False,
    variant: PageForm = "zentriert",
) -> HTMLResponse | RedirectResponse:
    page, briefing = _page_or_404(session, page_id)
    data = _page_input(
        page.kind, headline, subline, intro, points, closing, asset, show_facts, variant
    )
    try:
        update_page(session, config, client, page, data, user)
    except CarouselError as error:
        return _render_briefing(request, session, config, briefing.id or 0, str(error), 422)
    return _carousel_back(briefing.id or 0)


@router.post("/social/carousel-pages/{page_id}/move", response_model=None)
def carousel_move(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    page_id: int,
    direction: Annotated[int, Form()] = 1,
) -> HTMLResponse | RedirectResponse:
    page, briefing = _page_or_404(session, page_id)
    try:
        move_page(session, config, client, page, -1 if direction < 0 else 1, user)
    except CarouselError as error:
        return _render_briefing(request, session, config, briefing.id or 0, str(error), 409)
    return _carousel_back(briefing.id or 0)


@router.post("/social/carousel-pages/{page_id}/delete", response_model=None)
def carousel_remove(
    request: Request,
    session: SessionDep,
    config: ConfigDep,
    client: ClientDep,
    user: UserDep,
    page_id: int,
) -> HTMLResponse | RedirectResponse:
    page, briefing = _page_or_404(session, page_id)
    try:
        remove_page(session, config, client, page, user)
    except CarouselError as error:
        return _render_briefing(request, session, config, briefing.id or 0, str(error), 409)
    return _carousel_back(briefing.id or 0)


@router.post("/social/carousel-checks/{result_id}/confirm", response_model=None)
def carousel_confirm(
    request: Request, session: SessionDep, config: ConfigDep, user: UserDep, result_id: int
) -> HTMLResponse | RedirectResponse:
    result = session.get(CarouselCheck, result_id)
    if result is None or result.briefing_id is None:
        raise HTTPException(status_code=404, detail="Prüfergebnis nicht gefunden")
    try:
        confirm_carousel_check(session, result, user)
    except CarouselError as error:
        return _render_briefing(request, session, config, result.briefing_id, str(error), 409)
    return _carousel_back(result.briefing_id)


def _carousel_format(value: str) -> ImageFormat:
    try:
        fmt = ImageFormat(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Unbekanntes Format") from error
    if fmt not in CAROUSEL_FORMATS:
        raise HTTPException(status_code=422, detail="Karussells gibt es in 4:5 oder 1:1")
    return fmt


@router.get("/social/carousel-pages/{page_id}.jpg", response_model=None)
def carousel_page_file(
    session: SessionDep,
    config: ConfigDep,
    page_id: int,
    format: str = "4:5",  # noqa: A002
) -> Response:
    """Preview of one page (the download is the ZIP after approval)."""
    page, briefing = _page_or_404(session, page_id)
    try:
        body = render_page(session, config, briefing, page, _carousel_format(format))
    except CarouselError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(
        body,
        media_type="image/jpeg",
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/social/drafts/{draft_id}/carousel.zip", response_model=None)
def carousel_zip(
    session: SessionDep,
    config: ConfigDep,
    draft_id: int,
    format: str = "",  # noqa: A002
) -> Response:
    """All pages as ZIP with numbered JPEGs (01.jpg, 02.jpg, …), after the approval; by default
    in the format of the channel."""
    draft = _draft_or_404(session, draft_id)
    try:
        body = export_zip(session, config, draft, _carousel_format(format) if format else None)
    except CarouselError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    name = f"karussell-beitrag-{draft_id}-{draft.channel.value}.zip"
    return Response(
        body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
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
