"""pool_coins.radar_last_seen_at -- debounce radar-absence flapping.

Revision ID: 227_radar_last_seen_at
Revises: 226_held_for_open_position
"""

from alembic import op
import sqlalchemy as sa


revision = "227_radar_last_seen_at"
down_revision = "226_held_for_open_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pool_coins",
        sa.Column("radar_last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pool_coins", "radar_last_seen_at")
