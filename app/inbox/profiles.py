"""Inbox part of the industry profile: document types, fields, rules and actions."""

import re
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.core.profiles import KEY_PATTERN, PROFILES_DIR, active_profile_path, load_yaml_profile

# Compliance: profiles must not define fields that collect health data (SPEC section 7).
FORBIDDEN_HEALTH_TERMS = (
    "gesundheit",
    "krank",
    "diagnose",
    "attest",
    "medizin",
    "arzt",
    "verletz",
    "schwanger",
    "therapie",
    "behinderung",
    "health",
    "medical",
)

# Key of the extraction output listing requests beyond the profile fields (see
# app.inbox.llm.schemas); a profile field must not use it.
ADDITIONAL_REQUESTS_KEY = "weitere_anliegen"

FieldType = Literal["string", "date", "decimal", "boolean", "integer"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldSpec(_Strict):
    name: str = Field(pattern=KEY_PATTERN)
    type: FieldType
    description: str = Field(min_length=1)
    required: bool = False
    # Optional regular expression the value must match (string fields only).
    pattern: str | None = None
    # Optional closed list of allowed values (string fields only).
    allowed_values: list[str] | None = None

    @model_validator(mode="after")
    def _check_constraints(self) -> Self:
        if self.name == ADDITIONAL_REQUESTS_KEY:
            raise ValueError(f"field name '{ADDITIONAL_REQUESTS_KEY}' is reserved")
        if (self.pattern or self.allowed_values) and self.type != "string":
            raise ValueError("pattern/allowed_values are only allowed for string fields")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ValueError(f"invalid pattern: {error}") from error
        return self


class RuleRef(_Strict):
    rule: str = Field(pattern=KEY_PATTERN)
    params: dict[str, JsonValue] = Field(default_factory=dict)


RuleOutcomeName = Literal["ok", "warning", "error"]


class ActionSpec(_Strict):
    """Suggested next step. The first action whose conditions match is suggested."""

    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    # Jinja2 template for the reply draft (German); None if the action needs no reply.
    reply_template: str | None = None
    # Applies if any of these values (fields or computed rule data) is missing.
    when_missing: list[str] = Field(default_factory=list)
    # Applies if any listed rule has one of the given outcomes.
    when_rule: dict[str, list[RuleOutcomeName]] = Field(default_factory=dict)
    # Applies if any listed field has one of the given values, e.g. {dringend: [true]}.
    when_value: dict[str, list[str | bool | int]] = Field(default_factory=dict)

    @property
    def is_default(self) -> bool:
        return not self.when_missing and not self.when_rule and not self.when_value


class DocumentType(_Strict):
    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)
    rules: list[RuleRef] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        for kind, names in (
            ("field", [f.name for f in self.fields]),
            ("action", [a.key for a in self.actions]),
        ):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            if duplicates:
                raise ValueError(f"duplicate {kind} names: {', '.join(duplicates)}")
        if self.actions and not self.actions[-1].is_default:
            raise ValueError("the last action must have no conditions (default action)")
        return self

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)


class Profile(_Strict):
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    confidence_threshold: float = Field(ge=0, le=1)
    fallback_type: str
    document_types: list[DocumentType] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_profile(self) -> Self:
        keys = [t.key for t in self.document_types]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ValueError(f"duplicate document types: {', '.join(duplicates)}")
        if self.fallback_type not in keys:
            raise ValueError(f"fallback_type '{self.fallback_type}' is not a document type")
        for doc_type in self.document_types:
            for spec in doc_type.fields:
                text = " ".join([spec.name, spec.description, *(spec.allowed_values or [])])
                term = next((t for t in FORBIDDEN_HEALTH_TERMS if t in text.lower()), None)
                if term:
                    raise ValueError(
                        f"field '{doc_type.key}.{spec.name}' refers to health data ('{term}'), "
                        "which profiles must not collect"
                    )
        return self

    def document_type(self, key: str) -> DocumentType:
        for doc_type in self.document_types:
            if doc_type.key == key:
                return doc_type
        raise KeyError(key)

    @property
    def type_keys(self) -> list[str]:
        return [t.key for t in self.document_types]


def load_profile(path: Path) -> Profile:
    """Load and validate a profile file. Raises ProfileError with a readable message."""
    return load_yaml_profile(path, Profile)


def load_active_profile(profiles_dir: Path = PROFILES_DIR) -> Profile:
    """Load the profile selected via env `PROFILE` (default: fitness)."""
    return load_profile(active_profile_path(profiles_dir))
