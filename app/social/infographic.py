"""Info graphic: headline, subline, intro, ✓-list and closing over a background (SPEC_social 14.1).

The texts are written by a person or suggested by the AI (labelled as AI suggestion). The
facts of the briefing are added by code below the texts and checked exactly; the free texts
may not contain any other date, time, price or phone number. The texts run through the same
checks as posts (health claims, forbidden terms, persons, address form); warnings must be
confirmed before the download, errors prevent the graphic. Rendering is deterministic code:
the font shrinks with more text, and text that still does not fit is refused, never cut.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import JsonValue
from sqlmodel import Session, col, delete, select

from app.core.audit import write_human_audit_entry
from app.core.image_formats import FORMAT_SPECS, ImageFormat, fit_to_format, open_image, to_jpeg
from app.core.llm.calls import LLMCall, audit_llm_call, call_with_retry
from app.core.models import utcnow
from app.core.outcomes import Outcome
from app.core.theme import WHITE, mix, parse_hex
from app.social.checking import member_names
from app.social.checks import (
    Finding,
    check_facts,
    check_forbidden_terms,
    check_phrase_rules,
    check_term_rules,
)
from app.social.facts import FACT_KEYS, FACT_LABELS, BriefingFacts, FactKey, formatted_facts
from app.social.graphics import (
    DEFAULT_ACCENT,
    DEFAULT_PRIMARY,
    GraphicLayoutError,
    _font,
    _text_colour,
    _wrap,
    parse_fact_keys,
    select_facts,
)
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import AssessmentOutput, GraphicTextOutput
from app.social.models import Briefing, InfoGraphic, InfoGraphicCheck, PostDraft, PostStatus
from app.social.profiles import SocialConfig
from app.social.sensitive import (
    assessment_failed,
    check_address_form,
    check_health_claims,
    check_persons,
    merge_assessment,
    ok_findings,
)
from app.social.textscan import find_dates, find_phones, find_prices, find_times, phone_digits

Variant = Literal["zentriert", "farbfeld", "foto_oben"]
VARIANT_LABELS: dict[str, str] = {
    "zentriert": "Text zentriert",
    "farbfeld": "Text in Farbfeld unten",
    "foto_oben": "Foto oben, Text unten",
}
SUGGEST_STEP = "social.graphic_text"
REVISE_STEP = "social.graphic_text_revise"
ASSESS_STEP = "social.graphic_text_assess"
SAVE_STEP = "social.graphic_text_save"
CONFIRM_STEP = "social.graphic_text_confirm"
CONSENT_STEP = "social.graphic_text_consent"

# Limits per field (characters) and of the list.
LIMITS = {"headline": 60, "subline": 90, "intro": 260, "point": 80, "closing": 140}
MIN_POINTS, MAX_POINTS = 3, 6
# Smallest font scale before text is refused as too long (never cut).
MIN_SCALE = 0.55
# Largest font scale: tall formats with little text use the space instead of leaving it empty.
MAX_SCALE = 1.5


class InfoGraphicError(ValueError):
    """Texts invalid, checks not passed, or action not allowed (German messages for the UI)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class InfoTexts:
    """Free texts of an info graphic (without the facts)."""

    headline: str = ""
    subline: str = ""
    intro: str = ""
    points: list[str] = field(default_factory=list)
    closing: str = ""

    @property
    def lines(self) -> list[str]:
        return [
            line
            for line in (self.headline, self.subline, self.intro, *self.points, self.closing)
            if line
        ]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "headline": self.headline,
            "subline": self.subline,
            "intro": self.intro,
            "points": list(self.points),
            "closing": self.closing,
        }

    @classmethod
    def from_json(cls, data: dict[str, JsonValue] | None) -> "InfoTexts":
        data = data or {}
        points = data.get("points") or []
        return cls(
            headline=str(data.get("headline") or ""),
            subline=str(data.get("subline") or ""),
            intro=str(data.get("intro") or ""),
            points=[str(p) for p in points] if isinstance(points, list) else [],
            closing=str(data.get("closing") or ""),
        )

    @classmethod
    def of(cls, graphic: InfoGraphic) -> "InfoTexts":
        return cls(
            graphic.headline, graphic.subline, graphic.intro, list(graphic.points), graphic.closing
        )

    @property
    def is_empty(self) -> bool:
        return not self.lines


