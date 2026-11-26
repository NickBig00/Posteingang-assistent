"""The rendered post image of a briefing per channel (SPEC_social 14.3).

One image per briefing for all channels; the app picks the format per channel. With "Text
aufs Bild" the post image is the info graphic over the chosen image (or the colour fill),
otherwise the chosen image in the channel format. `render_post_image` is the only source for
preview, download and the first carousel page. The approval of a post requires that its image
renders and its checks are done (graphic text, AI hints, consent).

Also here: taking over the image of the first campaign step ("Wie Schritt 1") and the one-time
migration of demo data from images per draft (before U6).
"""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.core.audit import write_human_audit_entry
from app.core.image_formats import ImageExportError, ImageFormat, export_jpeg
from app.core.models import AuditEntry
from app.social.assets import asset_blockers, is_usable
from app.social.briefing_image import (
    BriefingImageError,
    briefing_image,
    require_unlocked,
    set_briefing_image,
)
from app.social.graphics import GraphicLayoutError
from app.social.images import image_hint_blockers, latest_proposal, prompt_hints
from app.social.infographic import (
    InfoGraphicError,
    InfoTexts,
    check_blockers,
    get_info_graphic,
    graphic_fact_keys,
    render_info_graphic,
    run_graphic_checks,
    story_texts,
    text_errors,
    texts_for_format,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    CHANNEL_IMAGE_FORMATS,
    Asset,
    Briefing,
    BriefingImage,
    CarouselCheck,
    CarouselPage,
    ImageProposal,
    ImageSource,
    InfoGraphic,
    PostDraft,
    PromptHint,
)
from app.social.profiles import SocialConfig

COPY_STEP = "social.briefing_image_copy"
MIGRATION_STEP = "social.migrate_briefing_images"
SYSTEM_ACTOR = "system"


class PostImageError(ValueError):
    """The post image cannot be rendered (German messages for the UI)."""

    def __init__(self, problems: list[str], *, from_checks: bool = False) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems
        # The reason is an error of the text checks (listed with the checks already).
        self.from_checks = from_checks


@dataclass(frozen=True)
class PostImage:
    content: bytes
    fmt: ImageFormat
    source: ImageSource
    with_text: bool
    asset: Asset | None = field(default=None, compare=False)

    @property
    def ai_generated(self) -> bool:
        return self.asset is not None and self.asset.ai_generated

    @property
    def ai_edited(self) -> bool:
        return self.asset is not None and self.asset.ai_edited

    @property
    def ai_label(self) -> str:
        """Suffix for the file name of the download (label when posting)."""
        if self.ai_generated:
            return "-ki-generiert"
        return "-ki-bearbeitet" if self.ai_edited else ""


def channel_format(draft: PostDraft) -> ImageFormat:
    return CHANNEL_IMAGE_FORMATS[draft.channel]


def _background(session: Session, briefing: Briefing, image: BriefingImage) -> Asset | None:
    if image.source is ImageSource.COLOR:
        return None
    asset = session.get(Asset, image.asset_id) if image.asset_id is not None else None
    if asset is None or asset.briefing_id != briefing.id:
        raise PostImageError(
            ["Das gewählte Bild ist nicht mehr vorhanden. Bitte ein anderes wählen."]
        )
    if asset.consent_revoked_at is not None or not Path(asset.stored_path).is_file():
        raise PostImageError(
            ["Das gewählte Bild darf nicht mehr verwendet werden (Einwilligung widerrufen)."]
        )
    return asset


