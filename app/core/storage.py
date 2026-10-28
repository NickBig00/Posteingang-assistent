"""Local file storage shared by all modules (git-ignored, see `.gitignore`)."""

import os
from pathlib import Path

from app.core.config import PROJECT_ROOT


def storage_dir() -> Path:
    """Directory for raw incoming files (env `STORAGE_DIR`, default `data/raw`)."""
    return Path(os.environ.get("STORAGE_DIR", PROJECT_ROOT / "data" / "raw"))


def get_storage_dir() -> Path:
    """FastAPI dependency for the storage directory (overridden in tests)."""
    return storage_dir()