def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_points(raw: str) -> list[str]:
    """One point per line; leading bullets or check marks are removed."""
    points = [_clean(line.lstrip(" -•*✓✔·")) for line in raw.replace("\r\n", "\n").split("\n")]
    return [p for p in points if p]


def clean_texts(texts: InfoTexts) -> InfoTexts:
    return InfoTexts(
        headline=_clean(texts.headline),
        subline=_clean(texts.subline),
        intro=_clean(texts.intro),
        points=[_clean(p) for p in texts.points if _clean(p)],
        closing=_clean(texts.closing),
    )


def validate_texts(texts: InfoTexts, *, story: bool = False) -> list[str]:
    """Length limits and the size of the list (a story text may have fewer points)."""
    problems: list[str] = []
    label = "Story: " if story else ""
    if not texts.headline:
        problems.append(f"{label}Bitte eine Überschrift eingeben.")
    for key, name in (
        ("headline", "Überschrift"),
        ("subline", "Unterzeile"),
        ("intro", "Einleitung"),
        ("closing", "Schlusssatz"),
    ):
        value = getattr(texts, key)
        if len(value) > LIMITS[key]:
            problems.append(f"{label}{name} ist zu lang (höchstens {LIMITS[key]} Zeichen).")
    minimum = 0 if story else MIN_POINTS
    if not minimum <= len(texts.points) <= MAX_POINTS:
        problems.append(
            f"{label}Die Aufzählung braucht {minimum} bis {MAX_POINTS} Punkte (einen je Zeile)."
        )
    for point in texts.points:
        if len(point) > LIMITS["point"]:
            problems.append(
                f"{label}Punkt „{point[:30]}…“ ist zu lang (höchstens {LIMITS['point']} Zeichen)."
            )
    return problems


# --- facts ---


def fact_lines(facts: BriefingFacts, keys: Iterable[FactKey]) -> list[str]:
    chosen = formatted_facts(select_facts(facts, keys))
    return [f"{FACT_LABELS[k]}: {chosen[k]}" for k in FACT_KEYS if k in chosen]


def fact_problems(texts: InfoTexts, facts: BriefingFacts, keys: Sequence[FactKey]) -> list[str]:
    """Deterministic fact check (errors): the facts block is exact, the free texts add none."""
    chosen = select_facts(facts, keys)
    lines = fact_lines(facts, keys)
    problems = [
        f.reason for f in check_facts("\n".join(lines), chosen) if f.outcome is Outcome.ERROR
    ]
    free = texts.text
    dates = [d for d in (facts.datum, facts.zeitraum_von, facts.zeitraum_bis) if d]
    for mention in find_dates(free):
        if not any(mention.matches(d) for d in dates):
            problems.append(f"Datum {mention.label} im Grafiktext steht nicht im Briefing.")
    for time_mention in find_times(free):
        if time_mention.value != facts.uhrzeit:
            problems.append(
                f"Uhrzeit {time_mention.value:%H:%M} im Grafiktext steht nicht im Briefing."
            )
    for price in find_prices(free):
        if price.value != facts.preis:
            problems.append(f"Preis {price.value} € im Grafiktext steht nicht im Briefing.")
    allowed_phone = phone_digits(facts.telefon) if facts.telefon else None
    for phone in find_phones(free):
        if phone.digits != allowed_phone:
            problems.append("Telefonnummer im Grafiktext steht nicht im Briefing.")
    return list(dict.fromkeys(problems))


# --- state and checks ---


def get_info_graphic(session: Session, briefing_id: int) -> InfoGraphic | None:
    return session.exec(select(InfoGraphic).where(InfoGraphic.briefing_id == briefing_id)).first()


def story_texts(graphic: InfoGraphic) -> InfoTexts | None:
    texts = InfoTexts.from_json(graphic.story) if graphic.story else None
    return texts if texts and not texts.is_empty else None


def graphic_checks(session: Session, graphic_id: int) -> list[InfoGraphicCheck]:
    statement = (
        select(InfoGraphicCheck)
        .where(InfoGraphicCheck.graphic_id == graphic_id)
        .order_by(col(InfoGraphicCheck.id))
    )
    return list(session.exec(statement).all())


def _checked_text(graphic: InfoGraphic) -> str:
    story = story_texts(graphic)
    return InfoTexts.of(graphic).text + (f"\n\n{story.text}" if story else "")


