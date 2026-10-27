"""Pydantic schemas every LLM output of the social media module is validated against."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Hashtag = Annotated[str, StringConstraints(pattern=r"^#\w+$", max_length=60)]


class DraftOutput(BaseModel):
    """Post draft for one channel: body text and hashtags (separately)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)
    hashtags: list[Hashtag] = Field(default_factory=list, max_length=30)


Quote = Annotated[str, StringConstraints(min_length=1, max_length=300)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class AssessmentFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Exact passage of the post the finding refers to.
    quote: Quote
    reason: Reason


class AssessmentOutput(BaseModel):
    """LLM assessment of a post: health claims, named persons, address form."""

    model_config = ConfigDict(extra="forbid")

    health_claims: list[AssessmentFinding] = Field(max_length=10)
    persons: list[AssessmentFinding] = Field(max_length=10)
    address_form: Literal["du", "sie", "neutral", "gemischt"]
