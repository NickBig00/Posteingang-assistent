"""Loading profiles from YAML: reading, schema validation and readable error messages.

The schemas themselves belong to the modules (e.g. `app.inbox.profiles.Profile`); the
active industry profile (env `PROFILE`) is shared by all modules.
"""

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from app.core.config import PROJECT_ROOT

PROFILES_DIR = PROJECT_ROOT / "profiles"
DEFAULT_PROFILE = "fitness"

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
# Top-level keys of an industry profile that belong to a module other than the inbox.
MODULE_SECTIONS = frozenset({"social"})


class ProfileError(ValueError):
    """Raised when a profile file is missing, not valid YAML or violates the schema."""


def _format_validation_error(error: ValidationError, prefix: str = "") -> str:
    lines = []
    for item in error.errors():
        location = prefix + ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def load_yaml_profile[M: BaseModel](path: Path, schema: type[M], section: str | None = None) -> M:
    """Load a YAML file and validate it against `schema`. Raises ProfileError.

    An industry profile carries the settings of several modules: the inbox part at the top
    level and one section per further module (`MODULE_SECTIONS`, e.g. `social:`). Without
    `section`, the module sections are left out; with `section`, only that section is
    validated.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProfileError(f"Profil '{path}' nicht gefunden") from error
    except yaml.YAMLError as error:
        raise ProfileError(f"Profil '{path}' ist kein gültiges YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ProfileError(f"Profil '{path}' muss ein YAML-Objekt sein")
    if section is None:
        data = {k: v for k, v in raw.items() if k not in MODULE_SECTIONS}
    else:
        data = raw.get(section)
        if not isinstance(data, dict):
            raise ProfileError(f"Profil '{path}' hat keinen gültigen Abschnitt '{section}:'")
    try:
        return schema.model_validate(data)
    except ValidationError as error:
        details = _format_validation_error(error, prefix=f"{section}." if section else "")
        raise ProfileError(f"Profil '{path}' ist ungültig:\n{details}") from error


def active_profile_key() -> str:
    return os.environ.get("PROFILE", DEFAULT_PROFILE)


def active_profile_path(profiles_dir: Path = PROFILES_DIR) -> Path:
    """File of the profile selected via env `PROFILE` (default: fitness)."""
    key = active_profile_key()
    if not re.fullmatch(KEY_PATTERN, key):
        raise ProfileError(f"Ungültiger Profilname: {key!r}")
    return profiles_dir / f"{key}.yaml"