def run_graphic_checks(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    graphic: InfoGraphic,
) -> list[Finding]:
    """Same checks as for posts (code + LLM assessment) on all free texts; stored results."""
    text = _checked_text(graphic)
    area = config.brand.area(briefing.area)
    industry = config.industry
    facts = BriefingFacts.from_json(briefing.facts)
    sensitive = area.label if config.is_sensitive(briefing.area) else None
    findings: list[Finding] = [
        *check_forbidden_terms(text, config.brand.forbidden_terms),
        *[f for f in check_term_rules(text, industry.term_rules) if f.outcome is not Outcome.OK],
        *[
            f
            for f in check_phrase_rules(text, industry.phrase_rules, facts)
            if f.outcome is not Outcome.OK
        ],
    ]
    code = [
        *check_health_claims(text, industry.health_claims, sensitive),
        *check_persons(text, member_names(session)),
        *check_address_form(text, area.address_form),
    ]
    ai: list[Finding] = []
    if text.strip():
        context: dict[str, JsonValue] = {"briefing_id": briefing.id, "graphic_id": graphic.id}

        def record(call: LLMCall[AssessmentOutput], attempt: int) -> None:
            audit_llm_call(
                session,
                call,
                attempt=attempt,
                provider=client.provider.name,
                step=ASSESS_STEP,
                context=context,
            )

        call = call_with_retry(lambda: client.assess_post(config, text), record)
        if call.ok and call.output is not None:
            ai = merge_assessment(text, call.output, code, area.address_form)
        else:
            ai = [assessment_failed()]
    findings += [*code, *ai, *ok_findings([*code, *ai], industry.health_claims is not None)]
    for old in graphic_checks(session, graphic.id or 0):
        session.delete(old)
    session.flush()
    for finding in findings:
        start, end = finding.span if finding.span else (None, None)
        session.add(
            InfoGraphicCheck(
                graphic_id=graphic.id or 0,
                check_key=finding.check_key,
                outcome=finding.outcome,
                reason=finding.reason,
                excerpt=text[start:end] if start is not None else None,
                is_ai_assessment=finding.is_ai_assessment,
                requires_confirmation=finding.requires_confirmation,
                consent_required=finding.consent_required,
            )
        )
    session.commit()
    return findings


def check_blockers(session: Session, graphic: InfoGraphic) -> list[str]:
    """Errors and unconfirmed warnings of the texts (block the download)."""
    blockers = []
    for result in graphic_checks(session, graphic.id or 0):
        if result.outcome is Outcome.ERROR and result.consent_required:
            if result.confirmed_at is None:
                blockers.append(f"Einwilligung fehlt: {result.reason}")
        elif result.outcome is Outcome.ERROR:
            blockers.append(f"Fehler: {result.reason}")
        elif result.requires_confirmation and result.confirmed_at is None:
            blockers.append(f"Warnung noch nicht bestätigt: {result.reason}")
    return blockers


def text_errors(session: Session, graphic: InfoGraphic) -> list[str]:
    """Errors that prevent the graphic itself (not only the download)."""
    return [
        r.reason
        for r in graphic_checks(session, graphic.id or 0)
        if r.outcome is Outcome.ERROR and not (r.consent_required and r.confirmed_at)
    ]


def _editable(session: Session, briefing_id: int) -> None:
    """The texts are publishing material of the briefing: fixed once every post is out."""
    statuses = session.exec(
        select(PostDraft.status).where(PostDraft.briefing_id == briefing_id)
    ).all()
    if statuses and all(s in (PostStatus.PUBLISHED, PostStatus.REJECTED) for s in statuses):
        raise InfoGraphicError(["Alle Beiträge sind veröffentlicht oder verworfen."])


def _audit(
    session: Session, graphic: InfoGraphic, step: str, user: str, **details: JsonValue
) -> None:
    write_human_audit_entry(
        session,
        item_id=None,
        step=step,
        actor=user,
        details={"briefing_id": graphic.briefing_id, "graphic_id": graphic.id, **details},
    )


