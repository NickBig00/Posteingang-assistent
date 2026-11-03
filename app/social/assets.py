"""Images of a briefing and the consent of the people shown on them (SPEC_social section 6).

A post whose briefing has an image with persons can only be approved once a person has
confirmed that everyone shown on it agreed. A consent can be withdrawn; then the image is
deleted and the affected posts are listed. Images are stored locally (git-ignored).
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.audit import write_human_audit_entry
from app.core.models import utcnow
from app.social.models import Asset, Briefing, PostDraft, PostStatus
from app.social.status import transition

MAX_IMAGE_BYTES = 10 * 1024 * 1024
UPLOAD_STEP = "social.asset_upload"
CONSENT_STEP = "social.asset_consent"
REVOKE_STEP = "social.asset_consent_revoked"
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
    blockers = []
    for asset in briefing_assets(session, briefing_id):
        if asset.consent_revoked_at is not None:
            blockers.append(
                f"Für Bild „{asset.filename}“ wurde die Einwilligung widerrufen – Bild entfernen."
            )
        elif asset.shows_persons and not asset.consent_confirmed:
            blockers.append(
                f"Bild „{asset.filename}“ zeigt Personen – die Einwilligung ist nicht bestätigt."
            )
    return blockers


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
    if asset.consent_revoked_at is not None:
        raise AssetError(
            "Die Einwilligung wurde widerrufen. Das Bild nur mit neuer Einwilligung neu hochladen."
        )
    if asset.consent_confirmed:
        raise AssetError("Die Einwilligung ist bereits bestätigt.")
    asset.consent_confirmed, asset.consent_by, asset.consent_at = True, user, utcnow()
    session.add(asset)
    session.commit()
    session.refresh(asset)
    _audit(session, asset, CONSENT_STEP, user)
    return asset


@dataclass(frozen=True)
class Revocation:
    """Posts affected by a withdrawn consent."""

    # Approved or planned posts: back to review, taken off the calendar.
    back_to_review: list[PostDraft]
    # Already published: must be removed or changed on the platform by hand.
    published: list[PostDraft]


def revoke_consent(session: Session, asset: Asset, user: str) -> Revocation:
    """The people shown withdrew their consent: the image may no longer be used.

    The file is deleted; approved and planned posts of the briefing go back to review (their
    approval is blocked until the image is removed); published posts are listed so that a
    person removes them on the platform. Everything is recorded in the audit trail.
    """
    if not asset.shows_persons or not asset.consent_confirmed:
        raise AssetError("Für dieses Bild liegt keine bestätigte Einwilligung vor.")
    asset.consent_confirmed = False
    asset.consent_revoked_by, asset.consent_revoked_at = user, utcnow()
    session.add(asset)
    session.commit()
    session.refresh(asset)
    drafts = session.exec(
        select(PostDraft)
        .where(PostDraft.briefing_id == asset.briefing_id)
        .order_by(col(PostDraft.id))
    ).all()
    back, published = [], []
    for draft in drafts:
        if draft.status in (PostStatus.APPROVED, PostStatus.SCHEDULED):
            draft.scheduled_at = None
            transition(session, draft, PostStatus.IN_REVIEW)
            back.append(draft)
        elif draft.status is PostStatus.PUBLISHED:
            published.append(draft)
    _remove_file(session, asset.stored_path, exclude_id=asset.id)
    write_human_audit_entry(
        session,
        item_id=None,
        step=REVOKE_STEP,
        actor=user,
        details={
            "briefing_id": asset.briefing_id,
            "asset_id": asset.id,
            "back_to_review": [d.id for d in back],
            "published": [d.id for d in published],
        },
    )
    return Revocation(back_to_review=back, published=published)


def _remove_file(session: Session, stored_path: str, exclude_id: int | None = None) -> None:
    """Delete the file unless another (not revoked) entry still uses it."""
    others = session.exec(select(Asset).where(Asset.stored_path == stored_path)).all()
    if any(a.id != exclude_id and a.consent_revoked_at is None for a in others):
        return
    path = Path(stored_path)
    if path.is_file():
        path.unlink()


def delete_asset(session: Session, asset: Asset, user: str) -> None:
    """Remove an image (database entry and file, unless another entry still uses it)."""
    _audit(session, asset, DELETE_STEP, user)
    stored_path = asset.stored_path
    session.delete(asset)
    session.commit()
    _remove_file(session, stored_path)
