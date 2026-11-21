"""U6: one image per briefing – choice, rendered post image, approval binding, copy, migration.

All with the fake providers (no real LLM or image API).
"""

import io
from datetime import date
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, col, select

from app.core.db import init_db, make_engine
from app.core.image_formats import ImageFormat
from app.core.images.fake import FakeImageProvider
from app.core.models import AuditEntry
from app.social.assets import revoke_consent, save_asset
from app.social.audit import briefing_audit_trail
from app.social.briefing_image import (
    CHOOSE_STEP,
    BriefingImageError,
    briefing_image,
    image_locked,
    set_briefing_image,
    set_with_text,
)
from app.social.briefings import create_briefing
from app.social.campaigns import campaign_plan, create_campaign, load_demo_campaign
from app.social.carousel import pages
from app.social.drafts import generate_drafts
from app.social.images import choose_candidate, generate_variants, prompt_hints
from app.social.infographic import (
    InfoGraphicError,
    InfoTexts,
    confirm_check,
    get_info_graphic,
    graphic_checks,
    save_layout,
    save_texts,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    Asset,
    Briefing,
    BriefingImage,
    CarouselPage,
    Channel,
    ImageCandidate,
    ImageProposal,
    ImageSource,
    PostDraft,
    PromptHint,
)
from app.social.photo_edit import edit_photo
from app.social.post_image import (
    COPY_STEP,
    MIGRATION_STEP,
    copy_from_first_step,
    copy_source,
    migrate_briefing_images,
    post_image_blockers,
    post_image_problems,
    remove_asset_choice,
    render_post_image,
)
from app.social.review import (
    approval_blockers,
    approve_draft,
    confirm_warning,
    reject_draft,
    reopen_draft,
)
from tests.social_helpers import course_input, demo_config, png_bytes, scripted

CONFIG = demo_config()
CLIENT = SocialLLMClient(scripted())
ALL_CHANNELS = [
    Channel.INSTAGRAM_POST,
    Channel.INSTAGRAM_STORY,
    Channel.FACEBOOK,
    Channel.LINKEDIN,
]


def _briefing(session: Session, channels: list[Channel] | None = None) -> Briefing:
    data = course_input(channels=channels or [Channel.INSTAGRAM_POST, Channel.FACEBOOK])
    briefing = create_briefing(session, CONFIG, data, "demo")
    generate_drafts(session, CONFIG, CLIENT, briefing)
    return briefing


def _drafts(session: Session, briefing: Briefing) -> list[PostDraft]:
    statement = (
        select(PostDraft).where(PostDraft.briefing_id == briefing.id).order_by(col(PostDraft.id))
    )
    return list(session.exec(statement).all())


def _photo(session: Session, briefing: Briefing, storage: Path, **flags: bool) -> Asset:
    return save_asset(
        session,
        briefing,
        "kursraum.png",
        png_bytes(),
        shows_persons=flags.get("shows_persons", False),
        consent_confirmed=flags.get("consent_confirmed", False),
        user="demo",
        storage=storage,
    )


def _headline(session: Session, briefing: Briefing, headline: str = "Starker Rücken") -> None:
    save_texts(
        session,
        CONFIG,
        CLIENT,
        briefing,
        InfoTexts(headline=headline),
        story=None,
        variant="zentriert",
        user="demo",
    )


def _confirm_everything(session: Session, briefing: Briefing) -> None:
    from app.social.models import CheckResult

    for draft in _drafts(session, briefing):
        statement = select(CheckResult).where(CheckResult.draft_id == draft.id)
        for result in session.exec(statement).all():
            if result.requires_confirmation and result.confirmed_at is None:
                confirm_warning(session, result, "demo")
    graphic = get_info_graphic(session, briefing.id or 0)
    if graphic is not None:
        for check in graphic_checks(session, graphic.id or 0):
            if check.requires_confirmation and check.confirmed_at is None:
                confirm_check(session, check, "demo")


def _size(content: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(content)).size


# --- choice of the image ---


