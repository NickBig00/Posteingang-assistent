"""Loading local configuration from the `.env` file in the project root."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
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
