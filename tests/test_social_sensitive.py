"""Health claims, named persons, address form; confirmation before approval (S4)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.audit import HUMAN_ACTOR_MODEL
from app.core.models import AuditEntry
from app.core.outcomes import Outcome
from app.inbox.seed import seed_members
from app.main import app
from app.social.briefings import create_briefing
from app.social.checking import ASSESS_STEP, run_checks
from app.social.drafts import generate_drafts
from app.social.llm.client import SocialLLMClient
from app.social.llm.schemas import AssessmentFinding, AssessmentOutput
from app.social.models import Channel, CheckResult, PostDraft, PostStatus
from app.social.profiles import AddressForm
from app.social.review import (
    CONFIRM_STEP,
    PERSON_CONSENT_STEP,
    ReviewNotAllowedError,
    approval_blockers,
    approve_draft,
    confirm_person_consent,
    confirm_warning,
)
from app.social.sensitive import (
    ADDRESS,
    HEALTH,
    PERSONS,
    address_forms,
    check_address_form,
    check_health_claims,
    check_persons,
    merge_assessment,
)
from app.social.web import get_social_client, get_social_config
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
RULES = CONFIG.industry.health_claims
CASES_FILE = Path(__file__).resolve().parent.parent / "samples/social/fitness/assessment_cases.json"
MEMBERS = ["Erika Beispiel", "Tim Testmann"]


def _cases() -> list[dict]:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    return data["cases"]


def _health(text: str, area: str) -> list:
    sensitive = CONFIG.brand.area(area).label if CONFIG.is_sensitive(area) else None
    return check_health_claims(text, RULES, sensitive)


# --- labelled test set ---


def test_labelled_set_is_balanced() -> None:
    cases = _cases()
    health = [c["labels"]["health_claim"] for c in cases]
    persons = [c["labels"]["person"] for c in cases]
    assert sum(health) >= 10 and health.count(False) >= 10
    assert sum(persons) >= 6 and persons.count(False) >= 10


def test_health_claim_detection_on_labelled_set() -> None:
    cases = _cases()
    positives = [c for c in cases if c["labels"]["health_claim"]]
    negatives = [c for c in cases if not c["labels"]["health_claim"]]

    found = [c["id"] for c in positives if _health(c["text"], c["area"])]
    false_alarms = [c["id"] for c in negatives if _health(c["text"], c["area"])]

    # SPEC_social section 9: at least 90 % of the health claims are recognised (code only;
    # the implicit promise g14 is left to the LLM assessment).
    assert len(found) / len(positives) >= 0.9
    assert sorted(set(c["id"] for c in positives) - set(found)) == ["g14"]
    # Known false alarm of the term list: "Bestpreis-Garantie" (a warning, not an error).
    assert false_alarms == ["n07"]


def test_person_detection_on_labelled_set() -> None:
    cases = _cases()

    for case in cases:
        found = bool(check_persons(case["text"], MEMBERS))
        assert found == case["labels"]["person"], case["id"]


def test_address_form_on_labelled_set() -> None:
    warned = {
        c["id"]
        for c in _cases()
        if check_address_form(c["text"], CONFIG.brand.address_form(c["area"]))[0].outcome
        is Outcome.WARNING
    }
    expected_wrong = {
        c["id"]
        for c in _cases()
        if c["labels"]["address_form"] == "gemischt"
        or c["labels"]["address_form"]
        not in ("neutral", CONFIG.brand.address_form(c["area"]).value)
    }

    assert warned <= expected_wrong
    # Known gap of the code check: imperative du-forms ("Sprich") are left to the LLM.
    assert expected_wrong - warned == {"a04"}


# --- health claims ---


def test_unambiguous_promise_is_an_error_in_sensitive_area() -> None:
    (finding, *_rest) = _health("Unsere Behandlung heilt Rückenschmerzen.", "physio")

    assert finding.outcome is Outcome.ERROR
    assert "sensiblen Bereich „Physiotherapie“" in finding.reason
    assert not finding.requires_confirmation


def test_unambiguous_promise_is_a_warning_elsewhere() -> None:
    (finding,) = _health("Dieser Kurs heilt jeden Muskelkater.", "kurse")

    assert finding.outcome is Outcome.WARNING
    assert finding.requires_confirmation


def test_overlapping_terms_are_reported_once() -> None:
    findings = _health("garantiert schmerzfrei", "fitness")

    assert len(findings) == 1
    assert findings[0].span == (0, 22)


def test_no_health_rules_no_findings() -> None:
    assert check_health_claims("heilt alles", None, None) == []


# --- persons ---


def test_member_is_an_error() -> None:
    (finding,) = check_persons("Danke an erika  beispiel!", MEMBERS)

    assert finding.outcome is Outcome.ERROR
    assert "Mitgliederliste" in finding.reason


@pytest.mark.parametrize(
    ("text", "quote"),
    [
        ("Frau Müller-Lüdenscheid berät Sie.", "Frau Müller-Lüdenscheid"),
        ("Herr Dr. Beispiel ist zurück.", "Herr Dr. Beispiel"),
        ("Physiotherapeutin Sabine Muster ist neu.", "Physiotherapeutin Sabine Muster"),
        ("Anna und Ben laufen mit.", "Anna"),
        ("Grüße von Jana Probe!", "Jana Probe"),
    ],
)
def test_name_patterns(text: str, quote: str) -> None:
    findings = check_persons(text, [])

    assert findings[0].outcome is Outcome.WARNING
    assert text[findings[0].span[0] : findings[0].span[1]] == quote  # type: ignore[index]


@pytest.mark.parametrize(
    "text",
    [
        "Januar-Aktion im Studio",
        "max. 10 Personen pro Kurs",
        "Ernst gemeint: Training lohnt sich",
        "Unser Team freut sich auf dich",
        "Herzlich willkommen im Demo-Gesundheitszentrum Musterstadt",
    ],
)
def test_no_false_persons(text: str) -> None:
    assert check_persons(text, MEMBERS) == []


# --- address form ---


def test_sie_at_sentence_start_is_ambiguous() -> None:
    du, sie = address_forms("Sie kommt morgen. Ihr Kurs beginnt bald.")

    assert du == [] and sie == []


def test_ihnen_counts_everywhere() -> None:
    _, sie = address_forms("Ihnen steht ab sofort unser Team zur Verfügung.")

    assert len(sie) == 1


def test_address_form_ok_and_mismatch() -> None:
    assert (
        check_address_form("Komm vorbei, wir freuen uns auf dich!", AddressForm.DU)[0].outcome
        is Outcome.OK
    )
    finding = check_address_form("Wir freuen uns auf Sie.", AddressForm.DU)[0]
    assert finding.outcome is Outcome.WARNING
    assert "wird geduzt" in finding.reason


# --- LLM assessment ---


def _assessment(**overrides: object) -> AssessmentOutput:
    values: dict[str, object] = {"health_claims": [], "persons": [], "address_form": "neutral"}
    return AssessmentOutput.model_validate(values | overrides)


def test_ai_findings_are_added_with_label() -> None:
    text = "Nach vier Wochen verschwinden deine Beschwerden wie von selbst."
    assessment = _assessment(
        health_claims=[
            AssessmentFinding(quote="verschwinden deine Beschwerden", reason="Heilversprechen")
        ]
    )

    (finding,) = merge_assessment(text, assessment, [], AddressForm.DU)

    assert finding.check_key == HEALTH
    assert finding.is_ai_assessment and finding.requires_confirmation
    assert finding.outcome is Outcome.WARNING
    assert text[finding.span[0] : finding.span[1]] == "verschwinden deine Beschwerden"  # type: ignore[index]


def test_ai_duplicates_of_code_findings_are_dropped() -> None:
    text = "Trainerin Anna zeigt dir alles."
    code = check_persons(text, [])
    assessment = _assessment(persons=[AssessmentFinding(quote="Anna", reason="Vorname")])

    assert merge_assessment(text, assessment, code, AddressForm.DU) == []


def test_ai_quote_not_in_text_is_kept_without_span() -> None:
    assessment = _assessment(persons=[AssessmentFinding(quote="Herr X", reason="Name")])

    (finding,) = merge_assessment("Text ohne Namen", assessment, [], AddressForm.DU)

    assert finding.span is None
    assert "nicht gefunden" in finding.reason


@pytest.mark.parametrize(("form", "expected"), [("gemischt", 1), ("sie", 1), ("du", 0)])
def test_ai_address_form(form: str, expected: int) -> None:
    findings = merge_assessment("Text", _assessment(address_form=form), [], AddressForm.DU)

    assert len([f for f in findings if f.check_key == ADDRESS]) == expected


# --- stored checks with assessment ---


def _draft(session: Session, provider: object | None = None, **overrides: object) -> PostDraft:
    briefing = create_briefing(
        session, CONFIG, course_input(channels=[Channel.FACEBOOK], **overrides), "demo"
    )
    client = SocialLLMClient(provider or scripted())  # type: ignore[arg-type]
    (draft,) = generate_drafts(session, CONFIG, client, briefing)
    return draft


def _results(session: Session, draft: PostDraft) -> list[CheckResult]:
    return list(session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all())


def test_clean_draft_is_ready_for_approval(session: Session) -> None:
    draft = _draft(session)

    assert draft.status is PostStatus.IN_REVIEW
    keys = {r.check_key for r in _results(session, draft)}
    assert {HEALTH, PERSONS, ADDRESS} <= keys
    assert approval_blockers(session, draft) == []


def test_health_claim_in_notes_needs_confirmation(session: Session) -> None:
    draft = _draft(session, notes="Rücken-Fit lindert Verspannungen")

    (warning,) = [r for r in _results(session, draft) if r.check_key == HEALTH]
    assert warning.outcome is Outcome.WARNING and warning.requires_confirmation
    assert warning.excerpt == "lindert"
    blockers = approval_blockers(session, draft)
    assert len(blockers) == 1 and blockers[0].startswith("Warnung noch nicht bestätigt")


def test_ai_assessment_is_stored_and_audited(session: Session) -> None:
    answer = json.dumps(
        {
            "health_claims": [],
            "persons": [{"quote": "Rücken-Fit", "reason": "klingt nach Name"}],
            "address_form": "du",
        }
    )
    draft = _draft(session, scripted(post_assess=[answer]))

    (ai,) = [r for r in _results(session, draft) if r.is_ai_assessment]
    assert ai.check_key == PERSONS and ai.requires_confirmation
    entries = session.exec(select(AuditEntry).where(AuditEntry.step == ASSESS_STEP)).all()
    assert len(entries) == 1
    assert entries[0].prompt_version == "post_assess_v1"


def test_invalid_assessment_twice_needs_manual_review(session: Session) -> None:
    draft = _draft(session, scripted(post_assess=["{}", "kein json"]))

    assert draft.status is PostStatus.NEEDS_MANUAL_REVIEW
    (warning,) = [r for r in _results(session, draft) if r.check_key == "ki_einschaetzung"]
    assert warning.requires_confirmation
    assert len(session.exec(select(AuditEntry).where(AuditEntry.step == ASSESS_STEP)).all()) == 2
    assert approval_blockers(session, draft)


def test_assessment_receives_only_the_post_text(session: Session) -> None:
    provider = scripted()
    draft = _draft(session, provider)

    (_task, system, user) = [c for c in provider.calls if c[0] == "post_assess"][0]
    assert user == f"<beitrag>\n{draft.full_text}\n</beitrag>"
    assert "fitness" in system


def test_member_in_post_blocks_approval(session: Session) -> None:
    seed_members(session)
    draft = _draft(session, notes="Danke an Erika Beispiel für die Idee")

    errors = [r for r in _results(session, draft) if r.outcome is Outcome.ERROR]
    assert [r.check_key for r in errors] == [PERSONS]
    with pytest.raises(ReviewNotAllowedError, match="Einwilligung fehlt.*Mitgliederliste"):
        approve_draft(session, draft, "demo")


def _member_draft(session: Session) -> tuple[PostDraft, CheckResult]:
    seed_members(session)
    draft = _draft(session, notes="Danke an Erika Beispiel für die Idee")
    (error,) = [r for r in _results(session, draft) if r.outcome is Outcome.ERROR]
    return draft, error


def test_member_error_needs_consent_not_a_plain_confirmation(session: Session) -> None:
    draft, error = _member_draft(session)

    assert error.consent_required and not error.requires_confirmation
    with pytest.raises(ReviewNotAllowedError, match="braucht keine Bestätigung"):
        confirm_warning(session, error, "demo")
    assert approval_blockers(session, draft)


def test_member_consent_lifts_only_that_error(session: Session) -> None:
    draft, error = _member_draft(session)

    confirm_person_consent(session, error, "demo")

    assert error.confirmed_by == "demo" and error.confirmed_at is not None
    assert approval_blockers(session, draft) == []
    approve_draft(session, draft, "demo")
    assert draft.status is PostStatus.APPROVED
    (entry,) = session.exec(select(AuditEntry).where(AuditEntry.step == PERSON_CONSENT_STEP)).all()
    assert entry.model_params["actor"] == "demo"
    assert entry.model_params["check_result_id"] == error.id
    # The member's name is not copied into the immutable audit trail.
    assert "Beispiel" not in json.dumps(entry.model_params)


def test_member_consent_does_not_lift_other_errors(session: Session) -> None:
    seed_members(session)
    draft = _draft(
        session, area="physio", notes="Erika Beispiel sagt: unsere Behandlung heilt Rückenschmerzen"
    )
    for result in _results(session, draft):
        if result.consent_required:
            confirm_person_consent(session, result, "demo")
        elif result.requires_confirmation:
            confirm_warning(session, result, "demo")

    with pytest.raises(ReviewNotAllowedError, match="Heilversprechen"):
        approve_draft(session, draft, "demo")


def test_consent_only_for_member_findings(session: Session) -> None:
    draft = _draft(session, notes="Trainerin Anna zeigt die Geräte")
    warning = next(r for r in _results(session, draft) if r.check_key == PERSONS)

    with pytest.raises(ReviewNotAllowedError, match="keine Einwilligung"):
        confirm_person_consent(session, warning, "demo")


def test_member_consent_twice_and_after_approval(session: Session) -> None:
    draft, error = _member_draft(session)
    confirm_person_consent(session, error, "demo")

    with pytest.raises(ReviewNotAllowedError, match="bereits bestätigt"):
        confirm_person_consent(session, error, "demo")
    approve_draft(session, draft, "demo")
    with pytest.raises(ReviewNotAllowedError, match="vor der Freigabe"):
        confirm_person_consent(session, error, "demo")


def test_edit_resets_member_consent(session: Session) -> None:
    draft, error = _member_draft(session)
    confirm_person_consent(session, error, "demo")

    run_checks(session, CONFIG, SocialLLMClient(scripted()), draft)

    assert any("Einwilligung fehlt" in b for b in approval_blockers(session, draft))


# --- confirmation and approval ---


def test_approval_without_confirmation_is_impossible(session: Session) -> None:
    draft = _draft(session, notes="Training, das Bluthochdruck vorbeugt")

    with pytest.raises(ReviewNotAllowedError, match="Warnung noch nicht bestätigt"):
        approve_draft(session, draft, "demo")
    assert draft.status is PostStatus.IN_REVIEW


def test_confirm_then_approve(session: Session) -> None:
    draft = _draft(session, notes="Training, das Bluthochdruck vorbeugt")
    warnings = [r for r in _results(session, draft) if r.requires_confirmation]

    for warning in warnings:
        confirm_warning(session, warning, "demo")
    review = approve_draft(session, draft, "demo")

    assert review.decision.value == "approved"
    assert draft.status is PostStatus.APPROVED
    confirmations = session.exec(select(AuditEntry).where(AuditEntry.step == CONFIRM_STEP)).all()
    assert len(confirmations) == len(warnings)
    entry = confirmations[0]
    assert entry.model == HUMAN_ACTOR_MODEL
    assert entry.model_params["actor"] == "demo"
    assert entry.model_params["draft_id"] == draft.id
    assert entry.model_params["check_key"] in (HEALTH,)
    # The confirmed passage itself is not copied into the immutable audit trail.
    assert "Bluthochdruck" not in json.dumps(entry.model_params)


def test_confirmation_does_not_lift_errors(session: Session) -> None:
    draft = _draft(session, area="physio", notes="Unsere Behandlung heilt Rückenschmerzen")
    for result in _results(session, draft):
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")

    with pytest.raises(ReviewNotAllowedError, match="Heilversprechen"):
        approve_draft(session, draft, "demo")


def test_confirm_only_warnings_once(session: Session) -> None:
    draft = _draft(session, notes="Training, das Bluthochdruck vorbeugt")
    ok = next(r for r in _results(session, draft) if r.outcome is Outcome.OK)
    warning = next(r for r in _results(session, draft) if r.requires_confirmation)

    with pytest.raises(ReviewNotAllowedError, match="braucht keine Bestätigung"):
        confirm_warning(session, ok, "demo")
    confirm_warning(session, warning, "demo")
    with pytest.raises(ReviewNotAllowedError, match="bereits bestätigt"):
        confirm_warning(session, warning, "demo")


def test_new_check_run_resets_confirmations(session: Session) -> None:
    draft = _draft(session, notes="Training, das Bluthochdruck vorbeugt")
    for result in _results(session, draft):
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")
    assert approval_blockers(session, draft) == []

    run_checks(session, CONFIG, SocialLLMClient(scripted()), draft)

    assert approval_blockers(session, draft)


# --- web ---


@pytest.fixture
def social_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    yield client


def test_confirm_and_approve_in_the_ui(social_client: TestClient, session: Session) -> None:
    draft = _draft(session, notes="Training, das Bluthochdruck vorbeugt")
    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text
    assert "Geprüft – bestätigen" in page
    assert "disabled" in page
    assert "<mark" in page and "Bluthochdruck</mark>" in page

    blocked = social_client.post(f"/social/drafts/{draft.id}/approve")
    assert blocked.status_code == 409
    assert "Warnung noch nicht bestätigt" in blocked.text

    for result in _results(session, draft):
        if result.requires_confirmation:
            response = social_client.post(
                f"/social/checks/{result.id}/confirm", follow_redirects=False
            )
            assert response.status_code == 303
    approved = social_client.post(f"/social/drafts/{draft.id}/approve", follow_redirects=False)
    assert approved.status_code == 303
    session.refresh(draft)
    assert draft.status is PostStatus.APPROVED
    assert "bestätigt von demo" in social_client.get(f"/social/briefings/{draft.briefing_id}").text


def test_health_notice_in_the_ui(social_client: TestClient) -> None:
    page = social_client.get("/social").text

    assert "besonderen rechtlichen Regeln" in page
    assert "ersetzt aber keine rechtliche Prüfung" in page


def test_member_consent_in_the_ui(social_client: TestClient, session: Session) -> None:
    draft, error = _member_draft(session)
    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text
    assert "Einwilligung der Person liegt vor" in page
    assert f'action="/social/checks/{error.id}/consent"' in page
    assert "Geprüft – bestätigen" not in page

    assert social_client.post(f"/social/drafts/{draft.id}/approve").status_code == 409
    consent = social_client.post(f"/social/checks/{error.id}/consent", follow_redirects=False)
    assert consent.status_code == 303
    page = social_client.get(f"/social/briefings/{draft.briefing_id}").text
    assert "Einwilligung bestätigt von demo" in page
    assert social_client.post(f"/social/drafts/{draft.id}/approve").status_code == 200


def test_unknown_check_result(social_client: TestClient) -> None:
    assert social_client.post("/social/checks/999/confirm").status_code == 404
    assert social_client.post("/social/checks/999/consent").status_code == 404
