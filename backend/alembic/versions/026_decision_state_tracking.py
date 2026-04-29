"""Add state tracking for decision opportunities to prevent duplicates

Revision ID: 026_decision_state_tracking
Revises: 025_trade_simulations
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "026_decision_state_tracking"
down_revision = "025_trade_simulations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create active_candidates table for state tracking
    op.create_table(
        "active_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("strategy", sa.String(50), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state", sa.String(10), nullable=False, server_default="IDLE"),
        sa.Column("state_hash", sa.String(64), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decision_id", sa.BigInteger(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Create composite unique index for (user_id, symbol, strategy)
    op.create_index(
        "idx_active_candidates_user_symbol_strategy",
        "active_candidates",
        ["user_id", "symbol", "strategy"],
        unique=True,
    )

    # Create index for state queries
    op.create_index("idx_active_candidates_state", "active_candidates", ["state"])

    # Create index for cleanup queries
    op.create_index("idx_active_candidates_last_seen", "active_candidates", ["last_seen_at"])

    # Add new columns to decisions_log for state tracking
    op.add_column(
        "decisions_log",
        sa.Column("decision_group_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "decisions_log",
        sa.Column("state_hash", sa.String(64), nullable=True),
    )

    # Create index for decision grouping queries
    op.create_index("idx_decisions_group_id", "decisions_log", ["decision_group_id"])
    op.create_index("idx_decisions_state_hash", "decisions_log", ["state_hash"])


def downgrade() -> None:
    op.drop_index("idx_decisions_state_hash", table_name="decisions_log")
    op.drop_index("idx_decisions_group_id", table_name="decisions_log")
    op.drop_column("decisions_log", "state_hash")
    op.drop_column("decisions_log", "decision_group_id")

    op.drop_index("idx_active_candidates_last_seen", table_name="active_candidates")
    op.drop_index("idx_active_candidates_state", table_name="active_candidates")
    op.drop_index("idx_active_candidates_user_symbol_strategy", table_name="active_candidates")
    op.drop_table("active_candidates")
