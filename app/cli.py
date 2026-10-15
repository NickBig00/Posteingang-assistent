"""Command line interface: `uv run python -m app.cli <command>`."""

import argparse
from pathlib import Path

from sqlmodel import Session

from app.db import engine, init_db
from app.ingest.service import DEFAULT_SAMPLES_DIR, import_samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Posteingangs-Assistent")
    commands = parser.add_subparsers(dest="command", required=True)
    import_cmd = commands.add_parser("import-samples", help="Beispieldaten importieren")
    import_cmd.add_argument("--dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    args = parser.parse_args(argv)

    init_db(engine)
    with Session(engine) as session:
        if args.command == "import-samples":
            summary = import_samples(session, args.dir)
            print(
                f"{summary.imported} Eingänge importiert, {summary.skipped} bereits vorhanden, "
                f"{summary.members_seeded} Mitglieder angelegt."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
