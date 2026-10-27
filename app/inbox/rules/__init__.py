"""Deterministic rule engine (deadlines, required fields, plausibility checks).

Rules are plain Python code, never LLM output.
"""

from app.inbox.rules import builtin as _builtin  # noqa: F401 - registers the built-in rules
from app.inbox.rules.engine import RULES, RuleCheck, RuleContext, run_rules, validate_profile_rules

__all__ = ["RULES", "RuleCheck", "RuleContext", "run_rules", "validate_profile_rules"]
