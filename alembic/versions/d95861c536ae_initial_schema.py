"""initial_schema

Revision ID: d95861c536ae
Revises: 
Create Date: 2026-05-09 13:55:28.419942

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd95861c536ae'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('include_rules', sa.JSON(), nullable=False),
        sa.Column('exclude_rules', sa.JSON(), nullable=False),
        sa.Column('must_include_any', sa.JSON(), nullable=False),
        sa.Column('must_exclude_any', sa.JSON(), nullable=False),
        sa.Column('soft_signals', sa.JSON(), nullable=False),
        sa.Column('source_tier', sa.String(length=64), nullable=False),
        sa.Column('rss_or_listing_url', sa.String(length=500), nullable=True),
        sa.Column('crawl_mode', sa.String(length=32), nullable=False),
        sa.Column('use_direct_source', sa.Boolean(), nullable=False),
        sa.Column('allow_images', sa.Boolean(), nullable=False),
        sa.Column('language', sa.String(length=32), nullable=True),
        sa.Column('country', sa.String(length=8), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('domain')
    )

    op.create_table('retrieval_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_date', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('shadow_mode', sa.Boolean(), nullable=False),
        sa.Column('query_count', sa.Integer(), nullable=False),
        sa.Column('candidate_count', sa.Integer(), nullable=False),
        sa.Column('extracted_count', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('debug_payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('app_settings',
        sa.Column('key', sa.String(length=128), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key')
    )

    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    op.create_table('article_pool',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=1024), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('source_type', sa.String(length=32), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('raw_content', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('ingested_at', sa.DateTime(), nullable=False),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('section', sa.String(length=32), nullable=True),
        sa.Column('category', sa.String(length=32), nullable=True),
        sa.Column('eval_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url')
    )
    op.create_index(op.f('ix_article_pool_content_hash'), 'article_pool', ['content_hash'], unique=False)

    op.create_table('patents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('patent_number', sa.String(length=64), nullable=False),
        sa.Column('publication_number', sa.String(length=64), nullable=True),
        sa.Column('grant_date', sa.Date(), nullable=True),
        sa.Column('application_date', sa.Date(), nullable=True),
        sa.Column('inventors', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patent_number')
    )
    op.create_index(op.f('ix_patents_category'), 'patents', ['category'], unique=False)

    op.create_table('retrieval_queries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('section', sa.String(length=64), nullable=False),
        sa.Column('language', sa.String(length=16), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=False),
        sa.Column('response_status', sa.String(length=32), nullable=False),
        sa.Column('result_count', sa.Integer(), nullable=False),
        sa.Column('filters', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['retrieval_runs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('retrieval_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('query_id', sa.Integer(), nullable=True),
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('section', sa.String(length=32), nullable=False),
        sa.Column('language', sa.String(length=16), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('source_name', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(length=1000), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['query_id'], ['retrieval_queries.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['retrieval_runs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('canonical_url', sa.String(length=1000), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('section', sa.String(length=32), nullable=False),
        sa.Column('language', sa.String(length=16), nullable=False),
        sa.Column('country', sa.String(length=8), nullable=True),
        sa.Column('source_name', sa.String(length=255), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('discovered_at', sa.DateTime(), nullable=False),
        sa.Column('image_url', sa.String(length=1000), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('raw_markdown', sa.Text(), nullable=True),
        sa.Column('raw_html', sa.Text(), nullable=True),
        sa.Column('extraction_status', sa.String(length=32), nullable=False),
        sa.Column('cluster_key', sa.String(length=255), nullable=True),
        sa.Column('freshness_score', sa.Float(), nullable=False),
        sa.Column('relevance_score', sa.Float(), nullable=False),
        sa.Column('source_trust_score', sa.Float(), nullable=False),
        sa.Column('research_value_score', sa.Float(), nullable=False),
        sa.Column('novelty_score', sa.Float(), nullable=False),
        sa.Column('combined_score', sa.Float(), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['retrieval_runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'url', name='uq_articles_run_url')
    )

    op.create_table('article_clusters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('cluster_key', sa.String(length=255), nullable=False),
        sa.Column('canonical_article_id', sa.Integer(), nullable=True),
        sa.Column('article_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['canonical_article_id'], ['articles.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['retrieval_runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'cluster_key', name='uq_clusters_run_key')
    )

    op.create_table('reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('markdown_content', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('pipeline_version', sa.String(length=64), nullable=False),
        sa.Column('retrieval_run_id', sa.Integer(), nullable=True),
        sa.Column('debug_url', sa.String(length=1000), nullable=True),
        sa.Column('report_type', sa.String(length=16), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['retrieval_run_id'], ['retrieval_runs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reports_report_date'), 'reports', ['report_date'], unique=False)

    op.create_table('agent_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('retrieval_run_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('stage_count', sa.Integer(), nullable=False),
        sa.Column('agent_type', sa.String(length=32), nullable=False),
        sa.Column('finished_reason', sa.String(length=32), nullable=True),
        sa.Column('total_steps', sa.Integer(), nullable=False),
        sa.Column('total_tokens', sa.Integer(), nullable=False),
        sa.Column('memory_snapshot', sa.JSON(), nullable=True),
        sa.Column('debug_payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['retrieval_run_id'], ['retrieval_runs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('auth_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token')
    )

    op.create_table('conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('retrieval_mode', sa.String(length=32), nullable=False),
        sa.Column('last_message_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('report_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=True),
        sa.Column('section', sa.String(length=32), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('source_name', sa.String(length=255), nullable=False),
        sa.Column('source_url', sa.String(length=1000), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('research_signal', sa.Text(), nullable=False),
        sa.Column('image_url', sa.String(length=1000), nullable=True),
        sa.Column('image_source_url', sa.String(length=1000), nullable=True),
        sa.Column('image_origin_type', sa.String(length=64), nullable=True),
        sa.Column('image_caption', sa.Text(), nullable=True),
        sa.Column('image_relevance_score', sa.Float(), nullable=False),
        sa.Column('has_verified_image', sa.Boolean(), nullable=False),
        sa.Column('visual_verdict', sa.String(length=32), nullable=True),
        sa.Column('context_verdict', sa.String(length=32), nullable=True),
        sa.Column('selected_for_publish', sa.Boolean(), nullable=False),
        sa.Column('image_reason', sa.Text(), nullable=True),
        sa.Column('window_bucket', sa.String(length=32), nullable=False),
        sa.Column('citations', sa.JSON(), nullable=False),
        sa.Column('combined_score', sa.Float(), nullable=False),
        sa.Column('decision_trace', sa.JSON(), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('agent_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agent_run_id', sa.Integer(), nullable=False),
        sa.Column('stage_name', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('model_name', sa.String(length=255), nullable=True),
        sa.Column('round_index', sa.Integer(), nullable=False),
        sa.Column('decision_type', sa.String(length=64), nullable=True),
        sa.Column('decision_summary', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('fallback_triggered', sa.Boolean(), nullable=False),
        sa.Column('input_ref_ids', sa.JSON(), nullable=False),
        sa.Column('output_ref_ids', sa.JSON(), nullable=False),
        sa.Column('input_payload', sa.JSON(), nullable=True),
        sa.Column('output_payload', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('thought', sa.Text(), nullable=True),
        sa.Column('tool_name', sa.String(length=64), nullable=True),
        sa.Column('harness_blocked', sa.Boolean(), nullable=False),
        sa.Column('tokens_used', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('article_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('image_url', sa.String(length=1000), nullable=False),
        sa.Column('image_source_url', sa.String(length=1000), nullable=True),
        sa.Column('image_origin_type', sa.String(length=64), nullable=False),
        sa.Column('image_relevance_score', sa.Float(), nullable=False),
        sa.Column('image_caption', sa.Text(), nullable=True),
        sa.Column('image_license_note', sa.Text(), nullable=True),
        sa.Column('visual_verdict', sa.String(length=32), nullable=True),
        sa.Column('context_verdict', sa.String(length=32), nullable=True),
        sa.Column('review_model', sa.String(length=255), nullable=True),
        sa.Column('selected_for_publish', sa.Boolean(), nullable=False),
        sa.Column('image_reason', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('selected', sa.Boolean(), nullable=False),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('citations', sa.JSON(), nullable=False),
        sa.Column('retrieval_mode', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('favorite_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'report_id', name='uq_favorite_report')
    )

    op.create_table('favorite_conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'conversation_id', name='uq_favorite_conversation')
    )

    op.create_table('quality_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('target_domain', sa.String(length=255), nullable=True),
        sa.Column('target_title', sa.Text(), nullable=True),
        sa.Column('label', sa.String(length=32), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('evaluation_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('judge_model', sa.String(length=64), nullable=False),
        sa.Column('evaluated_at', sa.DateTime(), nullable=False),
        sa.Column('faithfulness_score', sa.Float(), nullable=True),
        sa.Column('coverage_score', sa.Float(), nullable=True),
        sa.Column('dedup_score', sa.Float(), nullable=True),
        sa.Column('fluency_score', sa.Float(), nullable=True),
        sa.Column('research_value_score', sa.Float(), nullable=True),
        sa.Column('weighted_total', sa.Float(), nullable=True),
        sa.Column('total_claims', sa.Integer(), nullable=False),
        sa.Column('supported_claims', sa.Integer(), nullable=False),
        sa.Column('faithfulness_ratio', sa.Float(), nullable=True),
        sa.Column('precision_at_k', sa.Float(), nullable=True),
        sa.Column('recall_at_k', sa.Float(), nullable=True),
        sa.Column('judge_raw_output', sa.JSON(), nullable=True),
        sa.Column('top_issues', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('wechat_articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('title', sa.String(length=1024), nullable=False),
        sa.Column('account_name', sa.String(length=255), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('scraped_at', sa.DateTime(), nullable=True),
        sa.Column('scrape_status', sa.String(length=32), nullable=False),
        sa.Column('raw_content', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(length=1024), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('article_pool_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['article_pool_id'], ['article_pool.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url')
    )


def downgrade() -> None:
    op.drop_table('wechat_articles')
    op.drop_table('evaluation_runs')
    op.drop_table('quality_feedback')
    op.drop_table('favorite_conversations')
    op.drop_table('favorite_reports')
    op.drop_table('messages')
    op.drop_table('article_images')
    op.drop_table('agent_steps')
    op.drop_table('report_items')
    op.drop_table('conversations')
    op.drop_table('auth_sessions')
    op.drop_table('agent_runs')
    op.drop_index(op.f('ix_reports_report_date'), table_name='reports')
    op.drop_table('reports')
    op.drop_table('article_clusters')
    op.drop_table('articles')
    op.drop_table('retrieval_candidates')
    op.drop_table('retrieval_queries')
    op.drop_index(op.f('ix_patents_category'), table_name='patents')
    op.drop_table('patents')
    op.drop_index(op.f('ix_article_pool_content_hash'), table_name='article_pool')
    op.drop_table('article_pool')
    op.drop_table('users')
    op.drop_table('app_settings')
    op.drop_table('retrieval_runs')
    op.drop_table('sources')
