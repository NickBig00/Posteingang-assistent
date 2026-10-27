"""Evaluation of the pipeline against the labelled sample set (SPEC section 8).

Runs import + pipeline in a throw-away in-memory database and compares the
classification and the extracted fields with `labels.json`.
"""

import json
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.db import init_db, make_engine
from app.inbox.ingest.service import import_samples
from app.inbox.llm.client import LLMClient
from app.inbox.models import Classification, Extraction, InboxItem
from app.inbox.pipeline import process_new_items
from app.inbox.profiles import Profile

TARGET_ACCURACY = 0.9


@dataclass
class FieldResult:
    name: str
    expected: JsonValue
    actual: JsonValue
    hit: bool


@dataclass
class ItemResult:
    file: str
    expected_type: str
    predicted_type: str | None
    confidence: float | None
    status: str
    reason: str | None
    difficulty: str
    fields: list[FieldResult] = field(default_factory=list)

    @property
    def type_correct(self) -> bool:
        return self.predicted_type == self.expected_type


@dataclass
class TypeStats:
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class Report:
    profile: str
    provider: str
    model: str
    created_at: str
    items: list[ItemResult]

    @property
    def accuracy(self) -> float:
        return sum(i.type_correct for i in self.items) / len(self.items) if self.items else 0.0

    @property
    def per_type(self) -> dict[str, TypeStats]:
        stats: dict[str, TypeStats] = {}
        for item in self.items:
            entry = stats.setdefault(item.expected_type, TypeStats())
            entry.total += 1
            entry.correct += item.type_correct
        return dict(sorted(stats.items()))

    @property
    def field_results(self) -> list[FieldResult]:
        return [f for item in self.items for f in item.fields]

    @property
    def field_hit_rate(self) -> float:
        results = self.field_results
        return sum(f.hit for f in results) / len(results) if results else 0.0

    @property
    def per_field(self) -> dict[str, TypeStats]:
        stats: dict[str, TypeStats] = {}
        for item in self.items:
            for result in item.fields:
                entry = stats.setdefault(f"{item.expected_type}.{result.name}", TypeStats())
                entry.total += 1
                entry.correct += result.hit
        return dict(sorted(stats.items()))

    @property
    def statuses(self) -> Counter[str]:
        return Counter(item.status for item in self.items)


def normalize_value(value: JsonValue) -> JsonValue:
    """Comparable form: trimmed case-insensitive strings, amounts as exact decimals."""
    if isinstance(value, str):
        text = " ".join(value.split()).lower()
        try:
            return str(Decimal(text).quantize(Decimal("0.01")))
        except InvalidOperation:
            return text or None
    return value


def compare_fields(
    expected: dict[str, JsonValue], actual: dict[str, JsonValue]
) -> list[FieldResult]:
    """Compare every labelled field; a missing extraction counts as `null` for each field."""
    return [
        FieldResult(
            name,
            value,
            actual.get(name),
            normalize_value(value) == normalize_value(actual.get(name)),
        )
        for name, value in expected.items()
    ]


def evaluate(profile: Profile, client: LLMClient, samples_dir: Path) -> Report:
    labels = json.loads((samples_dir / "labels.json").read_text(encoding="utf-8"))
    if labels.get("profile") != profile.key:
        raise ValueError(f"labels.json belongs to profile {labels.get('profile')!r}")
    engine = make_engine("sqlite://")
    init_db(engine)
    with tempfile.TemporaryDirectory() as raw_dir, Session(engine) as session:
        import_samples(session, samples_dir, Path(raw_dir))
        results = {r.item_id: r for r in process_new_items(session, profile, client)}
        items = {i.filename: i for i in session.exec(select(InboxItem)).all()}
        evaluated = []
        for label in labels["items"]:
            item = items[label["file"]]
            classification = session.exec(
                select(Classification).where(Classification.item_id == item.id)
            ).first()
            extraction = session.exec(
                select(Extraction).where(Extraction.item_id == item.id)
            ).first()
            result = results.get(item.id or 0)
            evaluated.append(
                ItemResult(
                    file=label["file"],
                    expected_type=label["expected_type"],
                    predicted_type=classification.document_type if classification else None,
                    confidence=classification.confidence if classification else None,
                    status=item.status.value,
                    reason=result.reason if result else None,
                    difficulty=label.get("difficulty", "normal"),
                    fields=compare_fields(
                        label["expected_fields"], extraction.fields if extraction else {}
                    ),
                )
            )
    model = getattr(client.provider, "model", client.provider.name)
    return Report(
        profile=profile.key,
        provider=client.provider.name,
        model=str(model),
        created_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        items=evaluated,
    )


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %"


def _cell(value: JsonValue) -> str:
    return "–" if value is None else str(value).replace("|", "\\|")


def render_markdown(report: Report) -> str:
    target = "erreicht" if report.accuracy >= TARGET_ACCURACY else "nicht erreicht"
    lines = [
        f"# Evaluationsbericht – Profil `{report.profile}`",
        "",
        f"- Anbieter: `{report.provider}`, Modell: `{report.model}`",
        f"- Erstellt: {report.created_at}",
        f"- Eingänge: {len(report.items)}",
        f"- **Klassifizierungsgenauigkeit: {_pct(report.accuracy)}** "
        f"(Ziel ≥ {_pct(TARGET_ACCURACY)}: {target})",
        f"- **Feld-Trefferquote: {_pct(report.field_hit_rate)}** "
        f"({sum(f.hit for f in report.field_results)}/{len(report.field_results)} Felder)",
        "- Status: "
        + ", ".join(f"{status} {count}" for status, count in sorted(report.statuses.items())),
        "",
        "## Genauigkeit je Typ",
        "",
        "| Typ | Eingänge | korrekt | Genauigkeit |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {key} | {s.total} | {s.correct} | {_pct(s.accuracy)} |"
        for key, s in report.per_type.items()
    ]
    lines += [
        "",
        "## Trefferquote je Feld",
        "",
        "| Feld | Werte | Treffer | Quote |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {key} | {s.total} | {s.correct} | {_pct(s.accuracy)} |"
        for key, s in report.per_field.items()
    ]
    lines += ["", "## Fehlerliste", ""]
    errors = [i for i in report.items if not i.type_correct or not all(f.hit for f in i.fields)]
    if not errors:
        lines.append("Keine Abweichungen.")
    for item in errors:
        heading = f"### {item.file}"
        if item.difficulty == "hard":
            heading += " (schwieriger Fall)"
        lines += [heading, ""]
        if not item.type_correct:
            lines.append(
                f"- Typ: erwartet `{item.expected_type}`, erkannt `{item.predicted_type}` "
                f"(Konfidenz {_cell(item.confidence)})"
            )
        lines.append(f"- Status: `{item.status}`" + (f" – {item.reason}" if item.reason else ""))
        misses = [f for f in item.fields if not f.hit]
        if misses:
            lines += ["", "| Feld | erwartet | extrahiert |", "|---|---|---|"]
            lines += [f"| {f.name} | {_cell(f.expected)} | {_cell(f.actual)} |" for f in misses]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(report: Report, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.profile}_{report.provider}"
    markdown = out_dir / f"{stem}.md"
    data = out_dir / f"{stem}.json"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    summary = {
        "profile": report.profile,
        "provider": report.provider,
        "model": report.model,
        "created_at": report.created_at,
        "accuracy": report.accuracy,
        "field_hit_rate": report.field_hit_rate,
        "per_type": {k: asdict(v) for k, v in report.per_type.items()},
        "items": [asdict(i) for i in report.items],
    }
    data.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return markdown, data
