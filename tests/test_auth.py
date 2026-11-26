"""Login, roles, CSRF, lock after failed logins and account CLI (ROADMAP_pilot P1)."""

import re
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core import cli as user_cli
from app.core.auth import (
    CSRF_HEADER,
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    safe_next,
)
from app.core.auth import router as auth_router
from app.core.models import AuditEntry, utcnow
from app.core.users import (
    LOCK_DURATION,
    MAX_FAILED_LOGINS,
    LoginSession,
    Role,
    User,
    UserError,
    authenticate,
    create_user,
    find_user,
    reset_password,
    resolve_session,
    start_session,
    verify_password,
)
from app.core.web import router as core_router
from app.main import MODULES
from tests.conftest import TEST_PASSWORD, TEST_USER, add_test_user

NEW_PASSWORD = "ganz-neues-passwort-42"


def _login_token(client: TestClient) -> str:
    page = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, "login form without CSRF token"
    return match.group(1)


def _login(
    client: TestClient, username: str = TEST_USER, password: str = TEST_PASSWORD, next: str = "/"
) -> "object":
    token = _login_token(client)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token, "next": next},
        follow_redirects=False,
    )


# --- access without login ---


def _get_paths() -> list[str]:
    """All GET routes of core, login and the modules (path parameters set to 1)."""
    routers = [core_router, auth_router, *(router for router, _, _ in MODULES.values())]
    return [
        re.sub(r"\{[^}]+\}", "1", route.path)
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    ]


def test_every_page_except_login_and_health_needs_a_login(anonymous_client: TestClient) -> None:
    paths = _get_paths()
    assert len(paths) > 20
    for path in paths:
        response = anonymous_client.get(path, follow_redirects=False)
        if path in {"/login", "/health"}:
            assert response.status_code == 200, path
        else:
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/login"), path