def render_post_image(
    session: Session,
    config: SocialConfig,
    briefing: Briefing,
    fmt: ImageFormat,
    *,
    texts: InfoTexts | None = None,
) -> PostImage | None:
    """The finished post image in `fmt`, or None when the briefing has no image.

    `texts` renders unsaved text on the image for the live preview while typing (facts and
    layout are checked as always; never used for downloads). Raises `PostImageError` with the
    reasons (facts, layout, missing headline …).
    """
    image = briefing_image(session, briefing.id or 0)
    if image is None:
        return None
    asset = _background(session, briefing, image)
    content = Path(asset.stored_path).read_bytes() if asset else None
    try:
        if image.with_text:
            graphic = get_info_graphic(session, briefing.id or 0)
            headline = (
                texts.headline if texts is not None else (graphic.headline if graphic else "")
            )
            if not headline:
                raise PostImageError(["Text aufs Bild: Bitte eine Überschrift eingeben."])
            if graphic is None:
                graphic = InfoGraphic(briefing_id=briefing.id or 0)
            errors = text_errors(session, graphic) if texts is None and graphic.id else []
            if errors:
                raise PostImageError(errors, from_checks=True)
            shown = texts if texts is not None else texts_for_format(graphic, fmt)
            if texts is not None and fmt is ImageFormat.STORY and story_texts(graphic):
                shown = texts_for_format(graphic, fmt)
            body = render_info_graphic(
                config,
                briefing,
                shown,
                fmt,
                variant=graphic.variant,
                background=content,
                fact_keys=graphic_fact_keys(graphic, briefing),
                # Text a person wrote is not checked; AI suggestions are.
                check_text_facts=graphic.is_ai_suggestion,
            )
        elif content is None:
            raise PostImageError(
                [
                    "Eine Farbfläche ohne Text ist kein Beitragsbild: „Text aufs Bild“ "
                    "einschalten oder ein Foto wählen."
                ]
            )
        else:
            body = export_jpeg(content, fmt)
    except InfoGraphicError as error:
        raise PostImageError(error.problems) from error
    except (GraphicLayoutError, ImageExportError) as error:
        raise PostImageError([str(error)]) from error
    return PostImage(body, fmt, image.source, image.with_text, asset)


def post_image_problems(
    session: Session, config: SocialConfig, briefing: Briefing, fmt: ImageFormat
) -> list[str]:
    """Why the post image does not render in `fmt` (empty: renders or no image chosen)."""
    try:
        render_post_image(session, config, briefing, fmt)
    except PostImageError as error:
        return error.problems
    return []


def post_image_blockers(session: Session, config: SocialConfig, draft: PostDraft) -> list[str]:
    """Approval binding: the post image renders and its checks are done (SPEC_social 14.3)."""
    briefing = session.get(Briefing, draft.briefing_id)
    if briefing is None:
        return ["Briefing nicht gefunden."]
    image = briefing_image(session, briefing.id or 0)
    if image is None:
        return []
    blockers: list[str] = []
    try:
        render_post_image(session, config, briefing, channel_format(draft))
    except PostImageError as error:
        if not error.from_checks:
            blockers += [f"Beitragsbild: {p}" for p in error.problems]
    if image.asset_id is not None:
        asset = session.get(Asset, image.asset_id)
        if asset is not None:
            blockers += asset_blockers(asset)
    if image.with_text:
        graphic = get_info_graphic(session, briefing.id or 0)
        if graphic is not None:
            blockers += [f"Bildtext: {b}" for b in check_blockers(session, graphic)]
    blockers += image_hint_blockers(session, briefing.id or 0)
    return list(dict.fromkeys(blockers))


# --- campaigns: "Wie Schritt 1" ---


def first_step_briefing(session: Session, briefing: Briefing) -> Briefing | None:
    """The first briefing of the campaign (by date), if `briefing` is a later step."""
    if briefing.campaign_id is None:
        return None
    first = session.exec(
        select(Briefing)
        .where(Briefing.campaign_id == briefing.campaign_id)
        .order_by(col(Briefing.desired_date), col(Briefing.id))
    ).first()
    if first is None or first.id == briefing.id:
        return None
    return first


def copy_source(session: Session, briefing: Briefing) -> Briefing | None:
    """Step 1 of the campaign when it has an image to take over (else None)."""
    first = first_step_briefing(session, briefing)
    if first is None or briefing_image(session, first.id or 0) is None:
        return None
    return first


