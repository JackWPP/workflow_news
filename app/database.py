from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from app.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

_is_sqlite = settings.database_url.startswith("sqlite")


def _build_engine_args() -> dict:
    if _is_sqlite:
        return {
            "pool_pre_ping": False,
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False, "timeout": settings.sqlite_busy_timeout_seconds},
        }
    return {
        "pool_pre_ping": True,
        "poolclass": QueuePool,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 300,
        "connect_args": {},
    }


engine = create_engine(
    settings.database_url,
    future=True,
    **_build_engine_args(),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)


if _is_sqlite:
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
    max_retries = 3 if _is_sqlite else 0
    for attempt in range(max_retries + 1):
        session = SessionLocal()
        try:
            yield session
            session.commit()
            return
        except OperationalError as exc:
            session.rollback()
            if _is_sqlite and "database is locked" in str(exc) and attempt < max_retries:
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