def test_text_on_the_image_defaults_to_yes_only_for_a_colour_fill(
    session: Session, raw_dir: Path
) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir)

    color = set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")
    assert color.with_text is True and color.asset_id is None
    upload = set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo")
    assert upload.with_text is False and upload.asset_id == photo.id
    set_with_text(session, briefing.id or 0, True, "demo")
    # The same source keeps the person's choice; another source resets it to its default.
    again = set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo")
    assert again.with_text is True
    entries = [e for e in briefing_audit_trail(session, briefing.id or 0) if e.step == CHOOSE_STEP]
    assert [e.model_params["source"] for e in entries] == ["color", "upload", "upload"]


def test_only_own_and_fitting_images_can_be_chosen(session: Session, raw_dir: Path) -> None:
    briefing, other = _briefing(session), _briefing(session)
    foreign = _photo(session, other, raw_dir)
    own = _photo(session, briefing, raw_dir)

    with pytest.raises(BriefingImageError, match="nicht gefunden"):
        set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, foreign.id, "demo")
    with pytest.raises(BriefingImageError, match="kein KI-Bild"):
        set_briefing_image(session, briefing.id or 0, ImageSource.AI, own.id, "demo")
    with pytest.raises(BriefingImageError, match="Bitte zuerst"):
        set_with_text(session, other.id or 0, True, "demo")


# --- rendered post image per channel ---


@pytest.mark.parametrize("with_text", [False, True])
def test_post_image_in_the_format_of_every_channel(
    session: Session, raw_dir: Path, with_text: bool
) -> None:
    briefing = _briefing(session, ALL_CHANNELS)
    photo = _photo(session, briefing, raw_dir)
    set_briefing_image(
        session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo", with_text=with_text
    )
    _headline(session, briefing)

    sizes = {}
    for draft in _drafts(session, briefing):
        image = render_post_image(session, CONFIG, briefing, CHANNEL_FORMATS[draft.channel])
        assert image is not None and image.with_text is with_text
        sizes[draft.channel] = _size(image.content)

    assert sizes == {
        Channel.INSTAGRAM_POST: (1080, 1350),
        Channel.INSTAGRAM_STORY: (1080, 1920),
        Channel.FACEBOOK: (1080, 1350),
        Channel.LINKEDIN: (1080, 1080),
    }


def test_text_on_the_image_is_the_finished_post_image(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir)
    set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo")
    _headline(session, briefing)
    plain = render_post_image(session, CONFIG, briefing, ImageFormat.PORTRAIT)

    set_with_text(session, briefing.id or 0, True, "demo")
    with_text = render_post_image(session, CONFIG, briefing, ImageFormat.PORTRAIT)

    assert plain is not None and with_text is not None
    assert plain.content != with_text.content


def test_no_image_means_no_post_image_and_no_blocker(session: Session) -> None:
    briefing = _briefing(session)
    (draft, *_) = _drafts(session, briefing)

    assert render_post_image(session, CONFIG, briefing, ImageFormat.PORTRAIT) is None
    assert post_image_blockers(session, CONFIG, draft) == []


def test_colour_fill_without_text_or_headline_is_reported(session: Session) -> None:
    briefing = _briefing(session)
    (draft, *_) = _drafts(session, briefing)
    set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")

    assert any("Überschrift" in b for b in approval_blockers(session, draft, CONFIG))
    set_with_text(session, briefing.id or 0, False, "demo")
    problems = post_image_problems(session, CONFIG, briefing, ImageFormat.PORTRAIT)
    assert problems and "Farbfläche ohne Text" in problems[0]
    with pytest.raises(Exception, match="Freigabe nicht möglich"):
        approve_draft(session, draft, "demo", CONFIG)


def test_foreign_fact_on_the_image_blocks_every_post(session: Session) -> None:
    briefing = _briefing(session)
    set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")
    _headline(session, briefing, "Ab 10.12.2026")

    for draft in _drafts(session, briefing):
        assert any("10.12.2026" in b for b in approval_blockers(session, draft, CONFIG))


