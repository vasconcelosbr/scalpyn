"""pool_coins.held_for_open_position -- keep collection alive for a symbol
with an open shadow trade after it drops out of the radar/discovery
selection, while still excluding it from new L1/L2/L3 candidacy.

Revision ID: 226_pool_coins_held_for_open_position
Revises: 225_pump_radar_report_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "226_pool_coins_held_for_open_position"
down_revision = "225_pump_radar_report_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pool_coins",
        sa.Column(
            "held_for_open_position",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("pool_coins", "held_for_open_position")
