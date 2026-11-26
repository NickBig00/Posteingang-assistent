"""AI image suggestions for a briefing (SPEC_social 13.3–13.5, one image per briefing 14.3).

Flow: the LLM suggests a prompt from briefing and image style → a person sees and edits it →
a code check gives hints on people, text in the image, real places/results and contact data
(confirmed by a person before approval) and only blocks member names → 2–3 variants are
generated through `app.core.images` (switch, cost brake per briefing, audit) → a person chooses
one (it becomes an `Asset` labelled "KI-generiert" and the image of the briefing, approved with
each post) or discards them.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue
from sqlalchemy import or_
from sqlmodel import Session, col, delete, select

from app.core.audit import write_human_audit_entry
from app.core.image_formats import ImageFormat
from app.core.images.base import ImageProvider
from app.core.images.service import generate_images, image_generation_enabled
from app.core.llm.calls import audit_llm_call, call_with_retry
from app.core.models import utcnow
from app.social.briefing_image import (
    BriefingImageError,
    briefing_image,
    image_locked,
    set_briefing_image,
)
from app.social.carousel_refs import asset_in_carousel
from app.social.checking import member_names
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import ImagePromptOutput
from app.social.models import (
    Asset,
    Briefing,
    ImageCandidate,
    ImageProposal,
    ImageSource,
    PromptHint,
)
from app.social.profiles import SocialConfig
from app.social.sensitive import check_persons
from app.social.textscan import find_phones

PROMPT_STEP = "social.image_prompt"
GENERATE_STEP = "social.image_generate"
CHOOSE_STEP = "social.image_choose"
DISCARD_STEP = "social.image_discard"
HINT_CONFIRM_STEP = "social.image_hint_confirm"
MEMBER_CONSENT_STEP = "social.image_member_consent"
MAX_PROMPT_LENGTH = 800
AI_IMAGE_LABEL = "KI-generiert"

# Appended by code to every prompt sent to the image model (not editable). People and text
# are allowed with a confirmed hint (SPEC_social 13.7); real, recognisable persons never.
SAFETY_SUFFIX = (
    "Keine echten, erkennbaren Personen nachbilden (keine Prominenten, keine bestimmten "
    "Personen). Keine Logos, keine Wasserzeichen und kein Text außer dem, der oben "
    "ausdrücklich verlangt wird."
)


class ImageSuggestionError(ValueError):
    """An image action is not possible (state, missing prompt, unknown candidate)."""


class ImagePromptRejectedError(ImageSuggestionError):
    """The code check found an error that blocks generation (member names, empty, too long)."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("Bild-Prompt abgelehnt: " + " ".join(violations))
        self.violations = violations


class ImageHintsPendingError(ImageSuggestionError):
    """Hints on the prompt must be confirmed before anything is generated."""


# Keys of the hints on an image prompt (SPEC_social 13.5, table).
HINT_MEMBER = "mitglied"
HINT_PERSONS = "personen"
HINT_NAMES = "namen"
HINT_TEXT = "text"
HINT_REAL = "echt"
HINT_CONTACT = "kontakt"
HINT_LABELS: dict[str, str] = {
    HINT_MEMBER: "Mitglied genannt",
    HINT_PERSONS: "Personen im Bild",
    HINT_NAMES: "Name im Prompt",
    HINT_TEXT: "Text im Bild",
    HINT_REAL: "Echte Räume, Ergebnisse, Medizin",
    HINT_CONTACT: "Kontaktdaten",
}


@dataclass(frozen=True)
class PromptFinding:
    """A hint on an image prompt that a person confirms before the post is approved."""

    key: str
    reason: str
    # Member from the member list: only the confirmed consent of that person lifts it.
    consent_required: bool = False


@dataclass(frozen=True)
class PromptCheck:
    """Result of the code check: errors block generation, hints need a confirmation."""

    errors: list[str]
    hints: list[PromptFinding]


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


