"""Shared pytest fixtures: isolated in-memory database per test and a FastAPI test client."""

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ["ANTHROPIC_API_KEY"] = ""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base


@pytest.fixture()
def engine():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def _fk_on(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
    from app.db.database import get_db
    from app.main import app

    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
