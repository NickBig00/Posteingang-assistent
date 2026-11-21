"""B6: revising post texts, image prompts and images by instruction (fake providers only)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.audit import audit_outputs, hash_input
from app.core.image_formats import ImageFormat
from app.core.images.fake import FakeImageProvider
from app.core.llm.fake import FakeProvider
from app.core.models import AuditEntry, AuditOutput
from app.main import app
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.images import GENERATE_STEP, candidates, latest_proposal, suggest_prompt
from app.social.llm.client import SocialLLMClient
from app.social.models import (
    Channel,
    CheckResult,
    PostDraft,
    PostStatus,
    RevisionRequest,
    RevisionTarget,
)
from app.social.retention import delete_briefing
from app.social.review import approve_draft, confirm_warning
from app.social.revisions import (
    POST_REVISE_STEP,
    PROMPT_REVISE_STEP,
    REQUEST_STEP,
    RevisionError,
    revise_image,
    revise_image_prompt,
    revise_post,
)
from app.social.web import get_social_client, get_social_config, get_social_image_provider
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
CLIENT = SocialLLMClient(FakeProvider())


@pytest.fixture(autouse=True)
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")


def _draft(session: Session) -> PostDraft:
    briefing = create_briefing(
        session, CONFIG, course_input(channels=[Channel.INSTAGRAM_POST]), "demo"
    )
    (draft,) = generate_drafts(session, CONFIG, CLIENT, briefing)
    return draft


def _steps(session: Session) -> list[str]:
    return [e.step for e in session.exec(select(AuditEntry).order_by(AuditEntry.id)).all()]  # type: ignore[arg-type]


# --- post texts ---


def test_revision_follows_the_instruction_and_runs_all_checks(session: Session) -> None:
    draft = _draft(session)
    assert "Rücken-Fit" in draft.text

    revised = revise_post(session, CONFIG, CLIENT, draft, "Bitte ohne Rücken-Fit", "demo")

    assert "Rücken-Fit" not in revised.text
    assert revised.is_ai_suggestion
    steps = _steps(session)
    # LLM call, the human request and a new assessment (all checks again).
    assert POST_REVISE_STEP in steps and REQUEST_STEP in steps
    assert steps.index(POST_REVISE_STEP) < len(steps) - 1 and steps[-1] == "social.post_assess"
    results = session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all()
    assert any(r.check_key == "fakten" for r in results)


def test_new_version_with_a_health_claim_needs_a_new_confirmation(session: Session) -> None:
    claim = json.dumps(
        {"text": "Heilt garantiert Rückenschmerzen! 03.11.2026, 18:30 Uhr, Kursraum 2"}
    )
    client = SocialLLMClient(scripted(post_revise=[claim]))
    draft = _draft(session)

    revised = revise_post(session, CONFIG, client, draft, "Mehr Begeisterung", "demo")

    results = session.exec(select(CheckResult).where(CheckResult.draft_id == revised.id)).all()
    health = [r for r in results if r.check_key == "gesundheit" and r.outcome != "ok"]
    assert health and all(r.confirmed_at is None for r in health)


def test_invalid_output_is_retried_once_then_the_previous_text_stays(session: Session) -> None:
    draft = _draft(session)
    before = draft.text
    client = SocialLLMClient(scripted(post_revise=["kein json", '{"falsch": 1}']))

    with pytest.raises(RevisionError, match="bisherige Text bleibt"):
        revise_post(session, CONFIG, client, draft, "Kürzer", "demo")

    session.refresh(draft)
    assert draft.text == before
    attempts = session.exec(select(AuditEntry).where(AuditEntry.step == POST_REVISE_STEP)).all()
    assert [e.model_params["attempt"] for e in attempts] == [1, 2]
    (request,) = session.exec(select(RevisionRequest)).all()
    assert request.succeeded is False


def test_second_attempt_can_succeed(session: Session) -> None:
    draft = _draft(session)
    good = json.dumps({"text": "Neu: 03.11.2026, 18:30 Uhr, Kursraum 2", "hashtags": ["#Kurse"]})
    client = SocialLLMClient(scripted(post_revise=["kaputt", good]))

    revised = revise_post(session, CONFIG, client, draft, "Kürzer", "demo")

    assert revised.text.startswith("Neu:") and revised.hashtags == ["#Kurse"]


def test_request_is_audited_with_hash_and_deletable_plain_text(session: Session) -> None:
    draft = _draft(session)
    revise_post(session, CONFIG, CLIENT, draft, "Satz 2 neutraler formulieren", "demo")
    revise_post(session, CONFIG, CLIENT, draft, "Kürzer fassen", "demo")

    entries = session.exec(select(AuditEntry).where(AuditEntry.step == REQUEST_STEP)).all()
    assert [e.model_params["version"] for e in entries] == [1, 2]
    first = entries[0]
    assert first.input_hash == hash_input("Satz 2 neutraler formulieren")
    assert first.model_params["actor"] == "demo"
    assert first.model_params["target"] == RevisionTarget.POST_TEXT.value
    assert "neutraler" not in json.dumps(first.model_params)
    outputs = audit_outputs(session, [first.id or 0])
    assert json.loads(outputs[first.id or 0])["instruction"] == "Satz 2 neutraler formulieren"

    delete_briefing(session, draft.briefing_id, "demo")

    assert session.exec(select(RevisionRequest)).all() == []
    ids = [e.id for e in entries]
    assert session.exec(select(AuditOutput).where(AuditOutput.audit_entry_id.in_(ids))).all() == []  # type: ignore[attr-defined]
    # The audit entries themselves stay (hashes and metadata only).
    assert len(session.exec(select(AuditEntry).where(AuditEntry.step == REQUEST_STEP)).all()) == 2


@pytest.mark.parametrize("instruction", ["", "   ", "x" * 501])
def test_empty_or_too_long_instruction_is_refused(session: Session, instruction: str) -> None:
    with pytest.raises(RevisionError):
        revise_post(session, CONFIG, CLIENT, _draft(session), instruction, "demo")


def test_approved_post_is_not_revised(session: Session) -> None:
    draft = _draft(session)
    for result in session.exec(select(CheckResult).where(CheckResult.draft_id == draft.id)).all():
        if result.requires_confirmation:
            confirm_warning(session, result, "demo")
    approve_draft(session, draft, "demo")

    with pytest.raises(RevisionError, match="offene"):
        revise_post(session, CONFIG, CLIENT, draft, "Kürzer", "demo")
    assert draft.status is PostStatus.APPROVED


def test_only_needed_data_is_sent(session: Session) -> None:
    provider = FakeProvider()
    draft = _draft(session)

    revise_post(session, CONFIG, SocialLLMClient(provider), draft, "Kürzer", "demo")

    task, _, user = next(c for c in provider.calls if c[0] == "post_revise")
    assert "<stichpunkte>" not in user  # key points are not needed for a revision
    assert "<anweisung>\nKürzer\n</anweisung>" in user


# --- image prompts and images ---


def _with_prompt(session: Session) -> PostDraft:
    draft = _draft(session)
    suggest_prompt(session, CONFIG, CLIENT, draft, ImageFormat.PORTRAIT)
    return draft


def test_image_prompt_revision_creates_a_new_version(session: Session) -> None:
    draft = _with_prompt(session)
    first = latest_proposal(session, draft.id or 0)
    assert first is not None

    second = revise_image_prompt(session, CONFIG, CLIENT, draft, "Mehr Bewegung zeigen", "demo")

    assert second.id != first.id and second.prompt != first.prompt
    assert "Mehr Bewegung zeigen" in second.prompt and second.prompt_from_ai
    assert second.image_format == first.image_format
    assert PROMPT_REVISE_STEP in _steps(session)


def test_image_revision_generates_new_variants_through_the_checks(
    session: Session, raw_dir: Path
) -> None:
    from app.social.images import ImageHintsPendingError, confirm_prompt_hint, prompt_hints

    draft = _with_prompt(session)
    provider = FakeImageProvider()

    # The new prompt shows a person: first confirm the hint, then generate.
    with pytest.raises(ImageHintsPendingError):
        revise_image(
            session, CONFIG, CLIENT, provider, draft, "Frau beim Dehnen zeigen", "demo", raw_dir
        )
    assert provider.calls == []
    for hint in prompt_hints(session, draft.id or 0):
        confirm_prompt_hint(session, hint, "demo")
    proposal = latest_proposal(session, draft.id or 0)
    assert proposal is not None
    from app.social.images import generate_variants

    generate_variants(
        session,
        CONFIG,
        provider,
        draft,
        prompt=proposal.prompt,
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=raw_dir,
    )

    assert len(candidates(session, draft.id or 0)) == 3
    assert "Frau beim Dehnen zeigen" in provider.calls[0]
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == GENERATE_STEP)).one()
    assert "personen" in entry.model_params["hints"]  # type: ignore[operator]
    request = session.exec(select(RevisionRequest)).one()
    assert request.target is RevisionTarget.IMAGE


def test_image_revision_respects_the_cost_brake(
    session: Session, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_MAX_PER_POST", "0")
    draft = _with_prompt(session)
    provider = FakeImageProvider()

    from app.core.images.base import ImageBudgetExceededError

    with pytest.raises(ImageBudgetExceededError):
        revise_image(session, CONFIG, CLIENT, provider, draft, "Heller", "demo", raw_dir)
    assert provider.calls == []


def test_without_prompt_there_is_nothing_to_revise(session: Session) -> None:
    with pytest.raises(RevisionError, match="noch keinen Bild-Prompt"):
        revise_image_prompt(session, CONFIG, CLIENT, _draft(session), "Heller", "demo")


# --- web ---


@pytest.fixture
def web(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: CLIENT
    app.dependency_overrides[get_social_config] = demo_config
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    yield client


def test_revise_in_the_ui_with_quick_pick_and_own_words(web: TestClient, session: Session) -> None:
    draft = _draft(session)
    page = web.get(f"/social/briefings/{draft.briefing_id}").text
    assert "Was soll anders werden?" in page and "Kürzer fassen" in page

    response = web.post(
        f"/social/drafts/{draft.id}/revise",
        data={"instruction": "", "quick": "Kürzer fassen"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert session.exec(select(RevisionRequest)).one().instruction == "Kürzer fassen"

    refused = web.post(f"/social/drafts/{draft.id}/revise", data={"instruction": " "})
    assert refused.status_code == 422 and "anders werden soll" in refused.text

    audit = web.get(f"/social/briefings/{draft.briefing_id}/audit").text
    assert "data-instruction" in audit and "„Kürzer fassen“" in audit


def test_image_revise_route(web: TestClient, session: Session) -> None:
    draft = _with_prompt(session)

    page = web.post(
        f"/social/drafts/{draft.id}/images/revise",
        data={"instruction": "Wärmeres Licht", "regenerate": "true"},
        follow_redirects=True,
    ).text

    assert page.count("data-candidate=") == 3
    assert "Bild per Anweisung ändern" in page
