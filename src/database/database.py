"""Engine and session management (SQLite by default)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings
from src.database.models import Base


def create_db_engine(url: str) -> Engine:
    """Create an engine; for SQLite enable foreign keys and safe concurrency."""
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        future=True,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    Base.metadata.create_all(engine)
    return engine


class Database:
    """Thin wrapper owning an engine and a session factory."""

    def __init__(self, url: str) -> None:
        self.engine = create_db_engine(url)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def dispose(self) -> None:
        """Close all pooled connections."""
        self.engine.dispose()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@lru_cache(maxsize=1)
def get_database() -> Database:
    """Application-wide database built from settings."""
    return Database(get_settings().database_url)