def test_warnings_on_the_text_on_the_image_must_be_confirmed(session: Session) -> None:
    briefing = _briefing(session)
    (draft, *_) = _drafts(session, briefing)
    set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")
    _headline(session, briefing, "Garantiert schmerzfrei")
    from app.social.models import CheckResult

    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)):
        if result.requires_confirmation and result.confirmed_at is None:
            confirm_warning(session, result, "demo")

    assert any(b.startswith("Bildtext:") for b in approval_blockers(session, draft, CONFIG))
    _confirm_everything(session, briefing)
    assert approval_blockers(session, draft, CONFIG) == []
    approve_draft(session, draft, "demo", CONFIG)


def test_ai_labels_follow_the_chosen_image(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir)
    edited = edit_photo(
        session,
        FakeImageProvider(),
        photo,
        instruction="Wärmeres Licht",
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=raw_dir,
    )
    set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, edited.id, "demo")
    image = render_post_image(session, CONFIG, briefing, ImageFormat.PORTRAIT)
    assert image is not None and image.ai_edited and image.ai_label == "-ki-bearbeitet"

    rows = generate_variants(
        session,
        CONFIG,
        FakeImageProvider(),
        briefing,
        prompt="Heller Kursraum mit Matten",
        user="demo",
        storage=raw_dir,
    )
    choose_candidate(session, briefing, rows[0].id or 0, "demo")
    image = render_post_image(session, CONFIG, briefing, ImageFormat.SQUARE)
    assert image is not None and image.ai_generated and image.ai_label == "-ki-generiert"


@pytest.fixture(autouse=True)
def _images_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")


# --- consent: only the images in use count ---


def test_only_the_chosen_photo_needs_consent(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    (draft, *_) = _drafts(session, briefing)
    group = _photo(session, briefing, raw_dir, shows_persons=True)

    assert not any("Einwilligung" in b for b in approval_blockers(session, draft, CONFIG))
    set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, group.id, "demo")
    assert any("Einwilligung" in b for b in approval_blockers(session, draft, CONFIG))


# --- the image is fixed after the first approval ---


def test_image_is_locked_after_the_first_approval(session: Session, raw_dir: Path) -> None:
    briefing = _briefing(session)
    instagram, facebook = _drafts(session, briefing)
    photo = _photo(session, briefing, raw_dir)
    set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo")
    approve_draft(session, instagram, "demo", CONFIG)

    assert image_locked(session, briefing.id or 0)
    with pytest.raises(BriefingImageError, match="freigegeben"):
        set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")
    with pytest.raises(InfoGraphicError):
        _headline(session, briefing)
    with pytest.raises(BriefingImageError):
        remove_asset_choice(session, photo)
    # Facebook is approved with the same image.
    approve_draft(session, facebook, "demo", CONFIG)

    for draft in (instagram, facebook):
        reject_draft(session, draft, "demo")
    assert not image_locked(session, briefing.id or 0)
    set_briefing_image(session, briefing.id or 0, ImageSource.COLOR, None, "demo")
    reopen_draft(session, CONFIG, CLIENT, instagram, "demo")


# --- design and facts on the image ---


def test_layout_and_chosen_facts_are_stored(session: Session) -> None:
    briefing = _briefing(session)
    _headline(session, briefing)

    graphic = save_layout(session, briefing, variant="farbfeld", fact_keys=["datum"], user="demo")
    assert graphic.variant == "farbfeld" and graphic.fact_keys == ["datum"]
    graphic = save_layout(
        session, briefing, variant="farbfeld", fact_keys=["datum", "uhrzeit", "ort"], user="demo"
    )
    assert graphic.fact_keys is None  # all facts of the briefing
    with pytest.raises(InfoGraphicError):
        save_layout(session, briefing, variant="unbekannt", fact_keys=None, user="demo")


# --- campaigns: "Wie Schritt 1" ---


def _campaign_briefings(session: Session) -> list[Briefing]:
    data = load_demo_campaign(CONFIG, date(2026, 10, 1))
    campaign = create_campaign(session, CONFIG, CLIENT, data, "demo")
    return [step.briefing for step in campaign_plan(session, CONFIG, campaign)]


