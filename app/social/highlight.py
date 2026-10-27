"""Marking the passages of check findings in the post text (HTML-escaped)."""

from collections.abc import Sequence

from markupsafe import Markup, escape

from app.core.outcomes import Outcome
from app.social.models import CheckResult

_CLASSES = {
    Outcome.ERROR: "rounded bg-red-200 px-0.5",
    Outcome.WARNING: "rounded bg-amber-200 px-0.5",
}


def highlight(text: str, results: Sequence[CheckResult]) -> Markup:
    """`text` with every non-ok finding's span wrapped in <mark>; overlapping spans merge
    (the worse outcome wins)."""
    spans: list[tuple[int, int, Outcome, list[str]]] = []
    for result in sorted(
        (r for r in results if r.outcome != Outcome.OK and r.span_start is not None),
        key=lambda r: (r.span_start or 0, r.span_end or 0),
    ):
        start, end = result.span_start or 0, min(result.span_end or 0, len(text))
        if start >= end:
            continue
        if spans and start < spans[-1][1]:
            prev_start, prev_end, outcome, reasons = spans[-1]
            worse = Outcome.ERROR if Outcome.ERROR in (outcome, result.outcome) else outcome
            spans[-1] = (prev_start, max(prev_end, end), worse, [*reasons, result.reason])
        else:
            spans.append((start, end, result.outcome, [result.reason]))
    parts: list[str] = []
    position = 0
    for start, end, outcome, reasons in spans:
        parts.append(str(escape(text[position:start])))
        title = escape(" | ".join(reasons))
        parts.append(
            f'<mark class="{_CLASSES[outcome]}" title="{title}" data-outcome="{outcome.value}">'
            f"{escape(text[start:end])}</mark>"
        )
        position = end
    parts.append(str(escape(text[position:])))
    return Markup("".join(parts))  # noqa: S704 - every text part is escaped above
