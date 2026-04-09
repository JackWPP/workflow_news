from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import Base, engine, session_scope
from app.seed import seed_defaults

logger = logging.getLogger(__name__)


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
            report_item_statements = {
                "window_bucket": "ALTER TABLE report_items ADD COLUMN window_bucket VARCHAR(32) NOT NULL DEFAULT 'primary_24h'",
                "image_source_url": "ALTER TABLE report_items ADD COLUMN image_source_url VARCHAR(1000)",
                "image_origin_type": "ALTER TABLE report_items ADD COLUMN image_origin_type VARCHAR(64)",
                "image_caption": "ALTER TABLE report_items ADD COLUMN image_caption TEXT",
                "image_relevance_score": "ALTER TABLE report_items ADD COLUMN image_relevance_score FLOAT NOT NULL DEFAULT 0.0",
                "has_verified_image": "ALTER TABLE report_items ADD COLUMN has_verified_image BOOLEAN NOT NULL DEFAULT 0",
                "visual_verdict": "ALTER TABLE report_items ADD COLUMN visual_verdict VARCHAR(32)",
                "context_verdict": "ALTER TABLE report_items ADD COLUMN context_verdict VARCHAR(32)",
                "selected_for_publish": "ALTER TABLE report_items ADD COLUMN selected_for_publish BOOLEAN NOT NULL DEFAULT 0",
                "image_reason": "ALTER TABLE report_items ADD COLUMN image_reason TEXT",
            }
            for name, statement in report_item_statements.items():
                if name not in report_item_columns:
                    connection.execute(text(statement))

        if "agent_runs" in tables:
            agent_run_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info('agent_runs')"))
            }
            agent_run_statements = {
                "agent_type": "ALTER TABLE agent_runs ADD COLUMN agent_type VARCHAR(32) NOT NULL DEFAULT 'daily_report'",
                "finished_reason": "ALTER TABLE agent_runs ADD COLUMN finished_reason VARCHAR(32)",
                "total_steps": "ALTER TABLE agent_runs ADD COLUMN total_steps INTEGER NOT NULL DEFAULT 0",
                "total_tokens": "ALTER TABLE agent_runs ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0",
                "memory_snapshot": "ALTER TABLE agent_runs ADD COLUMN memory_snapshot JSON",
            }
            for name, statement in agent_run_statements.items():
                if name not in agent_run_columns:
                    connection.execute(text(statement))

        if "agent_steps" in tables:
            agent_step_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info('agent_steps')"))
            }
            agent_step_statements_v2 = {
                "round_index": "ALTER TABLE agent_steps ADD COLUMN round_index INTEGER NOT NULL DEFAULT 1",
                "decision_type": "ALTER TABLE agent_steps ADD COLUMN decision_type VARCHAR(64)",
                "decision_summary": "ALTER TABLE agent_steps ADD COLUMN decision_summary TEXT",
                "input_ref_ids": "ALTER TABLE agent_steps ADD COLUMN input_ref_ids JSON NOT NULL DEFAULT '[]'",
                "output_ref_ids": "ALTER TABLE agent_steps ADD COLUMN output_ref_ids JSON NOT NULL DEFAULT '[]'",
                "thought": "ALTER TABLE agent_steps ADD COLUMN thought TEXT",
                "tool_name": "ALTER TABLE agent_steps ADD COLUMN tool_name VARCHAR(64)",
                "harness_blocked": "ALTER TABLE agent_steps ADD COLUMN harness_blocked BOOLEAN NOT NULL DEFAULT 0",
            }
            for name, statement in agent_step_statements_v2.items():
                if name not in agent_step_columns:
                    connection.execute(text(statement))

        if "article_images" in tables:
            article_image_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info('article_images')"))
            }
            article_image_statements = {
                "visual_verdict": "ALTER TABLE article_images ADD COLUMN visual_verdict VARCHAR(32)",
                "context_verdict": "ALTER TABLE article_images ADD COLUMN context_verdict VARCHAR(32)",
                "review_model": "ALTER TABLE article_images ADD COLUMN review_model VARCHAR(255)",
                "selected_for_publish": "ALTER TABLE article_images ADD COLUMN selected_for_publish BOOLEAN NOT NULL DEFAULT 0",
                "image_reason": "ALTER TABLE article_images ADD COLUMN image_reason TEXT",
            }
            for name, statement in article_image_statements.items():
                if name not in article_image_columns:
                    connection.execute(text(statement))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema()
    with session_scope() as session:
        seed_defaults(session)
    _check_db_writable()


def _check_db_writable() -> None:
    """Verify the database is writable at startup.

    If another process holds the write lock (e.g. a stale uvicorn process),
    log an error so the operator knows to kill it.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS _writable_check (id INTEGER)"))
            conn.execute(text("INSERT INTO _writable_check (id) VALUES (1)"))
            conn.execute(text("DROP TABLE _writable_check"))
            conn.commit()
    except OperationalError as exc:
        if "locked" in str(exc):
            logger.error(
                "DATABASE IS LOCKED by another process! "
                "Check for stale uvicorn processes: `ps aux | grep uvicorn` and kill them.",
            )
        else:
            raise
