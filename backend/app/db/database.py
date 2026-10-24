"""SQLAlchemy 2.0 engine, session factory and declarative base.

The engine is built from DATABASE_URL, so switching from SQLite to MySQL only
requires changing that variable (e.g. mysql+pymysql://user:pass@host:3306/db).
"""

from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def configure_sqlite_connection(dbapi_connection, _connection_record=None) -> None:
    """Enable foreign keys and replace SQLite's ASCII-only lower() with Unicode-aware folding.

    SQLAlchemy renders ILIKE as lower(x) LIKE lower(y) on SQLite, so this makes searches such as
    'PAPELERÍA' match 'Papelería', as they already do under MySQL's utf8mb4 collations.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.create_function(
        "lower", 1, lambda value: value.lower() if isinstance(value, str) else value, deterministic=True
    )


def build_engine(database_url: str, echo: bool = False) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, echo=echo, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", configure_sqlite_connection)
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


@lru_cache
def get_engine() -> Engine:
    """Process-wide engine built lazily from DATABASE_URL (importing the app never opens the database)."""
    settings = get_settings()
    return build_engine(settings.database_url, echo=settings.database_echo)


def make_session_factory(bind: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return make_session_factory(get_engine())


def get_db(request: Request) -> Generator[Session, None, None]:
    """Session bound to the engine of the running application (`create_app(engine=...)`) or the default one."""
    factory = getattr(request.app.state, "session_factory", None) or get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def init_db(bind: Engine | None = None) -> None:
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    target = bind or get_engine()
    Base.metadata.create_all(bind=target)
    add_missing_columns(target)


def add_missing_columns(bind: Engine) -> list[str]:
    """Add columns introduced after a table was created (nullable or with a server default).

    create_all never alters existing tables, so databases created by an older version
    would otherwise fail on the new columns. Identifiers, types and defaults are rendered by the
    dialect's own DDL compiler (quoting, string literals), and indexes declared on the new columns
    are created too. Returns the "table.column" names added.
    """
    added: list[str] = []
    with bind.begin() as connection:
        # Reflect through the same connection that alters, so both see one consistent schema.
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        preparer = bind.dialect.identifier_preparer
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            new_columns = []
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.server_default is None:
                    raise RuntimeError(
                        f"Cannot add NOT NULL column {table.name}.{column.name} without a server default"
                    )
                column_ddl = CreateColumn(column).compile(dialect=bind.dialect)
                connection.exec_driver_sql(f"ALTER TABLE {preparer.format_table(table)} ADD COLUMN {column_ddl}")
                new_columns.append(column)
                added.append(f"{table.name}.{column.name}")
            if new_columns:
                existing_indexes = {index["name"] for index in inspector.get_indexes(table.name)}
                for index in table.indexes:
                    if index.name not in existing_indexes and any(column.name in index.columns.keys() for column in new_columns):
                        index.create(connection)
    return added
