"""Add overrides column to pools table

Revision ID: 001_add_overrides
Revises: 
Create Date: 2026-03-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '001_add_overrides'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add overrides column to pools table if it doesn't exist
    op.add_column('pools', sa.Column('overrides', JSONB, nullable=True, server_default='{}'))


def downgrade() -> None:
    op.drop_column('pools', 'overrides')
