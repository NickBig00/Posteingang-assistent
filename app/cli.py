"""Command line interface: `uv run python -m app.cli <command>`."""

import argparse
from pathlib import Path

from sqlmodel import Session

from app.db import engine, init_db
from app.ingest.service import DEFAULT_SAMPLES_DIR, import_samples
from app.llm.client import LLMClient
from app.llm.factory import get_provider
from app.models import ItemStatus
from app.pipeline import process_new_items
from app.profiles import load_active_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Posteingangs-Assistent")
    commands = parser.add_subparsers(dest="command", required=True)
    import_cmd = commands.add_parser("import-samples", help="Beispieldaten importieren")
    import_cmd.add_argument("--dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    commands.add_parser("process", help="Neue Eingänge klassifizieren und extrahieren")
    args = parser.parse_args(argv)

    init_db(engine)
    with Session(engine) as session:
        if args.command == "import-samples":
            summary = import_samples(session, args.dir)
            print(
                f"{summary.imported} Eingänge importiert, {summary.skipped} bereits vorhanden, "
                f"{summary.members_seeded} Mitglieder angelegt."
            )
        elif args.command == "process":
            client = LLMClient(get_provider())
            results = process_new_items(session, load_active_profile(), client)
            review = sum(r.status == ItemStatus.NEEDS_MANUAL_REVIEW for r in results)
            print(
                f"{len(results)} Eingänge verarbeitet, davon {review} zur manuellen Prüfung "
                f"(Anbieter: {client.provider.name})."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
