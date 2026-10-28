"""Evaluation of the social media module (SPEC_social section 9).

1. Generation: synthetic briefings with labels -> drafts via the configured provider ->
   share of drafts with all facts unchanged, length kept, address form fitting the area.
2. Detection: labelled post texts -> code checks plus LLM assessment -> recognition rate
   (recall) and precision for health claims and named persons, address form accuracy.

Runs in a temporary in-memory database; the audit entries of the run are written there.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session, col, select

from app.core.config import PROJECT_ROOT
from app.core.db import init_db, make_engine
from app.core.llm.calls import audit_llm_call, call_with_retry
from app.core.members import Member
from app.core.models import utcnow
from app.core.outcomes import Outcome
from app.social.briefings import BriefingInput, create_briefing
from app.social.drafts import generate_drafts
from app.social.facts import BriefingFacts
from app.social.llm.client import SocialLLMClient
from app.social.models import CHANNEL_LABELS, Channel, CheckResult, PostStatus
from app.social.profiles import AddressForm, SocialConfig
from app.social.sensitive import (
    ADDRESS,
    HEALTH,
    PERSONS,
    check_address_form,
    check_health_claims,
    check_persons,
    merge_assessment,
)

SAMPLES_ROOT = PROJECT_ROOT / "samples"
FACTS_TARGET = 1.0
HEALTH_TARGET = 0.9


@dataclass
class DraftEvaluation:
    sample_id: str
    area: str
    channel: str
    status: str
    has_facts: bool
    facts_ok: bool
    length_ok: bool
    address_ok: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class Detection:
    case_id: str
    expected: bool
    detected: bool


@dataclass
class DetectionScore:
    positives: int
    negatives: int
    true_positives: int
    false_positives: int
    missed: list[str]
    false_alarms: list[str]

    @property
    def recall(self) -> float:
        return self.true_positives / self.positives if self.positives else 1.0

    @property
    def precision(self) -> float:
        flagged = self.true_positives + self.false_positives
        return self.true_positives / flagged if flagged else 1.0


def _score(detections: list[Detection]) -> DetectionScore:
    return DetectionScore(
        positives=sum(d.expected for d in detections),
        negatives=sum(not d.expected for d in detections),
        true_positives=sum(d.expected and d.detected for d in detections),
        false_positives=sum(d.detected and not d.expected for d in detections),
        missed=[d.case_id for d in detections if d.expected and not d.detected],
        false_alarms=[d.case_id for d in detections if d.detected and not d.expected],
    )


@dataclass
class SocialReport:
    industry: str
    brand: str
    provider: str
    created_at: str
    drafts: list[DraftEvaluation]
    health: DetectionScore
    persons: DetectionScore
    address: list[Detection]

    def _rate(self, values: list[bool]) -> float:
        return sum(values) / len(values) if values else 1.0

    @property
    def facts_rate(self) -> float:
        return self._rate([d.facts_ok for d in self.drafts if d.has_facts])

    @property
    def length_rate(self) -> float:
        return self._rate([d.length_ok for d in self.drafts])

    @property
    def address_rate(self) -> float:
        return self._rate([d.address_ok for d in self.drafts])

    @property
    def manual_review(self) -> int:
        return sum(d.status == PostStatus.NEEDS_MANUAL_REVIEW.value for d in self.drafts)

    @property
    def address_accuracy(self) -> float:
        return self._rate([d.expected == d.detected for d in self.address])

    def to_json(self) -> dict[str, object]:
        return {
            "industry": self.industry,
            "brand": self.brand,
            "provider": self.provider,
            "created_at": self.created_at,
            "generation": {
                "drafts": len(self.drafts),
                "facts_rate": round(self.facts_rate, 4),
                "length_rate": round(self.length_rate, 4),
                "address_rate": round(self.address_rate, 4),
                "needs_manual_review": self.manual_review,
                "details": [asdict(d) for d in self.drafts],
            },
            "detection": {
                "health_claims": _score_json(self.health),
                "persons": _score_json(self.persons),
                "address_form_accuracy": round(self.address_accuracy, 4),
            },
            "targets": {
                "facts_rate": FACTS_TARGET,
                "health_recall": HEALTH_TARGET,
                "met": self.facts_rate >= FACTS_TARGET and self.health.recall >= HEALTH_TARGET,
            },
        }


def _score_json(score: DetectionScore) -> dict[str, object]:
    rates = {"recall": round(score.recall, 4), "precision": round(score.precision, 4)}
    return asdict(score) | rates


def _failed(results: list[CheckResult], key: str) -> list[CheckResult]:
    return [r for r in results if r.check_key == key and r.outcome is not Outcome.OK]


def _load(path: Path, key: str) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("synthetic") is not True:
        raise ValueError(f"{path} is not marked as synthetic data")
    return data[key]


def _seed_members(session: Session, path: Path) -> list[str]:
    if not path.is_file():
        return []
    for member in _load(path, "members"):
        session.add(
            Member(
                member_number=member["member_number"],
                name=member["name"],
                email=member["email"],
                contract_start=date.fromisoformat(member["contract_start"]),
                minimum_term_months=member["minimum_term_months"],
                notice_period_months=member["notice_period_months"],
                renewal_period_months=member["renewal_period_months"],
                monthly_fee_eur=Decimal(str(member["monthly_fee_eur"])),
            )
        )
    session.commit()
    return list(session.exec(select(Member.name)).all())


def _evaluate_drafts(
    session: Session, config: SocialConfig, client: SocialLLMClient, samples: list[dict]
) -> list[DraftEvaluation]:
    evaluations = []
    for sample in samples:
        data = BriefingInput(
            area=sample["area"],
            occasion_type=sample["occasion_type"],
            notes=sample["notes"],
            channels=[Channel(c) for c in sample["channels"]],
            facts=BriefingFacts.model_validate(sample.get("facts", {})),
        )
        briefing = create_briefing(session, config, data, "evaluation")
        expected = AddressForm(sample["labels"]["address_form"])
        if expected is not config.brand.address_form(sample["area"]):
            raise ValueError(f"{sample['id']}: label address_form does not match the area")
        for draft in generate_drafts(session, config, client, briefing):
            statement = select(CheckResult).where(CheckResult.draft_id == draft.id)
            results = list(session.exec(statement.order_by(col(CheckResult.id))).all())
            problems = [r.reason for r in results if r.outcome is Outcome.ERROR]
            problems += [f"Anrede: {r.reason}" for r in _failed(results, ADDRESS)]
            evaluations.append(
                DraftEvaluation(
                    sample_id=sample["id"],
                    area=sample["area"],
                    channel=CHANNEL_LABELS[draft.channel],
                    status=draft.status.value,
                    has_facts=bool(data.facts.present_keys()),
                    facts_ok=bool(draft.text) and not _failed(results, "fakten"),
                    length_ok=bool(draft.text) and not _failed(results, "laenge"),
                    address_ok=bool(draft.text) and not _failed(results, ADDRESS),
                    problems=problems,
                )
            )
    return evaluations


def _evaluate_detection(
    session: Session,
    config: SocialConfig,
    client: SocialLLMClient,
    cases: list[dict],
    members: list[str],
) -> tuple[list[Detection], list[Detection], list[Detection]]:
    health, persons, address = [], [], []
    for case in cases:
        text, area_key = case["text"], case["area"]
        area = config.brand.area(area_key)
        sensitive = area.label if config.is_sensitive(area_key) else None
        code = [
            *check_health_claims(text, config.industry.health_claims, sensitive),
            *check_persons(text, members),
            *check_address_form(text, area.address_form),
        ]

        def record(call: object, attempt: int, case_id: str = case["id"]) -> None:
            audit_llm_call(
                session,
                call,  # type: ignore[arg-type]
                attempt=attempt,
                provider=client.provider.name,
                step="social.evaluation_assess",
                context={"case_id": case_id},
            )

        call = call_with_retry(lambda text=text: client.assess_post(config, text), record)
        ai = merge_assessment(text, call.output, code, area.address_form) if call.output else []
        findings = [f for f in [*code, *ai] if f.outcome is not Outcome.OK]
        labels = case["labels"]
        keys = {f.check_key for f in findings}
        health.append(Detection(case["id"], labels["health_claim"], HEALTH in keys))
        persons.append(Detection(case["id"], labels["person"], PERSONS in keys))
        wrong = labels["address_form"] == "gemischt" or labels["address_form"] not in (
            "neutral",
            area.address_form.value,
        )
        address.append(Detection(case["id"], wrong, ADDRESS in keys))
    return health, persons, address


def samples_dir_for(industry_key: str) -> Path:
    return SAMPLES_ROOT / "social" / industry_key


def evaluate_social(
    config: SocialConfig, client: SocialLLMClient, samples_dir: Path | None = None
) -> SocialReport:
    samples_dir = samples_dir or samples_dir_for(config.industry_key)
    engine = make_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        members = _seed_members(session, SAMPLES_ROOT / config.industry_key / "members.json")
        drafts = _evaluate_drafts(
            session, config, client, _load(samples_dir / "briefings.json", "briefings")
        )
        health, persons, address = _evaluate_detection(
            session, config, client, _load(samples_dir / "assessment_cases.json", "cases"), members
        )
    return SocialReport(
        industry=config.industry_key,
        brand=config.brand.key,
        provider=client.provider.name,
        created_at=f"{utcnow():%Y-%m-%d %H:%M} UTC",
        drafts=drafts,
        health=_score(health),
        persons=_score(persons),
        address=address,
    )


def _percent(value: float) -> str:
    return f"{value * 100:.1f} %".replace(".", ",")


def render_markdown(report: SocialReport) -> str:
    target = "erreicht" if report.to_json()["targets"]["met"] else "**nicht erreicht**"  # type: ignore[index]
    lines = [
        f"# Evaluation Social Media – Branche `{report.industry}`, Anbieter `{report.provider}`",
        "",
        f"Erstellt: {report.created_at} · Markenprofil `{report.brand}` · nur synthetische Daten",
        "",
        "## Ergebnis",
        "",
        "| Kennzahl | Wert | Ziel |",
        "|---|---|---|",
        f"| Fakten unverändert übernommen | {_percent(report.facts_rate)} | 100 % |",
        f"| Länge je Kanal eingehalten | {_percent(report.length_rate)} | 100 % |",
        f"| Anrede passend zum Bereich | {_percent(report.address_rate)} | – |",
        f"| Entwürfe in manueller Bearbeitung | {report.manual_review} von "
        f"{len(report.drafts)} | – |",
        f"| Gesundheitsversprechen erkannt (Recall) | {_percent(report.health.recall)} | ≥ 90 % |",
        f"| Gesundheitsversprechen – Präzision | {_percent(report.health.precision)} | – |",
        f"| Personen erkannt (Recall) | {_percent(report.persons.recall)} | – |",
        f"| Personen – Präzision | {_percent(report.persons.precision)} | – |",
        f"| Anrede-Prüfung korrekt | {_percent(report.address_accuracy)} | – |",
        "",
        f"Ziele laut SPEC (100 % Fakten, ≥ 90 % Gesundheitsversprechen): {target}.",
        "",
        "## Erkennung (gelabelter Testsatz)",
        "",
        f"- Gesundheitsversprechen: {report.health.true_positives} von {report.health.positives} "
        f"erkannt; nicht erkannt: {', '.join(report.health.missed) or '–'}; Fehlalarme: "
        f"{', '.join(report.health.false_alarms) or '–'}",
        f"- Personen: {report.persons.true_positives} von {report.persons.positives} erkannt; "
        f"nicht erkannt: {', '.join(report.persons.missed) or '–'}; Fehlalarme: "
        f"{', '.join(report.persons.false_alarms) or '–'}",
        "- Anrede falsch erkannt bei: "
        + (", ".join(d.case_id for d in report.address if d.expected != d.detected) or "–"),
        "",
        "## Entwürfe mit Problemen",
        "",
    ]
    problems = [d for d in report.drafts if d.problems or not d.facts_ok or not d.length_ok]
    if not problems:
        lines.append("Keine.")
    for draft in problems:
        reasons = "; ".join(draft.problems) or "kein Text"
        lines.append(
            f"- {draft.sample_id} ({draft.area}, {draft.channel}, {draft.status}): {reasons}"
        )
    lines += [
        "",
        "## Hinweise",
        "",
        "- Mit dem Fake-Provider erzeugt eine feste Heuristik die Entwürfe und die KI-Einschätzung "
        "meldet nichts; die Erkennungsraten zeigen dann allein die Code-Prüfungen.",
        "- Briefings: `samples/social/<branche>/briefings.json`, Testsatz: "
        "`samples/social/<branche>/assessment_cases.json`.",
        "",
    ]
    return "\n".join(lines)


def write_report(report: SocialReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"social_{report.industry}_{report.provider}"
    markdown = out_dir / f"{stem}.md"
    data = out_dir / f"{stem}.json"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    data.write_text(
        json.dumps(report.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return markdown, data
