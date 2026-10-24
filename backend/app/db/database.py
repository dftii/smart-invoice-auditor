"""SQLAlchemy 2.0 engine, session factory and declarative base.

The engine is built from DATABASE_URL, so switching from SQLite to MySQL only
requires changing that variable (e.g. mysql+pymysql://user:pass@host:3306/db).
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str, echo: bool = False) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, echo=echo, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    settings = get_settings()
    return create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


_settings = get_settings()
engine = build_engine(_settings.database_url, echo=_settings.database_echo)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(bind: Engine | None = None) -> None:
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=bind or engine)
