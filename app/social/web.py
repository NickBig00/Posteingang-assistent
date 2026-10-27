"""Web routes of the social media module (SPEC_social section 8)."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.core.db import get_session
from app.core.llm.factory import get_provider
from app.core.users import current_user
from app.core.web import NavEntry, module_templates
from app.social.briefings import BriefingFormError, create_briefing, parse_briefing_form
from app.social.drafts import generate_drafts
from app.social.facts import FACT_LABELS, BriefingFacts, formatted_facts
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    CHANNEL_LABELS,
    POST_STATUS_LABELS,
    Briefing,
    Channel,
    PostDraft,
)
from app.social.profiles import ADDRESS_FORM_LABELS, SocialConfig, load_social_config

TEMPLATES_DIR = Path(__file__).parent / "templates"
NAV_ENTRY = NavEntry(module="social", label="Social Media", path="/social")

router = APIRouter()
templates = module_templates(TEMPLATES_DIR, NAV_ENTRY.module)
templates.env.globals["channel_labels"] = CHANNEL_LABELS
templates.env.globals["post_status_labels"] = POST_STATUS_LABELS
templates.env.globals["fact_labels"] = FACT_LABELS
templates.env.globals["address_form_labels"] = ADDRESS_FORM_LABELS


def get_social_config() -> SocialConfig:
    return load_social_config()


def get_social_client() -> SocialLLMClient:
    return SocialLLMClient(get_provider())


def _labels(config: SocialConfig) -> dict[str, dict[str, str]]:
    return {
        "area_labels": {a.key: a.label for a in config.brand.areas},
        "occasion_labels": {o.key: o.label for o in config.industry.occasion_types},
    }


SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[SocialConfig, Depends(get_social_config)]
ClientDep = Annotated[SocialLLMClient, Depends(get_social_client)]
UserDep = Annotated[str, Depends(current_user)]


def _drafts_by_briefing(session: Session) -> dict[int, list[PostDraft]]:
    drafts: dict[int, list[PostDraft]] = {}
    for draft in session.exec(select(PostDraft).order_by(PostDraft.id)).all():  # type: ignore[arg-type]
        drafts.setdefault(draft.briefing_id, []).append(draft)
    return drafts


@router.get("/social", response_class=HTMLResponse)
def social_index(request: Request, session: SessionDep, config: ConfigDep) -> HTMLResponse:
    """Overview: briefings with their drafts and status."""
    briefings = session.exec(select(Briefing).order_by(Briefing.id.desc())).all()  # type: ignore[union-attr]
    return templates.TemplateResponse(
        request,
        "social_index.html",
        {
            "briefings": briefings,
            "drafts": _drafts_by_briefing(session),
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
    values = {k: v for k, v in params.items() if k not in ("channels", "changed")}
    channels = None if params.get("changed") == "area" else params.getlist("channels")
    return templates.TemplateResponse(
        request, "_social_briefing_fields.html", _form_context(config, values, channels)
    )


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


@router.get("/social/briefings/{briefing_id}", response_class=HTMLResponse)
def briefing_detail(
    request: Request, session: SessionDep, config: ConfigDep, briefing_id: int
) -> HTMLResponse:
    """Briefing left, drafts per channel right."""
    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        raise HTTPException(status_code=404, detail="Briefing nicht gefunden")
    drafts = session.exec(
        select(PostDraft).where(PostDraft.briefing_id == briefing_id).order_by(PostDraft.id)  # type: ignore[arg-type]
    ).all()
    facts = formatted_facts(BriefingFacts.from_json(briefing.facts))
    return templates.TemplateResponse(
        request,
        "social_briefing_detail.html",
        {
            "briefing": briefing,
            "drafts": drafts,
            "facts": facts,
            "config": config,
            **_labels(config),
        },
    )
