from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.db import get_session, init_db, make_engine
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


@pytest.fixture
def client(engine: Engine, raw_dir: Path) -> Iterator[TestClient]:
    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_storage_dir] = lambda: raw_dir
    yield TestClient(app)
    app.dependency_overrides.clear()
