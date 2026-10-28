"""Social media module (docs/SPEC_social.md): briefings, AI drafts, checks, approval, calendar."""

# Register the tables with the shared metadata before `core.db.init_db` runs.
from app.social import models as _models  # noqa: F401