def check_prompt(prompt: str, member_list: list[str], brand_name: str) -> PromptCheck:
    """Code check of an image prompt (SPEC_social 13.5): hint instead of blockade.

    Errors only for unusable input (empty, too long). A member named in the prompt needs the
    confirmed consent of that person (`CLAUDE.md`); all other findings are hints that a
    person confirms before generating.
    """
    errors: list[str] = []
    hints: list[PromptFinding] = []
    if not prompt.strip():
        return PromptCheck(["Bitte einen Bild-Prompt eingeben."], [])
    if len(prompt) > MAX_PROMPT_LENGTH:
        errors.append(f"Der Prompt ist zu lang (höchstens {MAX_PROMPT_LENGTH} Zeichen).")
    named = [f for f in check_persons(prompt, member_list) if f.span]
    members = [prompt[f.span[0] : f.span[1]] for f in named if f.consent_required and f.span]
    others = [prompt[f.span[0] : f.span[1]] for f in named if not f.consent_required and f.span]
    if members:
        hints.append(
            PromptFinding(
                HINT_MEMBER,
                f"„{', '.join(dict.fromkeys(members))}“ steht in der Mitgliederliste. Der Name "
                "geht an den Bildanbieter – nur mit der dokumentierten Einwilligung der Person, "
                "sonst den Namen entfernen.",
                consent_required=True,
            )
        )
    persons = _hits(_PERSON, prompt)
    if persons:
        hints.append(
            PromptFinding(
                HINT_PERSONS,
                f"Das Bild soll Menschen zeigen ({', '.join(dict.fromkeys(persons))}). Erlaubt – "
                "bitte prüfen, dass niemand Echtes nachgebildet wird. Gezeigte Personen sind "
                "keine echten Mitglieder; das Bild bleibt als „KI-generiert“ gekennzeichnet.",
            )
        )
    if others:
        hints.append(
            PromptFinding(
                HINT_NAMES,
                f"Name im Prompt ({', '.join(dict.fromkeys(others))}): keine echten, erkennbaren "
                "Personen (Mitarbeitende, Prominente) nachbilden lassen.",
            )
        )
    text = _hits(_TEXT, prompt) + [m.group(0) for m in _QUOTED.finditer(prompt)]
    if text:
        hints.append(
            PromptFinding(
                HINT_TEXT,
                f"Text im Bild ({', '.join(dict.fromkeys(text))}): Bildmodelle verändern oft "
                "Buchstaben und Zahlen. Daten, Uhrzeiten, Preise und Orte im Bild genau prüfen "
                "oder besser über die Info-Grafik einfügen (dort geprüft).",
            )
        )
    real = _hits(_REAL, prompt)
    if brand_name and brand_name.lower() in prompt.lower():
        real.append(brand_name)
    if real:
        hints.append(
            PromptFinding(
                HINT_REAL,
                f"Das Bild könnte echte Räume, echte Ergebnisse oder medizinische Inhalte "
                f"vorgeben ({', '.join(dict.fromkeys(real))}). Ein KI-Bild darf nicht als "
                "Foto des eigenen Betriebs oder als echtes Ergebnis wirken; Vorher-nachher- und "
                "medizinische Darstellungen sind in der Gesundheitswerbung besonders heikel.",
            )
        )
    if _EMAIL.search(prompt) or find_phones(prompt):
        hints.append(
            PromptFinding(
                HINT_CONTACT,
                "Kontaktdaten im Prompt (E-Mail-Adresse oder Telefonnummer): sie gehen an den "
                "Bildanbieter und können verfälscht im Bild erscheinen.",
            )
        )
    return PromptCheck(errors, hints)


def prompt_violations(prompt: str, member_list: list[str], brand_name: str) -> list[str]:
    """Only the blocking errors of `check_prompt` (empty list = generation allowed)."""
    return check_prompt(prompt, member_list, brand_name).errors


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
    """What the briefing page shows for AI images."""

    proposal: ImageProposal | None
    candidates: list[ImageCandidate]
    chosen: Asset | None
    violations: list[str]
    # Live hints of the prompt in the text field (before generating).
    hints: list[PromptFinding]
    # Stored hints of the last generation, with their confirmations.
    stored_hints: list[PromptHint]


def latest_proposal(session: Session, briefing_id: int) -> ImageProposal | None:
    statement = (
        select(ImageProposal)
        .where(ImageProposal.briefing_id == briefing_id)
        .order_by(col(ImageProposal.id).desc())
    )
    return session.exec(statement).first()


