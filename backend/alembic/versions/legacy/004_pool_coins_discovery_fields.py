"""Add discovery fields to pool_coins table

Revision ID: 004_pool_coins_discovery_fields
Revises: 003_futures_position_fields
Create Date: 2026-03-22

Adds:
  - origin         VARCHAR(20)  DEFAULT 'manual'  — 'manual' or 'discovered'
  - discovered_at  TIMESTAMPTZ  nullable           — when auto-discovery added this coin

NOTE: Uses raw SQL with IF NOT EXISTS so this migration is safe to run
against a database that was bootstrapped via create_all (which would have
already created these columns from the current model definition).
"""

from alembic import op
import sqlalchemy as sa

revision = "004_pool_coins_discovery_fields"
down_revision = "003_futures_position_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE pool_coins ADD COLUMN IF NOT EXISTS origin VARCHAR(20) DEFAULT 'manual';"
    ))
    op.execute(sa.text(
        "ALTER TABLE pool_coins ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMPTZ;"
    ))


def downgrade() -> None:
    op.drop_column("pool_coins", "discovered_at")
    op.drop_column("pool_coins", "origin")