def save_texts(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    texts: InfoTexts,
    *,
    story: InfoTexts | None,
    variant: str,
    user: str,
    from_ai: bool = False,
) -> InfoGraphic:
    """Store texts written or changed by a person (or an AI suggestion); checks run again."""
    _editable(session, briefing.id or 0)
    texts = clean_texts(texts)
    story = clean_texts(story) if story is not None else None
    if story is not None and story.is_empty:
        story = None
    problems = validate_texts(texts)
    if story is not None:
        problems += validate_texts(story, story=True)
    if variant not in VARIANT_LABELS:
        problems.append("Unbekannte Gestaltungsvariante.")
    if problems:
        raise InfoGraphicError(problems)
    graphic = get_info_graphic(session, briefing.id or 0) or InfoGraphic(
        briefing_id=briefing.id or 0
    )
    changed = InfoTexts.of(graphic) != texts or (graphic.story or None) != (
        story.to_json() if story else None
    )
    graphic.headline, graphic.subline, graphic.intro = texts.headline, texts.subline, texts.intro
    graphic.points, graphic.closing = list(texts.points), texts.closing
    graphic.story = story.to_json() if story else None
    graphic.variant = variant
    if from_ai:
        graphic.is_ai_suggestion, graphic.edited_by_human = True, False
    elif changed:
        graphic.edited_by_human = True
    graphic.updated_by, graphic.updated_at = user, utcnow()
    session.add(graphic)
    session.commit()
    session.refresh(graphic)
    if not from_ai:
        _audit(session, graphic, SAVE_STEP, user, changed=changed)
    if changed or from_ai or not graphic_checks(session, graphic.id or 0):
        run_graphic_checks(session, config, client, briefing, graphic)
    return graphic


FACT_MASK = "[Fakt]"


def mask_facts(text: str, facts: BriefingFacts) -> str:
    """Replace dates, times, prices, phone numbers and the place with a marker.

    The AI writes the graphic texts without facts (code adds them), so it does not need them.
    """
    spans = [(m.start, m.end) for m in find_dates(text)]
    spans += [(m.start, m.end) for m in find_times(text)]
    spans += [(m.start, m.end) for m in find_prices(text)]
    spans += [(m.start, m.end) for m in find_phones(text)]
    if facts.ort:
        position = text.find(facts.ort)
        while position >= 0:
            spans.append((position, position + len(facts.ort)))
            position = text.find(facts.ort, position + 1)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + FACT_MASK + text[end:]
    return text


def _first_post_text(session: Session, briefing: Briefing) -> str:
    draft = session.exec(
        select(PostDraft).where(PostDraft.briefing_id == briefing.id).order_by(col(PostDraft.id))
    ).first()
    return mask_facts(draft.text, BriefingFacts.from_json(briefing.facts)) if draft else ""


def _texts_from(output: GraphicTextOutput) -> InfoTexts:
    return InfoTexts(
        output.headline, output.subline, output.intro, list(output.points), output.closing
    )


def suggest_texts(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    user: str,
) -> InfoGraphic:
    """ "Text für Grafik vorschlagen": AI suggestion from key points and post draft."""
    _editable(session, briefing.id or 0)
    context: dict[str, JsonValue] = {"briefing_id": briefing.id}
    post_text = _first_post_text(session, briefing)

    def record(call: LLMCall[GraphicTextOutput], attempt: int) -> None:
        audit_llm_call(
            session,
            call,
            attempt=attempt,
            provider=client.provider.name,
            step=SUGGEST_STEP,
            context=context,
        )

    call = call_with_retry(
        lambda: client.graphic_text(
            config,
            area_key=briefing.area,
            occasion_key=briefing.occasion_type,
            notes=briefing.notes,
            post_text=post_text,
        ),
        record,
    )
    if not call.ok or call.output is None:
        raise InfoGraphicError(
            [
                "Die KI hat keinen gültigen Grafiktext geliefert (auch nicht beim zweiten "
                "Versuch). Bitte selbst schreiben."
            ]
        )
    current = get_info_graphic(session, briefing.id or 0)
    return save_texts(
        session,
        config,
        client,
        briefing,
        _fit_limits(_texts_from(call.output)),
        story=story_texts(current) if current else None,
        variant=current.variant if current else "zentriert",
        user=user,
        from_ai=True,
    )


def _fit_limits(texts: InfoTexts) -> InfoTexts:
    """AI output beyond the field limits is not cut silently: invalid → the person fixes it."""
    problems = validate_texts(texts)
    if problems:
        raise InfoGraphicError(["Der KI-Vorschlag passt nicht in die Grafik:", *problems])
    return texts


