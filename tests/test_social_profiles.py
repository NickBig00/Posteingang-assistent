"""Industry profile section `social:` and brand profiles (SPEC_social section 4)."""

from pathlib import Path

import pytest
import yaml

from app.core.profiles import PROFILES_DIR, ProfileError
from app.inbox.profiles import load_profile
from app.social.models import Channel
from app.social.profiles import (
    DEFAULT_BRAND_PROFILES_DIR,
    AddressForm,
    active_brand_path,
    load_brand_profile,
    load_industry_social,
    load_social_config,
)

FITNESS = PROFILES_DIR / "fitness.yaml"
DEMO_BRAND = DEFAULT_BRAND_PROFILES_DIR / "demo.yaml"


def _brand_data() -> dict:
    return yaml.safe_load(DEMO_BRAND.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict, name: str = "brand.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# --- industry profile ---


def test_fitness_social_section() -> None:
    industry = load_industry_social(FITNESS)

    keys = [o.key for o in industry.occasion_types]
    assert keys == [
        "kurs",
        "aktion",
        "probetraining",
        "veranstaltung",
        "gesundheitstipp",
        "team",
        "oeffnungszeiten",
        "stellenangebot",
    ]
    assert industry.occasion_type("kurs").required_facts == ["datum", "uhrzeit", "ort"]
    assert industry.occasion_type("aktion").required_facts == ["zeitraum", "preis"]
    assert industry.health_claims is not None
    assert "heilt" in industry.health_claims.certain_terms
    assert [r.key for r in industry.term_rules] == ["vorher_nachher"]
    assert industry.sensitive_areas == ["physio", "reha"]


def test_inbox_profile_ignores_social_section() -> None:
    # The inbox schema forbids unknown keys; the module section must not break it.
    assert load_profile(FITNESS).key == "fitness"


def test_missing_social_section(tmp_path: Path) -> None:
    path = _write(tmp_path, {"key": "x"}, "industry.yaml")

    with pytest.raises(ProfileError, match="keinen gültigen Abschnitt 'social:'"):
        load_industry_social(path)


@pytest.mark.parametrize(
    ("social", "message"),
    [
        ({"occasion_types": []}, "social.occasion_types"),
        (
            {"occasion_types": [{"key": "kurs", "label": "Kurs", "required_facts": ["farbe"]}]},
            "social.occasion_types.0.required_facts.0",
        ),
        (
            {"occasion_types": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]},
            "duplicate occasion type keys: a",
        ),
        (
            {"occasion_types": [{"key": "a", "label": "A"}], "health_claims": {"terms": []}},
            "social.health_claims.terms",
        ),
        ({"occasion_types": [{"key": "a", "label": "A"}], "unbekannt": 1}, "social.unbekannt"),
    ],
)
def test_invalid_industry_section(tmp_path: Path, social: dict, message: str) -> None:
    path = _write(tmp_path, {"key": "x", "social": social}, "industry.yaml")

    with pytest.raises(ProfileError) as excinfo:
        load_industry_social(path)

    assert message in str(excinfo.value)
    assert "ist ungültig" in str(excinfo.value)


# --- brand profile ---


def test_demo_brand_profile() -> None:
    brand = load_brand_profile(DEMO_BRAND)

    assert brand.industry == "fitness"
    assert [a.key for a in brand.areas] == ["fitness", "kurse", "physio", "reha", "firmenfitness"]
    assert all(2 <= len(a.examples) <= 3 for a in brand.areas)
    assert brand.contact.email.endswith(".example")


@pytest.mark.parametrize(
    ("area", "address_form", "channels"),
    [
        (
            "fitness",
            AddressForm.DU,
            [Channel.INSTAGRAM_POST, Channel.INSTAGRAM_STORY, Channel.FACEBOOK],
        ),
        ("kurse", AddressForm.DU, [Channel.INSTAGRAM_POST, Channel.FACEBOOK]),
        ("physio", AddressForm.SIE, [Channel.FACEBOOK, Channel.INSTAGRAM_POST]),
        ("reha", AddressForm.SIE, [Channel.FACEBOOK]),
        ("firmenfitness", AddressForm.SIE, [Channel.LINKEDIN]),
    ],
)
def test_area_mapping(area: str, address_form: AddressForm, channels: list[Channel]) -> None:
    brand = load_brand_profile(DEMO_BRAND)

    assert brand.address_form(area) is address_form
    assert brand.channels(area) == channels


