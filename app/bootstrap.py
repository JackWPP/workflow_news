from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import Base, _is_sqlite, engine, session_scope
from app.seed import seed_defaults

logger = logging.getLogger(__name__)


def _add_column_if_missing(
    eng, table: str, column: str, col_type: str
) -> None:
    try:
        with eng.connect() as conn:
            conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
            conn.commit()
    except OperationalError:
        logger.info("Adding column %s.%s (%s)", table, column, col_type)
        with eng.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            conn.commit()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _add_column_if_missing(engine, "article_pool", "source_tier", "VARCHAR(16)")
    _add_column_if_missing(engine, "article_pool", "source_kind", "VARCHAR(64)")
    _add_column_if_missing(engine, "article_pool", "page_kind", "VARCHAR(32)")
    with session_scope() as session:
        seed_defaults(session)
    _check_db_writable()


def _check_db_writable() -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
    except OperationalError as exc:
        if _is_sqlite and "locked" in str(exc):
            logger.error(
                "DATABASE IS LOCKED by another process! "
                "Check for stale uvicorn processes: `ps aux | grep uvicorn` and kill them.",
            )
        else:
            raise
