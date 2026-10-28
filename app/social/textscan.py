"""Finding dates, times, prices and phone numbers in German post texts (deterministic).

Each finder returns mentions with their character span, normalised to comparable values,
so that "3.11.", "03.11.2026" and "3. November" all mean the same day and
"19 €", "19,00 EUR" and "€ 19,-" the same price.
"""

import re
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation

MONTHS: dict[str, int] = {
    "januar": 1, "jan": 1, "jänner": 1,
    "februar": 2, "feb": 2, "febr": 2,
    "märz": 3, "maerz": 3, "mär": 3, "mrz": 3,
    "april": 4, "apr": 4,
    "mai": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}  # fmt: skip
WEEKDAYS: dict[str, int] = {
    "montag": 0, "mo": 0,
    "dienstag": 1, "di": 1,
    "mittwoch": 2, "mi": 2,
    "donnerstag": 3, "do": 3,
    "freitag": 4, "fr": 4,
    "samstag": 5, "sa": 5, "sonnabend": 5,
    "sonntag": 6, "so": 6,
}  # fmt: skip

_MONTH = r"(?P<{name}>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_DASH = r"\s*(?:–|-|—|bis(?:\s+(?:zum|einschließlich))?)\s*"
# Four-digit year (optionally after a space) or two-digit year directly after the dot;
# "3.11. 18 Uhr" must not read as the year 2018.
_YEAR = r"(?:\s?(?P<{name}>\d{{4}})(?![\d]|[:.,]\d)|(?P<{name}_short>\d{{2}})(?![\d]|[:.,]\d))?"


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_ISO = _compile(r"(?<!\d)(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_NUM_RANGE = _compile(
    r"(?<![\d.])(?P<d1>\d{1,2})\.(?:\s?(?P<m1>\d{1,2})\.)?"
    + _DASH
    + r"(?P<d2>\d{1,2})\.\s?(?P<m2>\d{1,2})\."
    + _YEAR.format(name="y")
)
_TEXT_RANGE = _compile(
    r"(?<![\d.])(?P<d1>\d{1,2})\.(?:\s*"
    + _MONTH.format(name="m1")
    + r")?"
    + _DASH
    + r"(?P<d2>\d{1,2})\.\s*"
    + _MONTH.format(name="m2")
    + r"(?:\s+(?P<y>\d{4}))?"
)
_NUM_DATE = _compile(r"(?<![\d.])(?P<d>\d{1,2})\.\s?(?P<m>\d{1,2})\." + _YEAR.format(name="y"))
_TEXT_DATE = _compile(
    r"(?<![\d.])(?P<d>\d{1,2})\.\s*" + _MONTH.format(name="m") + r"(?![a-zäöü])(?:\s+(?P<y>\d{4}))?"
)
_WEEKDAY_BEFORE = _compile(
    r"(?P<w>" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\.?,?\s*(?:den\s+)?$"
)


@dataclass(frozen=True)
class DateMention:
    day: int
    month: int
    year: int | None
    start: int
    end: int
    # Weekday named directly before the date (0 = Monday), if any.
    weekday: int | None = None

    def matches(self, value: date) -> bool:
        return (self.day, self.month) == (value.day, value.month) and self.year in (
            None,
            value.year,
        )

    @property
    def label(self) -> str:
        return f"{self.day}.{self.month}." + (str(self.year) if self.year else "")


def _month(value: str) -> int:
    return int(value) if value.isdigit() else MONTHS[value.lower().rstrip(".")]


def _year(match: re.Match[str], name: str = "y") -> int | None:
    groups = match.groupdict()
    value = groups.get(name) or groups.get(f"{name}_short")
    if not value:
        return None
    return 2000 + int(value) if len(value) == 2 else int(value)


def _valid(day: int, month: int, year: int | None) -> bool:
    try:
        date(year or 2000, month, day)  # 2000: leap year, so 29.2. without year is valid
    except ValueError:
        return False
    return True


def _weekday_before(text: str, start: int) -> int | None:
    match = _WEEKDAY_BEFORE.search(text[max(0, start - 20) : start])
    return WEEKDAYS[match.group("w").lower()] if match else None


def find_dates(text: str) -> list[DateMention]:
    """All dates in `text`, ranges as two mentions sharing the span of the whole range."""
    mentions: list[DateMention] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    def add(day: int, month: int, year: int | None, start: int, end: int) -> None:
        if _valid(day, month, year):
            weekday = _weekday_before(text, start)
            mentions.append(DateMention(day, month, year, start, end, weekday))

    for match in _ISO.finditer(text):
        add(int(match["d"]), int(match["m"]), int(match["y"]), *match.span())
        taken.append(match.span())
    for pattern in (_NUM_RANGE, _TEXT_RANGE):
        for match in pattern.finditer(text):
            if not free(*match.span()):
                continue
            end_month = _month(match["m2"])
            start_month = _month(match["m1"]) if match["m1"] else end_month
            year = _year(match)
            if not (
                _valid(int(match["d1"]), start_month, year)
                and _valid(int(match["d2"]), end_month, year)
            ):
                continue
            add(int(match["d1"]), start_month, year, *match.span())
            add(int(match["d2"]), end_month, year, *match.span())
            taken.append(match.span())
    for pattern in (_NUM_DATE, _TEXT_DATE):
        for match in pattern.finditer(text):
            if not free(*match.span()):
                continue
            add(int(match["d"]), _month(match["m"]), _year(match), *match.span())
            taken.append(match.span())
    return sorted(mentions, key=lambda m: (m.start, m.day))