def test_area_specific_settings() -> None:
    brand = load_brand_profile(DEMO_BRAND)

    assert brand.area("fitness").emojis.max == 3
    assert not brand.area("physio").emojis.allowed
    assert brand.fixed_hashtags("fitness") == [
        "#DemoGesundheitszentrum",
        "#Musterstadt",
        "#Fitness",
    ]
    assert brand.call_to_action_for("fitness") == "14 Tage kostenlos probetrainieren"
    assert brand.call_to_action_for("firmenfitness").startswith("Jetzt ein unverbindliches")


def test_unknown_area() -> None:
    with pytest.raises(KeyError):
        load_brand_profile(DEMO_BRAND).area("sauna")


def _broken(mutate: str) -> dict:
    data = _brand_data()
    area = data["areas"][0]
    match mutate:
        case "address_form":
            area["address_form"] = "ihr"
        case "channel":
            area["channels"] = ["tiktok"]
        case "no_channels":
            area["channels"] = []
        case "duplicate_channel":
            area["channels"] = ["facebook", "facebook"]
        case "one_example":
            area["examples"] = ["nur einer"]
        case "emojis":
            area["emojis"] = {"allowed": False, "max": 2}
        case "hashtag":
            area["hashtags"] = ["Fitness"]
        case "too_many_hashtags":
            data["max_hashtags"] = 2
        case "duplicate_area":
            data["areas"].append(dict(area))
        case "missing_contact":
            del data["contact"]
        case "unknown_key":
            data["farbe"] = "blau"
    return data


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ("address_form", "areas.0.address_form"),
        ("channel", "areas.0.channels.0"),
        ("no_channels", "areas.0.channels"),
        ("duplicate_channel", "duplicate channels: facebook"),
        ("one_example", "areas.0.examples"),
        ("emojis", "max must be 0"),
        ("hashtag", "invalid hashtags"),
        ("too_many_hashtags", "exceed max_hashtags"),
        ("duplicate_area", "duplicate areas: fitness"),
        ("missing_contact", "contact: Field required"),
        ("unknown_key", "farbe: Extra inputs are not permitted"),
    ],
)
def test_invalid_brand_profile(tmp_path: Path, mutate: str, message: str) -> None:
    path = _write(tmp_path, _broken(mutate))

    with pytest.raises(ProfileError) as excinfo:
        load_brand_profile(path)

    assert message in str(excinfo.value)


# --- active configuration ---


def test_social_config_of_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PROFILE", "BRAND_PROFILE", "BRAND_PROFILES_DIR"):
        monkeypatch.delenv(name, raising=False)

    config = load_social_config()

    assert config.industry_key == "fitness"
    assert config.brand.key == "demo"
    assert config.is_sensitive("physio")
    assert config.is_sensitive("reha")
    assert not config.is_sensitive("fitness")


def test_brand_profiles_dir_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _brand_data() | {"key": "kunde", "name": "Kunde (fiktiv)"}
    _write(tmp_path, data, "kunde.yaml")
    monkeypatch.setenv("BRAND_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("BRAND_PROFILE", "kunde")
    monkeypatch.delenv("PROFILE", raising=False)

    assert active_brand_path() == tmp_path / "kunde.yaml"
    assert load_social_config().brand.name == "Kunde (fiktiv)"


def test_brand_must_match_industry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, _brand_data() | {"industry": "handwerk"})
    monkeypatch.delenv("PROFILE", raising=False)

    with pytest.raises(ProfileError, match="gehört zur Branche 'handwerk'"):
        load_social_config(brand_path=path)


@pytest.mark.parametrize("name", ["../geheim", "Demo"])
def test_invalid_brand_name(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("BRAND_PROFILE", name)

    with pytest.raises(ProfileError, match="Ungültiger Name des Markenprofils"):
        active_brand_path()


def test_real_customer_profiles_are_git_ignored() -> None:
    lines = (PROFILES_DIR.parent / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "profiles/brand/*" in lines
    assert "!profiles/brand/demo.yaml" in lines
