"""AI image suggestions for a draft (SPEC_social 13.3–13.5).

Flow: the LLM suggests a prompt from briefing and image style → a person sees and edits it →
a code check rejects people, text, real places/results and personal data → 2–3 variants are
generated through `app.core.images` (switch, cost brake, audit) → a person chooses one (it
becomes an `Asset` labelled "KI-generiert" and is approved with the post) or discards them.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue
from sqlmodel import Session, col, delete, select

from app.core.audit import write_human_audit_entry
from app.core.image_formats import ImageFormat
from app.core.images.base import ImageProvider
from app.core.images.service import generate_images, image_generation_enabled
from app.core.llm.calls import audit_llm_call, call_with_retry
from app.social.checking import member_names
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import ImagePromptOutput
from app.social.models import (
    Asset,
    Briefing,
    ImageCandidate,
    ImageProposal,
    PostDraft,
    PostStatus,
)
from app.social.profiles import SocialConfig
from app.social.sensitive import check_persons
from app.social.textscan import find_phones

PROMPT_STEP = "social.image_prompt"
GENERATE_STEP = "social.image_generate"
CHOOSE_STEP = "social.image_choose"
DISCARD_STEP = "social.image_discard"
MAX_PROMPT_LENGTH = 800
AI_IMAGE_LABEL = "KI-generiert"

# Appended by code to every prompt sent to the image model (not editable).
SAFETY_SUFFIX = (
    "Ohne Menschen, ohne Körperteile, ohne Gesichter. Ohne Text, Buchstaben, Zahlen, Schilder "
    "oder Logos. Keine fotorealistische Darstellung echter Räume oder Ergebnisse, keine "
    "medizinischen Darstellungen."
)

# Statuses in which the image can still change: it is approved together with the post.
EDITABLE_STATUSES = frozenset(
    {PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW}
)


class ImageSuggestionError(ValueError):
    """An image action is not possible (state, missing prompt, unknown candidate)."""


class ImagePromptRejectedError(ImageSuggestionError):
    """The code check found people, text, real places/results or personal data."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("Bild-Prompt abgelehnt: " + " ".join(violations))
        self.violations = violations


def _terms(*words: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\wäöüß])(?:" + "|".join(words) + r")(?![\wäöüß])", re.IGNORECASE)


