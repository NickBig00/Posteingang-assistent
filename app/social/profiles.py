"""Industry and brand profile of the social media module (docs/SPEC_social.md, section 4).

- Industry profile: section `social:` in `profiles/<industry>.yaml` (env `PROFILE`).
- Brand profile: `<BRAND_PROFILES_DIR>/<BRAND_PROFILE>.yaml`. The directory defaults to
  `profiles/brand/` (fictitious demo profiles only); profiles of real customers are kept
  outside the repository and selected via `BRAND_PROFILES_DIR`.
"""

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.profiles import (
    KEY_PATTERN,
    PROFILES_DIR,
    ProfileError,
    active_profile_path,
    load_yaml_profile,
)
from app.social.facts import FactKey
from app.social.models import Channel

SECTION = "social"
DEFAULT_BRAND_PROFILE = "demo"
DEFAULT_BRAND_PROFILES_DIR = PROFILES_DIR / "brand"
HASHTAG_PATTERN = r"^#\w+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _duplicates(values: list[str]) -> list[str]:
    return sorted({v for v in values if values.count(v) > 1})


# --- industry profile (section `social:`) ---


class OccasionType(_Strict):
    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    # Facts the briefing form asks for; a briefing without them is rejected.
    required_facts: list[FactKey] = Field(default_factory=list)


class HealthClaimRules(_Strict):
    """Health claims in advertising: terms give a warning that must be confirmed;
    `certain_terms` are unambiguous healing promises (error in sensitive areas)."""

    terms: list[str] = Field(min_length=1)
    certain_terms: list[str] = Field(default_factory=list)
    # Terms that only count with a health context in the same sentence (e.g. "Garantie":
    # "Bestpreis-Garantie" is no health claim, "garantiert weniger Schmerzen" is one).
    context_only_terms: list[str] = Field(default_factory=list)
    # Words that make up such a health context (besides `terms` and `certain_terms`).
    context_terms: list[str] = Field(default_factory=list)


class TermRule(_Strict):
    """Industry-specific term list, e.g. before/after depictions."""

    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    terms: list[str] = Field(min_length=1)
    outcome: Literal["warning", "error"] = "warning"
    reason: str = Field(min_length=1)


class PhraseRule(_Strict):
    """If a fact is given, the post must contain one of the phrases (e.g. price incl. VAT)."""

    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    when_fact: FactKey
    phrases: list[str] = Field(min_length=1)
    outcome: Literal["warning", "error"] = "warning"
    reason: str = Field(min_length=1)


class CampaignStep(_Strict):
    """Step of a campaign plan, dated relative to the occasion (e.g. 7 days before)."""

    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    offset_days: int = Field(ge=-120, le=0)
    # Hint for the draft, e.g. "Erinnerung, die Anmeldung läuft noch".
    instruction: str = Field(min_length=1)


