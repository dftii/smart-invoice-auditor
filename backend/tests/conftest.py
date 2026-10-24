import os
import tempfile
from pathlib import Path

# Isolate the test run from the developer's real data BEFORE any app module reads the settings:
# the default DATABASE_URL / UPLOAD_DIR point at backend/data, which tests must never touch.
_BACKEND = Path(__file__).resolve().parents[1]
REAL_DB_PATH = _BACKEND / "data" / "smartinvoice.db"
_REAL_DB_BEFORE = (REAL_DB_PATH.exists(), REAL_DB_PATH.stat().st_mtime_ns if REAL_DB_PATH.exists() else None)
_SCRATCH = Path(tempfile.mkdtemp(prefix="smartinvoice-tests-"))

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_SCRATCH / "uploads")
for _name in ("ANTHROPIC_API_KEY", "API_KEY", "FACTURAE_CERT_PATH", "FACTURAE_CERT_PASSWORD"):
    os.environ[_name] = ""
os.environ["AI_FALLBACK_TO_DEMO"] = "false"

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, configure_sqlite_connection


@pytest.fixture(scope="session")
def real_db_snapshot() -> tuple[bool, int | None]:
    """(exists, mtime_ns) of backend/data/smartinvoice.db as it was before the test session started."""
    return _REAL_DB_BEFORE


@pytest.fixture()
def engine():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    event.listen(test_engine, "connect", configure_sqlite_connection)

    from app import models  # noqa: F401

    Base.metadata.create_all(test_engine)
    yield test_engine
    Base.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture()
def db_session(engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    application = create_app(engine=engine)
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
