"""Hard facts of a briefing (date, time, price, place, ...), structured and typed.

They must reach the post unchanged; the check lives in `app.social.checks`.
"""

from datetime import date, time
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

FactKey = Literal["datum", "uhrzeit", "preis", "ort", "zeitraum", "telefon"]
FACT_KEYS: tuple[FactKey, ...] = ("datum", "uhrzeit", "preis", "ort", "zeitraum", "telefon")

FACT_LABELS: dict[FactKey, str] = {
    "datum": "Datum",
    "uhrzeit": "Uhrzeit",
    "preis": "Preis",
    "ort": "Ort",
    "zeitraum": "Zeitraum",
    "telefon": "Telefonnummer",
}


class BriefingFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    datum: date | None = None
    uhrzeit: time | None = None
    preis: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    ort: str | None = Field(default=None, min_length=1, max_length=200)
    zeitraum_von: date | None = None
    zeitraum_bis: date | None = None
    telefon: str | None = Field(default=None, pattern=r"^\+?[0-9][0-9 /-]{4,24}[0-9]$")

    @model_validator(mode="after")
    def _check_period(self) -> Self:
        if (self.zeitraum_von is None) != (self.zeitraum_bis is None):
            raise ValueError("Zeitraum braucht Beginn und Ende")
        if self.zeitraum_von and self.zeitraum_bis and self.zeitraum_von > self.zeitraum_bis:
            raise ValueError("Zeitraum: Beginn liegt nach dem Ende")
        return self

    def has(self, key: FactKey) -> bool:
        if key == "zeitraum":
            return self.zeitraum_von is not None
        return getattr(self, key) is not None

    def present_keys(self) -> list[FactKey]:
        return [key for key in FACT_KEYS if self.has(key)]

    def missing(self, required: list[FactKey]) -> list[FactKey]:
        return [key for key in required if not self.has(key)]

    def to_json(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_json(cls, data: dict[str, JsonValue]) -> "BriefingFacts":
        return cls.model_validate(data)


# --- display format (prompt and UI); the checks accept further spellings ---


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def format_time(value: time) -> str:
    return f"{value:%H:%M} Uhr"


def format_price(value: Decimal) -> str:
    whole, _, cents = f"{value:.2f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{grouped},{cents} €"


def formatted_facts(facts: BriefingFacts) -> dict[FactKey, str]:
    """German spelling of every given fact, e.g. {"preis": "19,00 €"}."""
    result: dict[FactKey, str] = {}
    if facts.datum:
        result["datum"] = format_date(facts.datum)
    if facts.uhrzeit:
        result["uhrzeit"] = format_time(facts.uhrzeit)
    if facts.preis is not None:
        result["preis"] = format_price(facts.preis)
    if facts.ort:
        result["ort"] = facts.ort
    if facts.zeitraum_von and facts.zeitraum_bis:
        result["zeitraum"] = (
            f"{format_date(facts.zeitraum_von)} bis {format_date(facts.zeitraum_bis)}"
        )
    if facts.telefon:
        result["telefon"] = facts.telefon
    return result


def parse_price_input(text: str) -> Decimal:
    """Parse a price typed in the form: "19", "19,5", "19.50", "1.200,00" (euros)."""
    cleaned = text.strip().removesuffix("€").removesuffix("EUR").strip().replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(".") == 1 and len(cleaned.rpartition(".")[2]) == 3:
        cleaned = cleaned.replace(".", "")  # "1.200" is a thousands separator
    try:
        value = Decimal(cleaned)
    except ArithmeticError as error:
        raise ValueError(f"kein gültiger Preis: {text!r}") from error
    if not value.is_finite():
        raise ValueError(f"kein gültiger Preis: {text!r}")
    return value.quantize(Decimal("0.01"))
