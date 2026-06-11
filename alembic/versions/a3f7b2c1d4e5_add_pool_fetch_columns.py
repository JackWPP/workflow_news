"""add pool fetch columns

Revision ID: a3f7b2c1d4e5
Revises: d95861c536ae
Create Date: 2026-06-11 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f7b2c1d4e5'
down_revision: Union[str, Sequence[str], None] = 'd95861c536ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add fetch tracking and consumption columns to article_pool."""
    op.add_column('article_pool', sa.Column('fetch_status', sa.String(20), server_default='pending'))
    op.add_column('article_pool', sa.Column('fetch_attempts', sa.Integer(), server_default='0'))
    op.add_column('article_pool', sa.Column('last_fetch_at', sa.DateTime(), nullable=True))
    op.add_column('article_pool', sa.Column('image_url', sa.String(2048), nullable=True))
    op.add_column('article_pool', sa.Column('consumed_report_ids', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    """Remove fetch tracking columns from article_pool."""
    op.drop_column('article_pool', 'consumed_report_ids')
    op.drop_column('article_pool', 'image_url')
    op.drop_column('article_pool', 'last_fetch_at')
    op.drop_column('article_pool', 'fetch_attempts')
    op.drop_column('article_pool', 'fetch_status')