def revise_texts(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    instruction: str,
    user: str,
) -> InfoGraphic:
    """ "Was soll anders werden?" for the graphic texts (SPEC_social 13.8)."""
    from app.social.models import RevisionTarget  # noqa: PLC0415 - avoid a cycle
    from app.social.revisions import clean_instruction, record_request  # noqa: PLC0415

    _editable(session, briefing.id or 0)
    current = get_info_graphic(session, briefing.id or 0)
    if current is None or InfoTexts.of(current).is_empty:
        raise InfoGraphicError(["Es gibt noch keinen Grafiktext zum Überarbeiten."])
    try:
        instruction = clean_instruction(instruction)
    except ValueError as error:
        raise InfoGraphicError([str(error)]) from error
    context: dict[str, JsonValue] = {"briefing_id": briefing.id, "graphic_id": current.id}

    def record(call: LLMCall[GraphicTextOutput], attempt: int) -> None:
        audit_llm_call(
            session,
            call,
            attempt=attempt,
            provider=client.provider.name,
            step=REVISE_STEP,
            context=context,
        )

    call = call_with_retry(
        lambda: client.revise_graphic_text(
            config,
            area_key=briefing.area,
            texts=InfoTexts.of(current).to_json(),
            instruction=instruction,
        ),
        record,
    )
    ok = call.ok and call.output is not None
    record_request(
        session,
        briefing_id=briefing.id or 0,
        draft_id=None,
        target=RevisionTarget.GRAPHIC_TEXT,
        instruction=instruction,
        user=user,
        succeeded=ok,
    )
    if not ok or call.output is None:
        raise InfoGraphicError(
            ["Die KI hat keine gültige Überarbeitung geliefert. Der bisherige Grafiktext bleibt."]
        )
    return save_texts(
        session,
        config,
        client,
        briefing,
        _fit_limits(_texts_from(call.output)),
        story=story_texts(current),
        variant=current.variant,
        user=user,
        from_ai=True,
    )


def confirm_check(session: Session, result: InfoGraphicCheck, user: str) -> InfoGraphicCheck:
    """A person confirms a warning or (for members) the consent of the named person."""
    graphic = session.get(InfoGraphic, result.graphic_id)
    if graphic is None:
        raise InfoGraphicError(["Grafik nicht gefunden."])
    _editable(session, graphic.briefing_id)
    if result.confirmed_at is not None:
        raise InfoGraphicError(["Bereits bestätigt."])
    consent = result.consent_required and result.outcome is Outcome.ERROR
    if not consent and not (result.requires_confirmation and result.outcome is Outcome.WARNING):
        raise InfoGraphicError(["Dieses Prüfergebnis braucht keine Bestätigung."])
    result.confirmed_by, result.confirmed_at = user, utcnow()
    session.add(result)
    session.commit()
    session.refresh(result)
    _audit(
        session,
        graphic,
        CONSENT_STEP if consent else CONFIRM_STEP,
        user,
        check_result_id=result.id,
        check_key=result.check_key,
    )
    return result


def delete_info_graphic(session: Session, briefing_id: int) -> None:
    graphic = get_info_graphic(session, briefing_id)
    if graphic is None:
        return
    session.exec(delete(InfoGraphicCheck).where(col(InfoGraphicCheck.graphic_id) == graphic.id))  # type: ignore[call-overload]
    session.delete(graphic)


# --- rendering ---


@dataclass(frozen=True)
class _Block:
    kind: Literal["headline", "subline", "intro", "point", "closing", "rule", "fact"]
    lines: list[str]
    font: ImageFont.FreeTypeFont
    gap_before: int


BASE_SIZES = {
    "headline": 0.085,
    "subline": 0.046,
    "intro": 0.037,
    "point": 0.037,
    "closing": 0.041,
    "fact": 0.035,
}
WEIGHTS = {
    "headline": "bold",
    "subline": "semibold",
    "intro": "regular",
    "point": "regular",
    "closing": "semibold",
    "fact": "semibold",
}


