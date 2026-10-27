import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from app.core.profiles import PROFILES_DIR
from app.inbox.profiles import Profile, load_profile

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
DATA_SUFFIXES = {".eml", ".pdf"}
PROFILES = ["fitness", "handwerk"]
GENERATORS = {"fitness": "generate_samples.py", "handwerk": "generate_samples_handwerk.py"}

ALLOWED_MAIL_DOMAIN = re.compile(r"(^|\.)example\.(com|org|net)$")
EMAIL_PATTERN = re.compile(rb"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# Digit sequences that look like phone numbers; only the fictional range 030 23125 xxx is ok.
PHONE_PATTERN = re.compile(r"\b0\d{2,4}[ /-]?\d{3,}[ -]?\d*\b")
HEALTH_TERMS = ("krank", "gesundheit", "diagnose", "attest", "verletz", "schwanger", "arzt")


def _dir(profile_key: str) -> Path:
    return ROOT / "samples" / profile_key


def _labels(profile_key: str) -> dict:
    return json.loads((_dir(profile_key) / "labels.json").read_text(encoding="utf-8"))


def _items(profile_key: str) -> list[dict]:
    return _labels(profile_key)["items"]


def _profile(profile_key: str) -> Profile:
    return load_profile(PROFILES_DIR / f"{profile_key}.yaml")


def _sample_files(profile_key: str) -> set[str]:
    return {p.name for p in _dir(profile_key).iterdir() if p.suffix in DATA_SUFFIXES}


@pytest.mark.parametrize("profile_key", PROFILES)
def test_every_sample_file_has_a_label(profile_key: str) -> None:
    labelled = {item["file"] for item in _items(profile_key)}

    assert _sample_files(profile_key) - labelled == set()


@pytest.mark.parametrize("profile_key", PROFILES)
def test_every_label_points_to_an_existing_file(profile_key: str) -> None:
    labelled = [item["file"] for item in _items(profile_key)]

    assert len(labelled) == len(set(labelled)), "duplicate labels"
    assert set(labelled) - _sample_files(profile_key) == set()


@pytest.mark.parametrize("profile_key", PROFILES)
def test_dataset_size_and_type_coverage(profile_key: str) -> None:
    items = _items(profile_key)
    types = [item["expected_type"] for item in items]

    assert 20 <= len(items) <= 30
    assert set(types) == set(_profile(profile_key).type_keys)
    assert all(types.count(t) >= 3 for t in set(types))
    assert any(item["file"].endswith(".pdf") for item in items)
    assert sum(item["difficulty"] == "hard" for item in items) >= 5


@pytest.mark.parametrize("profile_key", PROFILES)
def test_labels_are_marked_synthetic(profile_key: str) -> None:
    labels = _labels(profile_key)

    assert labels["synthetic"] is True
    assert labels["profile"] == profile_key


@pytest.mark.parametrize("profile_key", PROFILES)
def test_label_fields_match_profile_schema(profile_key: str) -> None:
    profile = _profile(profile_key)
    for item in _items(profile_key):
        doc_type = profile.document_type(item["expected_type"])
        fields = item["expected_fields"]
        if item["expected_type"] == profile.fallback_type:
            assert fields == {}, item["file"]
            continue
        assert set(fields) == {spec.name for spec in doc_type.fields}, item["file"]
        for spec in doc_type.fields:
            value = fields[spec.name]
            if value is None:
                continue
            if spec.type == "date":
                date.fromisoformat(value)
            elif spec.type == "decimal":
                assert re.fullmatch(r"\d+\.\d{2}", value)
            elif spec.type == "boolean":
                assert isinstance(value, bool)
            if spec.pattern:
                assert re.fullmatch(spec.pattern, value), (item["file"], spec.name)
            if spec.allowed_values:
                assert value in spec.allowed_values, (item["file"], spec.name)


def test_labelled_member_numbers_exist_in_seed() -> None:
    members = json.loads((_dir("fitness") / "members.json").read_text(encoding="utf-8"))
    known = {member["member_number"] for member in members["members"]}
    referenced = {
        item["expected_fields"].get("mitgliedsnummer")
        for item in _items("fitness")
        if item["expected_fields"].get("mitgliedsnummer")
    }

    assert referenced <= known


@pytest.mark.parametrize("profile_key", PROFILES)
def test_all_email_addresses_use_example_domains(profile_key: str) -> None:
    for path in _dir(profile_key).iterdir():
        for match in EMAIL_PATTERN.finditer(path.read_bytes()):
            domain = match.group(1).decode()
            assert ALLOWED_MAIL_DOMAIN.search(domain), f"{path.name}: {match.group(0)!r}"


@pytest.mark.parametrize("profile_key", PROFILES)
def test_phone_numbers_use_fictional_range(profile_key: str) -> None:
    for path in _dir(profile_key).iterdir():
        if path.suffix == ".pdf":
            continue
        for match in PHONE_PATTERN.finditer(path.read_text(encoding="utf-8")):
            number = match.group(0)
            if re.fullmatch(r"0\d{3}", number.strip()):
                continue
            assert number.startswith("030 23125"), f"{path.name}: {number!r}"


@pytest.mark.parametrize("profile_key", PROFILES)
def test_no_health_terms_in_samples(profile_key: str) -> None:
    for path in _dir(profile_key).iterdir():
        text = path.read_bytes().decode("utf-8", errors="ignore").lower()
        for term in HEALTH_TERMS:
            assert term not in text, f"{path.name} contains {term!r}"


@pytest.mark.parametrize("profile_key", PROFILES)
def test_committed_samples_match_generator(
    profile_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    path = SCRIPTS_DIR / GENERATORS[profile_key]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.write_all(tmp_path)

    for generated in tmp_path.iterdir():
        committed = _dir(profile_key) / generated.name
        assert committed.read_bytes() == generated.read_bytes(), f"{generated.name} is stale"
    sys.modules.pop("generate_samples", None)
