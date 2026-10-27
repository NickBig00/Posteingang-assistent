"""Deterministic checks of post drafts (ROADMAP_social S3)."""

from datetime import date, time
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.core.llm.fake import FakeProvider
from app.core.outcomes import Outcome
from app.social.briefings import create_briefing
from app.social.checks import (
    CheckInput,
    Finding,
    check_emojis,
    check_facts,
    check_forbidden_terms,
    check_hashtags,
    check_length,
    check_phrase_rules,
    check_term_rules,
    deterministic_checks,
    run_checks,
)
from app.social.drafts import generate_drafts
from app.social.facts import BriefingFacts
from app.social.highlight import highlight
from app.social.llm.client import SocialLLMClient
from app.social.models import Channel, CheckResult, PostDraft
from app.social.profiles import EmojiPolicy, PhraseRule, TermRule
from app.social.textscan import find_dates, find_phones, find_prices, find_times
from tests.social_helpers import course_input, demo_config

CONFIG = demo_config()
COURSE = BriefingFacts(datum=date(2026, 11, 3), uhrzeit=time(18, 30), ort="Kursraum 2")


def _outcomes(findings: list[Finding]) -> list[Outcome]:
    return [f.outcome for f in findings]


def _fact(text: str, facts: BriefingFacts) -> Finding:
    (finding,) = check_facts(text, facts)
    return finding


# --- text scanner: dates ---


@pytest.mark.parametrize(
    "text",
    [
        "am 3.11.",
        "am 03.11.",
        "am 3.11.2026",
        "am 03.11.2026",
        "am 3. 11. 2026",
        "am 3.11.26",
        "am 3. November",
        "am 3. November 2026",
        "am 3. Nov.",
        "am 3. Nov 2026",
        "am Dienstag, 3. November",
        "am Di., 03.11.",
        "2026-11-03",
    ],
)
def test_date_spellings_match(text: str) -> None:
    assert _fact(text, BriefingFacts(datum=date(2026, 11, 3))).outcome is Outcome.OK


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("am 4.11.", "4.11."),
        ("am 3.12.", "3.12."),
        ("am 4. November", "4. November"),
        ("am 3.11.2025", "3.11.2025"),
        ("am 11.3.", "11.3."),
    ],
)
def test_changed_date_is_an_error(text: str, shown: str) -> None:
    finding = _fact(text, BriefingFacts(datum=date(2026, 11, 3)))

    assert finding.outcome is Outcome.ERROR
    assert f"im Text „{shown}“" in finding.reason
    assert finding.span is not None
    assert text[finding.span[0] : finding.span[1]] == shown


def test_missing_date() -> None:
    finding = _fact("Neuer Kurs, bald geht's los!", BriefingFacts(datum=date(2026, 11, 3)))

    assert finding.outcome is Outcome.ERROR
    assert finding.reason == "Datum 03.11.2026 fehlt im Text."
    assert finding.span is None


def test_wrong_weekday() -> None:
    # 3.11.2026 is a Tuesday.
    finding = _fact("am Mittwoch, 3. November", BriefingFacts(datum=date(2026, 11, 3)))

    assert finding.outcome is Outcome.ERROR
    assert "ist ein Dienstag" in finding.reason


def test_date_found_among_other_dates() -> None:
    text = "Anmeldung bis 30.10., Kursstart am 3.11."

    assert _fact(text, BriefingFacts(datum=date(2026, 11, 3))).outcome is Outcome.OK


@pytest.mark.parametrize(
    "text",
    ["31.11.", "32.1.", "3.13.", "Version 2.0", "19.50 €", "18.30 Uhr", "Tel. 01234 567890"],
)
def test_no_false_dates(text: str) -> None:
    assert find_dates(text) == []


def test_february_29_without_year_is_a_date() -> None:
    assert [m.label for m in find_dates("am 29.2.")] == ["29.2."]


def test_time_after_date_is_not_a_year() -> None:
    (mention,) = find_dates("am 3.11. 18 Uhr")
    assert mention.year is None


@pytest.mark.parametrize(
    "text",
    [
        "vom 1.–14.11.",
        "vom 1.-14.11.2026",
        "vom 1.11. bis 14.11.",
        "vom 01.11.2026 bis 14.11.2026",
        "vom 1. bis 14. November",
        "vom 1. bis zum 14. November 2026",
        "1. November – 14. November",
    ],
)
def test_period_spellings(text: str) -> None:
    facts = BriefingFacts(zeitraum_von=date(2026, 11, 1), zeitraum_bis=date(2026, 11, 14))

    assert _outcomes(check_facts(text, facts)) == [Outcome.OK, Outcome.OK]


