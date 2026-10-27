"""Campaigns: one occasion, several posts over several weeks (ROADMAP_social S7).

The plan comes from the industry profile (`campaign_steps`). Every step becomes its own
briefing with drafts per channel; every post is checked and approved on its own.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.audit import write_human_audit_entry
from app.core.config import PROJECT_ROOT
from app.social.briefings import BriefingFormError, BriefingInput, create_briefing
from app.social.drafts import generate_drafts
from app.social.facts import BriefingFacts
from app.social.llm.client import SocialLLMClient
from app.social.models import Briefing, Campaign, Channel, PostDraft
from app.social.profiles import CampaignStep, SocialConfig

CAMPAIGN_STEP = "social.campaign_create"
MAX_NAME_LENGTH = 100
DEMO_CAMPAIGNS_DIR = PROJECT_ROOT / "samples" / "social"


@dataclass(frozen=True)
class CampaignInput:
    name: str
    event_date: date
    briefing: BriefingInput


@dataclass
class CampaignStepPlan:
    step: CampaignStep
    briefing: Briefing
    drafts: list[PostDraft]


def plan_dates(steps: list[CampaignStep], event_date: date) -> list[tuple[CampaignStep, date]]:
    """Steps in chronological order with their dates."""
    ordered = sorted(steps, key=lambda s: s.offset_days)
    return [(step, event_date + timedelta(days=step.offset_days)) for step in ordered]


def parse_campaign_form(form: Mapping[str, str], errors: dict[str, str]) -> tuple[str, date | None]:
    """Name and occasion date of the campaign; problems are added to `errors`."""
    name = (form.get("name") or "").strip()
    if not name:
        errors["name"] = "Bitte einen Namen angeben."
    elif len(name) > MAX_NAME_LENGTH:
        errors["name"] = f"Höchstens {MAX_NAME_LENGTH} Zeichen."
    event: date | None = None
    try:
        event = date.fromisoformat((form.get("event_date") or "").strip())
    except ValueError:
        errors["event_date"] = "Bitte das Datum des Anlasses angeben."
    return name, event


def validate_campaign(
    config: SocialConfig, name: str, event_date: date, briefing: BriefingInput, today: date
) -> CampaignInput:
    steps = config.industry.campaign_steps
    if not steps:
        raise BriefingFormError(
            {"event_date": "Für diese Branche ist kein Kampagnenplan hinterlegt."}
        )
    first_step, first_date = plan_dates(steps, event_date)[0]
    if first_date < today:
        raise BriefingFormError(
            {
                "event_date": f"Zu kurzfristig: Der erste Beitrag („{first_step.label}“) wäre am "
                f"{first_date:%d.%m.%Y}."
            }
        )
    # The date of the occasion is a fact of every post (unless the briefing names another).
    facts = briefing.facts
    if facts.datum is None and facts.zeitraum_von is None:
        facts = BriefingFacts.model_validate(facts.model_dump() | {"datum": event_date})
    return CampaignInput(name=name, event_date=event_date, briefing=replace(briefing, facts=facts))


def create_campaign(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    data: CampaignInput,
    user: str,
) -> Campaign:
    campaign = Campaign(
        name=data.name,
        brand_key=config.brand.key,
        area=data.briefing.area,
        occasion_type=data.briefing.occasion_type,
        event_date=data.event_date,
        created_by=user,
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    write_human_audit_entry(
        session,
        item_id=None,
        step=CAMPAIGN_STEP,
        actor=user,
        details={"campaign_id": campaign.id, "steps": len(config.industry.campaign_steps)},
    )
    for step, step_date in plan_dates(config.industry.campaign_steps, data.event_date):
        briefing = create_briefing(
            session,
            config,
            replace(data.briefing, desired_date=step_date),
            user,
            campaign_id=campaign.id,
            campaign_step=step.key,
        )
        generate_drafts(
            session,
            config,
            client,
            briefing,
            campaign_step_label=f"{step.label} ({step.instruction})",
        )
    return campaign


def campaign_plan(
    session: Session, config: SocialConfig, campaign: Campaign
) -> list[CampaignStepPlan]:
    steps = {s.key: s for s in config.industry.campaign_steps}
    briefings = session.exec(
        select(Briefing)
        .where(Briefing.campaign_id == campaign.id)
        .order_by(col(Briefing.desired_date), col(Briefing.id))
    ).all()
    plan = []
    for briefing in briefings:
        drafts = session.exec(
            select(PostDraft)
            .where(PostDraft.briefing_id == briefing.id)
            .order_by(col(PostDraft.id))
        ).all()
        step = steps.get(briefing.campaign_step or "") or CampaignStep(
            key="unbekannt",
            label=briefing.campaign_step or "–",
            offset_days=0,
            instruction="–",
        )
        plan.append(CampaignStepPlan(step, briefing, list(drafts)))
    return plan


# --- demo ---


def load_demo_campaign(
    config: SocialConfig, today: date, path: Path | None = None
) -> CampaignInput:
    """The fictitious demo campaign of the active industry, dated relative to `today`."""
    path = path or DEMO_CAMPAIGNS_DIR / config.industry_key / "demo_campaign.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError(f"{path} is not marked as synthetic data")
    event_date = today + timedelta(days=int(data["event_in_days"]))
    facts = BriefingFacts.model_validate(data.get("facts", {}))
    briefing = BriefingInput(
        area=data["area"],
        occasion_type=data["occasion_type"],
        notes=data["notes"],
        channels=[Channel(c) for c in data["channels"]],
        facts=facts,
    )
    return validate_campaign(config, data["name"], event_date, briefing, today)
