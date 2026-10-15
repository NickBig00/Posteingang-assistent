import json
import re
from pathlib import Path

import pytest
from pydantic import JsonValue

from app import cli
from app.evaluation import (
    compare_fields,
    evaluate,
    normalize_value,
    render_markdown,
    write_report,
)
from app.ingest.normalize import normalize
from app.llm.base import LLMTask
from app.llm.client import LLMClient
from app.llm.fake import FakeProvider
from app.profiles import PROFILES_DIR, Profile, load_profile
from tests.conftest import SAMPLES_DIR


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


def _labels() -> list[dict]:
    return json.loads((SAMPLES_DIR / "labels.json").read_text(encoding="utf-8"))["items"]


def _oracle() -> FakeProvider:
    """Answers exactly with the labels – the evaluation must then report 100 %."""
    by_text = {
        normalize(label["file"], (SAMPLES_DIR / label["file"]).read_bytes()).text: label
        for label in _labels()
    }

    def respond(task: LLMTask, system: str, user: str) -> str:
        if task == "draft":
            return re.search(r"<vorlage>\n(.*)\n</vorlage>", user, re.S).group(1)  # type: ignore[union-attr]
        text = re.search(r"<dokument>\n(.*)\n</dokument>", user, re.S).group(1)  # type: ignore[union-attr]
        label = by_text[text]
        if task == "classify":
            return json.dumps({"document_type": label["expected_type"], "confidence": 0.99})
        fields: dict[str, JsonValue] = dict(label["expected_fields"])
        if label["expected_type"] == "sonstiges":
            fields = {"kurzbeschreibung": "Sonstige Nachricht"}
        return json.dumps(fields, ensure_ascii=False)

    return FakeProvider(respond)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Erika   Beispiel ", "erika beispiel"),
        ("499.8", "499.80"),
        ("499.80", "499.80"),
        ("", None),
        (None, None),
        (False, False),
    ],
)
def test_normalize_value(value: JsonValue, expected: JsonValue) -> None:
    assert normalize_value(value) == expected


def test_compare_fields() -> None:
    results = compare_fields(
        {"name": "Erika Beispiel", "datum": None, "betrag": "10.00"},
        {"name": "erika beispiel", "betrag": "11.00"},
    )

    assert [(r.name, r.hit) for r in results] == [
        ("name", True),
        ("datum", True),
        ("betrag", False),
    ]


def test_oracle_provider_scores_perfectly(profile: Profile) -> None:
    report = evaluate(profile, LLMClient(_oracle()), SAMPLES_DIR)

    assert len(report.items) == 27
    assert report.accuracy == 1.0
    assert report.field_hit_rate == 1.0
    assert all(stats.accuracy == 1.0 for stats in report.per_type.values())


def test_heuristic_fake_report(profile: Profile, tmp_path: Path) -> None:
    report = evaluate(profile, LLMClient(FakeProvider()), SAMPLES_DIR)

    markdown, data = write_report(report, tmp_path)

    text = markdown.read_text(encoding="utf-8")
    assert report.accuracy >= 0.9
    assert "## Genauigkeit je Typ" in text
    assert "## Trefferquote je Feld" in text
    assert "## Fehlerliste" in text
    assert "015_vertragspause_reise.eml" in text  # known misclassification of the heuristic
    summary = json.loads(data.read_text(encoding="utf-8"))
    assert summary["accuracy"] == pytest.approx(report.accuracy)
    assert len(summary["items"]) == 27


def test_render_without_errors(profile: Profile) -> None:
    report = evaluate(profile, LLMClient(_oracle()), SAMPLES_DIR)

    assert "Keine Abweichungen." in render_markdown(report)


def test_labels_of_other_profile_are_rejected(profile: Profile, tmp_path: Path) -> None:
    (tmp_path / "labels.json").write_text(json.dumps({"profile": "handwerk", "items": []}))

    with pytest.raises(ValueError, match="handwerk"):
        evaluate(profile, LLMClient(FakeProvider()), tmp_path)


def test_cli_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")

    assert cli.main(["evaluate", "--out", str(tmp_path)]) == 0

    assert "Genauigkeit 96.3%" in capsys.readouterr().out
    assert (tmp_path / "fitness_fake.md").is_file()
    assert (tmp_path / "fitness_fake.json").is_file()