def test_period_across_months() -> None:
    facts = BriefingFacts(zeitraum_von=date(2026, 10, 28), zeitraum_bis=date(2026, 11, 3))

    assert _outcomes(check_facts("28. Oktober bis 3. November", facts)) == [Outcome.OK] * 2


def test_period_with_changed_end() -> None:
    facts = BriefingFacts(zeitraum_von=date(2026, 11, 1), zeitraum_bis=date(2026, 11, 14))

    findings = check_facts("vom 1.–15.11.", facts)

    assert _outcomes(findings) == [Outcome.OK, Outcome.ERROR]
    assert "Zeitraum (Ende) weicht ab" in findings[1].reason


def test_period_dates_do_not_count_as_changed_date() -> None:
    facts = BriefingFacts(
        datum=date(2026, 11, 20), zeitraum_von=date(2026, 11, 1), zeitraum_bis=date(2026, 11, 14)
    )

    findings = check_facts("Aktion vom 1.–14.11.", facts)

    assert findings[0].outcome is Outcome.ERROR
    assert findings[0].reason == "Datum 20.11.2026 fehlt im Text."


# --- times ---


@pytest.mark.parametrize(
    "text", ["um 18:30", "um 18:30 Uhr", "um 18.30 Uhr", "um 18.30Uhr", "ab 18:30h"]
)
def test_time_spellings(text: str) -> None:
    assert _fact(text, BriefingFacts(uhrzeit=time(18, 30))).outcome is Outcome.OK


@pytest.mark.parametrize("text", ["um 18 Uhr", "um 18:00 Uhr", "um 18.00 Uhr", "18:00"])
def test_full_hour(text: str) -> None:
    assert _fact(text, BriefingFacts(uhrzeit=time(18, 0))).outcome is Outcome.OK


def test_changed_time() -> None:
    finding = _fact("um 19:30 Uhr", BriefingFacts(uhrzeit=time(18, 30)))

    assert finding.outcome is Outcome.ERROR
    assert "im Text „19:30 Uhr“" in finding.reason


@pytest.mark.parametrize("text", ["19.30 €", "Tel. 18 30", "am 18.11.", "Saal 18"])
def test_no_false_times(text: str) -> None:
    assert find_times(text) == []


# --- prices ---


@pytest.mark.parametrize(
    "text",
    [
        "nur 19 €",
        "nur 19€",
        "nur 19,00 €",
        "nur 19,00 EUR",
        "nur 19 EUR",
        "nur 19 Euro",
        "nur € 19",
        "nur EUR 19,00",
        "nur 19,- €",
        "nur 19,– €",
        "nur 19.00 €",
    ],
)
def test_price_spellings(text: str) -> None:
    assert _fact(text, BriefingFacts(preis=Decimal("19.00"))).outcome is Outcome.OK


def test_price_with_cents_and_thousands() -> None:
    assert _fact("1.200,50 €", BriefingFacts(preis=Decimal("1200.50"))).outcome is Outcome.OK
    assert _fact("19,90 €", BriefingFacts(preis=Decimal("19.90"))).outcome is Outcome.OK


@pytest.mark.parametrize("text", ["nur 19,90 €", "nur 1,90 €", "nur 190 €", "nur 9 €"])
def test_changed_price(text: str) -> None:
    finding = _fact(text, BriefingFacts(preis=Decimal("19.00")))

    assert finding.outcome is Outcome.ERROR
    assert "Preis weicht ab" in finding.reason


def test_price_without_currency_is_not_found() -> None:
    finding = _fact("nur 19 im Monat", BriefingFacts(preis=Decimal("19.00")))

    assert finding.outcome is Outcome.ERROR
    assert finding.reason == "Preis 19,00 € fehlt im Text."


def test_free_offer() -> None:
    assert _fact("Probetraining kostenlos", BriefingFacts(preis=Decimal("0"))).outcome is Outcome.OK


def test_free_word_is_no_price() -> None:
    assert find_prices("14 Tage kostenlos probetrainieren") == []


# --- place and phone ---


@pytest.mark.parametrize("text", ["im Kursraum 2", "im kursraum  2", "Kursraum 2!"])
def test_place(text: str) -> None:
    assert _fact(text, BriefingFacts(ort="Kursraum 2")).outcome is Outcome.OK


