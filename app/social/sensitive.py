"""Checks on health claims, named persons and the address form (SPEC_social section 6).

Code first (term lists, name patterns, member list, du/Sie forms), then the LLM assessment
adds what code cannot see. Findings on health claims and persons are warnings that a person
must confirm before approval; unambiguous healing promises in sensitive areas and members
named in a post are errors.
"""

import re
from collections.abc import Sequence

from app.core.outcomes import Outcome
from app.social.checks import Finding, Span
from app.social.llm.schemas import AssessmentFinding, AssessmentOutput
from app.social.profiles import AddressForm, HealthClaimRules
from app.social.textscan import find_term, place_pattern

HEALTH = "gesundheit"
PERSONS = "personen"
ADDRESS = "anrede"
ASSESSMENT = "ki_einschaetzung"

SENSITIVE_CHECK_LABELS: dict[str, str] = {
    HEALTH: "Gesundheitsversprechen",
    PERSONS: "Personenbezug",
    ADDRESS: "Anrede",
    ASSESSMENT: "KI-Einschätzung",
}

# Common German first names (a lexicon, not personal data). Words that are also ordinary
# German words or abbreviations (Ernst, Max., Mai, Rose ...) are left out on purpose.
FIRST_NAMES = frozenset(
    [
        "Alexander",
        "Alexandra",
        "Andrea",
        "Andreas",
        "Angelika",
        "Anja",
        "Anke",
        "Anna",
        "Anne",
        "Annika",
        "Anton",
        "Barbara",
        "Ben",
        "Benjamin",
        "Bernd",
        "Birgit",
        "Brigitte",
        "Carina",
        "Carsten",
        "Charlotte",
        "Christian",
        "Christiane",
        "Christina",
        "Christine",
        "Christoph",
        "Claudia",
        "Daniel",
        "Daniela",
        "David",
        "Dennis",
        "Dieter",
        "Dirk",
        "Doris",
        "Elena",
        "Elias",
        "Elisabeth",
        "Emil",
        "Emilia",
        "Emily",
        "Emma",
        "Erik",
        "Eva",
        "Fabian",
        "Felix",
        "Finn",
        "Florian",
        "Franziska",
        "Frieda",
        "Gabriele",
        "Georg",
        "Gerhard",
        "Gisela",
        "Hannah",
        "Hannes",
        "Heike",
        "Heinz",
        "Helga",
        "Helmut",
        "Henrik",
        "Ines",
        "Ingrid",
        "Jan",
        "Jana",
        "Janina",
        "Jannik",
        "Jasmin",
        "Jens",
        "Johanna",
        "Johannes",
        "Jonas",
        "Jörg",
        "Julia",
        "Julian",
        "Jürgen",
        "Karin",
        "Katharina",
        "Kathrin",
        "Katrin",
        "Kerstin",
        "Kevin",
        "Klaus",
        "Kristina",
        "Lara",
        "Lea",
        "Lena",
        "Leon",
        "Leonie",
        "Lina",
        "Lisa",
        "Lukas",
        "Luisa",
        "Luise",
        "Maik",
        "Manfred",
        "Manuel",
        "Manuela",
        "Marcel",
        "Marco",
        "Maria",
        "Marie",
        "Mario",
        "Marion",
        "Markus",
        "Martin",
        "Martina",
        "Matthias",
        "Maximilian",
        "Melanie",
        "Mia",
        "Michael",
        "Michaela",
        "Mila",
        "Milena",
        "Monika",
        "Nadine",
        "Natalie",
        "Nicole",
        "Niklas",
        "Nina",
        "Noah",
        "Ole",
        "Oliver",
        "Patrick",
        "Paul",
        "Paula",
        "Peter",
        "Petra",
        "Philipp",
        "Ralf",
        "Ramona",
        "Regina",
        "Renate",
        "Robert",
        "Sabine",
        "Sabrina",
        "Sandra",
        "Sarah",
        "Sascha",
        "Sebastian",
        "Silke",
        "Simon",
        "Simone",
        "Sophia",
        "Sophie",
        "Stefan",
        "Stefanie",
        "Stephan",
        "Susanne",
        "Sven",
        "Svenja",
        "Tanja",
        "Thomas",
        "Tim",
        "Tobias",
        "Tom",
        "Torsten",
        "Ulrike",
        "Ursula",
        "Uwe",
        "Vanessa",
        "Verena",
        "Werner",
        "Wolfgang",
        "Yvonne",
    ]
)
_CAPITALIZED = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
_TITLE = re.compile(r"(?<!\w)(?:Frau|Herrn?|Dr\.|Prof\.)\s+(?:Dr\.\s+)?(" + _CAPITALIZED + ")")
_ROLE = re.compile(
    r"(?<!\w)(?:Trainer(?:in)?|(?:Physio)?[Tt]herapeut(?:in)?|Kolleg(?:e|in)|Mitglied|"
    r"Patient(?:in)?|Kund(?:e|in)|Teilnehmer(?:in)?|Coach|Inhaber(?:in)?|Chef(?:in)?|"
    r"Azubi|Meister(?:in)?)\s+(" + _CAPITALIZED + r")(?:\s+(" + _CAPITALIZED + "))?"
)
_FIRST_NAME = re.compile(
    r"(?<![\w-])(" + "|".join(sorted(FIRST_NAMES, key=len, reverse=True)) + r")(?![\w-])"
    r"(?:\s+(" + _CAPITALIZED + r"))?"
)
# Words after a first name that are no surname (sentence continues).
_NOT_SURNAME = frozenset({"Und", "Oder", "Mit", "Im", "Am", "Ab", "Bei", "Uhr", "Euro"})


