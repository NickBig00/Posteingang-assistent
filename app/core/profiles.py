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


class ProfileError(ValueError):
    """Raised when a profile file is missing, not valid YAML or violates the schema."""


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def load_yaml_profile[M: BaseModel](path: Path, schema: type[M]) -> M:
    """Load a YAML file and validate it against `schema`. Raises ProfileError."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProfileError(f"Profil '{path}' nicht gefunden") from error
    except yaml.YAMLError as error:
        raise ProfileError(f"Profil '{path}' ist kein gültiges YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ProfileError(f"Profil '{path}' muss ein YAML-Objekt sein")
    try:
        return schema.model_validate(raw)
    except ValidationError as error:
        details = _format_validation_error(error)
        raise ProfileError(f"Profil '{path}' ist ungültig:\n{details}") from error


def active_profile_key() -> str:
    return os.environ.get("PROFILE", DEFAULT_PROFILE)


def active_profile_path(profiles_dir: Path = PROFILES_DIR) -> Path:
    """File of the profile selected via env `PROFILE` (default: fitness)."""
    key = active_profile_key()
    if not re.fullmatch(KEY_PATTERN, key):
        raise ProfileError(f"Ungültiger Profilname: {key!r}")
    return profiles_dir / f"{key}.yaml"
