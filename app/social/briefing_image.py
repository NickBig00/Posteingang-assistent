"""The one image of a briefing (SPEC_social 14.3): storage, choice and lock.

Low-level helpers shared by images, photo edits, info graphic and carousel. Rendering, approval
binding, the campaign copy and the migration live in `app.social.post_image`.
"""

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.core.audit import write_human_audit_entry
from app.core.models import utcnow
from app.social.assets import is_usable
from app.social.models import Asset, BriefingImage, ImageSource, PostDraft, PostStatus

CHOOSE_STEP = "social.briefing_image_choose"
TEXT_STEP = "social.briefing_image_text"
CLEAR_STEP = "social.briefing_image_clear"
RELEASED_STATUSES = frozenset({PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED})


class BriefingImageError(ValueError):
    """The image of the briefing cannot be changed (German message for the UI)."""


def briefing_image(session: Session, briefing_id: int) -> BriefingImage | None:
    statement = select(BriefingImage).where(BriefingImage.briefing_id == briefing_id)
    return session.exec(statement).first()


def chosen_asset(session: Session, briefing_id: int) -> Asset | None:
    image = briefing_image(session, briefing_id)
    if image is None or image.asset_id is None:
        return None
    return session.get(Asset, image.asset_id)


def image_locked(session: Session, briefing_id: int) -> bool:
    """After the first approval the image material is fixed: an approved post must not change
    unnoticed. Rejecting and reopening the post unlocks it again."""
    statement = (
        select(PostDraft.id)
        .where(PostDraft.briefing_id == briefing_id)
        .where(col(PostDraft.status).in_(RELEASED_STATUSES))
    )
    return session.exec(statement).first() is not None


LOCKED_MESSAGE = (
    "Das Bild ist mit einem freigegebenen Beitrag freigegeben und lässt sich nicht mehr ändern. "
    "Zum Ändern den Beitrag verwerfen und wieder öffnen."
)


def require_unlocked(session: Session, briefing_id: int) -> None:
    if image_locked(session, briefing_id):
        raise BriefingImageError(LOCKED_MESSAGE)


def default_with_text(source: ImageSource) -> bool:
    """ "Text aufs Bild": yes for a colour fill, otherwise no (product owner decision E10)."""
    return source is ImageSource.COLOR


def _audit(session: Session, step: str, user: str, **details: JsonValue) -> None:
    write_human_audit_entry(session, item_id=None, step=step, actor=user, details=details)


def set_briefing_image(
    session: Session,
    briefing_id: int,
    source: ImageSource,
    asset_id: int | None,
    user: str,
    *,
    with_text: bool | None = None,
    copied_from_briefing_id: int | None = None,
    audit: bool = True,
) -> BriefingImage:
    """Choose the image of the briefing (for all channels).

    Changing the source resets "Text aufs Bild" to its default unless `with_text` is given.
    """
    require_unlocked(session, briefing_id)
    if source is ImageSource.COLOR:
        asset_id = None
    else:
        asset = session.get(Asset, asset_id) if asset_id is not None else None
        if asset is None or asset.briefing_id != briefing_id:
            raise BriefingImageError("Bild nicht gefunden.")
        if source is ImageSource.AI and not asset.ai_generated:
            raise BriefingImageError("Das gewählte Bild ist kein KI-Bild.")
        if source is ImageSource.UPLOAD and asset.ai_generated:
            raise BriefingImageError("KI-Bilder werden als „KI-Bild“ gewählt.")
        if asset.consent_revoked_at is not None:
            raise BriefingImageError("Für dieses Bild wurde die Einwilligung widerrufen.")
    image = briefing_image(session, briefing_id)
    if image is None:
        image = BriefingImage(briefing_id=briefing_id, source=source, chosen_by=user)
        image.with_text = default_with_text(source)
    elif image.source is not source:
        image.with_text = default_with_text(source)
    if with_text is not None:
        image.with_text = with_text
    image.source, image.asset_id = source, asset_id
    image.chosen_by, image.chosen_at = user, utcnow()
    image.copied_from_briefing_id = copied_from_briefing_id
    session.add(image)
    session.commit()
    session.refresh(image)
    if audit:
        _audit(
            session,
            CHOOSE_STEP,
            user,
            briefing_id=briefing_id,
            source=source.value,
            asset_id=asset_id,
            with_text=image.with_text,
        )
    return image


def set_with_text(session: Session, briefing_id: int, with_text: bool, user: str) -> BriefingImage:
    """ "Text aufs Bild?" yes/no for the image of the briefing."""
    require_unlocked(session, briefing_id)
    image = briefing_image(session, briefing_id)
    if image is None:
        raise BriefingImageError("Bitte zuerst ein Bild wählen.")
    if image.with_text != with_text:
        image.with_text = with_text
        session.add(image)
        session.commit()
        session.refresh(image)
        _audit(session, TEXT_STEP, user, briefing_id=briefing_id, with_text=with_text)
    return image


def clear_briefing_image(session: Session, briefing_id: int, user: str) -> None:
    """No image for the posts of the briefing (allowed: a post without image)."""
    require_unlocked(session, briefing_id)
    image = briefing_image(session, briefing_id)
    if image is not None:
        session.delete(image)
        session.commit()
        _audit(session, CLEAR_STEP, user, briefing_id=briefing_id)


def usable_choice(session: Session, briefing_id: int) -> bool:
    """The chosen image may be used (colour fill always)."""
    image = briefing_image(session, briefing_id)
    if image is None or image.source is ImageSource.COLOR:
        return True
    asset = chosen_asset(session, briefing_id)
    return asset is not None and is_usable(asset)