_PERSON = _terms(
    r"personen?",
    r"menschen?",
    r"leute",
    r"frau(?:en)?",
    r"mann",
    r"männer",
    r"kind(?:er)?",
    r"jungen?",
    r"mädchen",
    r"babys?",
    r"gesicht(?:er)?",
    r"porträts?",
    r"portraits?",
    r"selfies?",
    r"trainer(?:in|innen)?",
    r"therapeut(?:in|innen|en)?",
    r"patient(?:in|innen|en)?",
    r"mitglied(?:er)?",
    r"kund(?:e|in|innen|en|schaft)",
    r"sportler(?:in|innen)?",
    r"athlet(?:in|en)?",
    r"famili(?:e|en)",
    r"senior(?:in|innen|en)?",
    r"händen?",
    r"hand",
    r"körper",
    r"arme",
    r"beine?",
    r"silhouetten?",
    r"models?",
    r"team",
    r"mitarbeitende[nr]?",
    r"people",
    r"person",
    r"woman",
    r"women",
    r"child(?:ren)?",
    r"faces?",
    r"hands?",
)
_TEXT = _terms(
    r"texte?",
    r"schrift(?:zug|zügen?)?",
    r"aufschrift(?:en)?",
    r"buchstaben",
    r"wörter",
    r"worte",
    r"zahlen",
    r"logos?",
    r"slogans?",
    r"überschrift(?:en)?",
    r"schilder?",
    r"beschriftung(?:en)?",
    r"typografie",
    r"lettering",
    r"text",
    r"words?",
    r"letters",
    r"signs?",
)
_REAL = _terms(
    r"unser(?:e|em|en)? (?:studio|räume|praxis|team|betrieb|werkstatt)",
    r"(?:echte|reale)[nrs]? (?:räume|raum|studio|praxis|ergebnisse|erfolge)",
    r"vorher",
    r"nachher",
    r"vorher-nachher",
    r"before",
    r"after",
    r"ergebnis(?:se)?",
    r"transformation",
    r"abnehmerfolg",
    r"röntgen\w*",
    r"operation(?:en)?",
    r"wunden?",
    r"verletzung(?:en)?",
    r"krankheit(?:en)?",
    r"diagnosen?",
    r"spritzen?",
    r"medikamente?",
    r"skelett",
    r"organe?",
    r"blut",
    r"narben?",
)
_NEGATION = re.compile(r"(?:kein\w*|ohne|nicht|no|without)\W+(?:\w+\W+){0,2}$", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_QUOTED = re.compile(r"[„\"“”'‚‘][^„\"“”'‚‘]{2,}[“”\"'‘’]")


def _negated(text: str, start: int) -> bool:
    clause = re.split(r"[.;:!?\n]", text[:start])[-1]
    return _NEGATION.search(clause) is not None


def _hits(pattern: re.Pattern[str], text: str) -> list[str]:
    return [m.group(0) for m in pattern.finditer(text) if not _negated(text, m.start())]


def prompt_violations(prompt: str, member_list: list[str], brand_name: str) -> list[str]:
    """Code check of an image prompt (SPEC_social 13.5); empty list = allowed."""
    violations: list[str] = []
    if not prompt.strip():
        return ["Bitte einen Bild-Prompt eingeben."]
    if len(prompt) > MAX_PROMPT_LENGTH:
        violations.append(f"Der Prompt ist zu lang (höchstens {MAX_PROMPT_LENGTH} Zeichen).")
    persons = _hits(_PERSON, prompt)
    named = [f for f in check_persons(prompt, member_list) if f.span]
    if persons or named:
        found = persons + [prompt[f.span[0] : f.span[1]] for f in named if f.span]
        violations.append(
            "Personenbezug: KI-Bilder dürfen keine Menschen, Gesichter oder Namen zeigen "
            f"(gefunden: {', '.join(dict.fromkeys(found))})."
        )
    text = _hits(_TEXT, prompt) + [m.group(0) for m in _QUOTED.finditer(prompt)]
    if text:
        violations.append(
            "Kein Text im KI-Bild: Schrift kommt nur über Vorlagen "
            f"(gefunden: {', '.join(dict.fromkeys(text))})."
        )
    real = _hits(_REAL, prompt)
    if brand_name and brand_name.lower() in prompt.lower():
        real.append(brand_name)
    if real:
        violations.append(
            "Keine Darstellung echter Räume oder Ergebnisse, keine Vorher-nachher- oder "
            f"medizinischen Darstellungen (gefunden: {', '.join(dict.fromkeys(real))})."
        )
    if _EMAIL.search(prompt) or find_phones(prompt):
        violations.append("Keine personenbezogenen Daten (E-Mail-Adressen, Telefonnummern).")
    return violations


def provider_prompt(prompt: str, config: SocialConfig, area_key: str) -> str:
    """What the image model receives: the checked prompt plus fixed exclusions."""
    style = config.brand.area(area_key).image_style
    exclusions = (
        f" Nicht zeigen: {', '.join(style.exclusions)}." if style and style.exclusions else ""
    )
    return f"{prompt.strip()}\n\n{SAFETY_SUFFIX}{exclusions}"


# --- state ---


@dataclass
class ImageState:
    """What the draft page shows for AI images."""

    proposal: ImageProposal | None
    candidates: list[ImageCandidate]
    chosen: Asset | None
    violations: list[str]


def _briefing(session: Session, draft: PostDraft) -> Briefing:
    briefing = session.get(Briefing, draft.briefing_id)
    if briefing is None:
        raise ImageSuggestionError("Briefing nicht gefunden.")
    return briefing


def latest_proposal(session: Session, draft_id: int) -> ImageProposal | None:
    statement = (
        select(ImageProposal)
        .where(ImageProposal.draft_id == draft_id)
        .order_by(col(ImageProposal.id).desc())
    )
    return session.exec(statement).first()


def candidates(session: Session, draft_id: int) -> list[ImageCandidate]:
    statement = (
        select(ImageCandidate)
        .where(ImageCandidate.draft_id == draft_id)
        .order_by(col(ImageCandidate.variant))
    )
    return list(session.exec(statement).all())


def chosen_image(session: Session, draft_id: int) -> Asset | None:
    statement = select(Asset).where(Asset.draft_id == draft_id).where(Asset.ai_generated)
    return session.exec(statement).first()


def image_state(session: Session, config: SocialConfig, draft: PostDraft) -> ImageState:
    proposal = latest_proposal(session, draft.id or 0)
    violations: list[str] = []
    if proposal and proposal.prompt:
        violations = prompt_violations(proposal.prompt, member_names(session), config.brand.name)
    return ImageState(
        proposal=proposal,
        candidates=candidates(session, draft.id or 0),
        chosen=chosen_image(session, draft.id or 0),
        violations=violations,
    )


def _require_editable(draft: PostDraft) -> None:
    if not image_generation_enabled():
        raise ImageSuggestionError("Die Bilderzeugung ist ausgeschaltet.")
    if draft.status not in EDITABLE_STATUSES:
        raise ImageSuggestionError(
            "Das Bild lässt sich nur vor der Freigabe ändern (es wird mit dem Beitrag freigegeben)."
        )


def _context(draft: PostDraft) -> dict[str, JsonValue]:
    return {"briefing_id": draft.briefing_id, "draft_id": draft.id, "channel": draft.channel.value}


# --- actions ---


def suggest_prompt(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    draft: PostDraft,
    fmt: ImageFormat,
) -> ImageProposal:
    """LLM suggestion for the image prompt (one retry, then an empty prompt to fill in)."""
    _require_editable(draft)
    briefing = _briefing(session, draft)

    def attempt() -> object:
        return client.image_prompt(
            config,
            area_key=briefing.area,
            occasion_key=briefing.occasion_type,
            notes=briefing.notes,
        )

    def record(call: object, number: int) -> None:
        audit_llm_call(
            session,
            call,  # type: ignore[arg-type]
            attempt=number,
            provider=client.provider.name,
            step=PROMPT_STEP,
            context=_context(draft),
        )

    call = call_with_retry(attempt, record)  # type: ignore[arg-type]
    output = call.output if call.ok else None
    proposal = ImageProposal(
        draft_id=draft.id or 0,
        briefing_id=draft.briefing_id,
        prompt=output.prompt if isinstance(output, ImagePromptOutput) else "",
        prompt_from_ai=output is not None,
        image_format=fmt.value,
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal


def _remove_candidates(session: Session, draft_id: int, keep_path: str | None = None) -> int:
    rows = candidates(session, draft_id)
    for row in rows:
        path = Path(row.stored_path)
        if row.stored_path != keep_path and path.is_file():
            path.unlink()
    session.exec(delete(ImageCandidate).where(col(ImageCandidate.draft_id) == draft_id))  # type: ignore[call-overload]
    session.commit()
    return len(rows)


def generate_variants(
    session: Session,
    config: SocialConfig,
    provider: ImageProvider,
    draft: PostDraft,
    *,
    prompt: str,
    fmt: ImageFormat,
    user: str,
    storage: Path,
) -> list[ImageCandidate]:
    """Check the (possibly edited) prompt, then generate 2–3 variants in `fmt`."""
    _require_editable(draft)
    briefing = _briefing(session, draft)
    prompt = prompt.strip()
    violations = prompt_violations(prompt, member_names(session), config.brand.name)
    proposal = latest_proposal(session, draft.id or 0)
    if proposal is None:
        proposal = ImageProposal(
            draft_id=draft.id or 0, briefing_id=draft.briefing_id, prompt_from_ai=False
        )
    if prompt != proposal.prompt:
        proposal.prompt_from_ai = False
    proposal.prompt = prompt
    proposal.image_format = fmt.value
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    if violations:
        raise ImagePromptRejectedError(violations)
    batch = generate_images(
        session,
        provider,
        prompt=provider_prompt(prompt, config, briefing.area),
        fmt=fmt,
        step=GENERATE_STEP,
        scope=f"draft:{draft.id}",
        user=user,
        details=_context(draft) | {"prompt_edited": not proposal.prompt_from_ai},
    )
    _remove_candidates(session, draft.id or 0)
    target_dir = storage / "social" / "ki"
    target_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, image in enumerate(batch.images, start=1):
        digest = hashlib.sha256(image.content).hexdigest()
        suffix = ".png" if image.media_type == "image/png" else ".jpg"
        path = target_dir / f"{digest}{suffix}"
        path.write_bytes(image.content)
        row = ImageCandidate(
            proposal_id=proposal.id or 0,
            draft_id=draft.id or 0,
            briefing_id=draft.briefing_id,
            variant=number,
            stored_path=str(path),
            media_type=image.media_type,
            content_hash=digest,
            model=batch.model,
        )
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def choose_candidate(session: Session, draft: PostDraft, candidate_id: int, user: str) -> Asset:
    """A person picks one variant: it becomes the post's image, labelled "KI-generiert"."""
    _require_editable(draft)
    candidate = session.get(ImageCandidate, candidate_id)
    if candidate is None or candidate.draft_id != draft.id:
        raise ImageSuggestionError("Variante nicht gefunden.")
    previous = chosen_image(session, draft.id or 0)
    if previous is not None:
        old_path = previous.stored_path
        session.delete(previous)
        session.commit()
        if Path(old_path).is_file() and old_path != candidate.stored_path:
            Path(old_path).unlink()
    asset = Asset(
        briefing_id=draft.briefing_id,
        draft_id=draft.id,
        filename=f"ki-bild-entwurf-{draft.id}-variante-{candidate.variant}"
        + Path(candidate.stored_path).suffix,
        stored_path=candidate.stored_path,
        media_type=candidate.media_type,
        content_hash=candidate.content_hash,
        uploaded_by=user,
        ai_generated=True,
        shows_persons=False,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    _remove_candidates(session, draft.id or 0, keep_path=candidate.stored_path)
    write_human_audit_entry(
        session,
        item_id=None,
        step=CHOOSE_STEP,
        actor=user,
        details=_context(draft)
        | {"asset_id": asset.id, "variant": candidate.variant, "image_hash": asset.content_hash},
    )
    return asset


def discard_candidates(session: Session, draft: PostDraft, user: str) -> int:
    """A person discards all open variants (files are deleted)."""
    _require_editable(draft)
    removed = _remove_candidates(session, draft.id or 0)
    write_human_audit_entry(
        session,
        item_id=None,
        step=DISCARD_STEP,
        actor=user,
        details=_context(draft) | {"variants": removed},
    )
    return removed


def delete_image_data(session: Session, draft_ids: list[int]) -> set[str]:
    """Deletion concept: remove proposals and candidates of drafts; returns candidate files.

    AI images chosen as assets are deleted with the assets (same rules as uploads).
    """
    if not draft_ids:
        return set()
    rows = session.exec(
        select(ImageCandidate).where(col(ImageCandidate.draft_id).in_(draft_ids))
    ).all()
    files = {row.stored_path for row in rows}
    session.exec(delete(ImageCandidate).where(col(ImageCandidate.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
    session.exec(delete(ImageProposal).where(col(ImageProposal.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
    return files
