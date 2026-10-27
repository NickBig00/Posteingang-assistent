"""Editorial calendar: planning approved posts, marking them published, exports.

Times are entered and shown in local time (Europe/Berlin) and stored in UTC. Publishing
happens by hand on the platform; the click only records it. Nothing is posted by the system.
"""

import calendar as month_calendar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.core.audit import write_human_audit_entry
from app.core.models import utcnow
from app.social.models import CHANNEL_LABELS, Briefing, PostDraft, PostStatus, as_utc
from app.social.status import transition

LOCAL_TZ = ZoneInfo("Europe/Berlin")
SCHEDULE_STEP = "social.schedule"
UNSCHEDULE_STEP = "social.unschedule"
PUBLISH_STEP = "social.publish"
CALENDAR_STATUSES = (PostStatus.SCHEDULED, PostStatus.PUBLISHED)
EXPORTABLE = frozenset({PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED})


class CalendarError(ValueError):
    """Planning or publishing not possible (German message for the UI)."""


def to_local(value: datetime) -> datetime:
    return as_utc(value).astimezone(LOCAL_TZ)


def local_to_utc(value: datetime) -> datetime:
    """A naive local date-time (form input) as aware UTC datetime."""
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=LOCAL_TZ).astimezone(UTC)


def _audit(session: Session, draft: PostDraft, step: str, user: str, **details: JsonValue) -> None:
    write_human_audit_entry(
        session,
        item_id=None,
        step=step,
        actor=user,
        details={"briefing_id": draft.briefing_id, "draft_id": draft.id, **details},
    )


def schedule_draft(
    session: Session,
    draft: PostDraft,
    when_local: datetime,
    user: str,
    now: datetime | None = None,
) -> PostDraft:
    """Set or move the planned date of an approved post."""
    if draft.status not in (PostStatus.APPROVED, PostStatus.SCHEDULED):
        raise CalendarError("Nur freigegebene Beiträge können geplant werden.")
    when = local_to_utc(when_local)
    if when < (now or utcnow()):
        raise CalendarError("Der Termin liegt in der Vergangenheit.")
    previous = draft.scheduled_at
    draft.scheduled_at = when
    transition(session, draft, PostStatus.SCHEDULED)
    _audit(
        session,
        draft,
        SCHEDULE_STEP,
        user,
        scheduled_at=when.isoformat(),
        previous=as_utc(previous).isoformat() if previous else None,
    )
    return draft


def unschedule_draft(session: Session, draft: PostDraft, user: str) -> PostDraft:
    """Take a planned post off the calendar (it stays approved)."""
    if draft.status is not PostStatus.SCHEDULED:
        raise CalendarError("Nur geplante Beiträge können aus dem Kalender genommen werden.")
    draft.scheduled_at = None
    transition(session, draft, PostStatus.APPROVED)
    _audit(session, draft, UNSCHEDULE_STEP, user)
    return draft


def publish_draft(
    session: Session, draft: PostDraft, user: str, now: datetime | None = None
) -> PostDraft:
    """Record that a person published the planned post on the platform."""
    if draft.status is not PostStatus.SCHEDULED:
        raise CalendarError("Nur geplante Beiträge können als veröffentlicht markiert werden.")
    draft.published_at = now or utcnow()
    transition(session, draft, PostStatus.PUBLISHED)
    _audit(session, draft, PUBLISH_STEP, user, published_at=draft.published_at.isoformat())
    return draft


# --- views ---


@dataclass
class CalendarEntry:
    draft: PostDraft
    briefing: Briefing
    local_time: datetime


@dataclass
class CalendarDay:
    day: date
    in_month: bool = True
    entries: list[CalendarEntry] = field(default_factory=list)


