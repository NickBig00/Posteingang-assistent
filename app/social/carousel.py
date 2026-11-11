"""Carousel posts for Instagram: several pages in one post (SPEC_social 14.2).

Each page is an info graphic (own texts, optional background image, optional facts block) or
an image of the briefing. The same checks and labels apply per page; the facts are checked
exactly on every page. Pages can be reordered; the AI can suggest a split (labelled AI
suggestion, editable). The carousel is approved together with its Instagram post; the
download is a ZIP with numbered JPEGs (01.jpg, 02.jpg, …) in 4:5 or 1:1.
"""

import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue
from sqlmodel import Session, col, delete, select

from app.core.audit import write_human_audit_entry
from app.core.image_formats import ImageExportError, ImageFormat, export_jpeg
from app.core.llm.calls import LLMCall, audit_llm_call, call_with_retry
from app.core.models import utcnow
from app.core.outcomes import Outcome
from app.social.assets import is_usable
from app.social.checking import member_names
from app.social.checks import Finding, check_forbidden_terms, check_term_rules
from app.social.facts import BriefingFacts
from app.social.graphics import GraphicLayoutError
from app.social.infographic import (
    VARIANT_LABELS,
    InfoGraphicError,
    InfoTexts,
    clean_texts,
    fact_problems,
    mask_facts,
    render_info_graphic,
    validate_texts,
)
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import AssessmentOutput, CarouselPlanOutput
from app.social.models import (
    Asset,
    Briefing,
    CarouselCheck,
    CarouselPage,
    Channel,
    PostDraft,
    PostStatus,
)
from app.social.profiles import SocialConfig
from app.social.sensitive import (
    assessment_failed,
    check_address_form,
    check_health_claims,
    check_persons,
    merge_assessment,
)

MIN_PAGES, MAX_PAGES = 2, 10
CAROUSEL_FORMATS = (ImageFormat.PORTRAIT, ImageFormat.SQUARE)
PLAN_STEP = "social.carousel_plan"
ASSESS_STEP = "social.carousel_assess"
CHANGE_STEP = "social.carousel_change"
CONFIRM_STEP = "social.carousel_confirm"
EDITABLE = frozenset({PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW})


class CarouselError(ValueError):
    """Carousel action not possible (German message for the UI)."""


def pages(session: Session, draft_id: int) -> list[CarouselPage]:
    statement = (
        select(CarouselPage)
        .where(CarouselPage.draft_id == draft_id)
        .order_by(col(CarouselPage.position), col(CarouselPage.id))
    )
    return list(session.exec(statement).all())


def carousel_checks(session: Session, draft_id: int) -> list[CarouselCheck]:
    statement = (
        select(CarouselCheck)
        .where(CarouselCheck.draft_id == draft_id)
        .order_by(col(CarouselCheck.id))
    )
    return list(session.exec(statement).all())


def _require(session: Session, draft: PostDraft) -> Briefing:
    if draft.channel is not Channel.INSTAGRAM_POST:
        raise CarouselError("Karussells gibt es nur für Instagram-Beiträge.")
    if draft.status not in EDITABLE:
        raise CarouselError("Das Karussell wird mit dem Beitrag freigegeben und ist danach fest.")
    briefing = session.get(Briefing, draft.briefing_id)
    if briefing is None:
        raise CarouselError("Briefing nicht gefunden.")
    return briefing


def _audit(session: Session, draft: PostDraft, step: str, user: str, **details: JsonValue) -> None:
    write_human_audit_entry(
        session,
        item_id=None,
        step=step,
        actor=user,
        details={"briefing_id": draft.briefing_id, "draft_id": draft.id, **details},
    )


def _renumber(session: Session, rows: Sequence[CarouselPage]) -> None:
    for index, row in enumerate(rows, start=1):
        row.position = index
        session.add(row)
    session.commit()


# --- checks ---


def page_problems(
    session: Session, briefing: Briefing, page: CarouselPage, number: int
) -> list[str]:
    """Deterministic errors of one page: text limits, facts, usable image."""
    label = f"Seite {number}: "
    facts = BriefingFacts.from_json(briefing.facts)
    if page.kind == "image":
        asset = session.get(Asset, page.asset_id) if page.asset_id else None
        if asset is None or asset.briefing_id != briefing.id:
            return [f"{label}Bild nicht vorhanden."]
        if not is_usable(asset):
            return [
                f"{label}Bild darf nicht verwendet werden (Einwilligung fehlt oder widerrufen)."
            ]
        return []
    texts = InfoTexts.from_json(page.texts)
    problems = validate_texts(texts, story=True, label=label)
    keys = tuple(facts.present_keys()) if page.show_facts else ()
    problems += [f"{label}{p}" for p in fact_problems(texts, facts, keys)]
    if page.asset_id is not None:
        asset = session.get(Asset, page.asset_id)
        if asset is None or asset.briefing_id != briefing.id or not is_usable(asset):
            problems.append(f"{label}Hintergrundbild darf nicht verwendet werden.")
    return problems


