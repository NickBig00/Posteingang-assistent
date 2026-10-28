"""Checks of a post draft (SPEC_social section 6). Deterministic code wherever possible.

Every finding carries a reason and, where possible, the marked passage (character span in
`PostDraft.full_text`). Errors block the approval.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.core.outcomes import Outcome
from app.social.channels import CHANNEL_SPECS
from app.social.facts import (
    FACT_LABELS,
    BriefingFacts,
    format_date,
    format_price,
    format_time,
)
from app.social.models import CHANNEL_LABELS, Briefing, Channel, PostDraft
from app.social.profiles import EmojiPolicy, PhraseRule, SocialConfig, TermRule
from app.social.textscan import (
    FREE_WORDS,
    find_dates,
    find_emojis,
    find_hashtags,
    find_phones,
    find_prices,
    find_term,
    find_times,
    normalize_space,
    phone_digits,
    place_pattern,
)

Span = tuple[int, int]

CHECK_LABELS: dict[str, str] = {
    "laenge": "Länge",
    "hashtags": "Hashtags",
    "verbotene_begriffe": "Verbotene Begriffe",
    "fakten": "Fakten",
    "emojis": "Emojis",
}
WEEKDAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


@dataclass(frozen=True)
class Finding:
    check_key: str
    outcome: Outcome
    reason: str
    span: Span | None = None
    requires_confirmation: bool = False
    is_ai_assessment: bool = False
    # Error that only the documented consent of the named person lifts (members in a post).
    consent_required: bool = False


def _ok(key: str, reason: str) -> Finding:
    return Finding(key, Outcome.OK, reason)


# --- length, hashtags, emojis ---


def check_length(text: str, channel: Channel) -> list[Finding]:
    limit = CHANNEL_SPECS[channel].max_length
    if not text.strip():
        return [Finding("laenge", Outcome.ERROR, "Der Beitrag hat keinen Text.")]
    if len(text) > limit:
        return [
            Finding(
                "laenge",
                Outcome.ERROR,
                f"{len(text)} Zeichen – erlaubt sind für {CHANNEL_LABELS[channel]} höchstens "
                f"{limit}.",
                (limit, len(text)),
            )
        ]
    return [_ok("laenge", f"{len(text)} von höchstens {limit} Zeichen.")]


def check_hashtags(text: str, channel: Channel, brand_max: int) -> list[Finding]:
    spans = find_hashtags(text)
    count = len(spans)
    platform_max = CHANNEL_SPECS[channel].max_hashtags
    if platform_max is not None and count > platform_max:
        return [
            Finding(
                "hashtags",
                Outcome.ERROR,
                f"{count} Hashtags – {CHANNEL_LABELS[channel]} erlaubt höchstens {platform_max}.",
                spans[platform_max],
            )
        ]
    if count > brand_max:
        return [
            Finding(
                "hashtags",
                Outcome.WARNING,
                f"{count} Hashtags – das Markenprofil sieht höchstens {brand_max} vor.",
                spans[brand_max],
            )
        ]
    return [_ok("hashtags", f"{count} Hashtags (höchstens {brand_max}).")]


def check_emojis(text: str, policy: EmojiPolicy, area_label: str) -> list[Finding]:
    spans = find_emojis(text)
    if spans and not policy.allowed:
        return [
            Finding(
                "emojis",
                Outcome.WARNING,
                f"Im Bereich „{area_label}“ sind keine Emojis vorgesehen ({len(spans)} gefunden).",
                spans[0],
            )
        ]
    if len(spans) > policy.max:
        return [
            Finding(
                "emojis",
                Outcome.WARNING,
                f"{len(spans)} Emojis – im Bereich „{area_label}“ höchstens {policy.max}.",
                spans[policy.max],
            )
        ]
    return [_ok("emojis", f"{len(spans)} Emojis.")]


# --- terms ---


def check_forbidden_terms(text: str, terms: Sequence[str]) -> list[Finding]:
    findings = [
        Finding(
            "verbotene_begriffe",
            Outcome.ERROR,
            f"Verbotener Begriff laut Markenprofil: „{text[start:end]}“.",
            (start, end),
        )
        for term in terms
        for start, end in find_term(text, term)
    ]
    return findings or [_ok("verbotene_begriffe", "Keine verbotenen Begriffe.")]


def check_term_rules(text: str, rules: Sequence[TermRule]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules:
        hits = [span for term in rule.terms for span in find_term(text, term)]
        if not hits:
            findings.append(_ok(rule.key, f"{rule.label}: nichts gefunden."))
        for start, end in sorted(hits):
            findings.append(
                Finding(
                    rule.key,
                    Outcome(rule.outcome),
                    f"„{text[start:end]}“: {rule.reason}",
                    (start, end),
                )
            )
    return findings


def check_phrase_rules(
    text: str, rules: Sequence[PhraseRule], facts: BriefingFacts
) -> list[Finding]:
    findings: list[Finding] = []
    normalized = normalize_space(text)
    for rule in rules:
        if not facts.has(rule.when_fact):
            continue
        if any(normalize_space(phrase) in normalized for phrase in rule.phrases):
            findings.append(_ok(rule.key, f"{rule.label}: vorhanden."))
        else:
            findings.append(Finding(rule.key, Outcome(rule.outcome), rule.reason))
    return findings


# --- facts from the briefing ---


def _check_date(text: str, label: str, value: date, others: Sequence[date]) -> Finding:
    mentions = find_dates(text)
    expected = format_date(value)
    for mention in mentions:
        if mention.matches(value):
            span = (mention.start, mention.end)
            if mention.weekday is not None and mention.weekday != value.weekday():
                return Finding(
                    "fakten",
                    Outcome.ERROR,
                    f"Wochentag passt nicht: {expected} ist ein {WEEKDAY_NAMES[value.weekday()]}.",
                    span,
                )
            return Finding("fakten", Outcome.OK, f"{label} {expected} ist enthalten.", span)
    different = [m for m in mentions if not any(m.matches(o) for o in others)]
    if different:
        first = different[0]
        return Finding(
            "fakten",
            Outcome.ERROR,
            f"{label} weicht ab: im Text „{text[first.start : first.end]}“, im Briefing "
            f"{expected}.",
            (first.start, first.end),
        )
    return Finding("fakten", Outcome.ERROR, f"{label} {expected} fehlt im Text.")


def check_facts(text: str, facts: BriefingFacts) -> list[Finding]:
    """Every fact of the briefing must appear unchanged (in any accepted spelling)."""
    findings: list[Finding] = []
    date_facts = [d for d in (facts.datum, facts.zeitraum_von, facts.zeitraum_bis) if d]
    if facts.datum:
        others = [d for d in date_facts if d != facts.datum]
        findings.append(_check_date(text, FACT_LABELS["datum"], facts.datum, others))
    if facts.zeitraum_von and facts.zeitraum_bis:
        for label, value in (("Beginn", facts.zeitraum_von), ("Ende", facts.zeitraum_bis)):
            others = [d for d in date_facts if d != value]
            findings.append(_check_date(text, f"Zeitraum ({label})", value, others))
    if facts.uhrzeit:
        expected = format_time(facts.uhrzeit)
        times = find_times(text)
        match = next((t for t in times if t.value == facts.uhrzeit), None)
        if match:
            span = (match.start, match.end)
            findings.append(
                Finding("fakten", Outcome.OK, f"Uhrzeit {expected} ist enthalten.", span)
            )
        elif times:
            first = times[0]
            findings.append(
                Finding(
                    "fakten",
                    Outcome.ERROR,
                    f"Uhrzeit weicht ab: im Text „{text[first.start : first.end]}“, im Briefing "
                    f"{expected}.",
                    (first.start, first.end),
                )
            )
        else:
            findings.append(Finding("fakten", Outcome.ERROR, f"Uhrzeit {expected} fehlt im Text."))
    if facts.preis is not None:
        expected = format_price(facts.preis)
        prices = find_prices(text)
        candidates = [(p.value, p.start, p.end) for p in prices]
        if facts.preis == 0:
            candidates += [(facts.preis, *m.span()) for m in FREE_WORDS.finditer(text)]
        match = next((c for c in candidates if c[0] == facts.preis), None)
        if match:
            findings.append(
                Finding("fakten", Outcome.OK, f"Preis {expected} ist enthalten.", match[1:])
            )
        elif prices:
            first = prices[0]
            findings.append(
                Finding(
                    "fakten",
                    Outcome.ERROR,
                    f"Preis weicht ab: im Text „{text[first.start : first.end]}“, im Briefing "
                    f"{expected}.",
                    (first.start, first.end),
                )
            )
        else:
            findings.append(Finding("fakten", Outcome.ERROR, f"Preis {expected} fehlt im Text."))
    if facts.ort:
        match_place = place_pattern(facts.ort).search(text)
        if match_place:
            findings.append(
                Finding(
                    "fakten", Outcome.OK, f"Ort „{facts.ort}“ ist enthalten.", match_place.span()
                )
            )
        else:
            findings.append(
                Finding(
                    "fakten",
                    Outcome.ERROR,
                    f"Ort „{facts.ort}“ fehlt im Text oder wurde verändert.",
                )
            )
    if facts.telefon:
        expected_digits = phone_digits(facts.telefon)
        phones = find_phones(text)
        match_phone = next((p for p in phones if p.digits == expected_digits), None)
        if match_phone:
            span = (match_phone.start, match_phone.end)
            findings.append(
                Finding("fakten", Outcome.OK, f"Telefonnummer {facts.telefon} ist enthalten.", span)
            )
        elif phones:
            first = phones[0]
            findings.append(
                Finding(
                    "fakten",
                    Outcome.ERROR,
                    f"Telefonnummer weicht ab: im Text „{text[first.start : first.end]}“, im "
                    f"Briefing {facts.telefon}.",
                    (first.start, first.end),
                )
            )
        else:
            findings.append(
                Finding("fakten", Outcome.ERROR, f"Telefonnummer {facts.telefon} fehlt im Text.")
            )
    return findings


# --- all deterministic checks of a draft ---


@dataclass(frozen=True)
class CheckInput:
    config: SocialConfig
    area_key: str
    channel: Channel
    facts: BriefingFacts = field(default_factory=BriefingFacts)


def deterministic_checks(text: str, check: CheckInput) -> list[Finding]:
    brand = check.config.brand
    area = brand.area(check.area_key)
    industry = check.config.industry
    return [
        *check_length(text, check.channel),
        *check_hashtags(text, check.channel, brand.max_hashtags),
        *check_facts(text, check.facts),
        *check_forbidden_terms(text, brand.forbidden_terms),
        *check_term_rules(text, industry.term_rules),
        *check_phrase_rules(text, industry.phrase_rules, check.facts),
        *check_emojis(text, area.emojis, area.label),
    ]


def check_input(config: SocialConfig, briefing: Briefing, draft: PostDraft) -> CheckInput:
    return CheckInput(
        config=config,
        area_key=briefing.area,
        channel=draft.channel,
        facts=BriefingFacts.from_json(briefing.facts),
    )