def _layout(
    draw: ImageDraw.ImageDraw,
    texts: InfoTexts,
    facts: list[str],
    width: int,
    text_width: int,
    scale: float,
) -> tuple[list[_Block], int]:
    blocks: list[_Block] = []

    def size(kind: str) -> int:
        return max(14, round(width * BASE_SIZES[kind] * scale))

    unit = round(width * 0.02 * scale)
    check_indent = round(size("point") * 1.5)
    for kind, value in (
        ("headline", texts.headline),
        ("subline", texts.subline),
        ("intro", texts.intro),
    ):
        if value:
            font = _font(WEIGHTS[kind], size(kind))
            gap = 0 if not blocks else unit * (2 if kind == "intro" else 1)
            blocks.append(_Block(kind, _wrap(draw, value, font, text_width), font, gap))  # type: ignore[arg-type]
    for index, point in enumerate(texts.points):
        font = _font("regular", size("point"))
        gap = unit * 2 if index == 0 else unit
        blocks.append(
            _Block("point", _wrap(draw, point, font, text_width - check_indent), font, gap)
        )
    if texts.closing:
        font = _font("semibold", size("closing"))
        blocks.append(
            _Block("closing", _wrap(draw, texts.closing, font, text_width), font, unit * 2)
        )
    if facts:
        font = _font("semibold", size("fact"))
        blocks.append(_Block("rule", [""], font, unit * 2))
        for index, line in enumerate(facts):
            blocks.append(
                _Block(
                    "fact",
                    _wrap(draw, line, font, text_width),
                    font,
                    unit if index == 0 else unit // 2,
                )
            )
    height = 0
    for block in blocks:
        line_height = round(block.font.size * (1.22 if block.kind != "rule" else 0.2))
        height += block.gap_before + line_height * len(block.lines)
    return blocks, height


def _line_height(block: _Block) -> int:
    return round(block.font.size * (1.22 if block.kind != "rule" else 0.2))


def _draw_check(
    draw: ImageDraw.ImageDraw, x: int, y: int, size: int, colour: tuple[int, int, int]
) -> None:
    """A check mark drawn as lines (independent of the font's glyphs)."""
    w = max(3, round(size * 0.14))
    points = [
        (x + size * 0.1, y + size * 0.55),
        (x + size * 0.38, y + size * 0.82),
        (x + size * 0.9, y + size * 0.22),
    ]
    draw.line(points, fill=colour, width=w, joint="curve")


