"""Images of a briefing and the consent of the people shown on them (SPEC_social section 6).

A post whose briefing has an image with persons can only be approved once a person has
confirmed that everyone shown on it agreed. Images are stored locally (git-ignored).
"""

import hashlib
from pathlib import Path

from sqlmodel import Session, select

from app.core.audit import write_human_audit_entry
from app.core.models import utcnow
from app.social.models import Asset, Briefing

MAX_IMAGE_BYTES = 10 * 1024 * 1024
UPLOAD_STEP = "social.asset_upload"
CONSENT_STEP = "social.asset_consent"
DELETE_STEP = "social.asset_delete"

# Media type by file signature – the file name or the browser's claim is not trusted.
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)


class AssetError(ValueError):
    """Upload or consent not possible (German message for the UI)."""


def image_type(content: bytes) -> tuple[str, str]:
    for signature, media_type, suffix in _SIGNATURES:
        if content.startswith(signature):
            return media_type, suffix
    raise AssetError("Nur Bilder im Format PNG oder JPG sind erlaubt.")


def briefing_assets(session: Session, briefing_id: int) -> list[Asset]:
    statement = select(Asset).where(Asset.briefing_id == briefing_id).order_by(Asset.id)  # type: ignore[arg-type]
    return list(session.exec(statement).all())


def consent_blockers(session: Session, briefing_id: int) -> list[str]:
    return [
        f"Bild „{asset.filename}“ zeigt Personen – die Einwilligung ist nicht bestätigt."
        for asset in briefing_assets(session, briefing_id)
        if asset.shows_persons and not asset.consent_confirmed
    ]


def _audit(session: Session, asset: Asset, step: str, user: str) -> None:
    write_human_audit_entry(
        session,
        item_id=None,
        step=step,
        actor=user,
        details={
            "briefing_id": asset.briefing_id,
            "asset_id": asset.id,
            "shows_persons": asset.shows_persons,
            "consent_confirmed": asset.consent_confirmed,
        },
    )


def save_asset(
    session: Session,
    briefing: Briefing,
    filename: str,
    content: bytes,
    *,
    shows_persons: bool,
    consent_confirmed: bool,
    user: str,
    storage: Path,
) -> Asset:
    if briefing.id is None:
        raise AssetError("Briefing ist nicht gespeichert.")
    if not content:
        raise AssetError("Die Datei ist leer.")
    if len(content) > MAX_IMAGE_BYTES:
        raise AssetError("Das Bild ist zu groß (höchstens 10 MB).")
    media_type, suffix = image_type(content)
    digest = hashlib.sha256(content).hexdigest()
    target = storage / "social" / f"{digest}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    consent = shows_persons and consent_confirmed
    asset = Asset(
        briefing_id=briefing.id,
        filename=Path(filename).name[:200] or f"bild{suffix}",
        stored_path=str(target),
        media_type=media_type,
        content_hash=digest,
        uploaded_by=user,
        shows_persons=shows_persons,
        consent_confirmed=consent,
        consent_by=user if consent else None,
        consent_at=utcnow() if consent else None,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    _audit(session, asset, UPLOAD_STEP, user)
    return asset


def confirm_consent(session: Session, asset: Asset, user: str) -> Asset:
    """A person confirms that everyone shown on the image agreed to its publication."""
    if not asset.shows_persons:
        raise AssetError("Das Bild zeigt laut Angabe keine Personen.")
    if asset.consent_confirmed:
        raise AssetError("Die Einwilligung ist bereits bestätigt.")
    asset.consent_confirmed, asset.consent_by, asset.consent_at = True, user, utcnow()
    session.add(asset)
    session.commit()
    session.refresh(asset)
    _audit(session, asset, CONSENT_STEP, user)
    return asset


def delete_asset(session: Session, asset: Asset, user: str) -> None:
    """Remove an image (database entry and file, unless another entry still uses it)."""
    _audit(session, asset, DELETE_STEP, user)
    path = Path(asset.stored_path)
    session.delete(asset)
    session.commit()
    still_used = session.exec(select(Asset).where(Asset.stored_path == str(path))).first()
    if still_used is None and path.is_file():
        path.unlink()
