"""Contribution of the social media module to the start page (core overview contract)."""

from datetime import date, datetime, timedelta

from sqlmodel import Session, func, select

from app.core.models import utcnow
from app.core.overview import ModuleOverview, Tile, TodoItem
from app.social.calendar import calendar_entries, to_local
from app.social.models import CHANNEL_LABELS, PostDraft, PostStatus

LIST_PATH = "/social"
# How far back overdue (scheduled, not yet marked as published) posts are listed.
OVERDUE_DAYS = 30


def status_counts(session: Session) -> dict[PostStatus, int]:
    rows = session.exec(select(PostDraft.status, func.count()).group_by(PostDraft.status)).all()
    counts = dict.fromkeys(PostStatus, 0)
    counts.update({PostStatus(status): count for status, count in rows})
    return counts


def status_href(*statuses: PostStatus) -> str:
    return f"{LIST_PATH}?status={','.join(s.value for s in statuses)}"


def social_overview(session: Session, now: datetime | None = None) -> ModuleOverview:
    counts = status_counts(session)
    tiles = [
        Tile(
            "Dringend",
            counts[PostStatus.NEEDS_MANUAL_REVIEW],
            status_href(PostStatus.NEEDS_MANUAL_REVIEW),
            "error",
            "Kein gültiger KI-Entwurf",
        ),
        Tile(
            "Entwürfe zur Prüfung",
            counts[PostStatus.DRAFT] + counts[PostStatus.IN_REVIEW],
            status_href(PostStatus.DRAFT, PostStatus.IN_REVIEW),
            "warning",
            "Prüfen und freigeben",
        ),
        Tile(
            "Freigegeben, ungeplant",
            counts[PostStatus.APPROVED],
            status_href(PostStatus.APPROVED),
            "info",
            "Termin im Kalender setzen",
        ),
        Tile(
            "Geplante Beiträge",
            counts[PostStatus.SCHEDULED],
            status_href(PostStatus.SCHEDULED),
            "info",
            "Von Hand veröffentlichen",
        ),
    ]
    today = to_local(now or utcnow()).date()
    return ModuleOverview(
        module="social",
        title="Social Media",
        href=LIST_PATH,
        tiles=tiles,
        todos=_publish_todos(session, today),
        empty_hint="Heute ist kein Beitrag zu veröffentlichen.",
    )


def _publish_todos(session: Session, today: date) -> list[TodoItem]:
    """Scheduled posts due today or overdue: they have to be published by hand."""
    entries = calendar_entries(
        session, today - timedelta(days=OVERDUE_DAYS), today + timedelta(days=1)
    )
    todos = []
    for entry in entries:
        if entry.draft.status != PostStatus.SCHEDULED:
            continue
        overdue = entry.local_time.date() < today
        when = entry.local_time.strftime("%d.%m. %H:%M" if overdue else "%H:%M")
        todos.append(
            TodoItem(
                title=f"{CHANNEL_LABELS[entry.draft.channel]} veröffentlichen",
                detail=f"Briefing {entry.briefing.id} · geplant {when} Uhr",
                href=f"/social/briefings/{entry.briefing.id}#draft-{entry.draft.id}",
                tone="error" if overdue else "warning",
                badge="überfällig" if overdue else "heute",
            )
        )
    return todos