def run_carousel_checks(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
) -> None:
    """Checks per page (facts, terms, health claims, persons, address form) plus one AI
    assessment of all page texts; results replace the previous ones (confirmations reset)."""
    briefing = _require(session, draft)
    rows = pages(session, draft.id or 0)
    area = config.brand.area(briefing.area)
    sensitive = area.label if config.is_sensitive(briefing.area) else None
    members = member_names(session)
    results: list[tuple[int | None, Finding]] = []
    for number, page in enumerate(rows, start=1):
        for problem in page_problems(session, briefing, page, number):
            results.append((number, Finding("fakten", Outcome.ERROR, problem)))
        if page.kind != "info":
            continue
        text = InfoTexts.from_json(page.texts).text
        found = [
            *[
                f
                for f in check_forbidden_terms(text, config.brand.forbidden_terms)
                if f.outcome is not Outcome.OK
            ],
            *[
                f
                for f in check_term_rules(text, config.industry.term_rules)
                if f.outcome is not Outcome.OK
            ],
            *check_health_claims(text, config.industry.health_claims, sensitive),
            *check_persons(text, members),
            *[
                f
                for f in check_address_form(text, area.address_form)
                if f.outcome is not Outcome.OK
            ],
        ]
        results += [(number, f) for f in found]
    if len(rows) < MIN_PAGES:
        results.append(
            (
                None,
                Finding(
                    "seiten", Outcome.ERROR, f"Ein Karussell braucht mindestens {MIN_PAGES} Seiten."
                ),
            )
        )
    combined = "\n\n".join(
        f"Seite {n}:\n{InfoTexts.from_json(p.texts).text}"
        for n, p in enumerate(rows, start=1)
        if p.kind == "info"
    )
    if combined.strip():
        context: dict[str, JsonValue] = {"briefing_id": draft.briefing_id, "draft_id": draft.id}

        def record(call: LLMCall[AssessmentOutput], attempt: int) -> None:
            audit_llm_call(
                session,
                call,
                attempt=attempt,
                provider=client.provider.name,
                step=ASSESS_STEP,
                context=context,
            )

        call = call_with_retry(lambda: client.assess_post(config, combined), record)
        known = [f for _, f in results]
        if call.ok and call.output is not None:
            results += [
                (None, f) for f in merge_assessment(combined, call.output, known, area.address_form)
            ]
        else:
            results.append((None, assessment_failed()))
    if not any(f.outcome is not Outcome.OK for _, f in results):
        results.append(
            (None, Finding("seiten", Outcome.OK, "Fakten und Texte aller Seiten geprüft."))
        )
    for old in carousel_checks(session, draft.id or 0):
        session.delete(old)
    session.flush()
    for number, finding in results:
        session.add(
            CarouselCheck(
                draft_id=draft.id or 0,
                page=number,
                check_key=finding.check_key,
                outcome=finding.outcome,
                reason=finding.reason,
                is_ai_assessment=finding.is_ai_assessment,
                requires_confirmation=finding.requires_confirmation,
                consent_required=finding.consent_required,
            )
        )
    session.commit()


def carousel_blockers(session: Session, draft: PostDraft) -> list[str]:
    """A carousel with errors or unconfirmed warnings blocks the approval of its post."""
    if draft.id is None or not pages(session, draft.id):
        return []
    blockers = []
    for result in carousel_checks(session, draft.id):
        prefix = "Karussell: "
        if result.outcome is Outcome.ERROR and result.consent_required:
            if result.confirmed_at is None:
                blockers.append(f"{prefix}Einwilligung fehlt: {result.reason}")
        elif result.outcome is Outcome.ERROR:
            blockers.append(f"{prefix}{result.reason}")
        elif result.requires_confirmation and result.confirmed_at is None:
            blockers.append(f"{prefix}Warnung noch nicht bestätigt: {result.reason}")
    return blockers


def confirm_carousel_check(session: Session, result: CarouselCheck, user: str) -> CarouselCheck:
    draft = session.get(PostDraft, result.draft_id)
    if draft is None or draft.status not in EDITABLE:
        raise CarouselError("Nur vor der Freigabe möglich.")
    if result.confirmed_at is not None:
        raise CarouselError("Bereits bestätigt.")
    consent = result.consent_required and result.outcome is Outcome.ERROR
    if not consent and not (result.requires_confirmation and result.outcome is Outcome.WARNING):
        raise CarouselError("Dieses Prüfergebnis braucht keine Bestätigung.")
    result.confirmed_by, result.confirmed_at = user, utcnow()
    session.add(result)
    session.commit()
    session.refresh(result)
    _audit(session, draft, CONFIRM_STEP, user, check_result_id=result.id, consent=consent)
    return result


