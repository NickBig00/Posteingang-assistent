"""Retention period shared by all modules (deletion concept, SPEC section 7)."""

import os

DEFAULT_RETENTION_DAYS = 90


def retention_days() -> int:
    """Retention period in days from env `RETENTION_DAYS` (default 90, minimum 1)."""
    raw = os.environ.get("RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        days = int(raw)
    except ValueError as error:
        raise ValueError(f"RETENTION_DAYS must be an integer, got {raw!r}") from error
    if days < 1:
        raise ValueError("RETENTION_DAYS must be at least 1")
    return days
