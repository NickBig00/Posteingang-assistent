from collections.abc import Mapping
from datetime import date
from decimal import Decimal

import pytest
from pydantic import JsonValue
from sqlmodel import Session

from app.models import Member, RuleOutcome
from app.profiles import PROFILES_DIR, DocumentType, Profile, load_profile
from app.rules import RULES, RuleContext, run_rules, validate_profile_rules
from app.rules.builtin import invoice_totals, notice_period, pause_period, required_fields
from app.rules.engine import FieldValue, UnknownRuleError, find_member, rule, typed_fields
from app.seed import seed_members

RECEIVED = date(2026, 10, 15)


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


def _member(**overrides: object) -> Member:
    values: dict[str, object] = {
        "member_number": "M-99999",
        "name": "Fiktiv Mitglied",
        "email": "fiktiv@example.com",
        "contract_start": date(2024, 3, 1),
        "minimum_term_months": 12,
        "notice_period_months": 1,
        "renewal_period_months": 1,
        "monthly_fee_eur": Decimal("29.90"),
    } | overrides
    return Member(**values)


def _ctx(
    doc_type: DocumentType,
    fields: Mapping[str, FieldValue],
    member: Member | None = None,
    text: str = "",
) -> RuleContext:
    complete = {spec.name: None for spec in doc_type.fields} | dict(fields)
    return RuleContext(doc_type, complete, text, RECEIVED, member)


# --- profile / engine ---


def test_all_profile_rules_are_implemented(profile: Profile) -> None:
    validate_profile_rules(profile)
    assert {"required_fields", "notice_period", "pause_period", "invoice_totals"} <= set(RULES)


def test_unknown_rule_in_profile_is_rejected(profile: Profile) -> None:
    data = profile.model_dump()
    data["document_types"][0]["rules"].append({"rule": "gibt_es_nicht", "params": {}})
    broken = Profile.model_validate(data)

    with pytest.raises(UnknownRuleError, match="gibt_es_nicht"):
        validate_profile_rules(broken)


def test_crashing_rule_becomes_error_result(profile: Profile) -> None:
    @rule("test_crash")
    def crash(context: RuleContext, params: Mapping[str, JsonValue]) -> None:
        raise RuntimeError("bug")

    data = profile.document_type("sonstiges").model_dump()
    data["rules"] = [{"rule": "test_crash", "params": {}}]
    doc_type = DocumentType.model_validate(data)
    try:
        checks = run_rules(_ctx(doc_type, {}))
    finally:
        del RULES["test_crash"]

    assert checks[0].outcome == RuleOutcome.ERROR
    assert "RuntimeError" in checks[0].reason


def test_typed_fields_converts_json(profile: Profile) -> None:
    fields = typed_fields(
        profile.document_type("rechnung"),
        {"rechnungsdatum": "2026-09-10", "betrag": "499.80", "e_rechnung": False, "lieferant": ""},
    )

    assert fields["rechnungsdatum"] == date(2026, 9, 10)
    assert fields["betrag"] == Decimal("499.80")
    assert fields["e_rechnung"] is False
    assert fields["lieferant"] is None
    assert fields["rechnungsnummer"] is None


def test_find_member(session: Session) -> None:
    seed_members(session)

    assert find_member(session, "M-10004") is not None
    assert find_member(session, "M-00000") is None
    assert find_member(session, None) is None


# --- required_fields ---


def test_required_fields_ok(profile: Profile) -> None:
    doc_type = profile.document_type("probetraining_anfrage")

    check = required_fields(_ctx(doc_type, {"name": "A", "kontakt": "a@example.com"}), {})

    assert check.outcome == RuleOutcome.OK


def test_required_fields_missing(profile: Profile) -> None:
    doc_type = profile.document_type("rechnung")

    check = required_fields(_ctx(doc_type, {"lieferant": "X", "betrag": Decimal("1.00")}), {})

    assert check.outcome == RuleOutcome.ERROR
    assert check.data["missing"] == ["rechnungsnummer", "rechnungsdatum", "e_rechnung"]


def test_required_fields_false_boolean_counts_as_present(profile: Profile) -> None:
    doc_type = profile.document_type("rechnung")
    fields = {
        "lieferant": "X",
        "rechnungsnummer": "1",
        "rechnungsdatum": date(2026, 1, 1),
        "betrag": Decimal("1.00"),
        "e_rechnung": False,
    }

    assert required_fields(_ctx(doc_type, fields), {}).outcome == RuleOutcome.OK


def test_required_fields_explicit_list(profile: Profile) -> None:
    doc_type = profile.document_type("probetraining_anfrage")

    check = required_fields(_ctx(doc_type, {"name": "A"}), {"fields": ["kontakt"]})

    assert check.outcome == RuleOutcome.ERROR
    assert "kontakt" in check.reason


