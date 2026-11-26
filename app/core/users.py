"""User accounts, roles, server-side login sessions and the lock after failed logins.

Passwords are hashed with Argon2id. A login session is a random token in an HttpOnly cookie;
the database keeps only its SHA-256 hash, so a leaked database does not leak live sessions.
"""

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlmodel import Field, Session, SQLModel, col, delete, select

from app.core.models import timestamp_column, utcnow


class Role(StrEnum):
    """`admin`: operator of the installation; `editor`: employee of the customer."""

    ADMIN = "admin"
    EDITOR = "editor"


ROLE_LABELS: dict[Role, str] = {Role.ADMIN: "Administration", Role.EDITOR: "Redaktion"}


class User(SQLModel, table=True):
    """Account of a person who may log in. Created and reset only via the CLI."""

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: Role
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())


class LoginSession(SQLModel, table=True):
    """Server-side login session; the cookie holds the token, the table only its hash."""

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    csrf_token: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=timestamp_column())
    expires_at: datetime = Field(sa_column=timestamp_column())


class LoginThrottle(SQLModel, table=True):
    """Failed login attempts per (normalised) user name, also for unknown names."""

    key: str = Field(primary_key=True)
    failures: int = 0
    locked_until: datetime | None = Field(default=None, sa_column=timestamp_column(nullable=True))


class UserError(ValueError):
    """Invalid user name, role or password when managing accounts."""


MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)
# Lifetime of a login session (env SESSION_HOURS, default 12 hours).
SESSION_HOURS_VARIABLE = "SESSION_HOURS"
DEFAULT_SESSION_HOURS = 12

_hasher = PasswordHasher()
# Verified for unknown user names so the response time does not reveal which names exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def find_user(session: Session, username: str) -> User | None:
    return session.exec(select(User).where(User.username == normalize_username(username))).first()


def create_user(session: Session, username: str, password: str, role: Role) -> User:
    """Create an account (CLI only). User names are stored in lower case."""
    name = normalize_username(username)
    if not name or not name.replace(".", "").replace("-", "").replace("_", "").isalnum():
        raise UserError("Benutzername: nur Buchstaben, Ziffern, Punkt, Binde- und Unterstrich")
    if find_user(session, name) is not None:
        raise UserError(f"Benutzer „{name}“ existiert bereits")
    validate_password(password)
    user = User(username=name, password_hash=hash_password(password), role=Role(role))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def reset_password(session: Session, username: str, password: str) -> User:
    """Set a new password, end all sessions of the user and lift a login lock (CLI only)."""
    user = find_user(session, username)
    if user is None:
        raise UserError(f"Benutzer „{normalize_username(username)}“ nicht gefunden")
    validate_password(password)
    user.password_hash = hash_password(password)
    session.add(user)
    session.exec(delete(LoginSession).where(col(LoginSession.user_id) == user.id))  # type: ignore[call-overload]
    session.exec(delete(LoginThrottle).where(col(LoginThrottle.key) == user.username))  # type: ignore[call-overload]
    session.commit()
    session.refresh(user)
    return user


def list_users(session: Session) -> list[User]:
    return list(session.exec(select(User).order_by(col(User.username))).all())


# --- login and lock after failed attempts ---


@dataclass(frozen=True)
class LoginResult:
    user: User | None
    locked: bool = False


def _aware(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; all stored timestamps are UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=utcnow().tzinfo)


def authenticate(
    session: Session, username: str, password: str, now: datetime | None = None
) -> LoginResult:
    """Check the credentials; after MAX_FAILED_LOGINS failures the name is locked for a while.

    A locked name is refused even with the correct password, so guessing stays pointless.
    """
    now = now or utcnow()
    key = normalize_username(username)
    throttle = session.get(LoginThrottle, key)
    if throttle and throttle.locked_until and _aware(throttle.locked_until) > now:
        return LoginResult(user=None, locked=True)
    user = find_user(session, key) if key else None
    valid = verify_password(user.password_hash if user else _DUMMY_HASH, password)
    if user is not None and user.active and valid:
        if throttle is not None:
            session.delete(throttle)
            session.commit()
        return LoginResult(user=user)
    throttle = throttle or LoginThrottle(key=key)
    if throttle.locked_until is not None:  # lock expired: start counting again
        throttle.failures, throttle.locked_until = 0, None
    throttle.failures += 1
    locked = throttle.failures >= MAX_FAILED_LOGINS
    if locked:
        throttle.locked_until = now + LOCK_DURATION
    session.add(throttle)
    session.commit()
    return LoginResult(user=None, locked=locked)


# --- server-side sessions ---


def session_lifetime() -> timedelta:
    raw = (os.environ.get(SESSION_HOURS_VARIABLE) or "").strip()
    try:
        hours = int(raw) if raw else DEFAULT_SESSION_HOURS
    except ValueError:
        hours = DEFAULT_SESSION_HOURS
    return timedelta(hours=hours if hours > 0 else DEFAULT_SESSION_HOURS)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def start_session(session: Session, user: User, now: datetime | None = None) -> str:
    """Create a new login session (fresh token on every login) and return the cookie token."""
    if user.id is None:
        raise ValueError("user must be stored first")
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    session.add(
        LoginSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + session_lifetime(),
        )
    )
    session.commit()
    return token


@dataclass(frozen=True)
class SessionUser:
    """The logged-in person of a request."""

    username: str
    role: Role
    csrf_token: str

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[self.role]


def resolve_session(
    session: Session, token: str | None, now: datetime | None = None
) -> SessionUser | None:
    """The user of a session token, or None for unknown, expired or deactivated sessions."""
    if not token:
        return None
    now = now or utcnow()
    login = session.exec(
        select(LoginSession).where(LoginSession.token_hash == _token_hash(token))
    ).first()
    if login is None:
        return None
    user = session.get(User, login.user_id)
    if _aware(login.expires_at) <= now or user is None or not user.active:
        session.delete(login)
        session.commit()
        return None
    return SessionUser(username=user.username, role=user.role, csrf_token=login.csrf_token)


def end_session(session: Session, token: str | None) -> None:
    if not token:
        return
    session.exec(delete(LoginSession).where(col(LoginSession.token_hash) == _token_hash(token)))  # type: ignore[call-overload]
    session.commit()
