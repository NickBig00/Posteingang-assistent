"""Action suggestions and reply drafts (SPEC section 3, step 6).

The action is chosen deterministically from the profile (first matching condition).
The reply is rendered from the profile template with deterministic values; the LLM
may only rephrase it. A draft is rejected if it drops or changes any number, date or
amount of the template.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import JsonValue

from app.llm.client import LLMCall, LLMClient
from app.profiles import ActionSpec, DocumentType, Profile
from app.rules import RuleCheck
from app.rules.engine import FieldValue

AI_LABEL = "[KI-Vorschlag – bitte vor dem Versand prüfen]"

_env = SandboxedEnvironment(  # noqa: S701 - plain-text replies, not HTML
    undefined=StrictUndefined,
    autoescape=False,
    finalize=lambda value: "" if value is None else value,
)
_FACT_TOKEN = re.compile(r"[\w./,-]*\d[\w./,-]*")


class TemplateRenderError(ValueError):
    """The reply template of an action cannot be filled with the available values."""


def _format(value: FieldValue | JsonValue) -> FieldValue | JsonValue:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Decimal):
        return f"{value:.2f}".replace(".", ",") + " EUR"
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    return value


def template_values(
    fields: Mapping[str, FieldValue], checks: Sequence[RuleCheck]
) -> dict[str, FieldValue | JsonValue]:
    """Extracted fields plus deterministic rule data, formatted for German text."""
    values: dict[str, FieldValue | JsonValue] = {k: _format(v) for k, v in fields.items()}
    for check in checks:
        for key, value in check.data.items():
            values.setdefault(key, _format(value))
    return values


def select_action(
    doc_type: DocumentType,
    values: Mapping[str, FieldValue | JsonValue],
    checks: Sequence[RuleCheck],
) -> ActionSpec | None:
    """Return the first action whose conditions match (the last action is the default)."""
    outcomes = {check.rule: check.outcome.value for check in checks}
    for action in doc_type.actions:
        if action.is_default:
            return action
        if any(values.get(name) in (None, "") for name in action.when_missing):
            return action
        if any(outcomes.get(rule) in allowed for rule, allowed in action.when_rule.items()):
            return action
    return None


def render_template(template: str, values: Mapping[str, FieldValue | JsonValue]) -> str:
    try:
        return _env.from_string(template).render(**values).strip()
    except TemplateError as error:
        raise TemplateRenderError(f"Vorlage nicht anwendbar: {error}") from error


def missing_facts(template_text: str, draft: str) -> list[str]:
    """Numbers, dates and amounts of the template that do not appear in the draft."""
    tokens = {token.strip(".,") for token in _FACT_TOKEN.findall(template_text)}
    return sorted(token for token in tokens if token and token not in draft)


def label_draft(draft: str) -> str:
    """Mark the text itself as AI suggestion (transparency requirement)."""
    return f"{AI_LABEL}\n\n{draft.strip()}"


def draft_reply(
    client: LLMClient, profile: Profile, template_text: str, document_text: str
) -> LLMCall[str]:
    """Ask the LLM to adapt the rendered template; reject drafts that alter facts."""
    call = client.draft(template_text, document_text, profile)
    if call.output is not None:
        missing = missing_facts(template_text, call.output)
        if missing:
            call.error = f"invalid output: draft changes facts ({', '.join(missing)})"
            call.output = None
    return call
