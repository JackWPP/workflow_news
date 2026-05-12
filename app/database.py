from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()


def _connect_args() -> dict:
    if settings.database_url.startswith("sqlite"):
        return {"check_same_thread": False, "timeout": settings.sqlite_busy_timeout_seconds}
    return {}


_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=not _is_sqlite,
    **({"poolclass": NullPool} if _is_sqlite else {}),
    connect_args=_connect_args(),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_seconds * 1000}")
        cursor.close()


@contextmanager
def session_scope():
    """Provide a transactional scope with automatic retry on database lock.

    Retries up to 3 times with exponential backoff (0.5s, 1s, 2s) when
    SQLite reports "database is locked" — typically caused by concurrent
    writers or stale processes holding the write lock.
    """
    max_retries = 3
    for attempt in range(max_retries + 1):
        session = SessionLocal()
        try:
            yield session
            session.commit()
            return
        except OperationalError as exc:
            session.rollback()
            if "database is locked" in str(exc) and attempt < max_retries:
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    "database is locked, retry %d/%d in %.1fs",
                    attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
