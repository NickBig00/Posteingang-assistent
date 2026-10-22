import os

# Tests must never pick up a developer's local `.env` (API keys, LLM_PROVIDER=anthropic, ...):
# this runs before the `app` package is imported by any test module or conftest.
os.environ["POSTEINGANG_NO_DOTENV"] = "1"
