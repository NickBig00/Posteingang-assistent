from datetime import date

import pytest

from app.rules.dates import (
    add_months,
    contract_term_ends,
    earliest_contract_end,
    notice_deadline,
    term_end,
)


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 1, 15), 1, date(2026, 2, 15)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),  # clamp to shorter month
        (date(2028, 1, 31), 1, date(2028, 2, 29)),  # leap year
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 12, 31), 2, date(2027, 2, 28)),  # year change
        (date(2026, 1, 1), -1, date(2025, 12, 1)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),  # leap day + 1 year
        (date(2024, 2, 29), 48, date(2028, 2, 29)),
        (date(2026, 5, 31), 0, date(2026, 5, 31)),
    ],
)
def test_add_months(day: date, months: int, expected: date) -> None:
    assert add_months(day, months) == expected


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2024, 3, 1), 12, date(2025, 2, 28)),
        (date(2023, 3, 1), 12, date(2024, 2, 29)),  # ends in leap February
        (date(2025, 1, 15), 24, date(2027, 1, 14)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),  # § 188 (3) BGB: no 31.02. → month end
        (date(2027, 12, 31), 2, date(2028, 2, 29)),
        (date(2026, 1, 30), 1, date(2026, 2, 28)),
    ],
)
def test_term_end(start: date, months: int, expected: date) -> None:
    assert term_end(start, months) == expected


@pytest.mark.parametrize(
    ("end", "notice", "expected"),
    [
        (date(2026, 10, 31), 1, date(2026, 9, 30)),  # month end stays month end
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
        (date(2028, 3, 31), 1, date(2028, 2, 29)),  # leap year
        (date(2028, 2, 29), 1, date(2028, 1, 31)),
        (date(2027, 1, 14), 1, date(2026, 12, 14)),  # mid-month end
        (date(2027, 5, 31), 3, date(2027, 2, 28)),
        (date(2026, 10, 31), 0, date(2026, 10, 31)),  # no notice period
    ],
)
def test_notice_deadline(end: date, notice: int, expected: date) -> None:
    assert notice_deadline(end, notice) == expected


def test_contract_term_ends_monthly_after_minimum_term() -> None:
    ends = contract_term_ends(date(2024, 3, 1), 12, 1, until=date(2025, 5, 15))

    assert ends == [date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31)]


@pytest.mark.parametrize(
    ("received", "expected"),
    [
        (date(2024, 6, 1), date(2025, 2, 28)),  # during minimum term
        (date(2025, 1, 31), date(2025, 2, 28)),  # exactly on the deadline
        (date(2025, 2, 1), date(2025, 3, 31)),  # one day late
        (date(2026, 9, 30), date(2026, 10, 31)),  # monthly renewal, last day
        (date(2026, 10, 1), date(2026, 11, 30)),
    ],
)
def test_earliest_contract_end_monthly(received: date, expected: date) -> None:
    assert earliest_contract_end(date(2024, 3, 1), 12, 1, 1, received) == expected


def test_earliest_contract_end_yearly_renewal_with_three_months_notice() -> None:
    start = date(2023, 6, 1)

    assert earliest_contract_end(start, 12, 12, 3, date(2026, 2, 28)) == date(2026, 5, 31)
    assert earliest_contract_end(start, 12, 12, 3, date(2026, 3, 1)) == date(2027, 5, 31)
