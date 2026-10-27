"""Command line interface: `uv run python -m app.cli <command>`."""

from app.inbox.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
