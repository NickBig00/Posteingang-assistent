"""Start page "Übersicht" (ROADMAP_social U2): figures match the filtered lists."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.web import german_date
from app.inbox.models import InboxItem, ItemSource, ItemStatus
from app.inbox.overview import inbox_overview
from app.main import app
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import Channel, PostDraft, PostStatus
from app.social.overview import social_overview
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()

INBOX_STATUSES = [
    ItemStatus.NEW,
    ItemStatus.NEW,
    ItemStatus.PROCESSED,
    ItemStatus.PROCESSED,
    ItemStatus.PROCESSED,
    ItemStatus.NEEDS_MANUAL_REVIEW,
    ItemStatus.APPROVED,
    ItemStatus.DONE,
    ItemStatus.REJECTED,
]
SOCIAL_STATUSES = [
    PostStatus.IN_REVIEW,
    PostStatus.IN_REVIEW,
    PostStatus.DRAFT,
    PostStatus.NEEDS_MANUAL_REVIEW,
    PostStatus.APPROVED,
    PostStatus.SCHEDULED,
    PostStatus.SCHEDULED,
    PostStatus.PUBLISHED,
    PostStatus.REJECTED,
]


def _inbox(session: Session) -> None:
    for number, status in enumerate(INBOX_STATUSES):
        session.add(
            InboxItem(
                source=ItemSource.SAMPLE,
                raw_content_ref=f"/tmp/beispiel-{number}.eml",  # noqa: S108 - never read
                filename=f"beispiel-{number}.eml",
                content_hash=f"hash-{number}",
                status=status,
            )
        )
    session.commit()


def _social(session: Session, now: datetime) -> list[PostDraft]:
    drafts: list[PostDraft] = []
    for _ in range(0, len(SOCIAL_STATUSES), 3):
        briefing = create_briefing(
            session,
            CONFIG,
            course_input(channels=[Channel.INSTAGRAM_POST, Channel.FACEBOOK, Channel.LINKEDIN]),
            "demo",
        )
        drafts += generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    for draft, status in zip(drafts, SOCIAL_STATUSES, strict=True):
        draft.status = status
        if status == PostStatus.SCHEDULED:
            draft.scheduled_at = now
        session.add(draft)
    session.commit()
    return drafts


@pytest.fixture
def overview_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def _tiles(html: str) -> dict[tuple[str, str], tuple[int, str]]:
    """(module, label) -> (number, link) from the rendered start page."""
    tiles = {}
    for module, body in re.findall(r'data-module="(\w+)"(.*?)</section>', html, flags=re.S):
        for href, label, value in re.findall(
            r'<a href="([^"]+)"[^>]*data-stat="([^"]+)">.*?data-value>(\d+)<', body, flags=re.S
        ):
            tiles[(module, label)] = (int(value), href.replace("&amp;", "&"))
    return tiles


def test_inbox_figures(session: Session) -> None:
    _inbox(session)

    tiles = {t.label: t.count for t in inbox_overview(session).tiles}

    assert tiles == {"Dringend": 1, "Zur Prüfung": 3, "Neu": 2, "Freigegeben": 1}


def test_social_figures(session: Session) -> None:
    _social(session, datetime.now(UTC))

    tiles = {t.label: t.count for t in social_overview(session).tiles}

    assert tiles == {
        "Dringend": 1,
        "Entwürfe zur Prüfung": 3,
        "Freigegeben, ungeplant": 1,
        "Geplante Beiträge": 2,
    }


def test_every_figure_matches_its_filtered_list(
    overview_client: TestClient, session: Session
) -> None:
    _inbox(session)
    _social(session, datetime.now(UTC))

    tiles = _tiles(overview_client.get("/").text)

    assert len(tiles) == 8
    for (module, label), (count, href) in tiles.items():
        listing = overview_client.get(href).text
        marker = "data-item=" if module == "inbox" else "data-draft="
        assert listing.count(marker) == count, (label, href)
        assert 'id="status-filter"' in listing


def test_empty_start_page(overview_client: TestClient) -> None:
    html = overview_client.get("/").text

    assert "Was ist heute zu tun?" in html
    assert "Nichts Dringendes offen." in html
    assert "Keine Eingänge zur manuellen Prüfung." in html
    assert "Heute ist kein Beitrag zu veröffentlichen." in html
    assert all(count == 0 for count, _ in _tiles(html).values())


def test_todos_list_urgent_items_and_posts_due_today(session: Session) -> None:
    _inbox(session)
    now = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    drafts = _social(session, now)
    overdue = drafts[SOCIAL_STATUSES.index(PostStatus.SCHEDULED)]
    overdue.scheduled_at = now - timedelta(days=2)
    session.add(overdue)
    tomorrow = drafts[SOCIAL_STATUSES.index(PostStatus.PUBLISHED)]
    tomorrow.status = PostStatus.SCHEDULED
    tomorrow.scheduled_at = now + timedelta(days=1)
    session.add(tomorrow)
    session.commit()

    inbox_todos = inbox_overview(session).todos
    social_todos = social_overview(session, now=now).todos

    assert [t.badge for t in inbox_todos] == ["Manuelle Prüfung"]
    assert [t.badge for t in social_todos] == ["überfällig", "heute"]
    assert social_todos[0].href.endswith(f"#draft-{overdue.id}")


def test_status_filter_ignores_unknown_values(
    overview_client: TestClient, session: Session
) -> None:
    _inbox(session)

    assert overview_client.get("/inbox?status=gibtsnicht").text.count("data-item=") == len(
        INBOX_STATUSES
    )


def test_german_date() -> None:
    assert german_date(datetime(2026, 11, 3).date()) == "Dienstag, 3. November 2026"


def test_overview_is_the_active_navigation_entry(overview_client: TestClient) -> None:
    html = overview_client.get("/").text

    assert re.search(r'<a href="/"[^>]*aria-current="page"', html)
