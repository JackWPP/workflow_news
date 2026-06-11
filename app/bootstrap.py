from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import Base, _is_sqlite, engine, session_scope
from app.seed import seed_defaults

logger = logging.getLogger(__name__)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
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
