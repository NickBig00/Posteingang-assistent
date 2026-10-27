"""Web routes of the social media module (SPEC_social section 8)."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlmodel import Session, col, select

from app.core.db import get_session
from app.core.llm.factory import get_provider
from app.core.models import utcnow
from app.core.outcomes import worst_outcome
from app.core.storage import get_storage_dir
from app.core.users import current_user
from app.core.web import NavEntry, module_templates
from app.social.assets import (
    MAX_IMAGE_BYTES,
    AssetError,
    briefing_assets,
    confirm_consent,
    delete_asset,
    save_asset,
)
from app.social.audit import briefing_audit_trail
from app.social.briefings import BriefingFormError, create_briefing, parse_briefing_form
from app.social.calendar import (
    CalendarError,
    build_ics,
    calendar_entries,
    export_text,
    month_view,
    publish_draft,
    schedule_draft,
    summary_line,
    to_local,
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
from app.social.highlight import highlight
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    CHANNEL_LABELS,
    POST_STATUS_LABELS,
    Asset,
    Briefing,
    Campaign,
    Channel,
    CheckResult,
    PostDraft,
    PostStatus,
)
from app.social.profiles import ADDRESS_FORM_LABELS, SocialConfig, load_social_config
from app.social.review import (
    ReviewNotAllowedError,
    approval_blockers,
    approve_draft,
    confirm_warning,
    edit_draft,
    recheck_draft,
    reject_draft,
    reopen_draft,
)
from app.social.sensitive import SENSITIVE_CHECK_LABELS
from app.social.status import latest_review

TEMPLATES_DIR = Path(__file__).parent / "templates"
NAV_ENTRY = NavEntry(module="social", label="Social Media", path="/social")

router = APIRouter()
templates = module_templates(TEMPLATES_DIR, NAV_ENTRY.module)
templates.env.globals["channel_labels"] = CHANNEL_LABELS
templates.env.globals["post_status_labels"] = POST_STATUS_LABELS
templates.env.globals["fact_labels"] = FACT_LABELS
templates.env.globals["address_form_labels"] = ADDRESS_FORM_LABELS
templates.env.globals["light_ok_label"] = "Prüfungen ok"
templates.env.filters["highlight"] = highlight
templates.env.filters["local"] = to_local


def get_social_config() -> SocialConfig:
    return load_social_config()


def get_social_client() -> SocialLLMClient:
    return SocialLLMClient(get_provider())


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


@router.get("/social", response_class=HTMLResponse)
def social_index(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    """Overview: briefings with their drafts and status."""
    briefings = session.exec(select(Briefing).order_by(Briefing.id.desc())).all()  # type: ignore[union-attr]
    drafts = _drafts_by_briefing(session)
    results = _results_by_draft(session, [d.id for ds in drafts.values() for d in ds if d.id])
    return templates.TemplateResponse(
        request,
        "social_index.html",
        {
            "briefings": briefings,
            "drafts": drafts,
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


def _render_briefing(
    request: Request,
    session: Session,
    config: SocialConfig,
    briefing_id: int,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        raise HTTPException(status_code=404, detail="Briefing nicht gefunden")
    drafts = session.exec(
        select(PostDraft).where(PostDraft.briefing_id == briefing_id).order_by(PostDraft.id)  # type: ignore[arg-type]
    ).all()
    facts = formatted_facts(BriefingFacts.from_json(briefing.facts))
    results = _results_by_draft(session, [d.id for d in drafts if d.id])
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
            "facts": facts,
            "config": config,
            "error": error,
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
