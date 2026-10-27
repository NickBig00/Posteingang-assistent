"""Rule engine: runs the rules configured in the profile against extracted fields."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import JsonValue
from sqlmodel import Session, select

from app.core.members import Member
from app.inbox.models import RuleOutcome
from app.inbox.profiles import DocumentType, Profile

logger = logging.getLogger(__name__)

FieldValue = str | date | Decimal | bool | int | None


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule may look at. Rules never call an LLM."""

    doc_type: DocumentType
    fields: Mapping[str, FieldValue]
    text: str
    received_on: date
    member: Member | None


@dataclass(frozen=True)
class RuleCheck:
    rule: str
    outcome: RuleOutcome
    reason: str
    data: dict[str, JsonValue] = field(default_factory=dict)


RuleFunction = Callable[[RuleContext, Mapping[str, JsonValue]], RuleCheck]
RULES: dict[str, RuleFunction] = {}


def rule(name: str) -> Callable[[RuleFunction], RuleFunction]:
    def register(function: RuleFunction) -> RuleFunction:
        RULES[name] = function
        return function

    return register


class UnknownRuleError(ValueError):
    """A profile references a rule that does not exist."""


def validate_profile_rules(profile: Profile) -> None:
    """Fail fast if the profile references rules the engine does not know."""
    unknown = sorted(
        {ref.rule for t in profile.document_types for ref in t.rules if ref.rule not in RULES}
    )
    if unknown:
        raise UnknownRuleError(
            f"Profil '{profile.key}' nutzt unbekannte Regeln: {', '.join(unknown)}"
        )


def typed_fields(doc_type: DocumentType, raw: Mapping[str, JsonValue]) -> dict[str, FieldValue]:
    """Convert stored JSON field values back to Python types according to the profile."""
    result: dict[str, FieldValue] = {}
    for spec in doc_type.fields:
        value = raw.get(spec.name)
        if value is None or value == "":
            result[spec.name] = None
        elif spec.type == "date":
            result[spec.name] = date.fromisoformat(str(value))
        elif spec.type == "decimal":
            result[spec.name] = Decimal(str(value))
        elif spec.type in {"boolean", "integer"}:
            result[spec.name] = value  # type: ignore[assignment]
        else:
            result[spec.name] = str(value)
    return result


def run_rules(context: RuleContext) -> list[RuleCheck]:
    """Run all rules of the document type; a crashing rule yields an `error` result."""
    checks = []
    for ref in context.doc_type.rules:
        function = RULES.get(ref.rule)
        if function is None:
            checks.append(RuleCheck(ref.rule, RuleOutcome.ERROR, "Regel ist nicht implementiert"))
            continue
        try:
            checks.append(function(context, ref.params))
        except Exception as error:  # noqa: BLE001 - a rule bug must never pass silently as ok
            # No traceback: exception messages may contain document data.
            logger.error("rule %s failed with %s", ref.rule, type(error).__name__)
            checks.append(
                RuleCheck(
                    ref.rule, RuleOutcome.ERROR, f"Regel fehlgeschlagen ({type(error).__name__})"
                )
            )
    return checks


def find_member(session: Session, member_number: FieldValue) -> Member | None:
    if not isinstance(member_number, str):
        return None
    return session.exec(select(Member).where(Member.member_number == member_number)).first()
