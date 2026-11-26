"""What the briefing page shows per channel tab (SPEC_social 8.1): one review list, checklist.

The page follows the way people work: ① text → ② image → ③ check and approve → ④ publish.
This module gathers the check results of post text, text on the image, AI image hints, consent
of the images in use, the rendered post image and the carousel into one sorted list (errors →
open warnings → notes → confirmed → ok) and derives the short checklist "Noch offen" with links
to the place where each item is resolved. No decisions are made here: the approval itself is
still decided by `approval_blockers`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlmodel import Session

from app.core.outcomes import Outcome
from app.social.audit import briefing_audit_trail
from app.social.briefing_image import briefing_image, chosen_asset
from app.social.carousel import carousel_checks
from app.social.images import HINT_LABELS, chosen_image, prompt_hints
from app.social.infographic import get_info_graphic, graphic_checks
from app.social.models import Briefing, PostDraft, PostStatus
from app.social.post_image import PostImageError, channel_format, render_post_image
from app.social.profiles import SocialConfig
from app.social.review import approval_blockers, check_results

Status = Literal["error", "open", "note", "confirmed", "ok"]
STATUS_ORDER: dict[Status, int] = {"error": 0, "open": 1, "note": 2, "confirmed": 3, "ok": 4}
RELEASED = frozenset({PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED})
EDITABLE = frozenset({PostStatus.DRAFT, PostStatus.IN_REVIEW, PostStatus.NEEDS_MANUAL_REVIEW})

# Status symbol of a channel tab (SPEC_social 8.1) with its tone (docs/DESIGN.md).
TAB_SYMBOLS: dict[PostStatus, tuple[str, str]] = {
    PostStatus.DRAFT: ("●", "neutral"),
    PostStatus.IN_REVIEW: ("●", "warning"),
    PostStatus.NEEDS_MANUAL_REVIEW: ("!", "error"),
    PostStatus.APPROVED: ("✓", "ok"),
    PostStatus.SCHEDULED: ("◷", "info"),
    PostStatus.PUBLISHED: ("✓✓", "ok"),
    PostStatus.REJECTED: ("–", "neutral"),
}


@dataclass(frozen=True)
class ReviewItem:
    """One line of step ③: a check result with at most one action."""

    uid: str
    source: str
    label: str
    status: Status
    reason: str
    excerpt: str | None = None
    is_ai: bool = False
    action_url: str | None = None
    action_label: str | None = None
    # Question shown before the action (consent of a named person).
    confirm_text: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True)
class ChecklistEntry:
    label: str
    href: str
    # A hint, not a blocker (e.g. "Bild wählen": a post without image is possible).
    optional: bool = False


@dataclass
class DraftFlow:
    draft: PostDraft
    items: list[ReviewItem]
    checklist: list[ChecklistEntry]
    blockers: list[str]
    released: bool
    editable: bool
    image_problems: list[str] = field(default_factory=list)
    # Why the AI delivered no text (provider error of the last attempt, e.g. expired key).
    ai_error: str | None = None

    @property
    def open_items(self) -> list[ReviewItem]:
        return [i for i in self.items if i.status in ("error", "open", "note", "confirmed")]

    @property
    def ok_items(self) -> list[ReviewItem]:
        return [i for i in self.items if i.status == "ok"]

    @property
    def to_do(self) -> int:
        return sum(1 for i in self.items if i.status in ("error", "open"))

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def tab_symbol(self) -> tuple[str, str]:
        if self.draft.status in EDITABLE and any(i.status == "error" for i in self.items):
            return "!", "error"
        return TAB_SYMBOLS[self.draft.status]


CONSENT_QUESTION = (
    "Nur bestätigen, wenn die genannte Person der Nennung ausdrücklich zugestimmt hat und die "
    "Einwilligung dokumentiert ist."
)


def _status(
    outcome: Outcome, requires_confirmation: bool, consent: bool, confirmed: bool
) -> Status:
    if outcome is Outcome.ERROR:
        if consent:
            return "confirmed" if confirmed else "open"
        return "error"
    if outcome is Outcome.WARNING:
        if requires_confirmation:
            return "confirmed" if confirmed else "open"
        return "note"
    return "ok"


def _post_items(session: Session, draft: PostDraft, labels: dict[str, str]) -> list[ReviewItem]:
    items = []
    for r in check_results(session, draft.id or 0):
        consent = r.consent_required and r.outcome is Outcome.ERROR
        status = _status(r.outcome, r.requires_confirmation, consent, r.confirmed_at is not None)
        action = None
        if status == "open" and draft.status in EDITABLE:
            action = f"/social/checks/{r.id}/{'consent' if consent else 'confirm'}"
        items.append(
            ReviewItem(
                uid=f"check-{r.id}",
                source="Beitragstext",
                label=labels.get(r.check_key, r.check_key),
                status=status,
                reason=r.reason,
                excerpt=r.excerpt,
                is_ai=r.is_ai_assessment,
                action_url=action,
                action_label="Einwilligung liegt vor" if consent else "Bestätigen",
                confirm_text=CONSENT_QUESTION if consent else None,
                confirmed_by=r.confirmed_by,
                confirmed_at=r.confirmed_at,
            )
        )
    return items


def _image_items(
    session: Session, config: SocialConfig, briefing: Briefing, draft: PostDraft,
    labels: dict[str, str],
) -> tuple[list[ReviewItem], list[str]]:  # fmt: skip
    """Items of the post image: text on the image, AI hints, consent, rendering."""
    image = briefing_image(session, briefing.id or 0)
    if image is None:
        return [], []
    editable = draft.status in EDITABLE
    items: list[ReviewItem] = []
    problems: list[str] = []
    try:
        render_post_image(session, config, briefing, channel_format(draft))
    except PostImageError as error:
        if not error.from_checks:
            problems = error.problems
    for number, problem in enumerate(problems):
        items.append(ReviewItem(f"image-{number}", "Bild", "Beitragsbild", "error", problem))
    if image.with_text:
        graphic = get_info_graphic(session, briefing.id or 0)
        for r in graphic_checks(session, graphic.id or 0) if graphic else []:
            consent = r.consent_required and r.outcome is Outcome.ERROR
            status = _status(
                r.outcome, r.requires_confirmation, consent, r.confirmed_at is not None
            )
            items.append(
                ReviewItem(
                    uid=f"graphic-{r.id}",
                    source="Text auf dem Bild",
                    label=labels.get(r.check_key, r.check_key),
                    status=status,
                    reason=r.reason,
                    excerpt=r.excerpt,
                    is_ai=r.is_ai_assessment,
                    action_url=f"/social/info-graphic-checks/{r.id}/confirm"
                    if status == "open" and editable
                    else None,
                    action_label="Einwilligung liegt vor" if consent else "Bestätigen",
                    confirm_text=CONSENT_QUESTION if consent else None,
                    confirmed_by=r.confirmed_by,
                    confirmed_at=r.confirmed_at,
                )
            )
    if chosen_image(session, briefing.id or 0) is not None:
        for h in prompt_hints(session, briefing.id or 0):
            # Only a member name needs the consent; every other hint is just listed.
            status = ("confirmed" if h.confirmed_at else "open") if h.consent_required else "note"
            items.append(
                ReviewItem(
                    uid=f"hint-{h.id}",
                    source="KI-Bild",
                    label=HINT_LABELS.get(h.key, h.key),
                    status=status,
                    reason=h.reason,
                    action_url=f"/social/image-hints/{h.id}/confirm"
                    if status == "open" and editable
                    else None,
                    action_label="Einwilligung liegt vor" if h.consent_required else "Verstanden",
                    confirm_text=CONSENT_QUESTION if h.consent_required else None,
                    confirmed_by=h.confirmed_by,
                    confirmed_at=h.confirmed_at,
                )
            )
    asset = chosen_asset(session, briefing.id or 0)
    if asset is not None:
        if asset.consent_revoked_at is not None:
            items.append(
                ReviewItem(
                    f"asset-{asset.id}-revoked",
                    "Bild",
                    "Einwilligung",
                    "error",
                    "Die Einwilligung wurde widerrufen: bitte ein anderes Bild wählen.",
                )
            )
        elif asset.shows_persons:
            items.append(
                ReviewItem(
                    uid=f"asset-{asset.id}-consent",
                    source="Bild",
                    label="Personen im Bild",
                    status="confirmed" if asset.consent_confirmed else "open",
                    reason="Das Foto zeigt Personen: Alle abgebildeten Personen müssen der "
                    "Veröffentlichung zugestimmt haben.",
                    action_url=f"/social/assets/{asset.id}/consent"
                    if not asset.consent_confirmed and editable
                    else None,
                    action_label="Einwilligung liegt vor",
                    confirm_text="Liegt die Einwilligung aller abgebildeten Personen vor?",
                    confirmed_by=asset.consent_by,
                    confirmed_at=asset.consent_at,
                )
            )
        if asset.edit_hint and asset.consent_revoked_at is None:
            items.append(
                ReviewItem(
                    uid=f"asset-{asset.id}-edit",
                    source="Bild",
                    label="KI-Bearbeitung",
                    status="note",
                    reason=asset.edit_hint,
                )
            )
    return items, problems


def _carousel_items(session: Session, draft: PostDraft, labels: dict[str, str]) -> list[ReviewItem]:
    if not draft.carousel_enabled:
        return []
    items = []
    for r in carousel_checks(session, draft.briefing_id):
        consent = r.consent_required and r.outcome is Outcome.ERROR
        status = _status(r.outcome, r.requires_confirmation, consent, r.confirmed_at is not None)
        items.append(
            ReviewItem(
                uid=f"carousel-{r.id}",
                source=f"Karussell, Seite {r.page}" if r.page else "Karussell",
                label=labels.get(r.check_key, r.check_key),
                status=status,
                reason=r.reason,
                is_ai=r.is_ai_assessment,
                action_url=f"/social/carousel-checks/{r.id}/confirm"
                if status == "open" and draft.status in EDITABLE
                else None,
                action_label="Einwilligung liegt vor" if consent else "Bestätigen",
                confirm_text=CONSENT_QUESTION if consent else None,
                confirmed_by=r.confirmed_by,
                confirmed_at=r.confirmed_at,
            )
        )
    return items


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def checklist(flow_items: list[ReviewItem], draft: PostDraft, blockers: list[str],
              has_image: bool, image_problems: list[str]) -> list[ChecklistEntry]:  # fmt: skip
    """ "Noch offen": what is missing until the approval, each with a link to its place."""
    if draft.status not in EDITABLE:
        return []
    entries: list[ChecklistEntry] = []
    if not draft.text:
        entries.append(ChecklistEntry("Text schreiben", f"#text-{draft.id}"))
    # Without text the checks of the post text only repeat "missing" (facts, length): they
    # are left out until a text is there; "Text schreiben" covers them.
    relevant = [i for i in flow_items if draft.text or i.source != "Beitragstext"]
    errors = [i for i in relevant if i.status == "error" and i.source != "Bild"]
    open_items = [i for i in relevant if i.status == "open"]
    if errors:
        entries.append(
            ChecklistEntry(
                f"{len(errors)} Fehler beheben",
                f"#{errors[0].uid}",
            )
        )
    if open_items:
        entries.append(
            ChecklistEntry(
                _plural(len(open_items), "Hinweis bestätigen", "Hinweise bestätigen"),
                f"#{open_items[0].uid}",
            )
        )
    if image_problems:
        entries.append(ChecklistEntry("Bild prüfen", "#bild"))
    if not has_image:
        entries.append(ChecklistEntry("Bild wählen", "#bild", optional=True))
    required = [e for e in entries if not e.optional]
    if blockers and not required:
        # Anything else that blocks (e.g. not checked yet, wrong status): show it plainly.
        entries.insert(0, ChecklistEntry(blockers[0], f"#pruefen-{draft.id}"))
    return entries


TEXT_STEPS = ("social.post_draft", "social.post_revise")


def last_text_error(session: Session, draft: PostDraft) -> str | None:
    """Error of the last AI attempt at the post text, if that attempt failed.

    Comes from the audit trail (the provider message never contains the API key).
    """
    attempts = [
        e
        for e in briefing_audit_trail(session, draft.briefing_id)
        if e.step in TEXT_STEPS and e.model_params.get("draft_id") == draft.id
    ]
    if not attempts or not attempts[-1].error:
        return None
    return attempts[-1].error.removeprefix("LLMProviderError: ")


def draft_flow(
    session: Session, config: SocialConfig, draft: PostDraft, labels: dict[str, str]
) -> DraftFlow:
    briefing = session.get(Briefing, draft.briefing_id)
    if briefing is None:
        raise ValueError("Briefing nicht gefunden.")
    image_items, problems = _image_items(session, config, briefing, draft, labels)
    items = [
        *_post_items(session, draft, labels),
        *image_items,
        *_carousel_items(session, draft, labels),
    ]
    items.sort(key=lambda i: STATUS_ORDER[i.status])
    editable = draft.status in EDITABLE
    blockers = approval_blockers(session, draft, config) if editable else []
    has_image = briefing_image(session, briefing.id or 0) is not None
    return DraftFlow(
        ai_error=None if draft.text else last_text_error(session, draft),
        draft=draft,
        items=items,
        checklist=checklist(items, draft, blockers, has_image, problems),
        blockers=blockers,
        released=draft.status in RELEASED,
        editable=editable,
        image_problems=problems,
    )