# --- notice_period ---


def _kuendigung(profile: Profile, received: date | None, requested: date | None) -> RuleContext:
    return _ctx(
        profile.document_type("kuendigung"),
        {"eingangsdatum": received, "gewuenschtes_kuendigungsdatum": requested},
        _member(),
    )


def test_notice_without_member_is_warning(profile: Profile) -> None:
    ctx = _ctx(profile.document_type("kuendigung"), {"eingangsdatum": RECEIVED})

    check = notice_period(ctx, {})

    assert check.outcome == RuleOutcome.WARNING
    assert "nicht prüfbar" in check.reason


def test_notice_kept_exactly_on_deadline(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, date(2026, 9, 30), date(2026, 10, 31)), {})

    assert check.outcome == RuleOutcome.OK
    assert check.data["vertragsende"] == "2026-10-31"
    assert check.data["frist_bis"] == "2026-09-30"


def test_notice_one_day_late(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, date(2026, 10, 1), date(2026, 10, 31)), {})

    assert check.outcome == RuleOutcome.WARNING
    assert "nicht eingehalten" in check.reason
    assert check.data["vertragsende"] == "2026-11-30"


def test_notice_next_possible_date(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, date(2026, 9, 14), None), {})

    assert check.outcome == RuleOutcome.OK
    assert check.data["vertragsende"] == "2026-10-31"
    assert "31.10.2026" in check.reason


def test_notice_requested_date_is_not_a_term_end(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, date(2026, 9, 1), date(2026, 11, 15)), {})

    assert check.outcome == RuleOutcome.WARNING
    assert check.data["vertragsende"] == "2026-11-30"


def test_notice_during_minimum_term(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, date(2024, 6, 1), date(2024, 12, 31)), {})

    assert check.outcome == RuleOutcome.WARNING
    assert check.data["vertragsende"] == "2025-02-28"


def test_notice_leap_year_term_end(profile: Profile) -> None:
    ctx = _ctx(
        profile.document_type("kuendigung"),
        {"eingangsdatum": date(2028, 1, 31), "gewuenschtes_kuendigungsdatum": date(2028, 2, 29)},
        _member(contract_start=date(2027, 3, 1)),
    )

    check = notice_period(ctx, {})

    assert check.outcome == RuleOutcome.OK
    assert check.data["vertragsende"] == "2028-02-29"


def test_notice_missing_receipt_date_falls_back_to_received_on(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, None, None), {})

    assert check.data["eingang"] == RECEIVED.isoformat()
    assert check.data["vertragsende"] == "2026-11-30"


def test_notice_received_before_contract_start(profile: Profile) -> None:
    check = notice_period(_kuendigung(profile, date(2024, 1, 1), None), {})

    assert check.outcome == RuleOutcome.WARNING
    assert "vor Vertragsbeginn" in check.reason


# --- pause_period ---


def _pause(profile: Profile, start: date | None, end: date | None) -> RuleContext:
    return _ctx(
        profile.document_type("vertragspause"),
        {"zeitraum_von": start, "zeitraum_bis": end, "eingangsdatum": None},
    )


def test_pause_ok(profile: Profile) -> None:
    check = pause_period(_pause(profile, date(2026, 11, 1), date(2027, 1, 31)), {"max_months": 6})

    assert check.outcome == RuleOutcome.OK
    assert check.data["tage"] == 92


def test_pause_exactly_max_duration(profile: Profile) -> None:
    check = pause_period(_pause(profile, date(2026, 11, 1), date(2027, 4, 30)), {"max_months": 6})

    assert check.outcome == RuleOutcome.OK


def test_pause_one_day_too_long(profile: Profile) -> None:
    check = pause_period(_pause(profile, date(2026, 11, 1), date(2027, 5, 1)), {"max_months": 6})

    assert check.outcome == RuleOutcome.ERROR
    assert check.data["max_bis"] == "2027-04-30"


def test_pause_max_duration_over_leap_february(profile: Profile) -> None:
    ctx = _pause(profile, date(2027, 12, 31), date(2028, 2, 29))

    assert pause_period(ctx, {"max_months": 2}).outcome == RuleOutcome.OK
    ctx = _pause(profile, date(2027, 12, 31), date(2028, 3, 1))
    assert pause_period(ctx, {"max_months": 2}).outcome == RuleOutcome.ERROR


def test_pause_end_before_start(profile: Profile) -> None:
    check = pause_period(_pause(profile, date(2026, 12, 31), date(2026, 12, 1)), {})

    assert check.outcome == RuleOutcome.ERROR


