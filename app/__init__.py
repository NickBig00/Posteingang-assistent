"""Posteingangs-Assistent demo application."""

from app.core.config import load_env_file

# Load `.env` from the project root before any module reads its configuration
# (e.g. app.core.db builds the engine from DATABASE_URL at import time).
load_env_file()
