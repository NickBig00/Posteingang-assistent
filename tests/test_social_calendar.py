"""Editorial calendar, publishing by click and exports (ROADMAP_social S6)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.models import AuditEntry
from app.main import app
from app.social.briefings import create_briefing
from app.social.calendar import (
    PUBLISH_STEP,
    SCHEDULE_STEP,
    CalendarError,
    build_ics,
    export_text,
    local_to_utc,
    month_view,
    publish_draft,
    schedule_draft,
    unschedule_draft,
    week_view,
)
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.models import Channel, PostDraft, PostStatus, as_utc
from app.social.review import ReviewNotAllowedError, approve_draft, reject_draft
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _approved(session: Session, channel: Channel = Channel.FACEBOOK) -> PostDraft:
    briefing = create_briefing(session, CONFIG, course_input(channels=[channel]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    approve_draft(session, draft, "demo")
    return draft


def _scheduled(session: Session, when: datetime) -> PostDraft:
    return schedule_draft(session, _approved(session), when, "demo", now=NOW)


# --- planning ---


@pytest.mark.parametrize(
    ("local", "utc"),
    [
        (datetime(2026, 11, 3, 18, 30), datetime(2026, 11, 3, 17, 30, tzinfo=UTC)),  # CET
        (datetime(2027, 7, 1, 9, 0), datetime(2027, 7, 1, 7, 0, tzinfo=UTC)),  # CEST
        (datetime(2027, 3, 28, 3, 30), datetime(2027, 3, 28, 1, 30, tzinfo=UTC)),  # after switch
    ],
)
def test_local_time_is_stored_as_utc(local: datetime, utc: datetime) -> None:
    assert local_to_utc(local) == utc


def test_schedule_and_move(session: Session) -> None:
    draft = _scheduled(session, datetime(2026, 11, 2, 9, 0))
    assert draft.status is PostStatus.SCHEDULED
    assert as_utc(draft.scheduled_at) == datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # type: ignore[arg-type]

    schedule_draft(session, draft, datetime(2026, 11, 4, 17, 15), "demo", now=NOW)

    assert as_utc(draft.scheduled_at) == datetime(2026, 11, 4, 16, 15, tzinfo=UTC)  # type: ignore[arg-type]
    entries = session.exec(select(AuditEntry).where(AuditEntry.step == SCHEDULE_STEP)).all()
    assert [e.model_params["previous"] for e in entries] == [None, "2026-11-02T08:00:00+00:00"]


def test_only_approved_posts_are_planned(session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)

    with pytest.raises(CalendarError, match="Nur freigegebene"):
        schedule_draft(session, draft, datetime(2026, 11, 2, 9, 0), "demo", now=NOW)


def test_no_dates_in_the_past(session: Session) -> None:
    with pytest.raises(CalendarError, match="Vergangenheit"):
        schedule_draft(session, _approved(session), datetime(2026, 10, 27, 12, 0), "demo", now=NOW)


def test_unschedule(session: Session) -> None:
    draft = _scheduled(session, datetime(2026, 11, 2, 9, 0))

    unschedule_draft(session, draft, "demo")

    assert draft.status is PostStatus.APPROVED and draft.scheduled_at is None
    with pytest.raises(CalendarError):
        unschedule_draft(session, draft, "demo")


def test_publish_by_click(session: Session) -> None:
    draft = _scheduled(session, datetime(2026, 11, 2, 9, 0))
    when = datetime(2026, 11, 2, 9, 5, tzinfo=UTC)

    publish_draft(session, draft, "demo", now=when)

    assert draft.status is PostStatus.PUBLISHED
    assert as_utc(draft.published_at) == when  # type: ignore[arg-type]
    (entry,) = session.exec(select(AuditEntry).where(AuditEntry.step == PUBLISH_STEP)).all()
    assert entry.model_params["actor"] == "demo"
    with pytest.raises(CalendarError):
        publish_draft(session, draft, "demo")
    with pytest.raises(ReviewNotAllowedError):
        reject_draft(session, draft, "demo")
    with pytest.raises(CalendarError):
        schedule_draft(session, draft, datetime(2026, 11, 9, 9, 0), "demo", now=NOW)


def test_publish_needs_a_planned_date(session: Session) -> None:
    with pytest.raises(CalendarError, match="Nur geplante"):
        publish_draft(session, _approved(session), "demo")


# --- views ---


def test_week_view_uses_local_days(session: Session) -> None:
    late_sunday = _scheduled(session, datetime(2026, 11, 8, 23, 30))  # 22:30 UTC
    early_monday = _scheduled(session, datetime(2026, 11, 9, 0, 30))  # Sunday 23:30 UTC
    wednesday = _scheduled(session, datetime(2026, 11, 4, 9, 0))

    week = week_view(session, date(2026, 11, 5))

    assert [d.day for d in week] == [date(2026, 11, 2 + i) for i in range(7)]
    assert [e.draft.id for e in week[2].entries] == [wednesday.id]
    assert [e.draft.id for e in week[6].entries] == [late_sunday.id]
    assert week[6].entries[0].local_time.strftime("%H:%M") == "23:30"
    next_week = week_view(session, date(2026, 11, 9))
    assert [e.draft.id for e in next_week[0].entries] == [early_monday.id]


def test_month_view_grid(session: Session) -> None:
    draft = _scheduled(session, datetime(2026, 11, 30, 10, 0))

    weeks = month_view(session, date(2026, 11, 15))

    assert all(len(week) == 7 for week in weeks)
    assert weeks[0][0].day == date(2026, 10, 26) and not weeks[0][0].in_month
    assert weeks[-1][0].day == date(2026, 11, 30) and weeks[-1][0].in_month
    assert [e.draft.id for e in weeks[-1][0].entries] == [draft.id]


def test_rejected_and_unplanned_posts_are_not_in_the_calendar(session: Session) -> None:
    withdrawn = _scheduled(session, datetime(2026, 11, 4, 9, 0))
    reject_draft(session, withdrawn, "demo")
    _approved(session)

    assert all(not d.entries for d in week_view(session, date(2026, 11, 4)))


# --- exports ---


def _parse_ics(text: str) -> list[dict[str, str]]:
    """Minimal RFC 5545 reader: unfold lines, collect VEVENT properties, unescape text."""
    assert text.endswith("\r\n")
    raw_lines = text.split("\r\n")[:-1]
    assert all("\n" not in line for line in raw_lines)
    assert all(len(line.encode("utf-8")) <= 75 for line in raw_lines)
    lines: list[str] = []
    for line in raw_lines:
        if line.startswith(" "):
            lines[-1] += line[1:]
        else:
            lines.append(line)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    events, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            events.append(current)
            current = None
        elif current is not None:
            name, _, value = line.partition(":")
            current[name] = (
                value.replace("\\n", "\n")
                .replace("\\,", ",")
                .replace(r"\;", ";")
                .replace("\\\\", "\\")
            )
    return events


def test_ics_is_valid_and_complete(session: Session) -> None:
    draft = _scheduled(session, datetime(2026, 11, 2, 9, 0))
    draft.text = "Zeile 1; mit Semikolon, Komma und \\ Backslash\nÄÖÜ ß 💪 " + "lang " * 40

    body = build_ics([(draft, "Facebook-Beitrag: Kurs (Kurse)")], now=NOW)

    (event,) = _parse_ics(body)
    assert event["UID"] == f"social-draft-{draft.id}@posteingang-assistent.example"
    assert event["DTSTART"] == "20261102T080000Z"
    assert event["DTEND"] == "20261102T081500Z"
    assert event["DTSTAMP"] == "20261027T120000Z"
    assert event["SUMMARY"] == "Facebook-Beitrag: Kurs (Kurse)"
    assert event["DESCRIPTION"] == draft.full_text
    assert "VERSION:2.0\r\n" in body and "PRODID:" in body


def test_ics_marks_published_posts(session: Session) -> None:
    draft = _scheduled(session, datetime(2026, 11, 2, 9, 0))
    publish_draft(session, draft, "demo")

    (event,) = _parse_ics(build_ics([(draft, "X")], now=NOW))

    assert event["SUMMARY"] == "Veröffentlicht – X"


def test_text_export_only_after_approval(session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)

    with pytest.raises(CalendarError):
        export_text(draft)
    approve_draft(session, draft, "demo")
    assert export_text(draft) == draft.full_text + "\n"


# --- web ---


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_plan_publish_and_export_in_the_ui(social_client: TestClient, session: Session) -> None:
    draft = _approved(session)

    planned = social_client.post(
        f"/social/drafts/{draft.id}/schedule", data={"when": "2099-01-12T10:30"}
    )
    assert planned.status_code == 200
    assert "Geplant: <strong>12.01.2099 10:30 Uhr</strong>" in planned.text

    week = social_client.get("/social/calendar", params={"date": "2099-01-13"}).text
    assert 'data-day="2099-01-12"' in week
    assert f'data-draft="{draft.id}"' in week and "10:30 Facebook-Beitrag" in week
    month = social_client.get("/social/calendar", params={"view": "month", "date": "2099-01-01"})
    assert f'data-draft="{draft.id}"' in month.text

    moved = social_client.post(
        f"/social/drafts/{draft.id}/schedule",
        data={"when": "2099-01-14T08:00", "back": "/social/calendar?view=week&date=2099-01-13"},
        follow_redirects=False,
    )
    assert moved.headers["location"] == "/social/calendar?view=week&date=2099-01-13"

    ics = social_client.get("/social/calendar.ics")
    assert ics.headers["content-type"].startswith("text/calendar")
    (event,) = _parse_ics(ics.text)
    assert event["DTSTART"] == "20990114T070000Z"
    assert event["SUMMARY"] == "Facebook-Beitrag: Kurs (Kurse)"

    social_client.post(f"/social/drafts/{draft.id}/publish")
    session.refresh(draft)
    assert draft.status is PostStatus.PUBLISHED
    exported = social_client.get(f"/social/drafts/{draft.id}/export")
    assert exported.text == draft.full_text + "\n"
    assert "beitrag-" in exported.headers["content-disposition"]


def test_ui_rejects_bad_dates(social_client: TestClient, session: Session) -> None:
    draft = _approved(session)

    assert (
        social_client.post(
            f"/social/drafts/{draft.id}/schedule", data={"when": "morgen"}
        ).status_code
        == 422
    )
    past = social_client.post(
        f"/social/drafts/{draft.id}/schedule", data={"when": "2020-01-01T10:00"}
    )
    assert past.status_code == 409 and "Vergangenheit" in past.text
    assert social_client.get("/social/calendar", params={"date": "x"}).status_code == 422


def test_export_before_approval_is_refused(social_client: TestClient, session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)

    assert social_client.get(f"/social/drafts/{draft.id}/export").status_code == 409


def test_unplanned_approved_posts_are_listed(social_client: TestClient, session: Session) -> None:
    draft = _approved(session)

    page = social_client.get("/social/calendar").text

    assert "Freigegeben, noch nicht geplant" in page
    assert f"Entwurf {draft.id}" in page
