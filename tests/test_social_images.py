"""AI image suggestions in the draft (ROADMAP_social B2): rules, labelling, deletion."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.audit import audit_outputs
from app.core.image_formats import ImageFormat
from app.core.images.fake import FakeImageProvider
from app.core.members import Member
from app.core.models import AuditEntry
from app.core.profiles import PROFILES_DIR
from app.main import app
from app.social.briefings import create_briefing
from app.social.drafts import generate_drafts
from app.social.images import (
    CHOOSE_STEP,
    DISCARD_STEP,
    GENERATE_STEP,
    HINT_CONTACT,
    HINT_PERSONS,
    HINT_REAL,
    HINT_TEXT,
    PROMPT_STEP,
    SAFETY_SUFFIX,
    ImagePromptRejectedError,
    ImageSuggestionError,
    candidates,
    check_prompt,
    choose_candidate,
    chosen_image,
    discard_candidates,
    generate_variants,
    latest_proposal,
    prompt_violations,
    suggest_prompt,
)
from app.social.llm.client import SocialLLMClient
from app.social.models import Asset, Briefing, Channel, ImageCandidate, ImageProposal, PostDraft
from app.social.profiles import DEFAULT_BRAND_PROFILES_DIR, load_social_config
from app.social.retention import delete_briefing, delete_draft
from app.social.review import approve_draft
from app.social.web import get_social_client, get_social_config, get_social_image_provider
from tests.social_helpers import course_input, demo_config, scripted

CONFIG = demo_config()
BRAND = CONFIG.brand.name
CLEAN = "Heller Kursraum mit Matten und Bällen, weiches Morgenlicht, Pflanzen, ruhige Stimmung"


@pytest.fixture(autouse=True)
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")


def _draft(session: Session, channel: Channel = Channel.INSTAGRAM_POST) -> PostDraft:
    briefing = create_briefing(session, CONFIG, course_input(channels=[channel]), "demo")
    (draft,) = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    return draft


def _generate(
    session: Session,
    draft: PostDraft,
    storage: Path,
    prompt: str = CLEAN,
    provider: FakeImageProvider | None = None,
) -> list[ImageCandidate]:
    return generate_variants(
        session,
        CONFIG,
        provider or FakeImageProvider(),
        draft,
        prompt=prompt,
        fmt=ImageFormat.PORTRAIT,
        user="demo",
        storage=storage,
    )


def _steps(session: Session) -> list[str]:
    return [e.step for e in session.exec(select(AuditEntry).order_by(AuditEntry.id)).all()]  # type: ignore[arg-type]


# --- protection rules (code check of the prompt): hints instead of rejection (B5) ---


def _hint_keys(prompt: str) -> list[str]:
    return [h.key for h in check_prompt(prompt, [], BRAND).hints]


@pytest.mark.parametrize(
    "prompt",
    [
        "Eine Trainerin zeigt eine Übung an der Hantelbank",
        "Lachende Menschen im Kursraum",
        "Nahaufnahme von Händen auf einer Matte",
        "Gesicht im Gegenlicht",
        "Gruppe von Senioren beim Rückentraining",
        "Smiling woman on a yoga mat",
    ],
)
def test_people_in_the_prompt_are_a_hint_not_a_rejection(prompt: str) -> None:
    assert prompt_violations(prompt, [], BRAND) == []
    assert HINT_PERSONS in _hint_keys(prompt)


def test_member_names_in_the_prompt_need_the_consent_of_the_person() -> None:
    check = check_prompt(
        "Hantelbank, darauf ein Zettel für Erika Beispiel", ["Erika Beispiel"], BRAND
    )

    (member,) = [h for h in check.hints if h.consent_required]
    assert "Erika Beispiel" in member.reason and "Einwilligung" in member.reason


@pytest.mark.parametrize(
    "prompt",
    [
        "Plakat mit Schriftzug Sommeraktion",
        "Kursraum mit dem Wort „Rückenfit“ an der Wand",
        "Logo des Studios auf der Wand",
    ],
)
def test_text_in_the_image_is_a_hint(prompt: str) -> None:
    assert prompt_violations(prompt, [], BRAND) == []
    assert HINT_TEXT in _hint_keys(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Vorher-nachher-Vergleich eines Bauches",
        "Röntgenbild einer Wirbelsäule",
        "Foto von unserem Studio am Abend",
        f"Eingangsbereich vom {BRAND}",
    ],
)
def test_real_places_results_and_medical_motifs_are_a_hint(prompt: str) -> None:
    assert prompt_violations(prompt, [], BRAND) == []
    assert HINT_REAL in _hint_keys(prompt)


def test_contact_data_is_a_hint() -> None:
    assert HINT_CONTACT in _hint_keys("Matten, Kontakt info@beispiel.example, 01234 567890")


@pytest.mark.parametrize(
    "prompt",
    [
        CLEAN,
        "Trainingsfläche ohne Menschen, ohne Text, keine Logos",
        "Leerer Bewegungsraum, keine Personen und keine Schrift",
    ],
)
def test_clean_and_negated_prompts_pass(prompt: str) -> None:
    assert prompt_violations(prompt, [], BRAND) == []
    assert _hint_keys(prompt) == []


@pytest.mark.parametrize("brand", ["demo.yaml", "handwerk_demo.yaml"])
def test_the_offline_suggestion_passes_the_check_for_every_area(
    session: Session, brand: str
) -> None:
    industry = "fitness" if brand == "demo.yaml" else "handwerk"
    config = load_social_config(
        PROFILES_DIR / f"{industry}.yaml", DEFAULT_BRAND_PROFILES_DIR / brand
    )
    client = SocialLLMClient(scripted())
    for area in config.brand.areas:
        assert area.image_style is not None, area.key
        call = client.image_prompt(
            config,
            area_key=area.key,
            occasion_key=config.industry.occasion_types[0].key,
            notes="Neuer Kurs am Abend",
        )
        assert call.ok and call.output is not None
        assert prompt_violations(call.output.prompt, [], config.brand.name) == [], area.key


# --- flow ---


def test_suggestion_is_audited_and_marked_as_ai(session: Session) -> None:
    draft = _draft(session)

    proposal = suggest_prompt(
        session, CONFIG, SocialLLMClient(scripted()), draft, ImageFormat.SQUARE
    )

    assert proposal.prompt and proposal.prompt_from_ai
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == PROMPT_STEP)).one()
    assert entry.model_params["draft_id"] == draft.id
    assert entry.prompt_version == "image_prompt_v1"


def test_invalid_suggestion_is_retried_once_then_left_to_a_person(session: Session) -> None:
    draft = _draft(session)
    client = SocialLLMClient(scripted(image_prompt=["kein json", '{"falsch": 1}']))

    proposal = suggest_prompt(session, CONFIG, client, draft, ImageFormat.SQUARE)

    assert proposal.prompt == ""
    assert proposal.prompt_from_ai is False
    assert _steps(session).count(PROMPT_STEP) == 2


def test_generation_creates_variants_with_safety_suffix(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    provider = FakeImageProvider()

    rows = _generate(session, draft, raw_dir, provider=provider)

    assert len(rows) == 3
    assert all(Path(r.stored_path).is_file() for r in rows)
    assert provider.calls[0].startswith(CLEAN)
    assert SAFETY_SUFFIX in provider.calls[0]
    assert "Gruppenfotos" in provider.calls[0]  # exclusion from the area's image style
    entry = session.exec(select(AuditEntry).where(AuditEntry.step == GENERATE_STEP)).one()
    assert entry.model_params["draft_id"] == draft.id
    assert entry.model_params["variants"] == 3


def test_rejected_prompt_generates_nothing(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    provider = FakeImageProvider()

    with pytest.raises(ImagePromptRejectedError, match="zu lang"):
        _generate(session, draft, raw_dir, "Matten " * 200, provider)

    assert provider.calls == []
    assert GENERATE_STEP not in _steps(session)
    proposal = latest_proposal(session, draft.id or 0)
    assert proposal is not None and proposal.prompt_from_ai is False  # kept for editing


def test_choose_labels_the_image_and_removes_the_other_variants(
    session: Session, raw_dir: Path
) -> None:
    draft = _draft(session)
    rows = _generate(session, draft, raw_dir)
    others = [Path(r.stored_path) for r in rows[1:]]

    asset = choose_candidate(session, draft, rows[0].id or 0, "demo")

    assert asset.ai_generated and asset.draft_id == draft.id
    assert not asset.shows_persons
    assert Path(asset.stored_path).is_file()
    assert not any(p.is_file() for p in others)
    assert candidates(session, draft.id or 0) == []
    assert _steps(session)[-1] == CHOOSE_STEP


def test_choosing_again_replaces_the_previous_ai_image(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    first = choose_candidate(session, draft, _generate(session, draft, raw_dir)[0].id or 0, "demo")
    first_path = Path(first.stored_path)
    rows = _generate(session, draft, raw_dir, CLEAN + ", Abendlicht")

    second = choose_candidate(session, draft, rows[1].id or 0, "demo")

    assert chosen_image(session, draft.id or 0) == second
    assert not first_path.is_file()


def test_discard_deletes_variants(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    paths = [Path(r.stored_path) for r in _generate(session, draft, raw_dir)]

    assert discard_candidates(session, draft, "demo") == 3

    assert not any(p.is_file() for p in paths)
    assert _steps(session)[-1] == DISCARD_STEP


def test_image_is_fixed_after_approval(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    approve_draft(session, draft, "demo")

    with pytest.raises(ImageSuggestionError, match="vor der Freigabe"):
        _generate(session, draft, raw_dir)


def test_switched_off_blocks_the_service(
    session: Session, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "false")

    with pytest.raises(ImageSuggestionError, match="ausgeschaltet"):
        _generate(session, _draft(session), raw_dir)


# --- deletion concept ---


def test_deleting_the_draft_deletes_proposal_variants_and_ai_image(
    session: Session, raw_dir: Path
) -> None:
    draft = _draft(session)
    suggest_prompt(session, CONFIG, SocialLLMClient(scripted()), draft, ImageFormat.SQUARE)
    rows = _generate(session, draft, raw_dir)
    chosen = choose_candidate(session, draft, rows[0].id or 0, "demo")
    pending = _generate(session, draft, raw_dir, CLEAN + ", Morgennebel")
    files = [Path(chosen.stored_path), *(Path(r.stored_path) for r in pending)]
    generate_entry = session.exec(
        select(AuditEntry).where(AuditEntry.step == GENERATE_STEP)
    ).first()
    assert generate_entry is not None and audit_outputs(session, [generate_entry.id or 0])

    delete_draft(session, draft.id or 0, "demo")

    assert session.exec(select(Asset)).all() == []
    assert session.exec(select(ImageProposal)).all() == []
    assert session.exec(select(ImageCandidate)).all() == []
    assert not any(p.is_file() for p in files)
    # Audit keeps hashes and metadata, the prompt text (output) is gone.
    assert GENERATE_STEP in _steps(session)
    assert audit_outputs(session, [generate_entry.id or 0]) == {}


def test_deleting_the_briefing_deletes_ai_images(session: Session, raw_dir: Path) -> None:
    draft = _draft(session)
    chosen = choose_candidate(session, draft, _generate(session, draft, raw_dir)[0].id or 0, "demo")
    pending = _generate(session, draft, raw_dir, CLEAN + ", Morgennebel")

    assert delete_briefing(session, draft.briefing_id, "demo")

    assert not Path(chosen.stored_path).is_file()
    assert not any(Path(r.stored_path).is_file() for r in pending)
    assert session.get(Briefing, draft.briefing_id) is None


# --- UI ---


@pytest.fixture
def image_client(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[get_social_client] = lambda: SocialLLMClient(scripted())
    app.dependency_overrides[get_social_config] = demo_config
    app.dependency_overrides[get_social_image_provider] = FakeImageProvider
    yield client


def test_image_feature_is_invisible_when_switched_off(
    image_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "false")
    draft = _draft(session)

    page = image_client.get(f"/social/briefings/{draft.briefing_id}").text

    assert "data-ai-images" not in page
    assert "Bild vorschlagen" not in page
    assert image_client.post(f"/social/drafts/{draft.id}/images/suggest").status_code == 404


def test_full_flow_in_the_ui(image_client: TestClient, session: Session) -> None:
    draft = _draft(session)
    url = f"/social/briefings/{draft.briefing_id}"
    assert "Bild vorschlagen" in image_client.get(url).text

    page = image_client.post(
        f"/social/drafts/{draft.id}/images/suggest", follow_redirects=True
    ).text
    assert f'id="prompt-{draft.id}"' in page and "Varianten erzeugen" in page

    rejected = image_client.post(
        f"/social/drafts/{draft.id}/images/generate",
        data={"prompt": "", "image_format": "4:5"},
    )
    assert rejected.status_code == 422
    assert "Bild-Prompt eingeben" in rejected.text

    page = image_client.post(
        f"/social/drafts/{draft.id}/images/generate",
        data={"prompt": CLEAN, "image_format": "4:5"},
        follow_redirects=True,
    ).text
    assert page.count("data-candidate=") == 3
    candidate = candidates(session, draft.id or 0)[0]
    assert image_client.get(f"/social/images/candidates/{candidate.id}").status_code == 200

    page = image_client.post(
        f"/social/drafts/{draft.id}/images/{candidate.id}/choose", follow_redirects=True
    ).text
    assert "KI-generiert" in page
    assert "Variante gewählt" in page

    session.expire_all()
    approve_draft(session, session.get(PostDraft, draft.id), "demo")  # type: ignore[arg-type]
    page = image_client.get(url).text
    assert "Beim Posten als KI-generiert kennzeichnen" in page
    asset = chosen_image(session, draft.id or 0)
    assert asset is not None
    download = image_client.get(f"/social/drafts/{draft.id}/image.jpg?asset={asset.id}")
    assert download.status_code == 200
    assert "ki-generiert.jpg" in download.headers["content-disposition"]


def test_ai_image_of_another_draft_is_not_offered(
    image_client: TestClient, session: Session, raw_dir: Path
) -> None:
    briefing = create_briefing(
        session, CONFIG, course_input(channels=[Channel.INSTAGRAM_POST, Channel.FACEBOOK]), "demo"
    )
    first, second = generate_drafts(session, CONFIG, SocialLLMClient(scripted()), briefing)
    asset = choose_candidate(session, first, _generate(session, first, raw_dir)[0].id or 0, "demo")
    approve_draft(session, second, "demo")

    response = image_client.get(f"/social/drafts/{second.id}/image.jpg?asset={asset.id}")

    assert response.status_code == 404


def test_cost_brake_message_in_the_ui(
    image_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMAGE_MAX_PER_POST", "1")
    draft = _draft(session)
    data = {"prompt": CLEAN, "image_format": "1:1"}
    image_client.post(f"/social/drafts/{draft.id}/images/generate", data=data)

    response = image_client.post(f"/social/drafts/{draft.id}/images/generate", data=data)

    assert response.status_code == 422
    assert "Höchstzahl" in response.text


def test_member_from_the_member_table_blocks_generation(session: Session, raw_dir: Path) -> None:
    session.add(
        Member(
            member_number="M-90001",
            name="Erika Beispiel",
            email="erika@example.com",
            contract_start=date(2025, 1, 1),
            minimum_term_months=12,
            notice_period_months=1,
            renewal_period_months=1,
            monthly_fee_eur=Decimal("29.90"),
        )
    )
    session.commit()

    from app.social.images import ImageHintsPendingError

    with pytest.raises(ImageHintsPendingError, match="Mitglied genannt"):
        _generate(session, _draft(session), raw_dir, "Hantelbank, Widmung für Erika Beispiel")
