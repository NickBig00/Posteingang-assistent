"""Result levels of deterministic checks, shared by all modules (traffic light)."""

from collections.abc import Iterable
from enum import StrEnum


class Outcome(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.OK: "ok",
    Outcome.WARNING: "Warnung",
    Outcome.ERROR: "Fehler",
}


def worst_outcome(outcomes: Iterable[Outcome]) -> Outcome | None:
    """`error` > `warning` > `ok`; None if there is no outcome at all."""
    present = set(outcomes)
    for outcome in (Outcome.ERROR, Outcome.WARNING, Outcome.OK):
        if outcome in present:
            return outcome
    return None
