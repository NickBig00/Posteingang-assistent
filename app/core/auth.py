"""Login, logout, access control and CSRF protection for the web interface.

Every route of the application depends on `enforce_access` (registered in `app/main.py`):
without a valid login session only the login page, `/health` and static files are reachable.
Every state-changing request (POST, PUT, PATCH, DELETE) must carry the CSRF token of the
session, as form field `csrf_token` or header `X-CSRF-Token` (HTMX sends the header).
"""

import os
import secrets
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from app.core.db import get_session
from app.core.users import (
    LOCK_DURATION,
    SessionUser,
    authenticate,
    end_session,
    resolve_session,
    session_lifetime,
    start_session,
)
from app.core.web import CORE_TEMPLATES_DIR, module_templates

SESSION_COOKIE = "pa_session"
LOGIN_CSRF_COOKIE = "pa_login_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
LOGIN_PATH = "/login"
# Reachable without login (static files are a separate mount and need no session either).
PUBLIC_PATHS = frozenset({LOGIN_PATH, "/health"})
# Reserved for the operator (role `admin`): configuration and the style guide.
ADMIN_PATHS = frozenset({"/settings", "/design"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Set to "0" only for local development over plain HTTP on a host other than localhost.
COOKIE_SECURE_VARIABLE = "SESSION_COOKIE_SECURE"

SessionDep = Annotated[Session, Depends(get_session)]
templates = module_templates(CORE_TEMPLATES_DIR, "core")
router = APIRouter()


class LoginRequiredError(Exception):
    """The request needs a login session."""


class CsrfError(Exception):
    """The CSRF token of a state-changing request is missing or wrong."""


class AdminRequiredError(Exception):
    """The page is reserved for the `admin` role."""


def cookie_secure() -> bool:
    return (os.environ.get(COOKIE_SECURE_VARIABLE) or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def session_user(request: Request, session: SessionDep) -> SessionUser | None:
    """The logged-in person of this request (evaluated once per request)."""
    return resolve_session(session, request.cookies.get(SESSION_COOKIE))


SessionUserDep = Annotated[SessionUser | None, Depends(session_user)]


async def _sent_csrf_token(request: Request) -> str | None:
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        return None
    form = await request.form()  # cached on the request: the route reads the same form
    value = form.get(CSRF_FIELD)
    return value if isinstance(value, str) else None


def tokens_match(sent: str | None, expected: str | None) -> bool:
    return bool(sent and expected) and secrets.compare_digest(str(sent), str(expected))


async def enforce_access(request: Request, user: SessionUserDep) -> None:
    """App-wide dependency: login required (except public paths), admin pages, CSRF check."""
    if request.url.path in PUBLIC_PATHS:
        return
    if user is None:
        raise LoginRequiredError
    request.state.user = user
    if request.url.path in ADMIN_PATHS and not user.is_admin:
        raise AdminRequiredError
    if request.method in UNSAFE_METHODS and not tokens_match(
        await _sent_csrf_token(request), user.csrf_token
    ):
        raise CsrfError


def current_user(user: SessionUserDep) -> str:
    """User name of the person acting in the UI (recorded in reviews and audit entries)."""
    if user is None:
        raise LoginRequiredError
    return user.username


def safe_next(target: str | None) -> str:
    """Only local paths are allowed as redirect target after the login (no open redirect)."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    return target


def _login_page(
    request: Request, *, error: str | None = None, next_path: str = "/", status: int = 200
) -> HTMLResponse:
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"login_csrf": token, "error": error, "next_path": next_path},
        status_code=status,
    )
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        token,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path=LOGIN_PATH,
    )
    return response


@router.get(LOGIN_PATH, response_model=None)
def login_form(
    request: Request, user: SessionUserDep, next: str | None = None
) -> HTMLResponse | RedirectResponse:
    if user is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return _login_page(request, next_path=safe_next(next))


@router.post(LOGIN_PATH, response_model=None)
def login_submit(
    request: Request,
    session: SessionDep,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse | RedirectResponse:
    """Check the credentials; the login form is protected by a double-submit token."""
    next_path = safe_next(next)
    if not tokens_match(csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE)):
        return _login_page(
            request,
            error="Die Anmeldeseite ist abgelaufen. Bitte erneut versuchen.",
            next_path=next_path,
            status=403,
        )
    result = authenticate(session, username, password)
    if result.user is None:
        minutes = int(LOCK_DURATION.total_seconds() // 60)
        error = (
            f"Zu viele fehlgeschlagene Anmeldeversuche. Bitte in {minutes} Minuten erneut "
            "versuchen oder die Administration um ein neues Passwort bitten."
            if result.locked
            else "Benutzername oder Passwort ist falsch."
        )
        return _login_page(
            request, error=error, next_path=next_path, status=429 if result.locked else 401
        )
    token = start_session(session, result.user)
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(session_lifetime().total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path=LOGIN_PATH)
    return response


@router.post("/logout")
def logout(request: Request, session: SessionDep) -> RedirectResponse:
    """End the session (the CSRF check of `enforce_access` applies)."""
    end_session(session, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse(LOGIN_PATH, status_code=303)
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=cookie_secure(), samesite="lax")
    return response


def _login_required(request: Request, _error: Exception) -> Response:
    if request.headers.get("HX-Request"):
        # HTMX swaps would otherwise insert the login page into a fragment.
        return Response(status_code=401, headers={"HX-Redirect": LOGIN_PATH})
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    location = LOGIN_PATH if request.method in UNSAFE_METHODS else _with_next(target)
    return RedirectResponse(location, status_code=303)


def _with_next(target: str) -> str:
    return LOGIN_PATH if target == "/" else f"{LOGIN_PATH}?next={quote(target, safe='')}"


def _error_page(request: Request, title: str, message: str, hint: str, status: int) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": title, "message": message, "hint": hint, "settings_link": False},
        status_code=status,
    )


def _csrf_failed(request: Request, _error: Exception) -> Response:
    return _error_page(
        request,
        "Sicherheitsprüfung fehlgeschlagen",
        "Die Anfrage enthielt kein gültiges Sicherheitstoken (CSRF).",
        "Bitte die Seite neu laden und die Aktion erneut ausführen.",
        403,
    )


def _admin_required(request: Request, _error: Exception) -> Response:
    return _error_page(
        request,
        "Keine Berechtigung",
        "Diese Seite ist der Administration vorbehalten.",
        "Bei Fragen zur Konfiguration bitte an die Administration wenden.",
        403,
    )


def register_auth(application: FastAPI) -> None:
    """Login routes and the handlers for missing login, CSRF and role errors."""
    application.include_router(router)
    application.add_exception_handler(LoginRequiredError, _login_required)
    application.add_exception_handler(CsrfError, _csrf_failed)
    application.add_exception_handler(AdminRequiredError, _admin_required)
