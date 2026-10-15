"""Calendar arithmetic for deadlines (month ends and leap years handled explicitly)."""

import calendar
from datetime import date, timedelta


def add_months(day: date, months: int) -> date:
    """Shift `day` by whole months; the day is clamped to the target month's length."""
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def term_end(start: date, months: int) -> date:
    """Last day of a term of `months` months beginning on `start` (inclusive).

    Follows § 188 BGB: the term ends the day before the corresponding day of the
    end month; if that month has no corresponding day, it ends on its last day.
    """
    corresponding = add_months(start, months)
    if corresponding.day < start.day:
        return corresponding
    return corresponding - timedelta(days=1)


def notice_deadline(end: date, notice_months: int) -> date:
    """Last day on which a notice must be received to end the contract on `end`.

    Computed from the day after the end so that month ends stay month ends:
    end 31.10. with one month notice → deadline 30.09.; end 29.02. → 31.01.
    """
    return add_months(end + timedelta(days=1), -notice_months) - timedelta(days=1)


def contract_term_ends(
    start: date, minimum_months: int, renewal_months: int, until: date
) -> list[date]:
    """All possible contract end dates from the minimum term up to (and including) `until`."""
    ends = []
    offset = minimum_months
    while True:
        end = term_end(start, offset)
        ends.append(end)
        if end >= until:
            return ends
        offset += renewal_months


def earliest_contract_end(
    start: date, minimum_months: int, renewal_months: int, notice_months: int, received: date
) -> date:
    """First contract end for which a notice received on `received` is still in time."""
    offset = minimum_months
    while True:
        end = term_end(start, offset)
        if received <= notice_deadline(end, notice_months):
            return end
        offset += renewal_months