def test_post_without_login_is_refused(anonymous_client: TestClient, engine: Engine) -> None:
    response = anonymous_client.post("/items/process", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    response = anonymous_client.post("/social/briefings/1/delete", follow_redirects=False)
    assert response.status_code == 303
    with Session(engine) as session:
        assert session.exec(select(AuditEntry)).first() is None


def test_htmx_request_without_login_redirects_the_whole_page(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get("/inbox", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


def test_redirect_remembers_the_requested_page(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/social/calendar?view=week", follow_redirects=False)
    assert response.headers["location"] == "/login?next=%2Fsocial%2Fcalendar%3Fview%3Dweek"


def test_api_docs_are_switched_off(anonymous_client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anonymous_client.get(path, follow_redirects=False).status_code in {303, 404}


def test_static_files_and_health_stay_public(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/health").json() == {"status": "ok"}
    assert anonymous_client.get("/static/css/app.css").status_code == 200


def test_unknown_session_cookie_is_not_accepted(anonymous_client: TestClient) -> None:
    anonymous_client.cookies.set(SESSION_COOKIE, "ausgedacht")
    assert anonymous_client.get("/", follow_redirects=False).status_code == 303


# --- login and logout ---


def test_login_sets_a_secure_session_cookie_and_opens_the_app(
    anonymous_client: TestClient, engine: Engine
) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)

    response = _login(anonymous_client, next="/social")

    assert response.status_code == 303
    assert response.headers["location"] == "/social"
    cookie = next(
        h for h in response.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE)
    )
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    page = anonymous_client.get("/")
    assert page.status_code == 200
    assert TEST_USER in page.text
    assert "Demo-Benutzer" not in page.text


def test_login_stores_only_a_hash_of_the_session_token(
    anonymous_client: TestClient, engine: Engine
) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)
    _login(anonymous_client)
    token = anonymous_client.cookies[SESSION_COOKIE]
    with Session(engine) as session:
        stored = session.exec(select(LoginSession)).one()
    assert stored.token_hash != token
    assert token not in stored.token_hash


def test_wrong_password_and_unknown_user_get_the_same_answer(
    anonymous_client: TestClient, engine: Engine
) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)

    wrong = _login(anonymous_client, password="falsches-passwort")
    unknown = _login(anonymous_client, username="niemand")

    assert wrong.status_code == unknown.status_code == 401
    assert "Benutzername oder Passwort ist falsch." in wrong.text
    assert "Benutzername oder Passwort ist falsch." in unknown.text
    assert SESSION_COOKIE not in anonymous_client.cookies


def test_logged_in_user_is_sent_on_from_the_login_page(client: TestClient) -> None:
    response = client.get("/login?next=/social", follow_redirects=False)
    assert response.headers["location"] == "/social"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/social", "/social"),
        ("/social/calendar?view=week", "/social/calendar?view=week"),
        ("https://example.invalid/", "/"),
        ("//example.invalid/", "/"),
        ("/\\example.invalid", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_only_local_redirects_after_login(target: str | None, expected: str) -> None:
    assert safe_next(target) == expected


def test_logout_ends_the_session(client: TestClient, engine: Engine) -> None:
    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    with Session(engine) as session:
        assert session.exec(select(LoginSession)).first() is None
    assert client.get("/", follow_redirects=False).status_code == 303


def test_logout_needs_the_csrf_token(client: TestClient) -> None:
    del client.headers[CSRF_HEADER]
    assert client.post("/logout", follow_redirects=False).status_code == 403
    assert client.get("/").status_code == 200


def test_header_shows_user_role_and_logout(client: TestClient) -> None:
    html = client.get("/").text
    assert "data-current-user" in html
    assert "testuser · Administration" in html
    assert 'action="/logout"' in html


def test_expired_session_is_refused_and_removed(engine: Engine) -> None:
    user = add_test_user(engine, TEST_USER, Role.EDITOR)
    with Session(engine) as session:
        token = start_session(session, user, now=utcnow() - timedelta(days=2))
        assert resolve_session(session, token) is None
        assert session.exec(select(LoginSession)).first() is None


def test_deactivated_user_loses_access(client: TestClient, engine: Engine) -> None:
    with Session(engine) as session:
        user = find_user(session, TEST_USER)
        assert user is not None
        user.active = False
        session.add(user)
        session.commit()
    assert client.get("/", follow_redirects=False).status_code == 303


# --- roles ---


def test_admin_sees_settings(client: TestClient) -> None:
    html = client.get("/").text
    assert 'href="/settings"' in html
    assert client.get("/settings").status_code == 200
    assert client.get("/design").status_code == 200


def test_editor_works_with_modules_but_not_with_settings(editor_client: TestClient) -> None:
    html = editor_client.get("/").text
    assert "testredaktion · Redaktion" in html
    assert 'href="/settings"' not in html
    assert editor_client.get("/social").status_code == 200
    assert editor_client.get("/inbox").status_code == 200
    for path in ("/settings", "/design"):
        response = editor_client.get(path)
        assert response.status_code == 403
        assert "Administration vorbehalten" in response.text


def test_roles_are_fixed() -> None:
    assert {role.value for role in Role} == {"admin", "editor"}


# --- CSRF ---


def test_post_without_csrf_token_is_refused(client: TestClient, engine: Engine) -> None:
    del client.headers[CSRF_HEADER]

    response = client.post("/samples/import", follow_redirects=False)

    assert response.status_code == 403
    assert "Sicherheitsprüfung fehlgeschlagen" in response.text
    with Session(engine) as session:
        assert session.exec(select(AuditEntry)).first() is None


def test_post_with_wrong_csrf_token_is_refused(client: TestClient) -> None:
    client.headers[CSRF_HEADER] = "falsch"
    assert client.post("/social/briefings/1/delete").status_code == 403


def test_csrf_token_in_the_form_field_is_accepted(client: TestClient) -> None:
    token = client.headers.pop(CSRF_HEADER)
    refused = client.post("/social/campaigns/demo", data={}, follow_redirects=False)
    assert refused.status_code == 403
    accepted = client.post(
        "/social/campaigns/demo", data={"csrf_token": token}, follow_redirects=False
    )
    assert accepted.status_code != 403


def test_csrf_token_of_another_session_is_refused(client: TestClient, engine: Engine) -> None:
    other = add_test_user(engine, "andere", Role.EDITOR)
    with Session(engine) as session:
        other_user = resolve_session(session, start_session(session, other))
    assert other_user is not None
    client.headers[CSRF_HEADER] = other_user.csrf_token
    assert client.post("/logout").status_code == 403


def test_pages_render_the_token_into_forms_and_htmx_headers(client: TestClient) -> None:
    token = client.headers[CSRF_HEADER]
    client.post("/social/campaigns/demo")  # creates briefings with forms on their pages
    for path in ("/", "/inbox", "/social", "/social/calendar", "/social/briefings/1"):
        html = client.get(path).text
        assert f'"X-CSRF-Token": "{token}"' in html.replace("&#34;", '"'), path
        assert 'name="csrf_token" value=""' not in html, path
        forms = re.findall(r'<form\b[^>]*method="post"[^>]*>', html)
        assert html.count(f'name="csrf_token" value="{token}"') >= len(forms), path


def test_login_form_needs_its_csrf_token(anonymous_client: TestClient, engine: Engine) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)
    anonymous_client.get("/login")
    response = anonymous_client.post(
        "/login",
        data={"username": TEST_USER, "password": TEST_PASSWORD, "csrf_token": "falsch"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert SESSION_COOKIE not in anonymous_client.cookies
    anonymous_client.cookies.delete(LOGIN_CSRF_COOKIE, path="/login")


# --- lock after failed attempts ---


def test_account_is_locked_after_too_many_failures(
    anonymous_client: TestClient, engine: Engine
) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert _login(anonymous_client, password="falsch-falsch").status_code == 401

    locked = _login(anonymous_client, password="falsch-falsch")
    assert locked.status_code == 429
    assert "Zu viele fehlgeschlagene Anmeldeversuche" in locked.text
    # Even the correct password is refused while the lock lasts.
    assert _login(anonymous_client).status_code == 429
    assert SESSION_COOKIE not in anonymous_client.cookies


def test_lock_ends_after_the_lock_duration(engine: Engine) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)
    start = utcnow()
    with Session(engine) as session:
        for _ in range(MAX_FAILED_LOGINS):
            authenticate(session, TEST_USER, "falsch", now=start)
        assert authenticate(session, TEST_USER, TEST_PASSWORD, now=start).locked
        later = start + LOCK_DURATION + timedelta(seconds=1)
        assert authenticate(session, TEST_USER, TEST_PASSWORD, now=later).user is not None


def test_successful_login_resets_the_counter(engine: Engine) -> None:
    add_test_user(engine, TEST_USER, Role.EDITOR)
    with Session(engine) as session:
        for _ in range(MAX_FAILED_LOGINS - 1):
            authenticate(session, TEST_USER, "falsch")
        assert authenticate(session, TEST_USER, TEST_PASSWORD).user is not None
        for _ in range(MAX_FAILED_LOGINS - 1):
            assert not authenticate(session, TEST_USER, "falsch").locked


def test_unknown_names_are_locked_as_well(engine: Engine) -> None:
    with Session(engine) as session:
        results = [authenticate(session, "unbekannt", "falsch") for _ in range(MAX_FAILED_LOGINS)]
    assert results[-1].locked


# --- accounts and CLI ---


def test_passwords_are_hashed_with_argon2(session: Session) -> None:
    user = create_user(session, "Neue.Person", NEW_PASSWORD, Role.EDITOR)
    assert user.username == "neue.person"
    assert user.password_hash.startswith("$argon2id$")
    assert NEW_PASSWORD not in user.password_hash
    assert verify_password(user.password_hash, NEW_PASSWORD)
    assert not verify_password(user.password_hash, "anders")


@pytest.mark.parametrize(
    ("username", "password"),
    [("kurz", "zu-kurz"), ("leer zeichen", NEW_PASSWORD), ("", NEW_PASSWORD)],
)
def test_invalid_accounts_are_refused(session: Session, username: str, password: str) -> None:
    with pytest.raises(UserError):
        create_user(session, username, password, Role.EDITOR)


def test_duplicate_user_is_refused(session: Session) -> None:
    create_user(session, "person", NEW_PASSWORD, Role.EDITOR)
    with pytest.raises(UserError):
        create_user(session, "PERSON", NEW_PASSWORD, Role.ADMIN)


def test_reset_password_ends_sessions_and_lifts_the_lock(engine: Engine) -> None:
    user = add_test_user(engine, TEST_USER, Role.EDITOR)
    with Session(engine) as session:
        token = start_session(session, user)
        for _ in range(MAX_FAILED_LOGINS):
            authenticate(session, TEST_USER, "falsch")

        reset_password(session, TEST_USER, NEW_PASSWORD)

        assert resolve_session(session, token) is None
        assert authenticate(session, TEST_USER, NEW_PASSWORD).user is not None
        assert authenticate(session, TEST_USER, TEST_PASSWORD).user is None


def test_reset_password_of_unknown_user_fails(session: Session) -> None:
    with pytest.raises(UserError):
        reset_password(session, "niemand", NEW_PASSWORD)


@pytest.fixture
def cli_engine(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(user_cli, "engine", engine)
    return engine


def test_cli_creates_lists_and_resets_users(
    cli_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    def password() -> str:
        return NEW_PASSWORD

    assert user_cli.main(["user-create", "chefin", "--role", "admin"], password) == 0
    assert user_cli.main(["user-create", "team.eins"], password) == 0
    assert user_cli.main(["user-list"]) == 0
    out = capsys.readouterr().out
    assert "chefin\tadmin" in out
    assert "team.eins\teditor" in out
    assert NEW_PASSWORD not in out

    assert user_cli.main(["user-reset-password", "team.eins"], lambda: "noch-ein-passwort") == 0
    with Session(cli_engine) as session:
        user = session.exec(select(User).where(User.username == "team.eins")).one()
        assert verify_password(user.password_hash, "noch-ein-passwort")


def test_cli_reports_errors_without_traceback(
    cli_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert user_cli.main(["user-create", "person"], lambda: "kurz") == 1
    assert "mindestens" in capsys.readouterr().err
    assert user_cli.main(["user-reset-password", "niemand"], lambda: NEW_PASSWORD) == 1


def test_cli_reads_the_password_from_stdin(
    cli_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(NEW_PASSWORD + "\n"))
    assert user_cli.main(["user-create", "skript", "--password-stdin"]) == 0
    with Session(cli_engine) as session:
        user = find_user(session, "skript")
        assert user is not None
        assert verify_password(user.password_hash, NEW_PASSWORD)


def test_interactive_password_must_match_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([NEW_PASSWORD, "anders-als-vorher"])
    monkeypatch.setattr(user_cli.getpass, "getpass", lambda _prompt: next(answers))
    with pytest.raises(UserError):
        user_cli.read_password_interactive()


# --- approvals and audit carry the logged-in user ---


def test_human_audit_entries_name_the_logged_in_user(
    editor_client: TestClient, engine: Engine
) -> None:
    editor_client.post("/social/campaigns/demo")
    editor_client.post("/social/briefings/1/delete")
    with Session(engine) as session:
        actors = {
            e.model_params.get("actor")
            for e in session.exec(select(AuditEntry)).all()
            if "actor" in e.model_params
        }
    assert actors == {"testredaktion"}