def test_copy_from_step_one_is_an_independent_copy(session: Session, raw_dir: Path) -> None:
    first, second, *_ = _campaign_briefings(session)
    assert copy_source(session, first) is None
    assert copy_source(session, second) is None  # step 1 has no image yet
    photo = _photo(session, first, raw_dir, shows_persons=True, consent_confirmed=True)
    set_briefing_image(session, first.id or 0, ImageSource.UPLOAD, photo.id, "demo", with_text=True)
    _headline(session, first, "Rücken-Fit startet")

    copied = copy_from_first_step(session, CONFIG, CLIENT, second, "demo")

    assert copy_source(session, second) == first
    assert copied.source is ImageSource.UPLOAD and copied.with_text
    assert copied.copied_from_briefing_id == first.id
    asset = session.get(Asset, copied.asset_id)
    assert asset is not None and asset.briefing_id == second.id
    assert asset.copied_from_asset_id == photo.id and asset.consent_confirmed
    graphic = get_info_graphic(session, second.id or 0)
    assert graphic is not None and graphic.headline == "Rücken-Fit startet"
    # The facts come from the own briefing: both images render with their own facts.
    one = render_post_image(session, CONFIG, first, ImageFormat.PORTRAIT)
    two = render_post_image(session, CONFIG, second, ImageFormat.PORTRAIT)
    assert one is not None and two is not None
    # Independent: changing step 2 leaves step 1 alone.
    _headline(session, second, "Noch Plätze frei")
    first_graphic = get_info_graphic(session, first.id or 0)
    assert first_graphic is not None and first_graphic.headline == "Rücken-Fit startet"
    # In the audit trail of both briefings.
    for briefing in (first, second):
        steps = [e.step for e in briefing_audit_trail(session, briefing.id or 0)]
        assert COPY_STEP in steps


def test_copied_ai_image_needs_its_hints_confirmed_again(session: Session, raw_dir: Path) -> None:
    first, second, *_ = _campaign_briefings(session)
    from app.social.images import ImageHintsPendingError, confirm_prompt_hint

    prompt = "Frau beim Rückentraining auf der Matte"
    with pytest.raises(ImageHintsPendingError):
        generate_variants(
            session, CONFIG, FakeImageProvider(), first, prompt=prompt, user="demo", storage=raw_dir
        )
    for hint in prompt_hints(session, first.id or 0):
        confirm_prompt_hint(session, hint, "demo")
    rows = generate_variants(
        session, CONFIG, FakeImageProvider(), first, prompt=prompt, user="demo", storage=raw_dir
    )
    choose_candidate(session, first, rows[0].id or 0, "demo")

    copy_from_first_step(session, CONFIG, CLIENT, second, "demo")

    (draft, *_) = _drafts(session, second)
    hints = prompt_hints(session, second.id or 0)
    assert hints and all(h.confirmed_at is None for h in hints)
    assert any("Hinweis zum KI-Bild" in b for b in post_image_blockers(session, CONFIG, draft))
    assert all(h.confirmed_at for h in prompt_hints(session, first.id or 0))


def test_revoking_the_photo_of_step_one_reaches_the_copy(session: Session, raw_dir: Path) -> None:
    first, second, *_ = _campaign_briefings(session)
    photo = _photo(session, first, raw_dir, shows_persons=True, consent_confirmed=True)
    set_briefing_image(session, first.id or 0, ImageSource.UPLOAD, photo.id, "demo")
    copy_from_first_step(session, CONFIG, CLIENT, second, "demo")
    (draft, *_) = _drafts(session, second)
    _confirm_everything(session, second)
    approve_draft(session, draft, "demo", CONFIG)

    revocation = revoke_consent(session, photo, "demo")

    assert draft.id in [d.id for d in revocation.back_to_review]
    session.expire_all()
    assert any("widerrufen" in b for b in approval_blockers(session, draft, CONFIG))


# --- migration of demo data (before U6: images per draft) ---


