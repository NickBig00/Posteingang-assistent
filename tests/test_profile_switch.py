"""Second profile (M12): switchable via configuration, evaluation runs for both profiles."""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.llm.base import LLMTask
from app.core.llm.fake import FakeProvider
from app.core.profiles import PROFILES_DIR
from app.inbox import cli
from app.inbox.evaluation import evaluate
from app.inbox.ingest.normalize import normalize
from app.inbox.llm.client import LLMClient
from app.inbox.models import Classification, InboxItem
from app.inbox.profiles import load_active_profile, load_profile
from app.inbox.rules import validate_profile_rules
from app.inbox.web import get_llm_client
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ["fitness", "handwerk"]


def _oracle(samples_dir: Path, fallback_type: str) -> FakeProvider:
    labels = json.loads((samples_dir / "labels.json").read_text(encoding="utf-8"))["items"]
    by_text = {
        normalize(label["file"], (samples_dir / label["file"]).read_bytes()).text: label
        for label in labels
    }

    def respond(task: LLMTask, system: str, user: str) -> str:
        if task == "draft":
            return re.search(r"<vorlage>\n(.*)\n</vorlage>", user, re.S).group(1)  # type: ignore[union-attr]
        text = re.search(r"<dokument>\n(.*)\n</dokument>", user, re.S).group(1)  # type: ignore[union-attr]
        label = by_text[text]
        if task == "classify":
            return json.dumps({"document_type": label["expected_type"], "confidence": 0.99})
        fields: dict[str, JsonValue] = dict(label["expected_fields"])
        if label["expected_type"] == fallback_type:
            fields = {"kurzbeschreibung": "Sonstige Nachricht"}
        return json.dumps(fields, ensure_ascii=False)

    return FakeProvider(respond)


@pytest.mark.parametrize("profile_key", PROFILES)
def test_profile_is_valid_and_uses_only_known_rules(profile_key: str) -> None:
    profile = load_profile(PROFILES_DIR / f"{profile_key}.yaml")

    validate_profile_rules(profile)
    assert profile.key == profile_key


@pytest.mark.parametrize("profile_key", PROFILES)
def test_profile_switch_via_environment(profile_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROFILE", profile_key)

    assert load_active_profile().key == profile_key


@pytest.mark.parametrize("profile_key", PROFILES)
def test_evaluation_runs_for_profile(profile_key: str) -> None:
    profile = load_profile(PROFILES_DIR / f"{profile_key}.yaml")
    samples = ROOT / "samples" / profile_key

    heuristic = evaluate(profile, LLMClient(FakeProvider()), samples)
    oracle = evaluate(profile, LLMClient(_oracle(samples, profile.fallback_type)), samples)

    assert heuristic.items and heuristic.profile == profile_key
    assert oracle.accuracy == 1.0
    assert oracle.field_hit_rate == 1.0


def test_handwerk_hard_cases_go_to_manual_review() -> None:
    profile = load_profile(PROFILES_DIR / "handwerk.yaml")
    samples = ROOT / "samples" / "handwerk"

    report = evaluate(profile, LLMClient(_oracle(samples, profile.fallback_type)), samples)

    review = {item.file for item in report.items if item.status == "needs_manual_review"}
    assert review == {
        "104_angebot_kontaktformular.pdf",
        "109_reparatur_ohne_ort.eml",
        "113_reklamation_ohne_auftragsnummer.eml",
        "117_rechnung_summe_falsch.pdf",
        "118_rechnung_ohne_nummer.pdf",
    }


@pytest.fixture
def handwerk_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PROFILE", "handwerk")
    app.dependency_overrides[get_llm_client] = lambda: LLMClient(FakeProvider())
    yield


def test_app_uses_switched_profile(client: TestClient, engine: Engine, handwerk_app: None) -> None:
    assert "21 Beispiel-Eingänge importiert" in client.post("/samples/import").text
    assert "21 Eingänge verarbeitet" in client.post("/items/process").text

    with Session(engine) as session:
        types = set(session.exec(select(Classification.document_type)).all())
        files = {item.filename for item in session.exec(select(InboxItem)).all()}
    assert types <= set(load_profile(PROFILES_DIR / "handwerk.yaml").type_keys)
    assert "reparaturauftrag" in types
    assert all(name and name.startswith("1") for name in files)


def test_cli_evaluate_for_second_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("PROFILE", "handwerk")
    monkeypatch.setenv("LLM_PROVIDER", "fake")

    assert cli.main(["evaluate", "--out", str(tmp_path)]) == 0

    assert (tmp_path / "handwerk_fake.md").is_file()
    assert "Genauigkeit" in capsys.readouterr().out