def test_pause_single_day(profile: Profile) -> None:
    check = pause_period(_pause(profile, date(2026, 12, 1), date(2026, 12, 1)), {})

    assert check.outcome == RuleOutcome.OK
    assert check.data["tage"] == 1


def test_pause_incomplete(profile: Profile) -> None:
    assert pause_period(_pause(profile, date(2026, 12, 1), None), {}).outcome == RuleOutcome.WARNING


def test_pause_starting_in_the_past(profile: Profile) -> None:
    check = pause_period(_pause(profile, date(2026, 10, 1), date(2026, 10, 31)), {})

    assert check.outcome == RuleOutcome.WARNING
    assert "vor dem Eingang" in check.reason


# --- invoice_totals ---


def _invoice_params(profile: Profile) -> Mapping[str, JsonValue]:
    return next(
        r for r in profile.document_type("rechnung").rules if r.rule == "invoice_totals"
    ).params


def _invoice_text(positions: list[str], net: str, vat: str, gross: str) -> str:
    lines = ["Rechnungsnummer: R-1", *(f"Position {i}: {p} EUR" for i, p in enumerate(positions))]
    lines += [
        f"Nettobetrag: {net} EUR",
        f"Umsatzsteuer 19 %: {vat} EUR",
        f"Gesamtbetrag: {gross} EUR",
    ]
    return "\n".join(lines)


def _invoice(profile: Profile, amount: Decimal | None, text: str) -> RuleContext:
    return _ctx(profile.document_type("rechnung"), {"betrag": amount}, text=text)


def test_invoice_plausible(profile: Profile) -> None:
    text = _invoice_text(["300.00", "120.00"], "420.00", "79.80", "499.80")

    check = invoice_totals(_invoice(profile, Decimal("499.80"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.OK


def test_invoice_positions_do_not_add_up(profile: Profile) -> None:
    text = _invoice_text(["200.00", "100.00"], "350.00", "66.50", "416.50")

    check = invoice_totals(_invoice(profile, Decimal("416.50"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.ERROR
    assert "Positionen ergeben 300.00 EUR" in check.reason


def test_invoice_net_plus_vat_not_gross(profile: Profile) -> None:
    text = _invoice_text([], "100.00", "19.00", "120.00")

    check = invoice_totals(_invoice(profile, Decimal("120.00"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.ERROR
    assert "≠ Brutto" in check.reason


def test_invoice_reduced_vat_rate_accepted(profile: Profile) -> None:
    text = _invoice_text([], "100.00", "7.00", "107.00")

    check = invoice_totals(_invoice(profile, Decimal("107.00"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.OK


def test_invoice_unknown_vat_rate(profile: Profile) -> None:
    text = _invoice_text([], "100.00", "10.00", "110.00")

    check = invoice_totals(_invoice(profile, Decimal("110.00"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.ERROR
    assert "Steuersatz" in check.reason


def test_invoice_rounding_tolerance_one_cent(profile: Profile) -> None:
    text = _invoice_text([], "33.33", "6.33", "39.66")  # 33.33 * 0.19 = 6.3327

    check = invoice_totals(_invoice(profile, Decimal("39.66"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.OK


def test_invoice_extracted_amount_differs(profile: Profile) -> None:
    text = _invoice_text([], "100.00", "19.00", "119.00")

    check = invoice_totals(_invoice(profile, Decimal("100.00"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.ERROR
    assert "Extrahierter Betrag" in check.reason


def test_invoice_xrechnung(profile: Profile) -> None:
    text = (
        '<cbc:TaxAmount currencyID="EUR">47.50</cbc:TaxAmount>'
        '<cbc:TaxExclusiveAmount currencyID="EUR">250.00</cbc:TaxExclusiveAmount>'
        '<cbc:PayableAmount currencyID="EUR">297.50</cbc:PayableAmount>'
    )

    check = invoice_totals(_invoice(profile, Decimal("297.50"), text), _invoice_params(profile))

    assert check.outcome == RuleOutcome.OK


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(None, RuleOutcome.WARNING), (Decimal("0.00"), RuleOutcome.ERROR)],
)
def test_invoice_missing_or_zero_amount(
    profile: Profile, amount: Decimal | None, expected: RuleOutcome
) -> None:
    check = invoice_totals(
        _invoice(profile, amount, "Gesamtbetrag: 0.00 EUR"), _invoice_params(profile)
    )

    assert check.outcome == expected


def test_invoice_without_totals_in_text(profile: Profile) -> None:
    check = invoice_totals(
        _invoice(profile, Decimal("10.00"), "nur Text"), _invoice_params(profile)
    )

    assert check.outcome == RuleOutcome.WARNING
    assert "nicht prüfbar" in check.reason
