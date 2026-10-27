import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.llm.base import LLMTask
from app.core.llm.fake import FakeProvider
from app.core.models import AuditEntry
from app.core.profiles import PROFILES_DIR
from app.inbox.ingest.service import import_samples
from app.inbox.llm.client import LLMClient
from app.inbox.models import InboxItem, ItemSource, ItemStatus, RuleOutcome, Suggestion
from app.inbox.pipeline import process_item, process_new_items
from app.inbox.profiles import DocumentType, Profile, load_profile
from app.inbox.rules import RuleCheck
from app.inbox.seed import seed_members
from app.inbox.suggestions import (
    AI_LABEL,
    TemplateRenderError,
    label_draft,
    missing_facts,
    reflow_paragraphs,
    render_template,
    select_action,
    template_values,
)
from tests.conftest import SAMPLES_DIR

CLASSIFY = '{"document_type": "kuendigung", "confidence": 0.95}'
FIELDS = {
    "name": "Erika Beispiel",
    "mitgliedsnummer": "M-10001",
    "eingangsdatum": "2026-09-14",
    "gewuenschtes_kuendigungsdatum": "2026-10-31",
}


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(PROFILES_DIR / "fitness.yaml")


def _type(profile: Profile, key: str) -> DocumentType:
    return profile.document_type(key)


# --- action selection ---


def test_default_action_when_nothing_missing(profile: Profile) -> None:
    values = {"mitgliedsnummer": "M-1", "vertragsende": "31.10.2026"}

    action = select_action(_type(profile, "kuendigung"), values, [])

    assert action is not None and action.key == "kuendigung_bestaetigen"


def test_missing_member_number_asks_back(profile: Profile) -> None:
    action = select_action(_type(profile, "kuendigung"), {"mitgliedsnummer": None}, [])

    assert action is not None and action.key == "rueckfrage_mitgliedsnummer"


def test_missing_contract_end_needs_manual_check(profile: Profile) -> None:
    action = select_action(_type(profile, "kuendigung"), {"mitgliedsnummer": "M-1"}, [])

    assert action is not None and action.key == "vertragsdaten_pruefen"
    assert action.reply_template is None


def test_rule_outcome_selects_action(profile: Profile) -> None:
    checks = [RuleCheck("invoice_totals", RuleOutcome.ERROR, "x")]

    action = select_action(_type(profile, "rechnung"), {}, checks)

    assert action is not None and action.key == "rueckfrage_lieferant"


def test_missed_notice_period_suggests_next_possible_date(profile: Profile) -> None:
    checks = [RuleCheck("notice_period", RuleOutcome.ERROR, "x", {"vertragsende": "2026-11-30"})]
    values = template_values({"mitgliedsnummer": "M-1"}, checks)

    action = select_action(_type(profile, "kuendigung"), values, checks)

    assert action is not None and action.key == "kuendigung_zum_naechsten_termin"


@pytest.mark.parametrize(
    ("dringend", "expected"),
    [(True, "notdienst_einplanen"), (False, "termin_bestaetigen"), (None, "termin_bestaetigen")],
)
def test_when_value_selects_emergency_action(dringend: bool | None, expected: str) -> None:
    handwerk = load_profile(PROFILES_DIR / "handwerk.yaml")
    values = {"einsatzort": "Musterstadt", "dringend": dringend}

    action = select_action(handwerk.document_type("reparaturauftrag"), values, [])

    assert action is not None and action.key == expected


def test_when_value_requires_same_type(profile: Profile) -> None:
    data = _type(profile, "sonstiges").model_dump()
    data["actions"] = [
        {"key": "a", "label": "A", "when_value": {"flag": [True]}},
        {"key": "b", "label": "B"},
    ]
    doc_type = DocumentType.model_validate(data)

    assert select_action(doc_type, {"flag": 1}, []).key == "b"  # type: ignore[union-attr]
    assert select_action(doc_type, {"flag": True}, []).key == "a"  # type: ignore[union-attr]


def test_rule_warning_does_not_trigger_error_action(profile: Profile) -> None:
    checks = [RuleCheck("invoice_totals", RuleOutcome.WARNING, "x")]

    action = select_action(_type(profile, "rechnung"), {}, checks)

    assert action is not None and action.key == "zur_zahlung_vormerken"


def test_every_document_type_ends_with_default_action(profile: Profile) -> None:
    for doc_type in profile.document_types:
        assert doc_type.actions and doc_type.actions[-1].is_default


def test_profile_without_default_action_is_rejected(profile: Profile) -> None:
    data = _type(profile, "kuendigung").model_dump()
    data["actions"] = data["actions"][:1]

    with pytest.raises(ValueError, match="default action"):
        DocumentType.model_validate(data)


# --- values and templates ---


def test_template_values_are_formatted_german() -> None:
    checks = [RuleCheck("notice_period", RuleOutcome.OK, "x", {"vertragsende": "2026-10-31"})]

    values = template_values(
        {"datum": date(2026, 9, 14), "betrag": Decimal("499.80"), "name": "A"}, checks
    )

    assert values == {
        "datum": "14.09.2026",
        "betrag": "499,80 EUR",
        "name": "A",
        "vertragsende": "31.10.2026",
    }


def test_fields_take_precedence_over_rule_data() -> None:
    checks = [RuleCheck("r", RuleOutcome.OK, "x", {"name": "aus Regel"})]

    assert template_values({"name": "aus Feld"}, checks)["name"] == "aus Feld"


def test_render_template_strict() -> None:
    assert render_template("Hallo {{ name }}", {"name": "Erika"}) == "Hallo Erika"
    with pytest.raises(TemplateRenderError):
        render_template("Hallo {{ unbekannt }}", {})


