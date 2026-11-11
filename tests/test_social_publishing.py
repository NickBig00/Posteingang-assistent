"""Social media in the new design with publishing aids (ROADMAP_social U4)."""

import io
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.core.db import add_missing_columns, make_engine
from app.core.image_formats import FORMAT_SPECS, ImageExportError, ImageFormat, export_jpeg
from app.core.models import AuditEntry
from app.main import app
from app.social.assets import save_asset
from app.social.briefings import create_briefing
from app.social.calendar import (
    EXTERNAL_STEP,
    EXTERNAL_UNDO_STEP,
    CalendarError,
    mark_scheduled_externally,
    schedule_draft,
    unmark_scheduled_externally,
    unschedule_draft,
)
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    CHANNEL_IMAGE_FORMATS,
    Briefing,
    Channel,
    PostDraft,
    PostStatus,
)
from app.social.review import approve_draft
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
WHEN = datetime(2099, 1, 12, 10, 30)


def _image(fmt: str = "PNG", size: tuple[int, int] = (300, 200), exif: bool = False) -> bytes:
    image = Image.new(
        "RGBA" if fmt == "PNG" else "RGB", size, (20, 120, 110, 255)[: 4 if fmt == "PNG" else 3]
    )
    buffer = io.BytesIO()
    if exif:
        data = Image.Exif()
        data[0x010F] = "Beispielkamera"  # Make
        data[0x0131] = "Beispielsoftware"  # Software
        image.save(buffer, format=fmt, exif=data.tobytes())
    else:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


