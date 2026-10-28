"""Human approval shared by all modules: nothing is executed without a review decision."""

from collections.abc import Mapping
from enum import StrEnum


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


DECISION_LABELS: dict[ReviewDecision, str] = {
    ReviewDecision.APPROVED: "freigegeben",
    ReviewDecision.REJECTED: "verworfen",
}


class InvalidStatusTransitionError(ValueError):
    """Raised for a status change that the module's status machine does not allow."""


def check_transition[S](allowed: Mapping[S, frozenset[S]], current: S, target: S) -> None:
    """Raise InvalidStatusTransitionError unless `current -> target` is allowed."""
    if target not in allowed.get(current, frozenset()):
        raise InvalidStatusTransitionError(f"transition {current} -> {target} is not allowed")
