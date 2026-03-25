from __future__ import annotations

from sqlalchemy import text

from app.database import Base, engine, session_scope
from app.seed import seed_defaults


def _ensure_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return

    source_columns = {
        "tags": "ALTER TABLE sources ADD COLUMN tags JSON NOT NULL DEFAULT '[]'",
        "crawl_mode": "ALTER TABLE sources ADD COLUMN crawl_mode VARCHAR(32) NOT NULL DEFAULT 'rss'",
        "use_direct_source": "ALTER TABLE sources ADD COLUMN use_direct_source BOOLEAN NOT NULL DEFAULT 0",
        "allow_images": "ALTER TABLE sources ADD COLUMN allow_images BOOLEAN NOT NULL DEFAULT 1",
        "must_include_any": "ALTER TABLE sources ADD COLUMN must_include_any JSON NOT NULL DEFAULT '[]'",
        "must_exclude_any": "ALTER TABLE sources ADD COLUMN must_exclude_any JSON NOT NULL DEFAULT '[]'",
        "soft_signals": "ALTER TABLE sources ADD COLUMN soft_signals JSON NOT NULL DEFAULT '[]'",
        "source_tier": "ALTER TABLE sources ADD COLUMN source_tier VARCHAR(64) NOT NULL DEFAULT 'unknown'",
    }

    with engine.begin() as connection:
        tables = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "sources" not in tables:
            return

        existing_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info('sources')"))
        }
        for name, statement in source_columns.items():
            if name not in existing_columns:
                connection.execute(text(statement))

        if "report_items" in tables:
            report_item_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info('report_items')"))
            }
            if "window_bucket" not in report_item_columns:
                connection.execute(
                    text("ALTER TABLE report_items ADD COLUMN window_bucket VARCHAR(32) NOT NULL DEFAULT 'primary_24h'")
                )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema()
    with session_scope() as session:
        seed_defaults(session)