def _overlaps(span: Span, spans: Sequence[Span]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


def _dedupe(spans: list[Span]) -> list[Span]:
    result: list[Span] = []
    for span in sorted(spans, key=lambda s: (s[0], -s[1])):
        if not _overlaps(span, result):
            result.append(span)
    return result


# --- health claims ---


def check_health_claims(
    text: str, rules: HealthClaimRules | None, sensitive_area: str | None
) -> list[Finding]:
    """Term list of the industry profile. `sensitive_area`: label of the area if it is
    sensitive (then unambiguous healing promises are errors)."""
    if rules is None:
        return []
    certain = _dedupe([s for term in rules.certain_terms for s in find_term(text, term)])
    general = [
        s
        for s in _dedupe([s for term in rules.terms for s in find_term(text, term)])
        if not _overlaps(s, certain)
    ]
    findings: list[Finding] = []
    for span in certain:
        quote = text[span[0] : span[1]]
        if sensitive_area:
            findings.append(
                Finding(
                    HEALTH,
                    Outcome.ERROR,
                    f"Eindeutiges Heilversprechen „{quote}“ im sensiblen Bereich "
                    f"„{sensitive_area}“ – so nicht freigebbar.",
                    span,
                )
            )
        else:
            findings.append(
                Finding(
                    HEALTH,
                    Outcome.WARNING,
                    f"Heilversprechen „{quote}“ – Werbung mit Gesundheitsbezug ist nur "
                    "eingeschränkt zulässig.",
                    span,
                    requires_confirmation=True,
                )
            )
    for span in general:
        findings.append(
            Finding(
                HEALTH,
                Outcome.WARNING,
                f"Möglicher Gesundheitsbezug „{text[span[0] : span[1]]}“ – bitte prüfen, ob "
                "eine Wirkung versprochen wird.",
                span,
                requires_confirmation=True,
            )
        )
    return sorted(findings, key=lambda f: f.span or (0, 0))


# --- persons ---


def check_persons(text: str, member_names: Sequence[str]) -> list[Finding]:
    """Members named in the post are errors that only their confirmed consent lifts; other
    names (title, role, first name) are warnings that must be confirmed."""
    findings: list[Finding] = []
    taken: list[Span] = []
    for name in member_names:
        for match in place_pattern(name).finditer(text):
            findings.append(
                Finding(
                    PERSONS,
                    Outcome.ERROR,
                    f"„{match.group(0)}“ steht in der Mitgliederliste – Namen von Mitgliedern "
                    "nur mit ihrer dokumentierten Einwilligung.",
                    match.span(),
                    consent_required=True,
                )
            )
            taken.append(match.span())
    candidates: list[Span] = []
    for pattern in (_TITLE, _ROLE):
        candidates += [m.span() for m in pattern.finditer(text)]
    for match in _FIRST_NAME.finditer(text):
        surname = match.group(2)
        end = match.end() if surname and surname not in _NOT_SURNAME else match.end(1)
        candidates.append((match.start(), end))
    for span in _dedupe(candidates):
        if _overlaps(span, taken):
            continue
        findings.append(
            Finding(
                PERSONS,
                Outcome.WARNING,
                f"Person genannt: „{text[span[0] : span[1]]}“ – nur mit Einverständnis der Person "
                "und nie Mitglieder, Patientinnen oder Patienten.",
                span,
                requires_confirmation=True,
            )
        )
        taken.append(span)
    return sorted(findings, key=lambda f: f.span or (0, 0))


# --- address form ---


_DU = re.compile(
    r"(?<!\w)(?:du|dich|dir|dein|deine|deinen|deinem|deiner|deines|euch|euer|eure|euren|"
    r"eurem|eurer)(?!\w)",
    re.IGNORECASE,
)
_SIE = re.compile(r"(?<!\w)(?:Sie|Ihnen|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres)(?!\w)")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s*|\n\s*|[„\"(]\s*)$")


def address_forms(text: str) -> tuple[list[Span], list[Span]]:
    """Spans of du-forms and of capitalised Sie-forms. A capitalised "Sie/Ihr" at the start of
    a sentence is ambiguous (also "she/they") and is not counted, except "Ihnen"."""
    du = [m.span() for m in _DU.finditer(text)]
    sie = []
    for match in _SIE.finditer(text):
        at_start = _SENTENCE_START.search(text[max(0, match.start() - 3) : match.start()])
        if at_start is None or match.group(0) == "Ihnen":
            sie.append(match.span())
    return du, sie


def check_address_form(text: str, expected: AddressForm) -> list[Finding]:
    du, sie = address_forms(text)
    if du and sie:
        first = sorted([du[0], sie[0]])[1]
        return [
            Finding(
                ADDRESS,
                Outcome.WARNING,
                f"„du“ und „Sie“ gemischt (z. B. „{text[first[0] : first[1]]}“).",
                first,
            )
        ]
    if expected is AddressForm.DU and sie:
        return [
            Finding(
                ADDRESS,
                Outcome.WARNING,
                f"Sie-Form „{text[sie[0][0] : sie[0][1]]}“ – in diesem Bereich wird geduzt.",
                sie[0],
            )
        ]
    if expected is AddressForm.SIE and du:
        return [
            Finding(
                ADDRESS,
                Outcome.WARNING,
                f"Du-Form „{text[du[0][0] : du[0][1]]}“ – in diesem Bereich wird gesiezt.",
                du[0],
            )
        ]
    form = "du" if expected is AddressForm.DU else "Sie"
    return [Finding(ADDRESS, Outcome.OK, f"Anrede passt zum Bereich ({form}).")]


# --- merging the LLM assessment ---


def _locate(text: str, quote: str) -> Span | None:
    match = place_pattern(quote).search(text) if quote.split() else None
    if match:
        return match.span()
    position = text.find(quote)
    return (position, position + len(quote)) if position >= 0 else None


def _ai_findings(
    text: str, key: str, items: Sequence[AssessmentFinding], known: Sequence[Span], what: str
) -> list[Finding]:
    findings = []
    for item in items:
        span = _locate(text, item.quote)
        if span is not None and _overlaps(span, known):
            continue  # already found by code
        location = "" if span else f" (Textstelle „{item.quote}“ nicht gefunden)"
        findings.append(
            Finding(
                key,
                Outcome.WARNING,
                f"KI-Einschätzung – {what}: {item.reason}{location}",
                span,
                requires_confirmation=True,
                is_ai_assessment=True,
            )
        )
    return findings


def merge_assessment(
    text: str,
    assessment: AssessmentOutput,
    code_findings: Sequence[Finding],
    expected: AddressForm,
) -> list[Finding]:
    """LLM findings that code did not already report (warnings, labelled as AI assessment)."""
    known = {
        key: [f.span for f in code_findings if f.check_key == key and f.span]
        for key in (HEALTH, PERSONS)
    }
    findings = [
        *_ai_findings(text, HEALTH, assessment.health_claims, known[HEALTH], "Gesundheitsbezug"),
        *_ai_findings(text, PERSONS, assessment.persons, known[PERSONS], "Person genannt"),
    ]
    code_address_warning = any(
        f.check_key == ADDRESS and f.outcome is not Outcome.OK for f in code_findings
    )
    wrong = assessment.address_form == "gemischt" or (
        assessment.address_form in ("du", "sie") and assessment.address_form != expected.value
    )
    if wrong and not code_address_warning:
        findings.append(
            Finding(
                ADDRESS,
                Outcome.WARNING,
                f"KI-Einschätzung: Anrede „{assessment.address_form}“ passt nicht zum Bereich "
                f"(erwartet „{'du' if expected is AddressForm.DU else 'Sie'}“).",
                is_ai_assessment=True,
            )
        )
    return findings


def ok_findings(findings: Sequence[Finding], health_configured: bool) -> list[Finding]:
    """Green results for the checks without findings (so the UI shows what was checked)."""
    keys = {f.check_key for f in findings}
    result = []
    if health_configured and HEALTH not in keys:
        result.append(Finding(HEALTH, Outcome.OK, "Keine Gesundheitsversprechen gefunden."))
    if PERSONS not in keys:
        result.append(Finding(PERSONS, Outcome.OK, "Keine Personen namentlich genannt."))
    return result


def assessment_failed() -> Finding:
    return Finding(
        ASSESSMENT,
        Outcome.WARNING,
        "KI-Einschätzung nicht verfügbar (ungültige Antwort nach erneutem Versuch). Bitte "
        "Gesundheitsbezug, Personen und Anrede selbst prüfen und den Entwurf erneut prüfen lassen.",
        requires_confirmation=True,
    )
