"""Pydantic schemas every LLM output is validated against."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    create_model,
)

from app.inbox.profiles import ADDITIONAL_REQUESTS_KEY, DocumentType, FieldSpec


class ClassificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str
    confidence: float = Field(ge=0, le=1)


def _matches(pattern: str) -> AfterValidator:
    def check(value: str) -> str:
        if not re.fullmatch(pattern, value):
            raise ValueError(f"does not match pattern {pattern}")
        return value

    return AfterValidator(check)


def _require_string(value: object) -> object:
    # Dates and amounts must be JSON strings: no timestamps, no float rounding.
    if not isinstance(value, str):
        raise ValueError("must be given as a string")
    return value


_StrictDate = Annotated[date, BeforeValidator(_require_string)]
_Amount = Annotated[Decimal, BeforeValidator(_require_string), Field(decimal_places=2)]


def _python_type(spec: FieldSpec) -> object:
    if spec.type == "string":
        if spec.allowed_values:
            return Literal[tuple(spec.allowed_values)]
        if spec.pattern:
            return Annotated[str, _matches(spec.pattern)]
        return str
    return {
        "date": _StrictDate,
        "decimal": _Amount,
        "boolean": StrictBool,
        "integer": StrictInt,
    }[spec.type]


# Short topic of a request the profile fields do not cover, e.g. "Preise".
_RequestTopic = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
MAX_ADDITIONAL_REQUESTS = 10


def extraction_model(doc_type: DocumentType) -> type[BaseModel]:
    """Build a strict schema for the fields of `doc_type`: every key present, value or null.

    Additionally `weitere_anliegen`: topics of further requests in the document that the
    fields do not cover. Optional (default: none), so a missing key is not an error.
    """
    fields: dict[str, tuple[object, object]] = {
        spec.name: (_python_type(spec) | None, Field(...)) for spec in doc_type.fields
    }
    fields[ADDITIONAL_REQUESTS_KEY] = (
        list[_RequestTopic],
        Field(default_factory=list, max_length=MAX_ADDITIONAL_REQUESTS),
    )
    return create_model(  # type: ignore[call-overload,no-any-return]
        f"Extraction_{doc_type.key}",
        __config__=ConfigDict(extra="forbid", strict=False),
        **fields,
    )
