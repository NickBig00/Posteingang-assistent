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