def test_render_template_renders_none_as_empty() -> None:
    template = "Guten Tag{% if name %} {{ name }}{% endif %}, Nr. {{ nummer }}"

    assert render_template(template, {"name": None, "nummer": None}) == "Guten Tag, Nr."


def test_render_template_is_sandboxed() -> None:
    with pytest.raises(TemplateRenderError):
        render_template("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})


def test_wrapped_template_lines_become_one_paragraph() -> None:
    template = "Guten Tag,\n\nIhr Vertrag {{ nr }}\nendet zum 31.05.2027.\n\nGrüße\nIhr Team"

    assert render_template(template, {"nr": "M-10004"}) == (
        "Guten Tag,\n\nIhr Vertrag M-10004 endet zum 31.05.2027.\n\nGrüße\nIhr Team"
    )


def test_reflow_keeps_closing_paragraph_and_single_paragraphs() -> None:
    assert reflow_paragraphs("Mit freundlichen Grüßen\nIhr Team") == (
        "Mit freundlichen Grüßen\nIhr Team"
    )
    assert reflow_paragraphs("A\n  b\n\n\n\nC\nD\n") == "A b\n\nC\nD"


def test_missing_facts_detects_changed_dates_and_numbers() -> None:
    template = "Ihr Vertrag M-10001 endet zum 31.10.2026. Betrag 499,80 EUR."

    assert missing_facts(template, template) == []
    assert missing_facts(template, "Ihr Vertrag M-10001 endet zum 30.11.2026, 499,80 EUR") == [
        "31.10.2026"
    ]


def test_label_draft_marks_ai_suggestion() -> None:
    assert label_draft("Text").startswith(AI_LABEL)


# --- pipeline integration ---


def _item(session: Session) -> InboxItem:
    item = InboxItem(source=ItemSource.UPLOAD, raw_content_ref="x", text="Ich kündige M-10001.")
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_processed_item_gets_labelled_draft(session: Session, profile: Profile) -> None:
    seed_members(session)
    draft = "Guten Tag Erika Beispiel, wir bestätigen die Kündigung M-10001 zum 31.10.2026."
    fake = FakeProvider([CLASSIFY, json.dumps(FIELDS), draft])

    result = process_item(session, _item(session), profile, LLMClient(fake))

    suggestion = session.exec(select(Suggestion)).one()
    assert result.status == ItemStatus.PROCESSED
    assert suggestion.action == "kuendigung_bestaetigen"
    assert suggestion.is_ai_suggestion is True
    assert suggestion.draft_text is not None
    assert suggestion.draft_text.startswith(AI_LABEL)
    assert "31.10.2026" in suggestion.draft_text
    draft_prompt = fake.calls[2][2]
    assert "Ihr Vertrag endet zum 31.10.2026." in draft_prompt
    # The wrapped profile template arrives as one paragraph.
    assert "Kündigung Ihrer Mitgliedschaft M-10001. Ihr Vertrag endet" in draft_prompt


def test_draft_changing_facts_is_retried_then_manual_review(
    session: Session, profile: Profile
) -> None:
    seed_members(session)
    wrong = "Wir bestätigen die Kündigung M-10001 zum 30.11.2026."
    fake = FakeProvider([CLASSIFY, json.dumps(FIELDS), wrong, wrong])

    result = process_item(session, _item(session), profile, LLMClient(fake))

    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert result.reason == "Antwortentwurf ungültig"
    assert session.exec(select(Suggestion)).all() == []
    drafts = session.exec(select(AuditEntry).where(AuditEntry.step == "draft")).all()
    assert len(drafts) == 2
    assert all(d.error and "changes 1 fact(s)" in d.error for d in drafts)


def test_action_without_template_needs_no_llm_call(session: Session, profile: Profile) -> None:
    fake = FakeProvider([CLASSIFY, json.dumps(FIELDS)])  # no member data → manual check

    result = process_item(session, _item(session), profile, LLMClient(fake))

    suggestion = session.exec(select(Suggestion)).one()
    assert result.status == ItemStatus.PROCESSED
    assert suggestion.action == "vertragsdaten_pruefen"
    assert suggestion.draft_text is None
    assert len(fake.calls) == 2


def test_rule_error_still_gets_suggestion(session: Session, profile: Profile) -> None:
    fields = FIELDS | {"mitgliedsnummer": None}

    def responder(task: LLMTask, system: str, user: str) -> str:
        if task == "classify":
            return CLASSIFY
        if task == "extract":
            return json.dumps(fields)
        return user.split("<vorlage>\n")[1].split("\n</vorlage>")[0]

    result = process_item(session, _item(session), profile, LLMClient(FakeProvider(responder)))

    suggestion = session.exec(select(Suggestion)).one()
    assert result.status == ItemStatus.NEEDS_MANUAL_REVIEW
    assert suggestion.action == "rueckfrage_mitgliedsnummer"
    assert suggestion.draft_text is not None and "Mitgliedsnummer" in suggestion.draft_text


def test_every_processed_sample_has_suggestion_or_manual_review(
    session: Session, profile: Profile, raw_dir: Path
) -> None:
    """Acceptance criterion M7."""
    import_samples(session, SAMPLES_DIR, raw_dir)

    process_new_items(session, profile, LLMClient(FakeProvider()))

    suggestions = {s.item_id: s for s in session.exec(select(Suggestion)).all()}
    for item in session.exec(select(InboxItem)).all():
        assert item.status == ItemStatus.NEEDS_MANUAL_REVIEW or item.id in suggestions
    drafts = [s.draft_text for s in suggestions.values() if s.draft_text]
    assert drafts
    assert all(d.startswith(AI_LABEL) for d in drafts)
    assert all(s.is_ai_suggestion for s in suggestions.values())