@pytest.mark.parametrize("text", ["im Kursraum 21", "im Kursraum", "im Raum 2"])
def test_changed_place(text: str) -> None:
    finding = _fact(text, BriefingFacts(ort="Kursraum 2"))

    assert finding.outcome is Outcome.ERROR
    assert "fehlt im Text oder wurde verändert" in finding.reason


@pytest.mark.parametrize(
    "text",
    ["Tel. 01234 567890", "Tel. 01234-567890", "Tel. 01234/567890", "Tel. +49 1234 567890"],
)
def test_phone_spellings(text: str) -> None:
    assert _fact(text, BriefingFacts(telefon="01234 567890")).outcome is Outcome.OK


def test_changed_phone() -> None:
    finding = _fact("Tel. 01234 567891", BriefingFacts(telefon="01234 567890"))

    assert finding.outcome is Outcome.ERROR
    assert "Telefonnummer weicht ab" in finding.reason


def test_dates_and_prices_are_no_phone_numbers() -> None:
    assert find_phones("am 03.11.2026 für 1.200,00 €, um 18:30 Uhr") == []


def test_all_course_facts_ok() -> None:
    text = "Rücken-Fit am Dienstag, 3. November, um 18:30 Uhr im Kursraum 2."

    assert _outcomes(check_facts(text, COURSE)) == [Outcome.OK] * 3


def test_no_facts_no_findings() -> None:
    assert check_facts("Unser Team stellt sich vor.", BriefingFacts()) == []


# --- length ---


def test_length_limits() -> None:
    assert check_length("x" * 2200, Channel.INSTAGRAM_POST)[0].outcome is Outcome.OK
    finding = check_length("x" * 2201, Channel.INSTAGRAM_POST)[0]
    assert finding.outcome is Outcome.ERROR
    assert finding.span == (2200, 2201)
    assert "höchstens 2200" in finding.reason
    assert check_length("x" * 251, Channel.INSTAGRAM_STORY)[0].outcome is Outcome.ERROR
    assert check_length("x" * 3000, Channel.LINKEDIN)[0].outcome is Outcome.OK
    assert check_length("x" * 3001, Channel.LINKEDIN)[0].outcome is Outcome.ERROR


def test_empty_text_is_an_error() -> None:
    assert check_length("  ", Channel.FACEBOOK)[0].reason == "Der Beitrag hat keinen Text."


# --- hashtags ---


def _tags(count: int) -> str:
    return "Text " + " ".join(f"#tag{i}" for i in range(count))


def test_hashtags_within_brand_limit() -> None:
    assert check_hashtags(_tags(6), Channel.INSTAGRAM_POST, 6)[0].outcome is Outcome.OK


def test_hashtags_over_brand_limit_warn() -> None:
    finding = check_hashtags(_tags(7), Channel.INSTAGRAM_POST, 6)[0]

    assert finding.outcome is Outcome.WARNING
    assert finding.span is not None


def test_hashtags_over_platform_limit_error() -> None:
    assert check_hashtags(_tags(30), Channel.INSTAGRAM_POST, 6)[0].outcome is Outcome.WARNING
    finding = check_hashtags(_tags(31), Channel.INSTAGRAM_POST, 6)[0]
    assert finding.outcome is Outcome.ERROR
    assert "höchstens 30" in finding.reason
    assert check_hashtags(_tags(11), Channel.INSTAGRAM_STORY, 30)[0].outcome is Outcome.ERROR
    # No platform limit on Facebook: only the brand limit applies.
    assert check_hashtags(_tags(40), Channel.FACEBOOK, 6)[0].outcome is Outcome.WARNING


def test_hashtag_counting_ignores_non_tags() -> None:
    assert (
        "0 Hashtags" in check_hashtags("Raum #2? Nein: Nr. 2 & C#", Channel.FACEBOOK, 6)[0].reason
    )
    assert "1 Hashtags" in check_hashtags("##doppelt", Channel.FACEBOOK, 6)[0].reason


# --- terms ---


def test_forbidden_terms() -> None:
    text = "Jetzt billiger trainieren – ein echtes Schnäppchen! Sofort  abnehmen."

    findings = check_forbidden_terms(text, ["billig", "Schnäppchen", "sofort abnehmen"])

    assert _outcomes(findings) == [Outcome.ERROR] * 3
    assert [text[f.span[0] : f.span[1]] for f in findings if f.span] == [
        "billig",
        "Schnäppchen",
        "Sofort  abnehmen",
    ]


def test_forbidden_term_only_at_word_start() -> None:
    assert check_forbidden_terms("unbillig", ["billig"])[0].outcome is Outcome.OK


