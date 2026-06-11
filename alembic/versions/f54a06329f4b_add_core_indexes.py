"""add_core_indexes

Revision ID: f54a06329f4b
Revises: b4e8f1a2c3d6
Create Date: 2026-06-12 02:16:53.574247

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f54a06329f4b'
down_revision: Union[str, Sequence[str], None] = 'b4e8f1a2c3d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_articles_run_id'), 'articles', ['run_id'], unique=False)
    op.create_index(op.f('ix_retrieval_queries_run_id'), 'retrieval_queries', ['run_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_retrieval_run_id'), 'agent_runs', ['retrieval_run_id'], unique=False)
    op.create_index(op.f('ix_agent_steps_agent_run_id'), 'agent_steps', ['agent_run_id'], unique=False)
    op.create_index(op.f('ix_report_items_report_id'), 'report_items', ['report_id'], unique=False)
    op.create_index(op.f('ix_article_pool_ingested_at'), 'article_pool', ['ingested_at'], unique=False)
    op.create_index(op.f('ix_article_pool_domain'), 'article_pool', ['domain'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_article_pool_domain'), table_name='article_pool')
    op.drop_index(op.f('ix_article_pool_ingested_at'), table_name='article_pool')
    op.drop_index(op.f('ix_report_items_report_id'), table_name='report_items')
    op.drop_index(op.f('ix_agent_steps_agent_run_id'), table_name='agent_steps')
    op.drop_index(op.f('ix_agent_runs_retrieval_run_id'), table_name='agent_runs')
    op.drop_index(op.f('ix_retrieval_queries_run_id'), table_name='retrieval_queries')
    op.drop_index(op.f('ix_articles_run_id'), table_name='articles')