def test_migration_moves_images_from_drafts_to_briefings(session: Session, raw_dir: Path) -> None:
    with_ai, with_photo, without = _briefing(session), _briefing(session), _briefing(session)
    first, second = _drafts(session, with_ai)
    ai_second = Asset(
        briefing_id=with_ai.id or 0,
        draft_id=second.id,
        filename="ki-2.png",
        stored_path=str(raw_dir / "ki-2.png"),
        media_type="image/png",
        content_hash="b",
        uploaded_by="demo",
        ai_generated=True,
    )
    ai_first = Asset(
        briefing_id=with_ai.id or 0,
        draft_id=first.id,
        filename="ki-1.png",
        stored_path=str(raw_dir / "ki-1.png"),
        media_type="image/png",
        content_hash="a",
        uploaded_by="demo",
        ai_generated=True,
    )
    session.add_all([ai_second, ai_first])
    session.commit()
    _photo(session, with_photo, raw_dir, shows_persons=True)  # not usable: no consent
    usable = _photo(session, with_photo, raw_dir)
    proposal = ImageProposal(draft_id=first.id, briefing_id=with_ai.id or 0, prompt="Matten")
    session.add(proposal)
    session.commit()
    session.add(
        PromptHint(
            draft_id=first.id, proposal_id=proposal.id or 0, key="text", reason="x", prompt_hash="h"
        )
    )
    session.add(CarouselPage(draft_id=first.id, position=1, kind="info", texts={"headline": "A"}))
    session.commit()

    assert migrate_briefing_images(session) == 2

    image = briefing_image(session, with_ai.id or 0)
    assert image is not None and image.source is ImageSource.AI and image.asset_id == ai_first.id
    image = briefing_image(session, with_photo.id or 0)
    assert image is not None and image.source is ImageSource.UPLOAD and image.asset_id == usable.id
    assert image.with_text is False
    assert briefing_image(session, without.id or 0) is None
    assert [h.briefing_id for h in prompt_hints(session, with_ai.id or 0)] == [with_ai.id]
    assert [p.kind for p in pages(session, with_ai.id or 0)] == ["info"]
    session.refresh(first)
    assert first.carousel_enabled
    # Once only: later briefings never get an image chosen by the migration.
    later = _briefing(session)
    _photo(session, later, raw_dir)
    assert migrate_briefing_images(session) == 0
    assert briefing_image(session, later.id or 0) is None
    assert len(session.exec(select(AuditEntry).where(AuditEntry.step == MIGRATION_STEP)).all()) == 1


def test_old_database_gets_nullable_draft_columns(tmp_path: Path) -> None:
    """SQLite cannot drop NOT NULL: the table is rebuilt and keeps its rows."""
    engine: Engine = make_engine(f"sqlite:///{tmp_path / 'alt.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE imagecandidate (id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL,"
                " draft_id INTEGER NOT NULL, briefing_id INTEGER NOT NULL, variant INTEGER NOT "
                "NULL, stored_path VARCHAR NOT NULL, media_type VARCHAR NOT NULL, content_hash "
                "VARCHAR NOT NULL, model VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO imagecandidate VALUES (7, 1, 2, 3, 1, 'x.png', 'image/png', 'h', "
                "'fake', '2026-11-01 10:00:00')"
            )
        )

    init_db(engine)

    columns = {c["name"]: c for c in inspect(engine).get_columns("imagecandidate")}
    assert columns["draft_id"]["nullable"]
    with Session(engine) as session:
        (row,) = session.exec(select(ImageCandidate)).all()
        assert row.id == 7 and row.draft_id == 2 and row.briefing_id == 3


def test_deleting_the_briefing_removes_its_image_choice(session: Session, raw_dir: Path) -> None:
    from app.social.retention import delete_briefing

    briefing = _briefing(session)
    photo = _photo(session, briefing, raw_dir)
    set_briefing_image(session, briefing.id or 0, ImageSource.UPLOAD, photo.id, "demo")

    delete_briefing(session, briefing.id or 0, "demo")

    assert session.exec(select(BriefingImage)).all() == []
    assert not Path(photo.stored_path).exists()


CHANNEL_FORMATS = {
    Channel.INSTAGRAM_POST: ImageFormat.PORTRAIT,
    Channel.INSTAGRAM_STORY: ImageFormat.STORY,
    Channel.FACEBOOK: ImageFormat.PORTRAIT,
    Channel.LINKEDIN: ImageFormat.SQUARE,
}