# --- times ---


_TIME_COLON = _compile(r"(?<![\d.:,])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?![\d:])(?:\s*Uhr)?")
_TIME_DOT = _compile(r"(?<![\d.:,])(?P<h>[01]?\d|2[0-3])\.(?P<m>[0-5]\d)\s*Uhr")
_TIME_HOUR = _compile(r"(?<![\d.:,])(?P<h>[01]?\d|2[0-3])\s*Uhr(?P<m>)")


@dataclass(frozen=True)
class TimeMention:
    value: time
    start: int
    end: int


def find_times(text: str) -> list[TimeMention]:
    mentions: list[TimeMention] = []
    taken: list[tuple[int, int]] = []
    for pattern in (_TIME_COLON, _TIME_DOT, _TIME_HOUR):
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(not (end <= s or start >= e) for s, e in taken):
                continue
            minute = int(match["m"] or 0)
            mentions.append(TimeMention(time(int(match["h"]), minute), start, end))
            taken.append((start, end))
    return sorted(mentions, key=lambda m: m.start)


# --- prices ---


_AMOUNT = (
    r"(?P<{name}>\d{{1,3}}(?:\.\d{{3}})+(?:,(?:\d{{1,2}}|-|–))?"
    r"|\d+(?:,(?:\d{{1,2}}|-|–)|\.\d{{2}})?)"
)
_CURRENCY = r"(?:€|EUR\b|Euro\b)"
_PRICE = _compile(
    r"(?<![\d.,])(?:"
    + _AMOUNT.format(name="a")
    + r"\s*"
    + _CURRENCY
    + r"|"
    + _CURRENCY
    + r"\s*"
    + _AMOUNT.format(name="b")
    + r")"
)
FREE_WORDS = _compile(r"(?<!\w)(?:kostenlos|kostenfrei|gratis|umsonst)(?!\w)")


@dataclass(frozen=True)
class PriceMention:
    value: Decimal
    start: int
    end: int


def parse_amount(text: str) -> Decimal | None:
    """German or plain amount ("1.200,50", "19,-", "19.00") as Decimal."""
    value = text.replace("–", "-").removesuffix(",-")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", value):
        value = value.replace(".", "")
    value = value.replace(",", ".")
    try:
        return Decimal(value).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def find_prices(text: str) -> list[PriceMention]:
    mentions = []
    for match in _PRICE.finditer(text):
        amount = parse_amount(match["a"] or match["b"])
        if amount is not None:
            mentions.append(PriceMention(amount, *match.span()))
    return sorted(mentions, key=lambda m: m.start)


# --- phone numbers ---


_PHONE = _compile(r"(?<![\w.,])\+?\d[\d /()-]{4,}\d(?![\w])")


@dataclass(frozen=True)
class PhoneMention:
    digits: str
    start: int
    end: int


def phone_digits(text: str) -> str:
    """Digits of a phone number in national form: "+49 1234 5678" -> "012345678"."""
    digits = re.sub(r"\D", "", text)
    if text.strip().startswith("+49"):
        return "0" + digits[2:]
    if digits.startswith("0049"):
        return "0" + digits[4:]
    return digits


def find_phones(text: str) -> list[PhoneMention]:
    mentions = []
    for match in _PHONE.finditer(text):
        digits = phone_digits(match.group(0))
        if len(digits) >= 6:
            mentions.append(PhoneMention(digits, *match.span()))
    return mentions


# --- terms ---


def term_pattern(term: str) -> re.Pattern[str]:
    """Case-insensitive match of a term at a word start (also inflected: "billig" ~ "billiger")."""
    words = [re.escape(word) for word in term.split()]
    return _compile(r"(?<!\w)" + r"\s+".join(words))


def find_term(text: str, term: str) -> list[tuple[int, int]]:
    return [match.span() for match in term_pattern(term).finditer(text)]


def place_pattern(place: str) -> re.Pattern[str]:
    """A place name as whole words, whitespace-tolerant ("Kursraum 2" is not "Kursraum 21")."""
    words = [re.escape(word) for word in place.split()]
    return _compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)")


def normalize_space(text: str) -> str:
    return " ".join(text.split()).casefold()


# A hashtag needs at least one letter ("#2" is none); "##tag" counts once.
_HASHTAG = re.compile(r"(?<![\w#])#+\w*[^\W\d_]\w*")
_EMOJI = re.compile("[\U0001f300-\U0001faff\U0001f000-\U0001f2ff☀-➿⬀-⯿\U0001f900-\U0001f9ff]")


def find_hashtags(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _HASHTAG.finditer(text)]


def find_emojis(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _EMOJI.finditer(text)]