def _copy_asset(session: Session, source: Asset, briefing: Briefing, user: str) -> Asset:
    copy = Asset(
        briefing_id=briefing.id or 0,
        filename=source.filename,
        stored_path=source.stored_path,
        media_type=source.media_type,
        content_hash=source.content_hash,
        uploaded_by=user,
        # The same photo: a confirmed consent applies to it in every briefing (a withdrawal
        # applies to all briefings using it, see `revoke_consent`).
        shows_persons=source.shows_persons,
        consent_confirmed=source.consent_confirmed,
        consent_by=source.consent_by,
        consent_at=source.consent_at,
        ai_generated=source.ai_generated,
        ai_edited=source.ai_edited,
        # A hint on an AI edit is confirmed again for this post.
        edit_hint=source.edit_hint,
        copied_from_asset_id=source.id,
    )
    session.add(copy)
    session.commit()
    session.refresh(copy)
    return copy


def _copy_prompt(session: Session, source_briefing_id: int, briefing: Briefing) -> None:
    """Prompt and hints behind a copied AI image; the hints must be confirmed again."""
    proposal = latest_proposal(session, source_briefing_id)
    if proposal is None:
        return
    copy = ImageProposal(
        briefing_id=briefing.id or 0,
        prompt=proposal.prompt,
        prompt_from_ai=proposal.prompt_from_ai,
        image_format=proposal.image_format,
    )
    session.add(copy)
    session.commit()
    session.refresh(copy)
    for hint in prompt_hints(session, source_briefing_id):
        session.add(
            PromptHint(
                briefing_id=briefing.id,
                proposal_id=copy.id or 0,
                key=hint.key,
                reason=hint.reason,
                prompt_hash=hint.prompt_hash,
                consent_required=hint.consent_required,
            )
        )
    session.commit()


def _copy_graphic(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    source: InfoGraphic,
    briefing: Briefing,
    user: str,
) -> None:
    """The texts as a copy; the facts always come from the own briefing and are checked."""
    graphic = get_info_graphic(session, briefing.id or 0) or InfoGraphic(
        briefing_id=briefing.id or 0
    )
    graphic.headline, graphic.subline, graphic.intro = (
        source.headline,
        source.subline,
        source.intro,
    )
    graphic.points, graphic.closing = list(source.points), source.closing
    graphic.story = dict(source.story) if source.story else None
    graphic.variant = source.variant
    graphic.fact_keys = None
    graphic.is_ai_suggestion = source.is_ai_suggestion
    graphic.edited_by_human = source.edited_by_human
    graphic.updated_by = user
    session.add(graphic)
    session.commit()
    session.refresh(graphic)
    run_graphic_checks(session, config, client, briefing, graphic)


def copy_from_first_step(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    briefing: Briefing,
    user: str,
) -> BriefingImage:
    """ "Wie Schritt 1": source, image and text on the image as an independent copy."""
    require_unlocked(session, briefing.id or 0)
    source_briefing = copy_source(session, briefing)
    source_image = briefing_image(session, source_briefing.id or 0) if source_briefing else None
    if source_briefing is None or source_image is None:
        raise BriefingImageError("Schritt 1 der Kampagne hat noch kein Bild.")
    asset_id: int | None = None
    if source_image.asset_id is not None:
        source_asset = session.get(Asset, source_image.asset_id)
        if source_asset is None or source_asset.consent_revoked_at is not None:
            raise BriefingImageError("Das Bild von Schritt 1 ist nicht mehr verfügbar.")
        asset_id = _copy_asset(session, source_asset, briefing, user).id
        if source_image.source is ImageSource.AI:
            _copy_prompt(session, source_briefing.id or 0, briefing)
    if source_image.with_text:
        graphic = get_info_graphic(session, source_briefing.id or 0)
        if graphic is not None:
            _copy_graphic(session, config, client, graphic, briefing, user)
    image = set_briefing_image(
        session,
        briefing.id or 0,
        source_image.source,
        asset_id,
        user,
        with_text=source_image.with_text,
        copied_from_briefing_id=source_briefing.id,
        audit=False,
    )
    details: dict[str, JsonValue] = {
        "source": source_image.source.value,
        "asset_id": asset_id,
        "with_text": source_image.with_text,
    }
    # In the audit trail of both briefings.
    write_human_audit_entry(
        session,
        item_id=None,
        step=COPY_STEP,
        actor=user,
        details={"briefing_id": briefing.id, "from_briefing_id": source_briefing.id} | details,
    )
    write_human_audit_entry(
        session,
        item_id=None,
        step=COPY_STEP,
        actor=user,
        details={"briefing_id": source_briefing.id, "to_briefing_id": briefing.id} | details,
    )
    return image