class IndustrySocial(_Strict):
    occasion_types: list[OccasionType] = Field(min_length=1)
    health_claims: HealthClaimRules | None = None
    term_rules: list[TermRule] = Field(default_factory=list)
    phrase_rules: list[PhraseRule] = Field(default_factory=list)
    # Area categories with stricter checks for healing promises (e.g. physio, reha).
    sensitive_areas: list[str] = Field(default_factory=list)
    # Plan of a campaign (S7); empty: no campaigns for this industry.
    campaign_steps: list[CampaignStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        for kind, keys in (
            ("occasion type", [o.key for o in self.occasion_types]),
            ("campaign step", [s.key for s in self.campaign_steps]),
            ("rule", [r.key for r in self.term_rules] + [r.key for r in self.phrase_rules]),
        ):
            duplicates = _duplicates(keys)
            if duplicates:
                raise ValueError(f"duplicate {kind} keys: {', '.join(duplicates)}")
        return self

    def occasion_type(self, key: str) -> OccasionType:
        for occasion in self.occasion_types:
            if occasion.key == key:
                return occasion
        raise KeyError(key)


# --- brand profile ---


class AddressForm(StrEnum):
    DU = "du"
    SIE = "sie"


ADDRESS_FORM_LABELS: dict[AddressForm, str] = {AddressForm.DU: "du", AddressForm.SIE: "Sie"}


class EmojiPolicy(_Strict):
    allowed: bool
    max: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.allowed and self.max:
            raise ValueError("max must be 0 if emojis are not allowed")
        if self.allowed and not self.max:
            raise ValueError("max must be at least 1 if emojis are allowed")
        return self


class Contact(_Strict):
    address: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    email: str = Field(min_length=3)
    website: str = Field(min_length=1)


class Area(_Strict):
    key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    # Category for industry rules (e.g. `physio` is a sensitive area); default: the key.
    category: str | None = Field(default=None, pattern=KEY_PATTERN)
    address_form: AddressForm
    tone: str = Field(min_length=1)
    channels: list[Channel] = Field(min_length=1)
    emojis: EmojiPolicy
    hashtags: list[str] = Field(default_factory=list)
    call_to_action: str | None = None
    examples: list[str] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def _check(self) -> Self:
        duplicates = _duplicates([c.value for c in self.channels])
        if duplicates:
            raise ValueError(f"duplicate channels: {', '.join(duplicates)}")
        _check_hashtags(self.hashtags)
        return self

    @property
    def rule_category(self) -> str:
        return self.category or self.key


def _check_hashtags(hashtags: list[str]) -> None:
    invalid = [tag for tag in hashtags if not re.fullmatch(HASHTAG_PATTERN, tag)]
    if invalid:
        raise ValueError(f"invalid hashtags (expected '#Wort'): {', '.join(invalid)}")


class BrandProfile(_Strict):
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # Industry profile the brand belongs to (must match env `PROFILE`).
    industry: str = Field(pattern=KEY_PATTERN)
    hashtags: list[str] = Field(default_factory=list)
    # Brand limit for hashtags per post (warning above it; platform limits give errors).
    max_hashtags: int = Field(ge=1, le=30)
    call_to_action: str = Field(min_length=1)
    contact: Contact
    forbidden_terms: list[str] = Field(default_factory=list)
    areas: list[Area] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        duplicates = _duplicates([a.key for a in self.areas])
        if duplicates:
            raise ValueError(f"duplicate areas: {', '.join(duplicates)}")
        _check_hashtags(self.hashtags)
        for area in self.areas:
            total = len(self.hashtags) + len(area.hashtags)
            if total > self.max_hashtags:
                raise ValueError(
                    f"area '{area.key}': {total} fixed hashtags exceed max_hashtags "
                    f"({self.max_hashtags})"
                )
        return self

    def area(self, key: str) -> Area:
        for area in self.areas:
            if area.key == key:
                return area
        raise KeyError(key)

    def address_form(self, area_key: str) -> AddressForm:
        return self.area(area_key).address_form

    def channels(self, area_key: str) -> list[Channel]:
        return list(self.area(area_key).channels)

    def fixed_hashtags(self, area_key: str) -> list[str]:
        return [*self.hashtags, *self.area(area_key).hashtags]

    def call_to_action_for(self, area_key: str) -> str:
        return self.area(area_key).call_to_action or self.call_to_action


# --- loading ---


@dataclass(frozen=True)
class SocialConfig:
    """Industry rules and brand profile that belong together."""

    industry_key: str
    industry: IndustrySocial
    brand: BrandProfile

    def is_sensitive(self, area_key: str) -> bool:
        return self.brand.area(area_key).rule_category in self.industry.sensitive_areas


def load_industry_social(path: Path) -> IndustrySocial:
    return load_yaml_profile(path, IndustrySocial, section=SECTION)


def load_brand_profile(path: Path) -> BrandProfile:
    return load_yaml_profile(path, BrandProfile)


def brand_profiles_dir() -> Path:
    return Path(os.environ.get("BRAND_PROFILES_DIR") or DEFAULT_BRAND_PROFILES_DIR)


def active_brand_key() -> str:
    return os.environ.get("BRAND_PROFILE") or DEFAULT_BRAND_PROFILE


def active_brand_path() -> Path:
    key = active_brand_key()
    if not re.fullmatch(KEY_PATTERN, key):
        raise ProfileError(f"Ungültiger Name des Markenprofils: {key!r}")
    return brand_profiles_dir() / f"{key}.yaml"


def load_social_config(
    industry_path: Path | None = None, brand_path: Path | None = None
) -> SocialConfig:
    """Load the active industry and brand profile and check that they fit together."""
    industry_path = industry_path or active_profile_path()
    brand_path = brand_path or active_brand_path()
    industry = load_industry_social(industry_path)
    brand = load_brand_profile(brand_path)
    industry_key = industry_path.stem
    if brand.industry != industry_key:
        raise ProfileError(
            f"Markenprofil '{brand_path}' gehört zur Branche '{brand.industry}', "
            f"aktiv ist das Branchenprofil '{industry_key}'"
        )
    return SocialConfig(industry_key=industry_key, industry=industry, brand=brand)
