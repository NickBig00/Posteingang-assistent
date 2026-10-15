import importlib.util
import json
import re
from datetime import date
from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "fitness"
GENERATOR = Path(__file__).resolve().parent.parent / "scripts" / "generate_samples.py"
DATA_SUFFIXES = {".eml", ".pdf"}

EXPECTED_FIELD_KEYS = {
    "kuendigung": {"name", "mitgliedsnummer", "eingangsdatum", "gewuenschtes_kuendigungsdatum"},
    "probetraining_anfrage": {"name", "kontakt", "wunschtermin", "interesse"},
    "vertragspause": {"name", "mitgliedsnummer", "zeitraum_von", "zeitraum_bis", "grund_kategorie"},
    "rechnung": {"lieferant", "rechnungsnummer", "rechnungsdatum", "betrag", "e_rechnung"},
    "sonstiges": set(),
}
DATE_FIELDS = {
    "eingangsdatum",
    "gewuenschtes_kuendigungsdatum",
    "wunschtermin",
    "zeitraum_von",
    "zeitraum_bis",
    "rechnungsdatum",
}
PAUSE_REASON_CATEGORIES = {"beruflich", "reise", "sonstiges"}
ALLOWED_MAIL_DOMAIN = re.compile(r"(^|\.)example\.(com|org|net)$")
EMAIL_PATTERN = re.compile(rb"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# Digits sequences that look like phone numbers; only the fictional range 030 23125 xxx is ok.
PHONE_PATTERN = re.compile(r"\b0\d{2,4}[ /-]?\d{3,}[ -]?\d*\b")


def _labels() -> dict:
    return json.loads((SAMPLES_DIR / "labels.json").read_text(encoding="utf-8"))


def _members() -> list[dict]:
    return json.loads((SAMPLES_DIR / "members.json").read_text(encoding="utf-8"))["members"]


def _sample_files() -> set[str]:
    return {p.name for p in SAMPLES_DIR.iterdir() if p.suffix in DATA_SUFFIXES}


def test_every_sample_file_has_a_label() -> None:
    labelled = {item["file"] for item in _labels()["items"]}

    assert _sample_files() - labelled == set()


def test_every_label_points_to_an_existing_file() -> None:
    labelled = [item["file"] for item in _labels()["items"]]

    assert len(labelled) == len(set(labelled)), "duplicate labels"
    assert set(labelled) - _sample_files() == set()


def test_dataset_size_and_type_coverage() -> None:
    items = _labels()["items"]
    types = [item["expected_type"] for item in items]

    assert 20 <= len(items) <= 30
    assert set(types) == set(EXPECTED_FIELD_KEYS)
    assert all(types.count(t) >= 3 for t in EXPECTED_FIELD_KEYS)
    assert any(item["file"].endswith(".pdf") for item in items)
    assert sum(item["difficulty"] == "hard" for item in items) >= 5


def test_labels_are_marked_synthetic() -> None:
    labels = _labels()

    assert labels["synthetic"] is True
    assert labels["profile"] == "fitness"


@pytest.mark.parametrize("item", _labels()["items"], ids=lambda item: item["file"])
def test_label_fields_match_type_schema(item: dict) -> None:
    fields = item["expected_fields"]

    assert set(fields) == EXPECTED_FIELD_KEYS[item["expected_type"]]
    for key in DATE_FIELDS & set(fields):
        if fields[key] is not None:
            date.fromisoformat(fields[key])
    if "betrag" in fields:
        assert re.fullmatch(r"\d+\.\d{2}", fields["betrag"])
    if "e_rechnung" in fields:
        assert isinstance(fields["e_rechnung"], bool)
    if "grund_kategorie" in fields and fields["grund_kategorie"] is not None:
        assert fields["grund_kategorie"] in PAUSE_REASON_CATEGORIES


def test_labelled_member_numbers_exist_in_seed() -> None:
    known = {member["member_number"] for member in _members()}
    referenced = {
        item["expected_fields"].get("mitgliedsnummer")
        for item in _labels()["items"]
        if item["expected_fields"].get("mitgliedsnummer")
    }

    assert referenced <= known


def test_all_email_addresses_use_example_domains() -> None:
    for path in SAMPLES_DIR.iterdir():
        for match in EMAIL_PATTERN.finditer(path.read_bytes()):
            domain = match.group(1).decode()
            assert ALLOWED_MAIL_DOMAIN.search(domain), f"{path.name}: {match.group(0)!r}"


def test_phone_numbers_use_fictional_range() -> None:
    for path in SAMPLES_DIR.iterdir():
        if path.suffix == ".pdf":
            continue
        for match in PHONE_PATTERN.finditer(path.read_text(encoding="utf-8")):
            number = match.group(0)
            if re.fullmatch(r"0\d{3}", number.strip()):
                continue
            assert number.startswith("030 23125"), f"{path.name}: {number!r}"


def test_no_health_terms_in_samples() -> None:
    health_terms = ("krank", "gesundheit", "diagnose", "attest", "verletz", "schwanger", "arzt")
    for path in SAMPLES_DIR.iterdir():
        text = path.read_bytes().decode("utf-8", errors="ignore").lower()
        for term in health_terms:
            assert term not in text, f"{path.name} contains {term!r}"


def test_committed_samples_match_generator(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("generate_samples", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.write_all(tmp_path)

    for generated in tmp_path.iterdir():
        committed = SAMPLES_DIR / generated.name
        assert committed.read_bytes() == generated.read_bytes(), f"{generated.name} is stale"
