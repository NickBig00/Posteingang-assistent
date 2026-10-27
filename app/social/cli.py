"""Social media commands of the command line interface (entry point: `python -m app.cli`)."""

import argparse
from pathlib import Path

from app.core.llm.factory import get_provider
from app.core.logging_config import setup_logging
from app.social.evaluation import evaluate_social, write_report
from app.social.llm.client import SocialLLMClient
from app.social.profiles import load_social_config

COMMANDS = ("social-evaluate",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Social-Media-Modul")
    commands = parser.add_subparsers(dest="command", required=True)
    eval_cmd = commands.add_parser(
        "social-evaluate", help="Social-Media-Entwürfe und Prüfungen gegen Labels messen"
    )
    eval_cmd.add_argument("--samples", type=Path, default=None)
    eval_cmd.add_argument("--out", type=Path, default=Path("docs/evaluation"))
    args = parser.parse_args(argv)
    setup_logging()

    config = load_social_config()
    report = evaluate_social(config, SocialLLMClient(get_provider()), args.samples)
    markdown, _ = write_report(report, args.out)
    print(
        f"Fakten {report.facts_rate:.1%}, Längen {report.length_rate:.1%}, "
        f"Gesundheitsversprechen erkannt {report.health.recall:.1%}, "
        f"Personen erkannt {report.persons.recall:.1%} – Bericht: {markdown}"
    )
    return 0
