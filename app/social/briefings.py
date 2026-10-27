"""Briefing input: deterministic validation of the form, including the required facts."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time

from pydantic import ValidationError
from sqlmodel import Session

from app.social.facts import FACT_LABELS, BriefingFacts, parse_price_input
from app.social.models import Briefing, Channel
from app.social.profiles import SocialConfig

MAX_NOTES_LENGTH = 2000
# Form fields of the facts; `zeitraum` has two fields.
FACT_FIELDS = ("datum", "uhrzeit", "preis", "ort", "zeitraum_von", "zeitraum_bis", "telefon")


@dataclass(frozen=True)
class BriefingInput:
    area: str
    occasion_type: str
    notes: str
    channels: list[Channel]
    facts: BriefingFacts
    desired_date: date | None = None


class BriefingFormError(ValueError):
    """The form has errors; `errors` maps field names to German messages."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _value(form: Mapping[str, str], name: str) -> str:
    return (form.get(name) or "").strip()


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def _parse_time(text: str) -> time:
    return time.fromisoformat(text)


def parse_briefing_form(
    form: Mapping[str, str], channels: list[str], config: SocialConfig
) -> BriefingInput:
    """Validate the raw form values. Raises BriefingFormError with all problems at once."""
    errors: dict[str, str] = {}
    area = _value(form, "area")
    if area not in {a.key for a in config.brand.areas}:
        errors["area"] = "Bitte einen Bereich wählen."
    occasion = _value(form, "occasion_type")
    if occasion not in {o.key for o in config.industry.occasion_types}:
        errors["occasion_type"] = "Bitte einen Anlass wählen."
    notes = _value(form, "notes")
    if not notes:
        errors["notes"] = "Bitte Stichpunkte angeben."
    elif len(notes) > MAX_NOTES_LENGTH:
        errors["notes"] = f"Höchstens {MAX_NOTES_LENGTH} Zeichen."
    selected: list[Channel] = []
    for value in dict.fromkeys(channels):
        try:
            selected.append(Channel(value))
        except ValueError:
            errors["channels"] = f"Unbekannter Kanal: {value}"
    if not selected and "channels" not in errors:
        errors["channels"] = "Bitte mindestens einen Kanal wählen."

    raw: dict[str, object] = {}
    parsers = {
        "datum": (_parse_date, "Ungültiges Datum."),
        "zeitraum_von": (_parse_date, "Ungültiges Datum."),
        "zeitraum_bis": (_parse_date, "Ungültiges Datum."),
        "uhrzeit": (_parse_time, "Ungültige Uhrzeit."),
        "preis": (parse_price_input, "Ungültiger Preis."),
    }
    for name in FACT_FIELDS:
        text = _value(form, name)
        if not text:
            continue
        parser, message = parsers.get(name, (str, ""))
        try:
            raw[name] = parser(text)
        except ValueError:
            errors[name] = message
    facts = BriefingFacts()
    try:
        facts = BriefingFacts.model_validate(raw)
    except ValidationError as error:
        for item in error.errors():
            field = str(item["loc"][0]) if item["loc"] else "zeitraum_von"
            errors.setdefault(field, _fact_message(field, item["msg"]))
    if occasion in {o.key for o in config.industry.occasion_types} and not errors.keys() & set(
        FACT_FIELDS
    ):
        label = config.industry.occasion_type(occasion).label
        for key in facts.missing(config.industry.occasion_type(occasion).required_facts):
            field = "zeitraum_von" if key == "zeitraum" else key
            errors[field] = f"{FACT_LABELS[key]} ist Pflichtangabe für den Anlass „{label}“."

    desired: date | None = None
    if text := _value(form, "desired_date"):
        try:
            desired = _parse_date(text)
        except ValueError:
            errors["desired_date"] = "Ungültiges Datum."
    if errors:
        raise BriefingFormError(errors)
    return BriefingInput(
        area=area,
        occasion_type=occasion,
        notes=notes,
        channels=selected,
        facts=facts,
        desired_date=desired,
    )


def _fact_message(field: str, message: str) -> str:
    if field == "telefon":
        return "Ungültige Telefonnummer."
    if field == "preis":
        return "Ungültiger Preis."
    return message.removeprefix("Value error, ")


def create_briefing(
    session: Session, config: SocialConfig, data: BriefingInput, user: str, **extra: object
) -> Briefing:
    briefing = Briefing(
        created_by=user,
        brand_key=config.brand.key,
        area=data.area,
        occasion_type=data.occasion_type,
        notes=data.notes,
        facts=data.facts.to_json(),
        channels=[c.value for c in data.channels],
        desired_date=data.desired_date,
        **extra,
    )
    session.add(briefing)
    session.commit()
    session.refresh(briefing)
    return briefing