def candidates(session: Session, briefing_id: int) -> list[ImageCandidate]:
    statement = (
        select(ImageCandidate)
        .where(ImageCandidate.briefing_id == briefing_id)
        .order_by(col(ImageCandidate.variant))
    )
    return list(session.exec(statement).all())


def ai_images(session: Session, briefing_id: int) -> list[Asset]:
    """AI images chosen for the briefing (the current one and those kept for the carousel)."""
    statement = (
        select(Asset)
        .where(Asset.briefing_id == briefing_id)
        .where(Asset.ai_generated)
        .order_by(col(Asset.id))
    )
    return list(session.exec(statement).all())


def chosen_image(session: Session, briefing_id: int) -> Asset | None:
    """The AI image that is the image of the briefing (None for photos or colour fills)."""
    image = briefing_image(session, briefing_id)
    if image is None or image.source is not ImageSource.AI or image.asset_id is None:
        return None
    return session.get(Asset, image.asset_id)


def image_state(session: Session, config: SocialConfig, briefing: Briefing) -> ImageState:
    proposal = latest_proposal(session, briefing.id or 0)
    check = PromptCheck([], [])
    if proposal and proposal.prompt:
        check = check_prompt(proposal.prompt, member_names(session), config.brand.name)
    return ImageState(
        proposal=proposal,
        candidates=candidates(session, briefing.id or 0),
        chosen=chosen_image(session, briefing.id or 0),
        violations=check.errors,
        hints=check.hints,
        stored_hints=prompt_hints(session, briefing.id or 0),
    )


def prompt_hints(session: Session, briefing_id: int) -> list[PromptHint]:
    statement = (
        select(PromptHint).where(PromptHint.briefing_id == briefing_id).order_by(col(PromptHint.id))
    )
    return list(session.exec(statement).all())


def _store_hints(
    session: Session, briefing: Briefing, proposal: ImageProposal, hints: list[PromptFinding]
) -> None:
    """Hints of the prompt to generate from. A confirmation stays only while the prompt is
    unchanged; a changed prompt starts over (like edited post texts)."""
    previous = prompt_hints(session, briefing.id or 0)
    confirmed = {
        h.key: h
        for h in previous
        if h.confirmed_at is not None and h.prompt_hash == _hash(proposal.prompt)
    }
    for old in previous:
        session.delete(old)
    session.flush()
    for hint in hints:
        kept = confirmed.get(hint.key)
        session.add(
            PromptHint(
                briefing_id=briefing.id,
                proposal_id=proposal.id or 0,
                key=hint.key,
                reason=hint.reason,
                prompt_hash=_hash(proposal.prompt),
                consent_required=hint.consent_required,
                confirmed_by=kept.confirmed_by if kept else None,
                confirmed_at=kept.confirmed_at if kept else None,
            )
        )
    session.commit()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def confirm_prompt_hint(session: Session, hint: PromptHint, user: str) -> PromptHint:
    """A person confirms the consent of a member named in the prompt (audit trail).

    Other hints are only listed and cannot be confirmed (nothing waits for them).
    """
    if not hint.consent_required:
        raise ImageSuggestionError(
            "Dieser Hinweis braucht keine Bestätigung – nur ein Mitgliedername braucht die "
            "Einwilligung der Person."
        )
    if hint.briefing_id is None or image_locked(session, hint.briefing_id):
        raise ImageSuggestionError("Einwilligungen können nur vor der Freigabe bestätigt werden.")
    if hint.confirmed_at is not None:
        raise ImageSuggestionError("Der Hinweis ist bereits bestätigt.")
    hint.confirmed_by, hint.confirmed_at = user, utcnow()
    session.add(hint)
    session.commit()
    session.refresh(hint)
    # Which hint, not the prompt or the name itself (data minimisation).
    write_human_audit_entry(
        session,
        item_id=None,
        step=MEMBER_CONSENT_STEP if hint.consent_required else HINT_CONFIRM_STEP,
        actor=user,
        details={"briefing_id": hint.briefing_id, "hint_id": hint.id, "hint": hint.key},
    )
    return hint


def image_hint_blockers(session: Session, briefing_id: int) -> list[str]:
    """A member named in the prompt behind the AI image blocks the approval until the consent
    of that person is confirmed; all other hints are only listed."""
    if chosen_image(session, briefing_id) is None:
        return []
    return [
        f"Einwilligung fehlt: {HINT_LABELS.get(h.key, h.key)} im Bild-Prompt"
        for h in prompt_hints(session, briefing_id)
        if h.consent_required and h.confirmed_at is None
    ]