# --- deletion ---


def delete_briefing_image(session: Session, briefing_id: int) -> None:
    image = briefing_image(session, briefing_id)
    if image is not None:
        session.delete(image)


def remove_asset_choice(session: Session, asset: Asset) -> None:
    """An image is deleted: it is no longer the image of the briefing.

    Not while a post with it is approved – except after a withdrawn consent: then the image
    must go (published posts are changed on the platform by hand).
    """
    image = briefing_image(session, asset.briefing_id)
    if image is not None and image.asset_id == asset.id:
        if asset.consent_revoked_at is None:
            require_unlocked(session, asset.briefing_id)
        session.delete(image)
        session.commit()


# --- migration of demo data from images per draft (before U6) ---


def migrate_briefing_images(session: Session) -> int:
    """One-time migration; returns the number of briefings that got an image.

    Per briefing: the AI image chosen for the first draft, otherwise the first usable uploaded
    photo, otherwise none. Prompt hints and carousel pages move from the draft to the briefing.
    Recorded once in the audit trail, so a later run never chooses images for new briefings.
    """
    done = session.exec(select(AuditEntry.id).where(AuditEntry.step == MIGRATION_STEP)).first()
    if done is not None:
        return 0
    drafts = {d.id: d for d in session.exec(select(PostDraft)).all()}
    for hint in session.exec(select(PromptHint).where(col(PromptHint.briefing_id).is_(None))):
        draft = drafts.get(hint.draft_id)
        if draft is not None:
            hint.briefing_id = draft.briefing_id
            session.add(hint)
    for model in (CarouselPage, CarouselCheck):
        for row in session.exec(select(model).where(col(model.briefing_id).is_(None))):
            draft = drafts.get(row.draft_id)
            if draft is not None:
                row.briefing_id = draft.briefing_id
                session.add(row)
                if not draft.carousel_enabled:
                    draft.carousel_enabled = True
                    session.add(draft)
    session.commit()
    migrated = 0
    for briefing in session.exec(select(Briefing).order_by(col(Briefing.id))).all():
        if briefing_image(session, briefing.id or 0) is not None:
            continue
        assets = session.exec(
            select(Asset).where(Asset.briefing_id == briefing.id).order_by(col(Asset.id))
        ).all()
        draft_order = sorted(d.id or 0 for d in drafts.values() if d.briefing_id == briefing.id)
        ai = sorted(
            (a for a in assets if a.ai_generated and a.draft_id in draft_order),
            key=lambda a: draft_order.index(a.draft_id or 0),
        )
        photos = [a for a in assets if not a.ai_generated and is_usable(a)]
        choice = (
            (ImageSource.AI, ai[0]) if ai else (ImageSource.UPLOAD, photos[0]) if photos else None
        )
        if choice is None:
            continue
        session.add(
            BriefingImage(
                briefing_id=briefing.id or 0,
                source=choice[0],
                asset_id=choice[1].id,
                with_text=False,
                chosen_by=SYSTEM_ACTOR,
            )
        )
        migrated += 1
    session.commit()
    write_human_audit_entry(
        session,
        item_id=None,
        step=MIGRATION_STEP,
        actor=SYSTEM_ACTOR,
        details={"briefings": migrated},
    )
    return migrated