def _briefing_with_draft(session: Session, channel: Channel = Channel.INSTAGRAM_POST) -> PostDraft:
    briefing = create_briefing(session, CONFIG, course_input(channels=[channel]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    return draft


def _approved(session: Session, channel: Channel = Channel.INSTAGRAM_POST) -> PostDraft:
    draft = _briefing_with_draft(session, channel)
    approve_draft(session, draft, "demo")
    return draft


def _scheduled(session: Session) -> PostDraft:
    return schedule_draft(session, _approved(session), WHEN, "demo", now=NOW)


def _steps(session: Session) -> list[str]:
    return [e.step for e in session.exec(select(AuditEntry).order_by(AuditEntry.id)).all()]  # type: ignore[arg-type]


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


# --- JPEG export ---


@pytest.mark.parametrize("fmt", list(ImageFormat))
def test_export_has_the_exact_size_of_the_format(fmt: ImageFormat) -> None:
    result = Image.open(io.BytesIO(export_jpeg(_image(), fmt)))

    spec = FORMAT_SPECS[fmt]
    assert result.format == "JPEG"
    assert result.size == (spec.width, spec.height)
    assert result.mode == "RGB"


def test_export_removes_metadata() -> None:
    source = _image("JPEG", exif=True)
    assert Image.open(io.BytesIO(source)).getexif()  # precondition: camera data present

    result = Image.open(io.BytesIO(export_jpeg(source, ImageFormat.SQUARE)))

    assert not result.getexif()
    assert b"Beispielkamera" not in export_jpeg(source, ImageFormat.SQUARE)


def test_export_rejects_non_images() -> None:
    with pytest.raises(ImageExportError):
        export_jpeg(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, ImageFormat.SQUARE)


def test_every_channel_has_an_image_format() -> None:
    assert set(CHANNEL_IMAGE_FORMATS) == set(Channel)
    assert CHANNEL_IMAGE_FORMATS[Channel.INSTAGRAM_STORY] is ImageFormat.STORY


def _asset(session: Session, draft: PostDraft, storage: Path, **flags: bool) -> int:
    briefing = session.get(Briefing, draft.briefing_id)
    assert briefing is not None
    asset = save_asset(
        session,
        briefing,
        "studio.png",
        _image(),
        shows_persons=flags.get("shows_persons", False),
        consent_confirmed=flags.get("consent_confirmed", False),
        user="demo",
        storage=storage,
    )
    assert asset.id is not None
    return asset.id


def test_jpeg_download_after_approval(
    social_client: TestClient, session: Session, raw_dir: Path
) -> None:
    draft = _approved(session, Channel.INSTAGRAM_STORY)
    asset_id = _asset(session, draft, raw_dir)

    response = social_client.get(f"/social/drafts/{draft.id}/image.jpg?asset={asset_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert "9x16.jpg" in response.headers["content-disposition"]
    assert Image.open(io.BytesIO(response.content)).size == (1080, 1920)
    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text
    assert f"/social/drafts/{draft.id}/image.jpg?asset={asset_id}" in page


def test_no_jpeg_download_before_approval(
    social_client: TestClient, session: Session, raw_dir: Path
) -> None:
    draft = _briefing_with_draft(session)
    asset_id = _asset(session, draft, raw_dir)

    response = social_client.get(f"/social/drafts/{draft.id}/image.jpg?asset={asset_id}")

    assert response.status_code == 409


def test_no_jpeg_of_persons_without_consent(
    social_client: TestClient, session: Session, raw_dir: Path
) -> None:
    draft = _approved(session)
    asset_id = _asset(session, draft, raw_dir, shows_persons=True)

    response = social_client.get(f"/social/drafts/{draft.id}/image.jpg?asset={asset_id}")

    assert response.status_code == 409


def test_no_jpeg_of_another_briefing(
    social_client: TestClient, session: Session, raw_dir: Path
) -> None:
    draft = _approved(session)
    other = _approved(session)
    asset_id = _asset(session, other, raw_dir)

    response = social_client.get(f"/social/drafts/{draft.id}/image.jpg?asset={asset_id}")

    assert response.status_code == 404


# --- "eingeplant (extern)" ---


def test_mark_scheduled_externally_is_audited(session: Session) -> None:
    draft = _scheduled(session)

    mark_scheduled_externally(session, draft, "demo", now=NOW)

    assert draft.is_scheduled_externally
    assert draft.external_scheduled_by == "demo"
    assert draft.status is PostStatus.SCHEDULED  # a marker, not a status of its own
    unmark_scheduled_externally(session, draft, "demo")
    assert not draft.is_scheduled_externally
    assert _steps(session)[-2:] == [EXTERNAL_STEP, EXTERNAL_UNDO_STEP]


def test_only_scheduled_posts_can_be_marked(session: Session) -> None:
    with pytest.raises(CalendarError, match="Nur geplante"):
        mark_scheduled_externally(session, _approved(session), "demo")


def test_moving_the_date_resets_the_marker(session: Session) -> None:
    draft = _scheduled(session)
    mark_scheduled_externally(session, draft, "demo")

    schedule_draft(session, draft, WHEN.replace(hour=11), "demo", now=NOW)

    assert not draft.is_scheduled_externally
    entry = session.exec(select(AuditEntry).order_by(AuditEntry.id.desc())).first()  # type: ignore[union-attr]
    assert entry is not None and entry.model_params["external_reset"] is True


def test_same_date_keeps_the_marker(session: Session) -> None:
    draft = _scheduled(session)
    mark_scheduled_externally(session, draft, "demo")

    schedule_draft(session, draft, WHEN, "demo", now=NOW)

    assert draft.is_scheduled_externally


def test_unscheduling_clears_the_marker(session: Session) -> None:
    draft = _scheduled(session)
    mark_scheduled_externally(session, draft, "demo")

    unschedule_draft(session, draft, "demo")

    assert draft.external_scheduled_at is None


def test_external_marker_in_the_ui(social_client: TestClient, session: Session) -> None:
    draft = _scheduled(session)

    response = social_client.post(f"/social/drafts/{draft.id}/external", follow_redirects=True)

    assert response.status_code == 200
    assert "eingeplant (extern)</span>" in response.text
    calendar = social_client.get("/social/calendar?view=week&date=2099-01-12").text
    assert "data-external>eingeplant (extern)" in calendar
    social_client.post(f"/social/drafts/{draft.id}/external/undo")
    assert (
        "data-external" not in social_client.get("/social/calendar?view=week&date=2099-01-12").text
    )


def test_marking_an_unscheduled_post_is_refused_in_the_ui(
    social_client: TestClient, session: Session
) -> None:
    draft = _approved(session)

    assert social_client.post(f"/social/drafts/{draft.id}/external").status_code == 409


# --- copy text and preview ---


def test_copy_button_contains_text_with_hashtags(
    social_client: TestClient, session: Session
) -> None:
    draft = _approved(session)
    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text

    assert "Text kopieren" in page
    assert draft.hashtags
    copy = page.split('data-copy="', 1)[1].split('"', 1)[0]
    assert all(tag in copy for tag in draft.hashtags)


@pytest.mark.parametrize("channel", list(Channel))
def test_preview_per_channel(social_client: TestClient, session: Session, channel: Channel) -> None:
    draft = _briefing_with_draft(session, channel)

    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text

    assert f'data-preview="{channel.value}"' in page


def test_long_text_is_cut_with_more(social_client: TestClient, session: Session) -> None:
    draft = _briefing_with_draft(session, Channel.INSTAGRAM_POST)
    draft.text = "Wort " * 80
    session.add(draft)
    session.commit()

    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text
    preview = page.split('data-preview="instagram_post"', 1)[1].split("</figure>", 1)[0]

    assert "<details" in preview and "mehr</span>" in preview


def test_preview_shows_an_approved_image(
    social_client: TestClient, session: Session, raw_dir: Path
) -> None:
    draft = _briefing_with_draft(session)
    asset_id = _asset(session, draft, raw_dir)
    hidden = _asset_with_name(session, draft, raw_dir)

    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text
    preview = page.split('data-preview="instagram_post"', 1)[1].split("</figure>", 1)[0]

    assert f'src="/social/assets/{asset_id}"' in preview
    assert f'src="/social/assets/{hidden}"' not in preview


def _asset_with_name(session: Session, draft: PostDraft, storage: Path) -> int:
    briefing = session.get(Briefing, draft.briefing_id)
    assert briefing is not None
    asset = save_asset(
        session,
        briefing,
        "gruppe.png",
        _image(size=(310, 200)),
        shows_persons=True,
        consent_confirmed=False,
        user="demo",
        storage=storage,
    )
    assert asset.id is not None
    return asset.id


# --- additive columns for existing databases ---


def test_new_columns_are_added_to_an_existing_database(tmp_path: Path) -> None:
    engine: Engine = make_engine(f"sqlite:///{tmp_path / 'alt.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE postdraft (id INTEGER PRIMARY KEY, briefing_id INTEGER, text VARCHAR)"
            )
        )

    added = add_missing_columns(engine)

    assert "postdraft.external_scheduled_by" in added
    assert "postdraft.is_ai_suggestion" in added
    columns = {c["name"] for c in inspect(engine).get_columns("postdraft")}
    assert {"external_scheduled_at", "external_scheduled_by", "status"} <= columns
    assert add_missing_columns(engine) == []
