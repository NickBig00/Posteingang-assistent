"""Design system and layout (ROADMAP_social U1): theme, static assets, navigation, settings."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from app.core.theme import (
    DEFAULT_BRAND_COLOR,
    MIN_CONTRAST,
    WHITE,
    BrandColorChoice,
    ThemeConfigError,
    build_theme,
    contrast_ratio,
    current_theme,
    parse_hex,
    resolve_brand_color,
)
from app.core.web import SETTINGS_ENTRY, NavEntry, active_entry

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_FILES = sorted(ROOT.glob("app/**/templates/**/*.html"))


# --- theme ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [("#0f766e", (15, 118, 110)), ("0F766E", (15, 118, 110)), ("#fff", (255, 255, 255))],
)
def test_parse_hex(value: str, expected: tuple[int, int, int]) -> None:
    assert parse_hex(value) == expected


@pytest.mark.parametrize("value", ["", "teal", "#12345", "#gggggg", "rgb(1,2,3)"])
def test_invalid_hex_is_rejected(value: str) -> None:
    with pytest.raises(ThemeConfigError, match="BRAND_COLOR"):
        parse_hex(value)


def test_contrast_ratio_extremes() -> None:
    assert contrast_ratio((0, 0, 0), WHITE) == pytest.approx(21)
    assert contrast_ratio(WHITE, WHITE) == pytest.approx(1)


@pytest.mark.parametrize(
    "color",
    [DEFAULT_BRAND_COLOR, "#ffcc00", "#e11d48", "#777777", "#7c8b9a", "#22c55e", "#1e3a8a", "#fff"],
)
def test_theme_keeps_text_readable_for_any_brand_colour(color: str) -> None:
    theme = build_theme(color)

    assert contrast_ratio(parse_hex(theme.brand), parse_hex(theme.on_brand)) >= MIN_CONTRAST
    assert contrast_ratio(parse_hex(theme.brand_text), WHITE) >= MIN_CONTRAST


def test_theme_keeps_a_dark_brand_colour_unchanged() -> None:
    assert build_theme("#1e3a8a").brand == "#1e3a8a"
    assert build_theme("#1e3a8a").on_brand == "#ffffff"


def test_light_brand_colour_gets_dark_text() -> None:
    assert build_theme("#ffcc00").on_brand == "#0f172a"


def test_invalid_configured_colour_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_COLOR", "lila")

    assert current_theme() == build_theme(DEFAULT_BRAND_COLOR)


def test_css_variables_use_rgb_channels() -> None:
    css = build_theme("#1e3a8a").css_variables()

    assert "--brand: 30 58 138;" in css
    assert "--brand-on: 255 255 255;" in css


def test_brand_colour_from_configuration_reaches_every_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRAND_COLOR", "#1e3a8a")

    for path in ("/", "/social", "/settings"):
        assert "--brand: 30 58 138;" in client.get(path).text, path


# --- static assets: served locally, nothing from third parties ---


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        ("/static/css/app.css", "text/css"),
        ("/static/vendor/htmx-2.0.3.min.js", "javascript"),
        ("/static/js/app.js", "javascript"),
        ("/static/fonts/Inter_400Regular.ttf", "font"),
    ],
)
def test_static_assets_are_served_locally(client: TestClient, path: str, media_type: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert media_type in response.headers["content-type"]


@pytest.mark.parametrize("template", TEMPLATE_FILES, ids=lambda p: p.name)
def test_templates_load_no_external_resources(template: Path) -> None:
    html = template.read_text(encoding="utf-8")

    external = re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="(?:https?:)?//[^"]+"', html)
    assert external == []


def test_font_and_htmx_licences_ship_with_the_files() -> None:
    assert "Open Font License" in (ROOT / "app/core/static/fonts/Inter-OFL.txt").read_text()
    assert "BSD" in (ROOT / "app/core/static/vendor/htmx-LICENSE.txt").read_text()


def _build_css_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_css", ROOT / "scripts" / "build_css.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compiled_css_is_up_to_date() -> None:
    """Changed templates or styles need `uv run python scripts/build_css.py`."""
    build_css = _build_css_module()
    css = build_css.OUTPUT.read_text(encoding="utf-8")

    assert build_css.recorded_hash(css) == build_css.sources_hash(), (
        "app/core/static/css/app.css ist veraltet: uv run python scripts/build_css.py"
    )


@pytest.mark.parametrize(
    "component",
    [
        ".card{",
        ".btn-primary{",
        ".badge-ai{",
        ".table ",
        ".input{",
        ".alert-warning{",
        ".empty-state{",
        ".spinner{",
        ".dot-error{",
        ".nav-link{",
    ],
)
def test_components_are_in_the_compiled_css(component: str) -> None:
    css = (ROOT / "app/core/static/css/app.css").read_text(encoding="utf-8")

    assert component in css


# --- navigation ---

OVERVIEW = NavEntry("core", "Übersicht", "/")
INBOX = NavEntry("inbox", "Posteingang", "/inbox", prefixes=("/items",))
SOCIAL = NavEntry("social", "Social Media", "/social")
CALENDAR = NavEntry("social", "Kalender", "/social/calendar")
NAVIGATION = [OVERVIEW, INBOX, SOCIAL, CALENDAR, SETTINGS_ENTRY]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", OVERVIEW),
        ("/inbox", INBOX),
        ("/items/3", INBOX),
        ("/items/3/audit", INBOX),
        ("/social", SOCIAL),
        ("/social/briefings/2", SOCIAL),
        ("/social/calendar", CALENDAR),
        ("/settings", SETTINGS_ENTRY),
        ("/itemsx", None),
        ("/socialx", None),
    ],
)
def test_active_navigation_entry(path: str, expected: NavEntry | None) -> None:
    assert active_entry(NAVIGATION, path) == expected


def test_sidebar_lists_modules_calendar_and_settings(client: TestClient) -> None:
    html = client.get("/social/calendar").text
    nav = html[html.index('id="sidebar"') : html.index("</nav>")]

    labels = re.findall(r'class="nav-icon".*?</svg>([^<]+)</a>', nav, flags=re.S)
    assert [label.strip() for label in labels] == [
        "Übersicht",
        "Posteingang",
        "Social Media",
        "Kalender",
        "Einstellungen",
    ]
    assert re.search(r'href="/social/calendar"[^>]*aria-current="page"', nav)
    assert nav.count('aria-current="page"') == 1


def test_layout_has_skip_link_and_header_title(client: TestClient) -> None:
    html = client.get("/settings").text

    assert 'href="#inhalt"' in html
    assert 'id="inhalt"' in html
    assert "data-app-title>Posteingangs-Assistent<" in html


# --- settings and style guide ---


def test_settings_show_configuration_but_no_secrets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-geheim-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-geheim-456")
    monkeypatch.setenv("BRAND_COLOR", "#1e3a8a")

    html = client.get("/settings").text

    assert "#1e3a8a" in html
    assert "BRAND_COLOR" in html
    assert "geheim" not in html


def test_settings_flag_an_invalid_brand_colour(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRAND_COLOR", "lila")

    assert "„lila“ ungültig, ignoriert" in client.get("/settings").text


def test_design_page_shows_all_components(client: TestClient) -> None:
    html = client.get("/design").text

    for marker in (
        "btn-primary",
        "badge-ai",
        "data-ai-label",
        'class="table"',
        "input-invalid",
        "alert-error",
        "empty-state",
        "spinner",
        "dot-warning",
    ):
        assert marker in html, marker


# --- uniform status tones (U3/U4) ---


def test_every_status_of_both_modules_has_a_design_tone() -> None:
    from typing import get_args

    from app.core.overview import Tone
    from app.inbox.models import STATUS_TONES, ItemStatus
    from app.social.models import POST_STATUS_TONES, PostStatus

    tones = set(get_args(Tone))
    assert set(STATUS_TONES) == set(ItemStatus)
    assert set(POST_STATUS_TONES) == set(PostStatus)
    assert set(STATUS_TONES.values()) <= tones
    assert set(POST_STATUS_TONES.values()) <= tones
    # Same meaning, same tone in both modules.
    assert (
        STATUS_TONES[ItemStatus.NEEDS_MANUAL_REVIEW]
        == POST_STATUS_TONES[PostStatus.NEEDS_MANUAL_REVIEW]
    )
    assert STATUS_TONES[ItemStatus.APPROVED] == POST_STATUS_TONES[PostStatus.APPROVED]
    assert STATUS_TONES[ItemStatus.REJECTED] == POST_STATUS_TONES[PostStatus.REJECTED]


# --- contrast of the design tokens (U5; the full page check is scripts/a11y.cjs) ---

# Text and background colours of the tones (Tailwind palette values used in app.css).
TONE_PAIRS = {
    "badge-neutral": ("#334155", "#f1f5f9"),
    "badge-info": ("#075985", "#f0f9ff"),
    "badge-ok": ("#166534", "#f0fdf4"),
    "badge-warning": ("#78350f", "#fffbeb"),
    "badge-error": ("#991b1b", "#fef2f2"),
    "badge-ai": ("#5b21b6", "#f5f3ff"),
    "alert-warning": ("#451a03", "#fffbeb"),
    "ink-subtle on surface": ("#64748b", "#ffffff"),
    "ink-subtle on page": ("#64748b", "#f8fafc"),
    "code": ("#475569", "#f1f5f9"),
    "btn-success": ("#ffffff", "#15803d"),
    "btn-danger": ("#b91c1c", "#ffffff"),
}


@pytest.mark.parametrize("name", list(TONE_PAIRS))
def test_tone_colours_meet_wcag_aa(name: str) -> None:
    text, background = TONE_PAIRS[name]

    assert contrast_ratio(parse_hex(text), parse_hex(background)) >= MIN_CONTRAST


def test_sidebar_list_contains_only_list_items(client: TestClient) -> None:
    html = client.get("/settings").text
    nav = html[html.index('id="sidebar"') : html.index("</nav>")]

    assert 'role="separator"' not in nav
    assert nav.count("<li") == nav.count("</li>")


# --- brand colour from the brand profile (follow-up to U1) ---


def test_brand_colour_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_COLOR", raising=False)
    assert resolve_brand_color(None) == BrandColorChoice(DEFAULT_BRAND_COLOR, "default")
    assert resolve_brand_color("#334155") == BrandColorChoice("#334155", "profile")
    monkeypatch.setenv("BRAND_COLOR", "#1e3a8a")
    assert resolve_brand_color("#334155") == BrandColorChoice("#1e3a8a", "env")
    monkeypatch.setenv("BRAND_COLOR", "lila")
    assert resolve_brand_color("#334155") == BrandColorChoice("#334155", "profile", "lila")


def test_interface_uses_the_colour_of_the_brand_profile(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BRAND_COLOR", raising=False)
    monkeypatch.setenv("BRAND_PROFILE", "handwerk_demo")

    assert "--brand: 51 65 85;" in client.get("/").text  # #334155 from handwerk_demo.yaml
    assert "aus dem Markenprofil" in client.get("/settings").text
    monkeypatch.setenv("BRAND_COLOR", "#1e3a8a")
    assert "--brand: 30 58 138;" in client.get("/").text


def test_without_social_module_the_profile_colour_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import create_app

    monkeypatch.delenv("BRAND_COLOR", raising=False)
    monkeypatch.setenv("BRAND_PROFILE", "handwerk_demo")
    monkeypatch.setenv("MODULES", "inbox")

    assert create_app().state.brand_color_providers == []
