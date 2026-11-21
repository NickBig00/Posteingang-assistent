"""Editing own photos with AI (SPEC_social 13.6).

A person describes in their own words what to change ("Hintergrund heller", "auf 9:16
erweitern") or picks a quick suggestion. The result is a new image labelled "KI-bearbeitet";
the original stays. Photos with persons are only sent to the provider with the confirmed
consent of everyone shown; instructions that would change persons in a misleading way
(body shape, before/after) give a hint that a person confirms before approval. Switch, cost
brake and audit come from `app.core.images`; deletion works like for uploads.
"""

import hashlib
import re
from pathlib import Path

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.audit import write_human_audit_entry
from app.core.image_formats import ImageFormat
from app.core.images.base import ImageProvider
from app.core.images.service import edit_image, image_generation_enabled
from app.core.models import utcnow
from app.social.assets import is_usable
from app.social.checking import member_names
from app.social.models import Asset, Briefing, PostDraft, PostStatus
from app.social.sensitive import check_persons
from app.social.textscan import find_phones

EDIT_STEP = "social.image_edit"
HINT_CONFIRM_STEP = "social.image_edit_hint_confirm"
AI_EDITED_LABEL = "KI-bearbeitet"
MAX_INSTRUCTION_LENGTH = 500
QUICK_PICKS = [
    "Hintergrund heller",
    "Auf 9:16 erweitern",
    "Störende Gegenstände am Boden entfernen",
    "Wärmeres Licht",
    "Farben etwas kräftiger",
]
# Appended by code to every edit instruction (not editable).
EDIT_SAFETY_SUFFIX = (
    "Personen nicht verändern: keine andere Körperform, kein anderes Gesicht, keine "
    "Vorher-nachher-Darstellung. Keine Logos, kein zusätzlicher Text."
)
_MISLEADING = re.compile(
    r"(?<![\wäöüß])(?:schlank\w*|dünn\w*|dicker|muskul\w*|abnehm\w*|abgenommen|figur|"
    r"körperform|bauch\w*|taille|falten|retusch\w*|gesicht\w*|haut|jünger|älter|"
    r"vorher|nachher|ergebnis\w*|transformation)(?![\wäöüß])",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class PhotoEditError(ValueError):
    """The edit is not possible (German message for the UI)."""


def instruction_problems(instruction: str, members: list[str]) -> list[str]:
    """Errors that prevent the edit: empty, too long, member names or contact data."""
    if not instruction.strip():
        return ["Bitte beschreiben, was am Foto geändert werden soll."]
    problems = []
    if len(instruction) > MAX_INSTRUCTION_LENGTH:
        problems.append(f"Die Anweisung ist zu lang (höchstens {MAX_INSTRUCTION_LENGTH} Zeichen).")
    named = [
        instruction[f.span[0] : f.span[1]]
        for f in check_persons(instruction, members)
        if f.consent_required and f.span
    ]
    if named:
        problems.append(
            f"Name aus der Mitgliederliste in der Anweisung ({', '.join(named)}): bitte entfernen."
        )
    if _EMAIL.search(instruction) or find_phones(instruction):
        problems.append("Keine Kontaktdaten in die Anweisung schreiben.")
    return problems


def instruction_hint(instruction: str, shows_persons: bool) -> str | None:
    """Hint (to confirm before approval) when persons could be changed in a misleading way."""
    hits = sorted({m.group(0).lower() for m in _MISLEADING.finditer(instruction)})
    if not hits:
        return None
    who = "Auf dem Foto sind Personen zu sehen. " if shows_persons else ""
    return (
        f"{who}Die Anweisung könnte Personen oder Ergebnisse verfälschen ({', '.join(hits)}). "
        "Die Bearbeitung darf Körperform, Gesichter oder Erfolge nicht verändern; bitte das "
        "Ergebnis genau prüfen."
    )


def _require(session: Session, source: Asset) -> Briefing:
    if not image_generation_enabled():
        raise PhotoEditError("Die Bildbearbeitung mit KI ist ausgeschaltet.")
    if source.ai_generated:
        raise PhotoEditError(
            "KI-Bilder werden über ihren Bild-Prompt geändert („Bild per Anweisung ändern“)."
        )
    if not is_usable(source) or not Path(source.stored_path).is_file():
        raise PhotoEditError(
            "Fotos mit Personen nur mit bestätigter Einwilligung aller abgebildeten Personen "
            "bearbeiten (das Foto geht dabei an den Bildanbieter)."
        )
    briefing = session.get(Briefing, source.briefing_id)
    if briefing is None:
        raise PhotoEditError("Briefing nicht gefunden.")
    statuses = session.exec(
        select(PostDraft.status).where(PostDraft.briefing_id == briefing.id)
    ).all()
    if statuses and all(s in (PostStatus.PUBLISHED, PostStatus.REJECTED) for s in statuses):
        raise PhotoEditError("Alle Beiträge dieses Briefings sind veröffentlicht oder verworfen.")
    return briefing


def edit_photo(
    session: Session,
    provider: ImageProvider,
    source: Asset,
    *,
    instruction: str,
    fmt: ImageFormat,
    user: str,
    storage: Path,
) -> Asset:
    """Edit `source` with AI; returns the new image labelled "KI-bearbeitet"."""
    briefing = _require(session, source)
    instruction = " ".join(instruction.split())
    problems = instruction_problems(instruction, member_names(session))
    if problems:
        raise PhotoEditError(" ".join(problems))
    hint = instruction_hint(instruction, source.shows_persons)
    batch = edit_image(
        session,
        provider,
        image=Path(source.stored_path).read_bytes(),
        media_type=source.media_type,
        instruction=f"{instruction}\n\n{EDIT_SAFETY_SUFFIX}",
        fmt=fmt,
        step=EDIT_STEP,
        scope=f"briefing:{briefing.id}",
        user=user,
        details={
            "briefing_id": briefing.id,
            "asset_id": source.id,
            "shows_persons": source.shows_persons,
            "hint": hint is not None,
        },
    )
    result = batch.images[0]
    digest = hashlib.sha256(result.content).hexdigest()
    suffix = ".png" if result.media_type == "image/png" else ".jpg"
    target = storage / "social" / "ki" / f"{digest}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result.content)
    stem = Path(source.filename).stem.removeprefix("ki-bearbeitet-")
    asset = Asset(
        briefing_id=source.briefing_id,
        filename=f"ki-bearbeitet-{stem}{suffix}"[:200],
        stored_path=str(target),
        media_type=result.media_type,
        content_hash=digest,
        uploaded_by=user,
        # The same people are shown: their confirmed consent carries over.
        shows_persons=source.shows_persons,
        consent_confirmed=source.consent_confirmed,
        consent_by=source.consent_by,
        consent_at=source.consent_at,
        ai_edited=True,
        source_asset_id=source.id,
        edit_hint=hint,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def confirm_edit_hint(session: Session, asset: Asset, user: str) -> Asset:
    if asset.edit_hint is None:
        raise PhotoEditError("Für dieses Bild gibt es keinen Hinweis zu bestätigen.")
    if asset.edit_hint_confirmed_at is not None:
        raise PhotoEditError("Der Hinweis ist bereits bestätigt.")
    asset.edit_hint_confirmed_by, asset.edit_hint_confirmed_at = user, utcnow()
    session.add(asset)
    session.commit()
    session.refresh(asset)
    details: dict[str, JsonValue] = {"briefing_id": asset.briefing_id, "asset_id": asset.id}
    write_human_audit_entry(
        session, item_id=None, step=HINT_CONFIRM_STEP, actor=user, details=details
    )
    return asset
