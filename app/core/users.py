"""Acting user. The demo has no login yet: every action is taken by the demo user."""

DEMO_USER = "demo"


def current_user() -> str:
    """FastAPI dependency for the person acting in the UI (recorded in reviews and audit)."""
    return DEMO_USER
