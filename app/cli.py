"""Command line interface: `uv run python -m app.cli <command>`."""

import argparse
from pathlib import Path

from sqlmodel import Session

from app.db import engine, init_db
from app.evaluation import evaluate, write_report
from app.ingest.service import DEFAULT_SAMPLES_DIR, import_samples
from app.llm.client import LLMClient
from app.llm.factory import get_provider
from app.logging_config import setup_logging
from app.models import ItemStatus
from app.pipeline import process_new_items
from app.profiles import load_active_profile
from app.retention import purge_expired
from app.rules import validate_profile_rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Posteingangs-Assistent")
    commands = parser.add_subparsers(dest="command", required=True)
    import_cmd = commands.add_parser("import-samples", help="Beispieldaten importieren")
    import_cmd.add_argument("--dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    commands.add_parser("process", help="Neue Eingänge klassifizieren und extrahieren")
    commands.add_parser("purge", help="Eingänge nach Ablauf der Aufbewahrungsfrist löschen")
    eval_cmd = commands.add_parser("evaluate", help="Pipeline gegen labels.json messen")
    eval_cmd.add_argument("--samples", type=Path, default=None)
    eval_cmd.add_argument("--out", type=Path, default=Path("docs/evaluation"))
    args = parser.parse_args(argv)
    setup_logging()

    if args.command == "evaluate":
        profile = load_active_profile()
        validate_profile_rules(profile)
        client = LLMClient(get_provider())
        samples = args.samples or DEFAULT_SAMPLES_DIR.parent / profile.key
        report = evaluate(profile, client, samples)
        markdown, _ = write_report(report, args.out)
        print(
            f"Genauigkeit {report.accuracy:.1%}, Feld-Trefferquote {report.field_hit_rate:.1%} "
            f"– Bericht: {markdown}"
        )
        return 0

    init_db(engine)
    with Session(engine) as session:
        if args.command == "import-samples":
            summary = import_samples(session, args.dir)
            print(
                f"{summary.imported} Eingänge importiert, {summary.skipped} bereits vorhanden, "
                f"{summary.members_seeded} Mitglieder angelegt."
            )
        elif args.command == "purge":
            summary = purge_expired(session)
            print(
                f"{len(summary.deleted_item_ids)} Eingänge gelöscht "
                f"(eingegangen vor {summary.cutoff:%d.%m.%Y %H:%M} UTC)."
            )
        elif args.command == "process":
            client = LLMClient(get_provider())
            profile = load_active_profile()
            validate_profile_rules(profile)
            results = process_new_items(session, profile, client)
            review = sum(r.status == ItemStatus.NEEDS_MANUAL_REVIEW for r in results)
            print(
                f"{len(results)} Eingänge verarbeitet, davon {review} zur manuellen Prüfung "
                f"(Anbieter: {client.provider.name})."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