def test_term_rule() -> None:
    rule = TermRule(
        key="vorher_nachher", label="Vorher-nachher", terms=["vorher-nachher"], reason="R"
    )

    findings = check_term_rules("Unser Vorher-Nachher-Vergleich", [rule])

    assert findings[0].outcome is Outcome.WARNING
    assert findings[0].reason == "„Vorher-Nachher“: R"


def test_phrase_rule_only_when_fact_given() -> None:
    rule = PhraseRule(
        key="preisangabe",
        label="Preisangabe",
        when_fact="preis",
        phrases=["inkl. MwSt."],
        reason="R",
    )
    with_price = BriefingFacts(preis=Decimal("49"))

    assert check_phrase_rules("49 € inkl.  MwSt.", [rule], with_price)[0].outcome is Outcome.OK
    assert check_phrase_rules("49 €", [rule], with_price)[0].outcome is Outcome.WARNING
    assert check_phrase_rules("Text", [rule], BriefingFacts()) == []


# --- emojis ---


def test_emojis() -> None:
    allowed = EmojiPolicy(allowed=True, max=2)
    none = EmojiPolicy(allowed=False)

    assert check_emojis("Los geht's 💪🔥", allowed, "Fitness")[0].outcome is Outcome.OK
    assert check_emojis("💪🔥🧘", allowed, "Fitness")[0].outcome is Outcome.WARNING
    finding = check_emojis("Termine frei 🙂", none, "Physiotherapie")[0]
    assert finding.outcome is Outcome.WARNING
    assert "keine Emojis" in finding.reason


# --- all checks together ---


def test_deterministic_checks_of_a_good_post() -> None:
    text = (
        "Rücken-Fit startet am Dienstag, 3. November, um 18:30 Uhr im Kursraum 2. "
        "Melde dich an! 💪\n\n#DemoGesundheitszentrum #Musterstadt #Kurse"
    )
    check = CheckInput(
        config=CONFIG, area_key="kurse", channel=Channel.INSTAGRAM_POST, facts=COURSE
    )

    findings = deterministic_checks(text, check)

    assert {f.outcome for f in findings} == {Outcome.OK}
    assert [f.check_key for f in findings] == [
        "laenge",
        "hashtags",
        "fakten",
        "fakten",
        "fakten",
        "verbotene_begriffe",
        "vorher_nachher",
        "emojis",
    ]


def test_stored_results_replace_old_ones(session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider()), briefing)
    first = session.exec(select(CheckResult)).all()
    assert first and {r.outcome for r in first} == {Outcome.OK}

    draft.text = draft.text.replace("03.11.2026", "04.11.2026")
    run_checks(session, CONFIG, draft)

    results = session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all()
    assert len(results) == len(first)
    (error,) = [r for r in results if r.outcome is Outcome.ERROR]
    assert error.excerpt == "04.11.2026"
    assert draft.full_text[error.span_start : error.span_end] == "04.11.2026"  # type: ignore[index]


def test_highlight_marks_and_escapes() -> None:
    results = [
        CheckResult(
            draft_id=1,
            check_key="a",
            outcome=Outcome.ERROR,
            reason="Fehler <x>",
            span_start=4,
            span_end=8,
        ),
        CheckResult(
            draft_id=1,
            check_key="b",
            outcome=Outcome.WARNING,
            reason="W",
            span_start=6,
            span_end=10,
        ),
        CheckResult(
            draft_id=1, check_key="c", outcome=Outcome.OK, reason="ok", span_start=0, span_end=3
        ),
    ]

    html = str(highlight("<b> billig & mehr", results))

    assert html.startswith("&lt;b&gt;")
    assert html.count("<mark") == 1
    assert 'data-outcome="error"' in html
    assert 'title="Fehler &lt;x&gt; | W"' in html
    assert "&amp;" in html


def test_highlight_ignores_spans_outside_text() -> None:
    result = CheckResult(
        draft_id=1, check_key="a", outcome=Outcome.ERROR, reason="x", span_start=50, span_end=60
    )

    assert str(highlight("kurz", [result])) == "kurz"


def test_draft_needing_manual_text_has_length_error(session: Session) -> None:
    briefing = create_briefing(session, CONFIG, course_input(channels=[Channel.FACEBOOK]), "demo")
    generate_drafts(session, CONFIG, SocialLLMClient(FakeProvider(["x", "y"])), briefing)

    draft = session.exec(select(PostDraft)).one()
    results = session.exec(select(CheckResult).where(CheckResult.check_key == "laenge")).all()
    assert draft.text == ""
    assert results[0].outcome is Outcome.ERROR
