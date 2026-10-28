"""Configuration: the `.env` file in the project root, the enabled modules, the title."""

import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

# Repository root (contains `.env`, `profiles/`, `prompts/` and `samples/`).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
# Set to "1" to ignore `.env` (the test suite does this so local keys never apply there).
DISABLE_VARIABLE = "POSTEINGANG_NO_DOTENV"


def load_env_file(path: Path = ENV_FILE) -> bool:
    """Load `path` into the environment. Variables that are already set take precedence.

    Returns True if a file was loaded.
    """
    if os.environ.get(DISABLE_VARIABLE) == "1" or not path.is_file():
        return False
    load_dotenv(path, override=False)
    return True


# Comma-separated list of enabled modules, e.g. `MODULES=inbox` (default: all available).
MODULES_VARIABLE = "MODULES"


class ModuleConfigError(ValueError):
    """Raised for an unknown module name or when no module is enabled."""


def enabled_modules(available: Sequence[str]) -> list[str]:
    """Modules switched on via env `MODULES`, in the order of `available`."""
    raw = os.environ.get(MODULES_VARIABLE)
    if raw is None:
        return list(available)
    requested = {name.strip().lower() for name in raw.split(",") if name.strip()}
    unknown = sorted(requested - set(available))
    if unknown:
        raise ModuleConfigError(
            f"Unbekannte Module in {MODULES_VARIABLE}: {', '.join(unknown)} "
            f"(verfügbar: {', '.join(available)})"
        )
    if not requested:
        raise ModuleConfigError(f"{MODULES_VARIABLE} muss mindestens ein Modul enthalten")
    return [name for name in available if name in requested]


# Name of the application in the page header and titles (e.g. the operator's product name).
APP_TITLE_VARIABLE = "APP_TITLE"
DEFAULT_APP_TITLE = "Posteingangs-Assistent"


def app_title() -> str:
    """Application title from env `APP_TITLE` (default: Posteingangs-Assistent)."""
    return (os.environ.get(APP_TITLE_VARIABLE) or "").strip() or DEFAULT_APP_TITLE
