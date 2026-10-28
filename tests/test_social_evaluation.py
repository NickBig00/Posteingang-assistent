"""Evaluation of the social media module (ROADMAP_social S8). Fake provider only."""

import json
from pathlib import Path

import pytest

from app import cli
from app.core.llm.base import LLMTask
from app.core.llm.fake import FakeProvider
from app.social.evaluation import evaluate_social, render_markdown, samples_dir_for, write_report
from app.social.facts import BriefingFacts
from app.social.llm.client import SocialLLMClient
from tests.social_helpers import demo_config, scripted

CONFIG = demo_config()
SAMPLES = samples_dir_for("fitness")


def _briefings() -> list[dict]:
    data = json.loads((SAMPLES / "briefings.json").read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    return data["briefings"]


def test_samples_cover_all_areas_and_are_consistent() -> None:
    briefings = _briefings()

    assert {b["area"] for b in briefings} == {a.key for a in CONFIG.brand.areas}
    assert len({b["id"] for b in briefings}) == len(briefings)
    for sample in briefings:
        facts = BriefingFacts.model_validate(sample["facts"])
        occasion = CONFIG.industry.occasion_type(sample["occasion_type"])
        assert facts.missing(occasion.required_facts) == [], sample["id"]
        assert sample["labels"]["address_form"] == CONFIG.brand.address_form(sample["area"]).value
    forms = {b["labels"]["address_form"] for b in briefings}
    assert forms == {"du", "sie"}


def test_evaluation_with_fake_provider() -> None:
    report = evaluate_social(CONFIG, SocialLLMClient(FakeProvider()), SAMPLES)

    assert len(report.drafts) == sum(len(b["channels"]) for b in _briefings())
    assert report.facts_rate == 1.0
    assert report.length_rate == 1.0
    assert report.address_rate == 1.0
    assert report.manual_review == 0
    assert report.health.recall >= 0.9
    assert report.health.missed == ["g14"]
    assert report.health.false_alarms == ["n07"]
    assert report.persons.recall == 1.0 and report.persons.false_alarms == []
    assert report.to_json()["targets"]["met"] is True  # type: ignore[index]


def test_llm_assessment_adds_what_code_misses() -> None:
    def respond(task: LLMTask, system: str, user: str) -> str:
        if task == "post_assess" and "verschwinden deine Beschwerden" in user:
            finding = {"quote": "verschwinden deine Beschwerden", "reason": "Heilversprechen"}
            return json.dumps({"health_claims": [finding], "persons": [], "address_form": "du"})
        return FakeProvider().complete(task=task, system=system, user=user).text

    report = evaluate_social(CONFIG, SocialLLMClient(FakeProvider(respond)), SAMPLES)

    assert report.health.recall == 1.0
    assert report.health.missed == []


def test_invalid_drafts_lower_the_rates() -> None:
    provider = scripted(post_draft=["kein json"] * 200)

    report = evaluate_social(CONFIG, SocialLLMClient(provider), SAMPLES)

    assert report.manual_review == len(report.drafts)
    assert report.facts_rate == 0.0
    assert report.to_json()["targets"]["met"] is False  # type: ignore[index]
    assert "nicht erreicht" in render_markdown(report)


def test_report_files(tmp_path: Path) -> None:
    report = evaluate_social(CONFIG, SocialLLMClient(FakeProvider()), SAMPLES)

    markdown, data = write_report(report, tmp_path)

    assert markdown.name == "social_fitness_fake.md"
    text = markdown.read_text(encoding="utf-8")
    assert "| Fakten unverändert übernommen | 100,0 % | 100 % |" in text
    assert "Gesundheitsversprechen: 13 von 14 erkannt" in text
    payload = json.loads(data.read_text(encoding="utf-8"))
    assert payload["generation"]["facts_rate"] == 1.0
    assert payload["detection"]["health_claims"]["missed"] == ["g14"]


def test_cli_social_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    for name in ("PROFILE", "BRAND_PROFILE", "BRAND_PROFILES_DIR"):
        monkeypatch.delenv(name, raising=False)

    assert cli.main(["social-evaluate", "--out", str(tmp_path)]) == 0

    assert (tmp_path / "social_fitness_fake.md").is_file()
    assert "Fakten 100.0%" in capsys.readouterr().out


def test_cli_dispatches_inbox_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "inbox_main", lambda argv: calls.append(argv) or 0)

    assert cli.main(["process"]) == 0

    assert calls == [["process"]]


def test_committed_report_matches_samples() -> None:
    committed = json.loads(
        (
            Path(__file__).resolve().parent.parent / "docs/evaluation/social_fitness_fake.json"
        ).read_text(encoding="utf-8")
    )
    report = evaluate_social(CONFIG, SocialLLMClient(FakeProvider()), SAMPLES).to_json()

    committed.pop("created_at")
    report.pop("created_at")
    assert committed == json.loads(json.dumps(report))
