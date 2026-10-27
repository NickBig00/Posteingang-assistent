from pathlib import Path

import pytest
import yaml

from app.core.profiles import PROFILES_DIR, ProfileError
from app.inbox.profiles import load_active_profile, load_profile

FITNESS = PROFILES_DIR / "fitness.yaml"


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "profil.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _fitness_data() -> dict:
    return yaml.safe_load(FITNESS.read_text(encoding="utf-8"))


def test_fitness_profile_is_valid_and_matches_spec() -> None:
    profile = load_profile(FITNESS)

    assert profile.type_keys == [
        "kuendigung",
        "probetraining_anfrage",
        "vertragspause",
        "rechnung",
        "sonstiges",
    ]
    assert profile.fallback_type == "sonstiges"
    assert 0 < profile.confidence_threshold < 1
    pause = profile.document_type("vertragspause")
    assert pause.field("grund_kategorie").allowed_values == ["beruflich", "reise", "sonstiges"]
    assert {"required_fields", "pause_period"} <= {r.rule for r in pause.rules}


def test_profile_fields_match_sample_labels() -> None:
    import json

    profile = load_profile(FITNESS)
    labels = json.loads((PROFILES_DIR.parent / "samples/fitness/labels.json").read_text())
    for item in labels["items"]:
        doc_type = profile.document_type(item["expected_type"])
        assert set(item["expected_fields"]) <= {f.name for f in doc_type.fields}


def test_active_profile_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROFILE", "fitness")

    assert load_active_profile().key == "fitness"


@pytest.mark.parametrize("name", ["../etc/passwd", "Fitness", "fit ness"])
def test_invalid_profile_name_is_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("PROFILE", name)

    with pytest.raises(ProfileError, match="Ungültiger Profilname"):
        load_active_profile()


def test_missing_profile_file(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="nicht gefunden"):
        load_profile(tmp_path / "gibtsnicht.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "kaputt.yaml"
    path.write_text("key: [unclosed", encoding="utf-8")

    with pytest.raises(ProfileError, match="kein gültiges YAML"):
        load_profile(path)


def test_non_mapping_yaml(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="YAML-Objekt"):
        load_profile(_write(tmp_path, ["liste"]))


def test_missing_required_key_names_the_location(tmp_path: Path) -> None:
    data = _fitness_data()
    del data["document_types"][0]["description"]

    with pytest.raises(ProfileError) as info:
        load_profile(_write(tmp_path, data))

    assert "document_types.0.description: Field required" in str(info.value)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["document_types"][0]["fields"][0]["pflicht"] = True

    with pytest.raises(ProfileError, match="fields.0.pflicht: Extra inputs are not permitted"):
        load_profile(_write(tmp_path, data))


def test_invalid_field_type_is_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["document_types"][0]["fields"][0]["type"] = "text"

    with pytest.raises(ProfileError, match="fields.0.type"):
        load_profile(_write(tmp_path, data))


def test_duplicate_document_types_are_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["document_types"].append(data["document_types"][0])

    with pytest.raises(ProfileError, match="duplicate document types: kuendigung"):
        load_profile(_write(tmp_path, data))


def test_duplicate_fields_are_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    fields = data["document_types"][0]["fields"]
    fields.append(fields[0])

    with pytest.raises(ProfileError, match="duplicate field names: name"):
        load_profile(_write(tmp_path, data))


def test_unknown_fallback_type_is_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["fallback_type"] = "unbekannt"

    with pytest.raises(ProfileError, match="fallback_type 'unbekannt'"):
        load_profile(_write(tmp_path, data))


def test_invalid_pattern_is_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["document_types"][0]["fields"][1]["pattern"] = "M-(\\d"

    with pytest.raises(ProfileError, match="invalid pattern"):
        load_profile(_write(tmp_path, data))


def test_pattern_on_non_string_field_is_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["document_types"][0]["fields"][2]["pattern"] = "\\d+"

    with pytest.raises(ProfileError, match="only allowed for string fields"):
        load_profile(_write(tmp_path, data))


def test_threshold_out_of_range_is_rejected(tmp_path: Path) -> None:
    data = _fitness_data()
    data["confidence_threshold"] = 1.5

    with pytest.raises(ProfileError, match="confidence_threshold"):
        load_profile(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "gesundheitszustand"),
        ("description", "Ärztliches Attest zur Pause"),
        ("allowed_values", ["reise", "krankheit"]),
    ],
)
def test_health_related_fields_are_rejected(tmp_path: Path, field: str, value: object) -> None:
    data = _fitness_data()
    spec = data["document_types"][2]["fields"][4]
    spec[field] = value

    with pytest.raises(ProfileError, match="health data"):
        load_profile(_write(tmp_path, data))