def render_info_graphic(
    config: SocialConfig,
    briefing: Briefing,
    texts: InfoTexts,
    fmt: ImageFormat,
    *,
    variant: str = "zentriert",
    background: bytes | None = None,
    fact_keys: Iterable[FactKey] | None = None,
) -> bytes:
    """Check the facts, lay out the texts (shrinking if needed) and render the JPEG.

    Raises `InfoGraphicError` for fact problems and `GraphicLayoutError` when the text does
    not fit even at the smallest font size.
    """
    facts = BriefingFacts.from_json(briefing.facts)
    keys = parse_fact_keys(fact_keys if fact_keys is not None else FACT_KEYS, facts)
    problems = validate_texts(texts, story=fmt is ImageFormat.STORY) + fact_problems(
        texts, facts, keys
    )
    if problems:
        raise InfoGraphicError(problems)
    if variant not in VARIANT_LABELS:
        variant = "zentriert"
    lines_of_facts = fact_lines(facts, keys)
    design = config.brand.design
    primary = parse_hex(design.primary_color if design else DEFAULT_PRIMARY)
    accent = parse_hex(design.accent_color if design else DEFAULT_ACCENT)
    dark = mix(primary, (0, 0, 0), 0.55)
    spec = FORMAT_SPECS[fmt]
    width, height = spec.width, spec.height
    margin = round(width * 0.075)
    bottom_reserve = round(height * 0.1) if fmt is ImageFormat.STORY else 0
    brand_size = round(width * 0.032)
    top = margin + brand_size + round(width * 0.04)

    photo = fit_to_format(open_image(background), fmt).convert("RGBA") if background else None
    canvas = Image.new("RGBA", (width, height), (*primary, 255))
    measure = ImageDraw.Draw(canvas)

    # Text area per variant (x0, y0, x1, y1) and how much the panel may grow.
    pad = round(width * 0.05)
    # Share of the photo in "Foto oben": less in the square format, which has less height.
    photo_share = 0.3 if fmt is ImageFormat.SQUARE else 0.38
    if photo is None:
        area = (margin, top, width - margin, height - margin - bottom_reserve)
    elif variant == "zentriert":
        area = (
            margin + pad,
            top + pad,
            width - margin - pad,
            height - margin - bottom_reserve - pad,
        )
    elif variant == "farbfeld":
        area = (margin, round(height * 0.28), width - margin, height - margin - bottom_reserve)
    else:  # foto_oben
        area = (
            margin,
            round(height * photo_share) + margin,
            width - margin,
            height - margin - bottom_reserve,
        )
    text_width = area[2] - area[0]
    available = area[3] - area[1]

    scale = MAX_SCALE
    while True:
        blocks, needed = _layout(measure, texts, lines_of_facts, width, text_width, scale)
        too_wide = any(
            measure.textlength(line, font=b.font) > text_width for b in blocks for line in b.lines
        )
        if needed <= available and not too_wide:
            break
        scale = round(scale - 0.05, 2)
        if scale < MIN_SCALE:
            raise GraphicLayoutError(
                "Zu viel Text für dieses Format – bitte kürzen (z. B. weniger Punkte oder eine "
                "kürzere Einleitung). Der Text wird nie abgeschnitten."
            )

    # Background and the semi-transparent panel for legibility.
    centred = variant == "zentriert"
    if photo is None:
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        deco = ImageDraw.Draw(layer)
        r = width * 0.5
        deco.ellipse((width - r * 1.1, -r * 0.7, width + r * 0.6, r * 0.9), fill=(*WHITE, 22))
        canvas = Image.alpha_composite(canvas, layer)
        y = area[1] + (available - needed) // 2
        panel_colour = primary
    else:
        canvas = photo
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shade = ImageDraw.Draw(overlay)
        if variant == "zentriert":
            y = area[1] + (available - needed) // 2
            shade.rounded_rectangle(
                (area[0] - pad, y - pad, area[2] + pad, y + needed + pad),
                radius=round(width * 0.03),
                fill=(*dark, 178),
            )
        elif variant == "farbfeld":
            y = area[3] - needed
            shade.rectangle((0, y - pad, width, height), fill=(*dark, 222))
        else:
            split = round(height * photo_share)
            shade.rectangle((0, split, width, height), fill=(*primary, 255))
            y = area[1] + (available - needed) // 2
        canvas = Image.alpha_composite(canvas, overlay)
        panel_colour = dark if variant != "foto_oben" else primary
    colour = _text_colour(panel_colour)
    draw = ImageDraw.Draw(canvas)

    # Brand line at the top (on photos with a small shade behind it).
    brand_font = _font("semibold", brand_size)
    brand = config.brand.name
    if photo is not None:
        tw = draw.textlength(brand, font=brand_font)
        strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(strip).rounded_rectangle(
            (
                margin - 14,
                margin - 10,
                margin + round(width * 0.03) + tw + 14,
                margin + brand_size + 14,
            ),
            radius=12,
            fill=(*dark, 190),
        )
        canvas = Image.alpha_composite(canvas, strip)
        draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (margin, margin, margin + round(width * 0.012), margin + brand_size), fill=accent
    )
    draw.text(
        (margin + round(width * 0.03), margin - 2),
        brand,
        font=brand_font,
        fill=WHITE if photo is not None else colour,
    )

    for block in blocks:
        y += block.gap_before
        lh = _line_height(block)
        if block.kind == "rule":
            length = text_width if centred else round(text_width * 0.3)
            draw.line(
                (area[0], y, area[0] + length, y), fill=accent, width=max(3, round(width * 0.004))
            )
            y += lh
            continue
        for index, line in enumerate(block.lines):
            fill = (
                accent if block.kind == "subline" and _contrast_ok(accent, panel_colour) else colour
            )
            if block.kind == "point":
                indent = round(block.font.size * 1.5)
                line_width = draw.textlength(line, font=block.font) + indent
                x = area[0] + (text_width - line_width) // 2 if centred else area[0]
                if index == 0:
                    _draw_check(
                        draw,
                        int(x),
                        y + round(block.font.size * 0.08),
                        block.font.size,
                        accent if _contrast_ok(accent, panel_colour) else colour,
                    )
                draw.text((x + indent, y), line, font=block.font, fill=colour)
            else:
                x = (
                    area[0] + (text_width - draw.textlength(line, font=block.font)) // 2
                    if centred
                    else area[0]
                )
                draw.text((x, y), line, font=block.font, fill=fill)
            y += lh
    return to_jpeg(canvas.convert("RGB"))


def _contrast_ok(colour: tuple[int, int, int], background: tuple[int, int, int]) -> bool:
    from app.core.theme import contrast_ratio  # noqa: PLC0415

    return contrast_ratio(colour, background) >= 3.0


def texts_for_format(graphic: InfoGraphic, fmt: ImageFormat) -> InfoTexts:
    """Story format uses the shorter story text if one is stored (decision 3c)."""
    if fmt is ImageFormat.STORY:
        story = story_texts(graphic)
        if story is not None:
            return replace(story, headline=story.headline or graphic.headline)
    return InfoTexts.of(graphic)
