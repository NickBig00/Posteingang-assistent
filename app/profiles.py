"""Industry profiles: document types, fields, rules and actions as YAML configuration."""

import os
import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
DEFAULT_PROFILE = "fitness"

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
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


class ActionSpec(_Strict):
    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    # Jinja2 template for the reply draft (German); None if the action needs no reply.
    reply_template: str | None = None


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


class ProfileError(ValueError):
    """Raised when a profile file is missing, not valid YAML or violates the schema."""


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def load_profile(path: Path) -> Profile:
    """Load and validate a profile file. Raises ProfileError with a readable message."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProfileError(f"Profil '{path}' nicht gefunden") from error
    except yaml.YAMLError as error:
        raise ProfileError(f"Profil '{path}' ist kein gültiges YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ProfileError(f"Profil '{path}' muss ein YAML-Objekt sein")
    try:
        return Profile.model_validate(raw)
    except ValidationError as error:
        details = _format_validation_error(error)
        raise ProfileError(f"Profil '{path}' ist ungültig:\n{details}") from error


def active_profile_key() -> str:
    return os.environ.get("PROFILE", DEFAULT_PROFILE)


def load_active_profile(profiles_dir: Path = PROFILES_DIR) -> Profile:
    """Load the profile selected via env `PROFILE` (default: fitness)."""
    key = active_profile_key()
    if not re.fullmatch(KEY_PATTERN, key):
        raise ProfileError(f"Ungültiger Profilname: {key!r}")
    return load_profile(profiles_dir / f"{key}.yaml")
