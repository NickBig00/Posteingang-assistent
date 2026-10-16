"""Built-in rules. Every result carries a German reason shown to the reviewer."""

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import JsonValue

from app.models import RuleOutcome
from app.rules.dates import contract_term_ends, earliest_contract_end, notice_deadline, term_end
from app.rules.engine import RuleCheck, RuleContext, rule

CENT = Decimal("0.01")


def _fmt(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def _eur(amount: Decimal) -> str:
    return f"{amount:.2f} EUR"


@rule("required_fields")
def required_fields(context: RuleContext, params: Mapping[str, JsonValue]) -> RuleCheck:
    """All fields marked as required in the profile (or listed in `fields`) are present."""
    names = params.get("fields")
    specs = [
        spec
        for spec in context.doc_type.fields
        if (spec.name in names if isinstance(names, list) else spec.required)
    ]
    missing = [spec.name for spec in specs if context.fields.get(spec.name) is None]
    if missing:
        return RuleCheck(
            "required_fields",
            RuleOutcome.ERROR,
            f"Pflichtangaben fehlen: {', '.join(missing)}",
            {"missing": list(missing)},
        )
    return RuleCheck("required_fields", RuleOutcome.OK, "Alle Pflichtangaben vorhanden")


@rule("notice_period")
def notice_period(context: RuleContext, params: Mapping[str, JsonValue]) -> RuleCheck:
    """Notice period kept? Computes the earliest possible contract end from contract data."""
    name = "notice_period"
    member = context.member
    if member is None:
        return RuleCheck(
            name,
            RuleOutcome.WARNING,
            "Keine Vertragsdaten zur Mitgliedsnummer gefunden – Frist nicht prüfbar",
        )
    received = context.fields.get("eingangsdatum")
    if not isinstance(received, date):
        received = context.received_on
    if received < member.contract_start:
        return RuleCheck(
            name, RuleOutcome.WARNING, "Eingang liegt vor Vertragsbeginn – bitte prüfen"
        )

    earliest = earliest_contract_end(
        member.contract_start,
        member.minimum_term_months,
        member.renewal_period_months,
        member.notice_period_months,
        received,
    )
    data: dict[str, JsonValue] = {
        "vertragsende": earliest.isoformat(),
        "eingang": received.isoformat(),
        "frist_bis": notice_deadline(earliest, member.notice_period_months).isoformat(),
    }
    requested = context.fields.get("gewuenschtes_kuendigungsdatum")
    if not isinstance(requested, date):
        return RuleCheck(
            name, RuleOutcome.OK, f"Frühestmögliches Vertragsende: {_fmt(earliest)}", data
        )
    if requested < earliest:
        # Not in time: a human must decide (manual review); the earliest end is suggested.
        return RuleCheck(
            name,
            RuleOutcome.ERROR,
            f"Kündigungsfrist zum {_fmt(requested)} nicht eingehalten; "
            f"frühestmögliches Vertragsende: {_fmt(earliest)}",
            data,
        )
    ends = contract_term_ends(
        member.contract_start, member.minimum_term_months, member.renewal_period_months, requested
    )
    if requested not in ends:
        effective = next(end for end in ends if end >= requested)
        data["vertragsende"] = effective.isoformat()
        return RuleCheck(
            name,
            RuleOutcome.WARNING,
            f"{_fmt(requested)} ist kein Laufzeitende; der Vertrag endet zum {_fmt(effective)}",
            data,
        )
    data["vertragsende"] = requested.isoformat()
    return RuleCheck(
        name, RuleOutcome.OK, f"Frist eingehalten; Vertragsende zum {_fmt(requested)}", data
    )


@rule("pause_period")
def pause_period(context: RuleContext, params: Mapping[str, JsonValue]) -> RuleCheck:
    """Pause period complete, in order, not in the past and not longer than `max_months`."""
    name = "pause_period"
    start, end = context.fields.get("zeitraum_von"), context.fields.get("zeitraum_bis")
    if not isinstance(start, date) or not isinstance(end, date):
        return RuleCheck(name, RuleOutcome.WARNING, "Zeitraum unvollständig – nicht prüfbar")
    if end < start:
        return RuleCheck(name, RuleOutcome.ERROR, "Ende der Pause liegt vor dem Beginn")
    max_months = params.get("max_months")
    if isinstance(max_months, int):
        latest_end = term_end(start, max_months)
        if end > latest_end:
            return RuleCheck(
                name,
                RuleOutcome.ERROR,
                f"Pause überschreitet die Maximaldauer von {max_months} Monaten "
                f"(höchstens bis {_fmt(latest_end)})",
                {"max_bis": latest_end.isoformat()},
            )
    received = context.fields.get("eingangsdatum")
    received = received if isinstance(received, date) else context.received_on
    if start < received:
        return RuleCheck(
            name,
            RuleOutcome.WARNING,
            f"Beginn {_fmt(start)} liegt vor dem Eingang {_fmt(received)}",
        )
    days = (end - start).days + 1
    return RuleCheck(
        name,
        RuleOutcome.OK,
        f"Zeitraum {_fmt(start)} bis {_fmt(end)} gültig ({days} Tage)",
        {"tage": days},
    )


def _amounts(text: str, patterns: JsonValue) -> list[Decimal]:
    found: list[Decimal] = []
    for pattern in patterns if isinstance(patterns, list) else [patterns]:
        if not isinstance(pattern, str):
            continue
        for match in re.finditer(pattern, text, re.MULTILINE):
            try:
                found.append(Decimal(match.group("amount").replace(",", ".")))
            except (InvalidOperation, IndexError):
                continue
    return found


@rule("invoice_totals")
def invoice_totals(context: RuleContext, params: Mapping[str, JsonValue]) -> RuleCheck:
    """Positions add up to net, net + VAT = gross at an allowed rate, gross = extracted amount.

    Amounts are read deterministically from the document text with the regular
    expressions configured in the profile (named group `amount`).
    """
    name = "invoice_totals"
    amount = context.fields.get("betrag")
    if not isinstance(amount, Decimal):
        return RuleCheck(name, RuleOutcome.WARNING, "Kein Betrag extrahiert – nicht prüfbar")
    if amount <= 0:
        return RuleCheck(name, RuleOutcome.ERROR, f"Betrag {_eur(amount)} ist nicht positiv")

    nets = _amounts(context.text, params.get("net_patterns"))
    vats = _amounts(context.text, params.get("vat_patterns"))
    grosses = _amounts(context.text, params.get("gross_patterns"))
    positions = _amounts(context.text, params.get("position_patterns"))
    if not (nets and vats and grosses):
        return RuleCheck(
            name,
            RuleOutcome.WARNING,
            "Netto/Steuer/Brutto nicht im Dokument gefunden – nicht prüfbar",
        )
    net, vat, gross = nets[0], vats[0], grosses[0]
    problems = []
    if positions and sum(positions) != net:
        problems.append(
            f"Positionen ergeben {_eur(sum(positions, Decimal(0)))}, Netto ist {_eur(net)}"
        )
    if (net + vat - gross).copy_abs() > CENT:
        problems.append(f"Netto + Steuer ({_eur(net + vat)}) ≠ Brutto ({_eur(gross)})")
    rates = [Decimal(str(r)) for r in params.get("vat_rates") or []]  # type: ignore[union-attr]
    if rates and not any((net * r - vat).copy_abs() <= CENT for r in rates):
        problems.append(f"Steuer {_eur(vat)} passt zu keinem zulässigen Steuersatz")
    if gross != amount:
        problems.append(f"Extrahierter Betrag {_eur(amount)} ≠ Brutto im Dokument ({_eur(gross)})")
    if problems:
        return RuleCheck(name, RuleOutcome.ERROR, "; ".join(problems))
    return RuleCheck(name, RuleOutcome.OK, f"Summen plausibel (Brutto {_eur(gross)})")