def _require_editable(session: Session, briefing: Briefing) -> None:
    if not image_generation_enabled():
        raise ImageSuggestionError("Die Bilderzeugung ist ausgeschaltet.")
    if image_locked(session, briefing.id or 0):
        raise ImageSuggestionError(
            "Das Bild lässt sich nur vor der Freigabe ändern (es wird mit den Beiträgen "
            "freigegeben). Zum Ändern den Beitrag verwerfen und wieder öffnen."
        )


def _context(briefing: Briefing) -> dict[str, JsonValue]:
    return {"briefing_id": briefing.id}


# --- actions ---


def suggest_prompt(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    fmt: ImageFormat = ImageFormat.PORTRAIT,
) -> ImageProposal:
    """LLM suggestion for the image prompt (one retry, then an empty prompt to fill in)."""
    _require_editable(session, briefing)

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
            context=_context(briefing),
        )

    call = call_with_retry(attempt, record)  # type: ignore[arg-type]
    output = call.output if call.ok else None
    proposal = ImageProposal(
        briefing_id=briefing.id or 0,
        prompt=output.prompt if isinstance(output, ImagePromptOutput) else "",
        prompt_from_ai=output is not None,
        image_format=fmt.value,
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal


def save_prompt(
    session: Session, config: SocialConfig, briefing: Briefing, prompt: str
) -> PromptCheck:
    """Store the prompt and its hints without generating ("Prüfen"): the hints appear below
    the field and are confirmed before "Bilder erzeugen" (first confirm, then generate)."""
    _require_editable(session, briefing)
    prompt = prompt.strip()
    check = check_prompt(prompt, member_names(session), config.brand.name)
    proposal = latest_proposal(session, briefing.id or 0)
    if proposal is None:
        proposal = ImageProposal(briefing_id=briefing.id or 0, prompt_from_ai=False)
    if prompt != proposal.prompt:
        proposal.prompt_from_ai = False
    proposal.prompt = prompt
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    if check.errors:
        raise ImagePromptRejectedError(check.errors)
    _store_hints(session, briefing, proposal, check.hints)
    return check


def pending_hints(session: Session, briefing_id: int) -> list[PromptHint]:
    """Member names in the stored prompt without the confirmed consent (block generating).

    All other hints are only listed and need no confirmation.
    """
    proposal = latest_proposal(session, briefing_id)
    if proposal is None:
        return []
    return [
        h
        for h in prompt_hints(session, briefing_id)
        if h.consent_required and h.confirmed_at is None and h.prompt_hash == _hash(proposal.prompt)
    ]


def _remove_candidates(session: Session, briefing_id: int, keep_path: str | None = None) -> int:
    rows = candidates(session, briefing_id)
    for row in rows:
        path = Path(row.stored_path)
        used = session.exec(select(Asset).where(Asset.stored_path == row.stored_path)).first()
        if row.stored_path != keep_path and used is None and path.is_file():
            path.unlink()
    session.exec(delete(ImageCandidate).where(col(ImageCandidate.briefing_id) == briefing_id))  # type: ignore[call-overload]
    session.commit()
    return len(rows)


def generate_variants(
    session: Session,
    config: SocialConfig,
    provider: ImageProvider,
    briefing: Briefing,
    *,
    prompt: str,
    fmt: ImageFormat = ImageFormat.PORTRAIT,
    user: str,
    storage: Path,
) -> list[ImageCandidate]:
    """Check the (possibly edited) prompt, then generate 2–3 variants in `fmt`.

    The cost brake counts per briefing (scope `briefing:{id}`), like photo edits.
    """
    _require_editable(session, briefing)
    prompt = prompt.strip()
    check = check_prompt(prompt, member_names(session), config.brand.name)
    violations = check.errors
    proposal = latest_proposal(session, briefing.id or 0)
    if proposal is None:
        proposal = ImageProposal(briefing_id=briefing.id or 0, prompt_from_ai=False)
    if prompt != proposal.prompt:
        proposal.prompt_from_ai = False
    proposal.prompt = prompt
    proposal.image_format = fmt.value
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    if violations:
        raise ImagePromptRejectedError(violations)
    # Hints are only listed (product owner, November 2026). A member named in the prompt is
    # the exception (CLAUDE.md): the name goes to the provider only with the confirmed consent.
    _store_hints(session, briefing, proposal, check.hints)
    pending = pending_hints(session, briefing.id or 0)
    if pending:
        raise ImageHintsPendingError(
            "Im Bild-Prompt steht ein Name aus der Mitgliederliste: erst die Einwilligung der "
            "Person bestätigen oder den Namen entfernen, dann erzeugen."
        )
    batch = generate_images(
        session,
        provider,
        prompt=provider_prompt(prompt, config, briefing.area),
        fmt=fmt,
        step=GENERATE_STEP,
        scope=f"briefing:{briefing.id}",
        user=user,
        details=_context(briefing)
        | {
            "prompt_edited": not proposal.prompt_from_ai,
            "hints": [h.key for h in check.hints],
        },
    )
    _remove_candidates(session, briefing.id or 0)
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
            briefing_id=briefing.id or 0,
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


def _drop_previous_ai_image(session: Session, briefing_id: int, keep_path: str) -> None:
    """The previous AI image goes, unless a carousel page still shows it."""
    previous = chosen_image(session, briefing_id)
    if previous is None or asset_in_carousel(session, previous.id or 0):
        return
    old_path = previous.stored_path
    session.delete(previous)
    session.commit()
    still_used = session.exec(select(Asset).where(Asset.stored_path == old_path)).first()
    if still_used is None and Path(old_path).is_file() and old_path != keep_path:
        Path(old_path).unlink()


def choose_candidate(session: Session, briefing: Briefing, candidate_id: int, user: str) -> Asset:
    """A person picks one variant: it becomes the image of the briefing, "KI-generiert"."""
    _require_editable(session, briefing)
    candidate = session.get(ImageCandidate, candidate_id)
    if candidate is None or candidate.briefing_id != briefing.id:
        raise ImageSuggestionError("Variante nicht gefunden.")
    _drop_previous_ai_image(session, briefing.id or 0, candidate.stored_path)
    asset = Asset(
        briefing_id=briefing.id or 0,
        filename=f"ki-bild-briefing-{briefing.id}-variante-{candidate.variant}"
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
    _remove_candidates(session, briefing.id or 0, keep_path=candidate.stored_path)
    try:
        set_briefing_image(session, briefing.id or 0, ImageSource.AI, asset.id, user, audit=False)
    except BriefingImageError as error:
        raise ImageSuggestionError(str(error)) from error
    write_human_audit_entry(
        session,
        item_id=None,
        step=CHOOSE_STEP,
        actor=user,
        details=_context(briefing)
        | {"asset_id": asset.id, "variant": candidate.variant, "image_hash": asset.content_hash},
    )
    return asset


def discard_candidates(session: Session, briefing: Briefing, user: str) -> int:
    """A person discards all open variants (files are deleted)."""
    _require_editable(session, briefing)
    removed = _remove_candidates(session, briefing.id or 0)
    write_human_audit_entry(
        session,
        item_id=None,
        step=DISCARD_STEP,
        actor=user,
        details=_context(briefing) | {"variants": removed},
    )
    return removed


def delete_image_data(
    session: Session, *, briefing_id: int | None = None, draft_ids: list[int] | None = None
) -> set[str]:
    """Deletion concept: remove proposals, hints and candidates; returns candidate files.

    Rows belong to the briefing; `draft_ids` covers rows from before U6 (images per draft).
    AI images chosen as assets are deleted with the assets (same rules as uploads).
    """
    files: set[str] = set()
    for model in (ImageCandidate, PromptHint, ImageProposal):
        clauses = []
        if briefing_id is not None:
            clauses.append(col(model.briefing_id) == briefing_id)
        if draft_ids:
            clauses.append(col(model.draft_id).in_(draft_ids))
        if not clauses:
            continue
        condition = or_(*clauses)
        if model is ImageCandidate:
            files |= {row.stored_path for row in session.exec(select(model).where(condition))}
        session.exec(delete(model).where(condition))  # type: ignore[call-overload]
    return files
