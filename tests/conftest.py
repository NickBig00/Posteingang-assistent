from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.auth import CSRF_HEADER, SESSION_COOKIE
from app.core.db import get_session, init_db, make_engine
from app.core.users import Role, User, hash_password, resolve_session, start_session
from app.inbox.web import get_storage_dir
from app.main import app

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "fitness"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    return tmp_path / "raw"


# Fixed test users (fictitious). The hash is computed once: Argon2 is slow on purpose.
TEST_USER = "testuser"
TEST_EDITOR = "testredaktion"
TEST_PASSWORD = "test-passwort-123"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)
# Secure cookies are only sent over HTTPS, as in production.
BASE_URL = "https://testserver"


def add_test_user(engine: Engine, username: str, role: Role) -> User:
    with Session(engine) as session:
        user = User(username=username, password_hash=TEST_PASSWORD_HASH, role=role)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def login_client(test_client: TestClient, engine: Engine, user: User) -> TestClient:
    """Log `test_client` in as `user` and send the CSRF token with every request."""
    with Session(engine) as session:
        token = start_session(session, user)
        logged_in = resolve_session(session, token)
    assert logged_in is not None
    test_client.cookies.set(SESSION_COOKIE, token)
    test_client.headers[CSRF_HEADER] = logged_in.csrf_token
    return test_client


@pytest.fixture
def anonymous_client(engine: Engine, raw_dir: Path) -> Iterator[TestClient]:
    """Client without login (the app itself, with the test database)."""

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_storage_dir] = lambda: raw_dir
    yield TestClient(app, base_url=BASE_URL)
    app.dependency_overrides.clear()


@pytest.fixture
def client(anonymous_client: TestClient, engine: Engine) -> TestClient:
    """Client logged in as the fixed test user (role admin)."""
    return login_client(anonymous_client, engine, add_test_user(engine, TEST_USER, Role.ADMIN))


@pytest.fixture
def editor_client(anonymous_client: TestClient, engine: Engine) -> TestClient:
    """Client logged in as an editor (employee of the customer)."""
    return login_client(anonymous_client, engine, add_test_user(engine, TEST_EDITOR, Role.EDITOR))