def calendar_entries(session: Session, start: date, end: date) -> list[CalendarEntry]:
    """Planned and published posts with a local date in [start, end)."""
    start_utc = local_to_utc(datetime.combine(start, datetime.min.time()))
    end_utc = local_to_utc(datetime.combine(end, datetime.min.time()))
    statement = (
        select(PostDraft, Briefing)
        .join(Briefing)
        .where(col(PostDraft.status).in_(CALENDAR_STATUSES))
        .where(col(PostDraft.scheduled_at).is_not(None))
        .order_by(col(PostDraft.scheduled_at), col(PostDraft.id))
    )
    entries = []
    for draft, briefing in session.exec(statement).all():
        when = as_utc(draft.scheduled_at)  # type: ignore[arg-type]
        if start_utc <= when < end_utc:
            entries.append(CalendarEntry(draft, briefing, when.astimezone(LOCAL_TZ)))
    return entries


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_view(session: Session, day: date) -> list[CalendarDay]:
    """Monday to Sunday of the week containing `day`."""
    start = week_start(day)
    days = [CalendarDay(start + timedelta(days=i)) for i in range(7)]
    by_day = {d.day: d for d in days}
    for entry in calendar_entries(session, start, start + timedelta(days=7)):
        by_day[entry.local_time.date()].entries.append(entry)
    return days


def month_view(session: Session, day: date) -> list[list[CalendarDay]]:
    """Weeks (Monday first) covering the month of `day`, days of other months marked."""
    weeks_dates = month_calendar.Calendar(firstweekday=0).monthdatescalendar(day.year, day.month)
    weeks = [[CalendarDay(d, d.month == day.month) for d in week] for week in weeks_dates]
    by_day = {d.day: d for week in weeks for d in week}
    first, last = weeks_dates[0][0], weeks_dates[-1][-1] + timedelta(days=1)
    for entry in calendar_entries(session, first, last):
        by_day[entry.local_time.date()].entries.append(entry)
    return weeks


# --- exports ---


def export_text(draft: PostDraft) -> str:
    if draft.status not in EXPORTABLE:
        raise CalendarError("Export erst nach der Freigabe.")
    return draft.full_text + "\n"


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """RFC 5545 line folding: at most 75 octets per line, continuation lines start with a
    space; never split a UTF-8 character."""
    lines: list[str] = []
    current, size, limit = "", 0, 75
    for char in line:
        length = len(char.encode("utf-8"))
        if size + length > limit:
            lines.append(current)
            current, size, limit = " ", 1, 75
        current += char
        size += length
    lines.append(current)
    return lines


def _ics_time(value: datetime) -> str:
    return as_utc(value).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    entries: list[tuple[PostDraft, str]], now: datetime | None = None, duration_minutes: int = 15
) -> str:
    """iCalendar (RFC 5545) with one event per planned/published post.

    `entries`: draft and its summary line (e.g. "Facebook-Beitrag: Kurs (Kurse)").
    """
    stamp = _ics_time(now or utcnow())
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Posteingangs-Assistent Demo//Redaktionskalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Redaktionskalender (Demo)",
    ]
    for draft, summary in entries:
        if draft.scheduled_at is None:
            continue
        start = as_utc(draft.scheduled_at)
        published = draft.status is PostStatus.PUBLISHED
        lines += [
            "BEGIN:VEVENT",
            f"UID:social-draft-{draft.id}@posteingang-assistent.example",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_ics_time(start)}",
            f"DTEND:{_ics_time(start + timedelta(minutes=duration_minutes))}",
            f"SUMMARY:{_ics_escape(('Veröffentlicht – ' if published else '') + summary)}",
            f"DESCRIPTION:{_ics_escape(draft.full_text)}",
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(f"{folded}\r\n" for line in lines for folded in _fold(line))


def summary_line(draft: PostDraft, briefing: Briefing, labels: dict[str, dict[str, str]]) -> str:
    occasion = labels["occasion_labels"].get(briefing.occasion_type, briefing.occasion_type)
    area = labels["area_labels"].get(briefing.area, briefing.area)
    return f"{CHANNEL_LABELS[draft.channel]}: {occasion} ({area})"
