"""Command line interface: `uv run python -m app.cli <command>`.

Commands of the social media module start with `social-`; all others belong to the inbox.
"""

import sys

from app.inbox.cli import main as inbox_main
from app.social.cli import COMMANDS as SOCIAL_COMMANDS
from app.social.cli import main as social_main


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in SOCIAL_COMMANDS:
        return social_main(args)
    return inbox_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
