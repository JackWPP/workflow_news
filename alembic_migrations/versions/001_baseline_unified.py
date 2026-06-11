"""unified baseline - consolidate bootstrap.py ALTER TABLE into Alembic

Revision ID: 001baseline
Revises:
Create Date: 2026-06-12

This migration replaces all manual ALTER TABLE statements from
bootstrap.py._ensure_sqlite_schema(). It is idempotent: columns that
already exist (added by bootstrap.py in prior runs) are skipped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

revision: str = '001baseline'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_existing_columns(table_name: str, conn) -> set[str]:
    try:
        result = conn.execute(text(f"PRAGMA table_info('{table_name}')"))
        return {row[1] for row in result}
    except Exception:
        return set()


def _get_existing_tables(conn) -> set[str]:
    result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    return {row[0] for row in result}


def _add_column_if_missing(table: str, column: str, col_type: str, conn) -> None:
    existing = _get_existing_columns(table, conn)
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def upgrade() -> None:
    conn = op.get_bind()
    tables = _get_existing_tables(conn)

    if 'sources' in tables:
        _add_column_if_missing('sources', 'tags', "JSON NOT NULL DEFAULT '[]'", conn)
        _add_column_if_missing('sources', 'crawl_mode', "VARCHAR(32) NOT NULL DEFAULT 'rss'", conn)
        _add_column_if_missing('sources', 'use_direct_source', "BOOLEAN NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('sources', 'allow_images', "BOOLEAN NOT NULL DEFAULT 1", conn)
        _add_column_if_missing('sources', 'must_include_any', "JSON NOT NULL DEFAULT '[]'", conn)
        _add_column_if_missing('sources', 'must_exclude_any', "JSON NOT NULL DEFAULT '[]'", conn)
        _add_column_if_missing('sources', 'soft_signals', "JSON NOT NULL DEFAULT '[]'", conn)
        _add_column_if_missing('sources', 'source_tier', "VARCHAR(64) NOT NULL DEFAULT 'unknown'", conn)

    if 'report_items' in tables:
        _add_column_if_missing('report_items', 'window_bucket', "VARCHAR(32) NOT NULL DEFAULT 'primary_24h'", conn)
        _add_column_if_missing('report_items', 'image_source_url', "VARCHAR(1000)", conn)
        _add_column_if_missing('report_items', 'image_origin_type', "VARCHAR(64)", conn)
        _add_column_if_missing('report_items', 'image_caption', "TEXT", conn)
        _add_column_if_missing('report_items', 'image_relevance_score', "FLOAT NOT NULL DEFAULT 0.0", conn)
        _add_column_if_missing('report_items', 'has_verified_image', "BOOLEAN NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('report_items', 'visual_verdict', "VARCHAR(32)", conn)
        _add_column_if_missing('report_items', 'context_verdict', "VARCHAR(32)", conn)
        _add_column_if_missing('report_items', 'selected_for_publish', "BOOLEAN NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('report_items', 'image_reason', "TEXT", conn)
        _add_column_if_missing('report_items', 'decision_trace', "JSON NOT NULL DEFAULT '{}'", conn)
        _add_column_if_missing('report_items', 'language', "VARCHAR(8) NOT NULL DEFAULT 'zh'", conn)

    if 'reports' in tables:
        _add_column_if_missing('reports', 'report_type', "VARCHAR(16) NOT NULL DEFAULT 'global'", conn)

    if 'agent_runs' in tables:
        _add_column_if_missing('agent_runs', 'agent_type', "VARCHAR(32) NOT NULL DEFAULT 'daily_report'", conn)
        _add_column_if_missing('agent_runs', 'finished_reason', "VARCHAR(32)", conn)
        _add_column_if_missing('agent_runs', 'total_steps', "INTEGER NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('agent_runs', 'total_tokens', "INTEGER NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('agent_runs', 'memory_snapshot', "JSON", conn)

    if 'agent_steps' in tables:
        _add_column_if_missing('agent_steps', 'round_index', "INTEGER NOT NULL DEFAULT 1", conn)
        _add_column_if_missing('agent_steps', 'decision_type', "VARCHAR(64)", conn)
        _add_column_if_missing('agent_steps', 'decision_summary', "TEXT", conn)
        _add_column_if_missing('agent_steps', 'input_ref_ids', "JSON NOT NULL DEFAULT '[]'", conn)
        _add_column_if_missing('agent_steps', 'output_ref_ids', "JSON NOT NULL DEFAULT '[]'", conn)
        _add_column_if_missing('agent_steps', 'thought', "TEXT", conn)
        _add_column_if_missing('agent_steps', 'tool_name', "VARCHAR(64)", conn)
        _add_column_if_missing('agent_steps', 'harness_blocked', "BOOLEAN NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('agent_steps', 'tokens_used', "INTEGER NOT NULL DEFAULT 0", conn)

    if 'article_images' in tables:
        _add_column_if_missing('article_images', 'visual_verdict', "VARCHAR(32)", conn)
        _add_column_if_missing('article_images', 'context_verdict', "VARCHAR(32)", conn)
        _add_column_if_missing('article_images', 'review_model', "VARCHAR(255)", conn)
        _add_column_if_missing('article_images', 'selected_for_publish', "BOOLEAN NOT NULL DEFAULT 0", conn)
        _add_column_if_missing('article_images', 'image_reason', "TEXT", conn)

    if 'article_pool' in tables:
        _add_column_if_missing('article_pool', 'fetch_status', "VARCHAR(20) DEFAULT 'pending'", conn)
        _add_column_if_missing('article_pool', 'fetch_attempts', "INTEGER DEFAULT 0", conn)
        _add_column_if_missing('article_pool', 'last_fetch_at', "DATETIME", conn)
        _add_column_if_missing('article_pool', 'image_url', "VARCHAR(2048)", conn)
        _add_column_if_missing('article_pool', 'consumed_report_ids', "JSON DEFAULT '[]'", conn)


def downgrade() -> None:
    pass