# --- changing pages ---


@dataclass(frozen=True)
class PageInput:
    kind: str
    texts: InfoTexts
    asset_id: int | None
    show_facts: bool
    variant: str


def _validate_input(session: Session, briefing: Briefing, data: PageInput) -> None:
    if data.kind not in ("info", "image"):
        raise CarouselError("Unbekannte Seitenart.")
    if data.variant not in VARIANT_LABELS:
        raise CarouselError("Unbekannte Gestaltungsvariante.")
    if data.asset_id is not None:
        asset = session.get(Asset, data.asset_id)
        if asset is None or asset.briefing_id != briefing.id:
            raise CarouselError("Bild nicht gefunden.")
    if data.kind == "image" and data.asset_id is None:
        raise CarouselError("Bitte ein Bild für die Seite wählen.")
    if data.kind == "info":
        problems = validate_texts(data.texts, story=True, label="")
        if problems:
            raise CarouselError(" ".join(problems))


def add_page(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
    data: PageInput,
    user: str,
) -> CarouselPage:
    briefing = _require(session, draft)
    rows = pages(session, draft.id or 0)
    if len(rows) >= MAX_PAGES:
        raise CarouselError(f"Höchstens {MAX_PAGES} Seiten.")
    data = PageInput(
        data.kind, clean_texts(data.texts), data.asset_id, data.show_facts, data.variant
    )
    _validate_input(session, briefing, data)
    page = CarouselPage(
        draft_id=draft.id or 0,
        position=len(rows) + 1,
        kind=data.kind,
        texts=data.texts.to_json() if data.kind == "info" else {},
        asset_id=data.asset_id,
        show_facts=data.show_facts and data.kind == "info",
        variant=data.variant,
        edited_by_human=True,
    )
    session.add(page)
    session.commit()
    session.refresh(page)
    _audit(session, draft, CHANGE_STEP, user, action="add", page_id=page.id)
    run_carousel_checks(session, config, client, draft)
    return page


def update_page(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    page: CarouselPage,
    data: PageInput,
    user: str,
) -> CarouselPage:
    draft = session.get(PostDraft, page.draft_id)
    if draft is None:
        raise CarouselError("Entwurf nicht gefunden.")
    briefing = _require(session, draft)
    data = PageInput(
        page.kind, clean_texts(data.texts), data.asset_id, data.show_facts, data.variant
    )
    _validate_input(session, briefing, data)
    if page.kind == "info":
        page.texts = data.texts.to_json()
        page.show_facts = data.show_facts
    page.asset_id = data.asset_id
    page.variant = data.variant
    page.edited_by_human = True
    session.add(page)
    session.commit()
    session.refresh(page)
    _audit(session, draft, CHANGE_STEP, user, action="edit", page_id=page.id)
    run_carousel_checks(session, config, client, draft)
    return page


def move_page(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    page: CarouselPage,
    direction: int,
    user: str,
) -> None:
    """Move a page one place up (-1) or down (+1)."""
    draft = session.get(PostDraft, page.draft_id)
    if draft is None:
        raise CarouselError("Entwurf nicht gefunden.")
    _require(session, draft)
    rows = pages(session, draft.id or 0)
    index = next(i for i, row in enumerate(rows) if row.id == page.id)
    target = index + direction
    if not 0 <= target < len(rows):
        raise CarouselError("Die Seite steht schon am Rand.")
    rows[index], rows[target] = rows[target], rows[index]
    _renumber(session, rows)
    _audit(session, draft, CHANGE_STEP, user, action="move", page_id=page.id, position=target + 1)
    run_carousel_checks(session, config, client, draft)


def remove_page(
    session: Session, config: SocialConfig, client: SocialLLMClient, page: CarouselPage, user: str
) -> None:
    draft = session.get(PostDraft, page.draft_id)
    if draft is None:
        raise CarouselError("Entwurf nicht gefunden.")
    _require(session, draft)
    session.delete(page)
    session.commit()
    _renumber(session, pages(session, draft.id or 0))
    _audit(session, draft, CHANGE_STEP, user, action="remove", page_id=page.id)
    if pages(session, draft.id or 0):
        run_carousel_checks(session, config, client, draft)
    else:
        session.exec(delete(CarouselCheck).where(col(CarouselCheck.draft_id) == draft.id))  # type: ignore[call-overload]
        session.commit()


