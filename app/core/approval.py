"""Human approval shared by all modules: nothing is executed without a review decision."""

from enum import StrEnum


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


DECISION_LABELS: dict[ReviewDecision, str] = {
    ReviewDecision.APPROVED: "freigegeben",
    ReviewDecision.REJECTED: "verworfen",
}
