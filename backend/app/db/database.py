"""SQLAlchemy 2.0 engine, session factory and declarative base.

The engine is built from DATABASE_URL, so switching from SQLite to MySQL only
requires changing that variable (e.g. mysql+pymysql://user:pass@host:3306/db).
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

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

    target = bind or engine
    Base.metadata.create_all(bind=target)
    add_missing_columns(target)


def add_missing_columns(bind: Engine) -> list[str]:
    """Add columns introduced after a table was created (nullable or with a server default).

    create_all never alters existing tables, so databases created by an older version
    would otherwise fail on the new columns. Returns the "table.column" names added.
    """
    added: list[str] = []
    with bind.begin() as connection:
        # Reflect through the same connection that alters, so both see one consistent schema.
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.server_default is None:
                    raise RuntimeError(
                        f"Cannot add NOT NULL column {table.name}.{column.name} without a server default"
                    )
                column_type = column.type.compile(dialect=bind.dialect)
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}"
                if column.server_default is not None:
                    ddl += f" DEFAULT {column.server_default.arg}"
                if not column.nullable:
                    ddl += " NOT NULL"
                connection.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
    return added
