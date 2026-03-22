"""Add discovery fields to pool_coins table

Revision ID: 004_pool_coins_discovery_fields
Revises: 003_futures_position_fields
Create Date: 2026-03-22

Adds:
  - origin         VARCHAR(20)  DEFAULT 'manual'  — 'manual' or 'discovered'
  - discovered_at  TIMESTAMPTZ  nullable           — when auto-discovery added this coin
"""

from alembic import op
import sqlalchemy as sa

revision = "004_pool_coins_discovery_fields"
down_revision = "003_futures_position_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pool_coins",
        sa.Column("origin", sa.String(20), nullable=True, server_default="manual"),
    )
    op.add_column(
        "pool_coins",
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pool_coins", "discovered_at")
    op.drop_column("pool_coins", "origin")
