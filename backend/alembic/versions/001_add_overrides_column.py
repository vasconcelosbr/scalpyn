"""Add overrides column to pools table

Revision ID: 001_add_overrides
Revises:
Create Date: 2026-03-17

NOTE: Uses raw SQL with IF NOT EXISTS so this migration is safe to run
against a database that was bootstrapped via create_all (which would have
already created the column from the current model definition).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001_add_overrides'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE pools ADD COLUMN IF NOT EXISTS overrides JSONB DEFAULT '{}';"
    ))


def downgrade() -> None:
    op.drop_column('pools', 'overrides')
