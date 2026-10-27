"""Pydantic schemas every LLM output of the social media module is validated against."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Hashtag = Annotated[str, StringConstraints(pattern=r"^#\w+$", max_length=60)]


class DraftOutput(BaseModel):
    """Post draft for one channel: body text and hashtags (separately)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000)
    hashtags: list[Hashtag] = Field(default_factory=list, max_length=30)
