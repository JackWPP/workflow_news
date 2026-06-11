"""add domain_scrape_stats

Revision ID: b4e8f1a2c3d6
Revises: a3f7b2c1d4e5
Create Date: 2026-06-11 20:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b4e8f1a2c3d6'
down_revision: Union[str, Sequence[str], None] = 'a3f7b2c1d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('domain_scrape_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(255), nullable=False),
        sa.Column('layer', sa.String(32), nullable=False),
        sa.Column('success_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failure_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_domain_scrape_stats_domain', 'domain_scrape_stats', ['domain'])

def downgrade() -> None:
    op.drop_index('ix_domain_scrape_stats_domain', table_name='domain_scrape_stats')
    op.drop_table('domain_scrape_stats')
