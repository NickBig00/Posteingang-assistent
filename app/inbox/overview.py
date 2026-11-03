"""Contribution of the inbox to the start page (core overview contract)."""

from sqlmodel import Session, col, func, select

from app.core.overview import ModuleOverview, Tile, TodoItem
from app.inbox.models import Classification, InboxItem, ItemStatus

LIST_PATH = "/inbox"
MAX_TODOS = 5


def status_counts(session: Session) -> dict[ItemStatus, int]:
    rows = session.exec(select(InboxItem.status, func.count()).group_by(InboxItem.status)).all()
    counts = dict.fromkeys(ItemStatus, 0)
    counts.update({ItemStatus(status): count for status, count in rows})
    return counts


def _href(*statuses: ItemStatus) -> str:
    return f"{LIST_PATH}?status={','.join(s.value for s in statuses)}"


def inbox_overview(session: Session) -> ModuleOverview:
    counts = status_counts(session)
    tiles = [
        Tile(
            "Dringend",
            counts[ItemStatus.NEEDS_MANUAL_REVIEW],
            _href(ItemStatus.NEEDS_MANUAL_REVIEW),
            "error",
            "Manuelle Prüfung nötig",
        ),
        Tile(
            "Zur Prüfung",
            counts[ItemStatus.PROCESSED],
            _href(ItemStatus.PROCESSED),
            "warning",
            "KI-Vorschlag liegt vor",
        ),
        Tile(
            "Neu", counts[ItemStatus.NEW], _href(ItemStatus.NEW), "info", "Noch nicht verarbeitet"
        ),
        Tile(
            "Freigegeben",
            counts[ItemStatus.APPROVED],
            _href(ItemStatus.APPROVED),
            "info",
            "Noch auszuführen",
        ),
    ]
    statement = (
        select(InboxItem, Classification)
        .join(Classification, isouter=True)
        .where(InboxItem.status == ItemStatus.NEEDS_MANUAL_REVIEW)
        .order_by(col(InboxItem.received_at), col(InboxItem.id))
        .limit(MAX_TODOS)
    )
    todos = [
        TodoItem(
            title=f"Eingang {item.id}: {item.filename or 'ohne Dateiname'}",
            detail=(
                f"{classification.document_type} · eingegangen "
                if classification
                else "eingegangen "
            )
            + item.received_at.strftime("%d.%m.%Y"),
            href=f"/items/{item.id}",
            tone="error",
            badge="Manuelle Prüfung",
        )
        for item, classification in session.exec(statement).all()
    ]
    return ModuleOverview(
        module="inbox",
        title="Posteingang",
        href=LIST_PATH,
        tiles=tiles,
        todos=todos,
        empty_hint="Keine Eingänge zur manuellen Prüfung.",
    )