def suggest_plan(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
    user: str,
) -> list[CarouselPage]:
    """AI suggestion for the split into pages; replaces the current pages."""
    briefing = _require(session, draft)
    context: dict[str, JsonValue] = {"briefing_id": draft.briefing_id, "draft_id": draft.id}
    facts = BriefingFacts.from_json(briefing.facts)

    def record(call: LLMCall[CarouselPlanOutput], attempt: int) -> None:
        audit_llm_call(
            session,
            call,
            attempt=attempt,
            provider=client.provider.name,
            step=PLAN_STEP,
            context=context,
        )

    call = call_with_retry(
        lambda: client.carousel_plan(
            config,
            area_key=briefing.area,
            occasion_key=briefing.occasion_type,
            notes=briefing.notes,
            post_text=mask_facts(draft.text, facts),
        ),
        record,
    )
    if not call.ok or call.output is None:
        raise CarouselError(
            "Die KI hat keine gültige Seitenaufteilung geliefert (auch nicht beim zweiten "
            "Versuch). Bitte die Seiten selbst anlegen."
        )
    for old in pages(session, draft.id or 0):
        session.delete(old)
    session.commit()
    for number, suggestion in enumerate(call.output.pages, start=1):
        texts = clean_texts(
            InfoTexts(
                suggestion.headline,
                suggestion.subline,
                suggestion.intro,
                list(suggestion.points),
                suggestion.closing,
            )
        )
        session.add(
            CarouselPage(
                draft_id=draft.id or 0,
                position=number,
                kind="info",
                texts=texts.to_json(),
                show_facts=suggestion.show_facts,
                is_ai_suggestion=True,
            )
        )
    session.commit()
    run_carousel_checks(session, config, client, draft)
    return pages(session, draft.id or 0)


# --- rendering and export ---


def render_page(
    session: Session,
    config: SocialConfig,
    briefing: Briefing,
    page: CarouselPage,
    fmt: ImageFormat,
) -> bytes:
    """JPEG of one page (raises `CarouselError` with the reason)."""
    asset = session.get(Asset, page.asset_id) if page.asset_id else None
    if asset is not None and (not is_usable(asset) or not Path(asset.stored_path).is_file()):
        raise CarouselError("Bild darf nicht verwendet werden.")
    content = Path(asset.stored_path).read_bytes() if asset else None
    try:
        if page.kind == "image":
            if content is None:
                raise CarouselError("Bild nicht vorhanden.")
            return export_jpeg(content, fmt)
        return render_info_graphic(
            config,
            briefing,
            InfoTexts.from_json(page.texts),
            fmt,
            variant=page.variant,
            background=content,
            show_facts=page.show_facts,
            few_points=True,
        )
    except InfoGraphicError as error:
        raise CarouselError(str(error)) from error
    except (GraphicLayoutError, ImageExportError) as error:
        raise CarouselError(str(error)) from error


def ai_pages(session: Session, rows: Sequence[CarouselPage]) -> list[int]:
    """Numbers of the pages whose image was made or edited with AI (label when posting)."""
    numbers = []
    for number, page in enumerate(rows, start=1):
        asset = session.get(Asset, page.asset_id) if page.asset_id else None
        if asset is not None and (asset.ai_generated or asset.ai_edited):
            numbers.append(number)
    return numbers


def export_zip(session: Session, config: SocialConfig, draft: PostDraft, fmt: ImageFormat) -> bytes:
    """ZIP with numbered JPEGs 01.jpg, 02.jpg, … – only after the approval of the post."""
    if draft.status not in (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED):
        raise CarouselError("Download erst nach der Freigabe des Beitrags.")
    if fmt not in CAROUSEL_FORMATS:
        raise CarouselError("Karussells gibt es im Format 4:5 oder 1:1.")
    briefing = session.get(Briefing, draft.briefing_id)
    rows = pages(session, draft.id or 0)
    if briefing is None or len(rows) < MIN_PAGES:
        raise CarouselError(f"Ein Karussell braucht mindestens {MIN_PAGES} Seiten.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for number, page in enumerate(rows, start=1):
            info = zipfile.ZipInfo(f"{number:02d}.jpg", date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, render_page(session, config, briefing, page, fmt))
    return buffer.getvalue()


def delete_carousel(session: Session, draft_ids: list[int]) -> None:
    """Deletion concept: pages and their checks go with their draft."""
    if not draft_ids:
        return
    session.exec(delete(CarouselCheck).where(col(CarouselCheck.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
    session.exec(delete(CarouselPage).where(col(CarouselPage.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
