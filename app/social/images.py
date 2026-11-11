"""AI image suggestions for a draft (SPEC_social 13.3–13.5).

Flow: the LLM suggests a prompt from briefing and image style → a person sees and edits it →
a code check gives hints on people, text in the image, real places/results and contact data
(confirmed by a person before approval) and only blocks member names → 2–3 variants are
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
from app.core.models import utcnow
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
MAX_PROMPT_LENGTH = 800
AI_IMAGE_LABEL = "KI-generiert"

# Appended by code to every prompt sent to the image model (not editable). People and text
# are allowed with a confirmed hint (SPEC_social 13.7); real, recognisable persons never.
SAFETY_SUFFIX = (
    "Keine echten, erkennbaren Personen nachbilden (keine Prominenten, keine bestimmten "
    "Personen). Keine Logos, keine Wasserzeichen und kein Text außer dem, der oben "
    "ausdrücklich verlangt wird."
)

# Statuses in which the image can still change: it is approved together with the post.
EDITABLE_STATUSES = frozenset(
    {PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW}
)


class ImageSuggestionError(ValueError):
    """An image action is not possible (state, missing prompt, unknown candidate)."""


class ImagePromptRejectedError(ImageSuggestionError):
    """The code check found an error that blocks generation (member names, empty, too long)."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("Bild-Prompt abgelehnt: " + " ".join(violations))
        self.violations = violations


# Keys of the hints on an image prompt (SPEC_social 13.5, table).
HINT_PERSONS = "personen"
HINT_NAMES = "namen"
HINT_TEXT = "text"
HINT_REAL = "echt"
HINT_CONTACT = "kontakt"
HINT_LABELS: dict[str, str] = {
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

    Errors only for what `CLAUDE.md` demands (member names) and for unusable input.
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
        errors.append(
            "Name aus der Mitgliederliste im Bild-Prompt "
            f"({', '.join(dict.fromkeys(members))}): keine Daten von Mitgliedern an den "
            "Bildanbieter. Bitte den Namen entfernen."
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
    """What the draft page shows for AI images."""

    proposal: ImageProposal | None
    candidates: list[ImageCandidate]
    chosen: Asset | None
    violations: list[str]
    # Live hints of the prompt in the text field (before generating).
    hints: list[PromptFinding]
    # Stored hints of the last generation, with their confirmations.
    stored_hints: list[PromptHint]


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
    check = PromptCheck([], [])
    if proposal and proposal.prompt:
        check = check_prompt(proposal.prompt, member_names(session), config.brand.name)
    return ImageState(
        proposal=proposal,
        candidates=candidates(session, draft.id or 0),
        chosen=chosen_image(session, draft.id or 0),
        violations=check.errors,
        hints=check.hints,
        stored_hints=prompt_hints(session, draft.id or 0),
    )


def prompt_hints(session: Session, draft_id: int) -> list[PromptHint]:
    statement = (
        select(PromptHint).where(PromptHint.draft_id == draft_id).order_by(col(PromptHint.id))
    )
    return list(session.exec(statement).all())


def _store_hints(
    session: Session, draft: PostDraft, proposal: ImageProposal, hints: list[PromptFinding]
) -> None:
    """Hints of the prompt used for the generation. A confirmation stays only while the same
    prompt is used again; a changed prompt starts over (like edited post texts)."""
    previous = prompt_hints(session, draft.id or 0)
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
                draft_id=draft.id or 0,
                proposal_id=proposal.id or 0,
                key=hint.key,
                reason=hint.reason,
                prompt_hash=_hash(proposal.prompt),
                confirmed_by=kept.confirmed_by if kept else None,
                confirmed_at=kept.confirmed_at if kept else None,
            )
        )
    session.commit()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def confirm_prompt_hint(session: Session, hint: PromptHint, user: str) -> PromptHint:
    """A person confirms a hint on the image prompt (recorded in the audit trail)."""
    draft = session.get(PostDraft, hint.draft_id)
    if draft is None or draft.status not in EDITABLE_STATUSES:
        raise ImageSuggestionError("Hinweise können nur vor der Freigabe bestätigt werden.")
    if hint.confirmed_at is not None:
        raise ImageSuggestionError("Der Hinweis ist bereits bestätigt.")
    hint.confirmed_by, hint.confirmed_at = user, utcnow()
    session.add(hint)
    session.commit()
    session.refresh(hint)
    # Which hint, not the prompt itself (its hash is in the generation's audit entry).
    write_human_audit_entry(
        session,
        item_id=None,
        step=HINT_CONFIRM_STEP,
        actor=user,
        details=_context(draft) | {"hint_id": hint.id, "hint": hint.key},
    )
    return hint


def image_hint_blockers(session: Session, draft: PostDraft) -> list[str]:
    """Unconfirmed hints of the prompt behind the post's AI image block its approval."""
    if draft.id is None or chosen_image(session, draft.id) is None:
        return []
    return [
        f"Hinweis zum KI-Bild noch nicht bestätigt: {HINT_LABELS.get(h.key, h.key)}"
        for h in prompt_hints(session, draft.id)
        if h.confirmed_at is None
    ]


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
    check = check_prompt(prompt, member_names(session), config.brand.name)
    violations = check.errors
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
        details=_context(draft)
        | {
            "prompt_edited": not proposal.prompt_from_ai,
            "hints": [h.key for h in check.hints],
        },
    )
    _store_hints(session, draft, proposal, check.hints)
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
    session.exec(delete(PromptHint).where(col(PromptHint.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
    session.exec(delete(ImageProposal).where(col(ImageProposal.draft_id).in_(draft_ids)))  # type: ignore[call-overload]
    return files
