"""Add spot engine fields to trades table

Revision ID: 002_spot_engine_fields
Revises: 001_add_overrides
Create Date: 2026-03-21

Adds:
  - profile              VARCHAR(20)    default 'spot'
  - original_entry_price NUMERIC(20,8)  nullable
  - dca_layers           INTEGER        default 0
  - dca_layers_data      JSONB          nullable (per-layer details)
  - engine_meta          JSONB          nullable (score_at_entry, indicators snapshot, etc.)

Status values for spot engine:
  'ACTIVE'             — position is profitable / in progress
  'HOLDING_UNDERWATER' — position is at a loss, holding until recovered
  'CLOSED'             — position exited
  (legacy 'open'/'closed' remain valid for old records)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "002_spot_engine_fields"
down_revision = "001_add_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("profile", sa.String(20), nullable=True, server_default="spot"),
    )
    op.add_column(
        "trades",
        sa.Column("original_entry_price", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("dca_layers", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "trades",
        sa.Column("dca_layers_data", JSONB(), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("engine_meta", JSONB(), nullable=True),
    )
    op.create_index(
        "ix_trades_profile_status",
        "trades",
        ["profile", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_trades_profile_status", table_name="trades")
    op.drop_column("trades", "engine_meta")
    op.drop_column("trades", "dca_layers_data")
    op.drop_column("trades", "dca_layers")
    op.drop_column("trades", "original_entry_price")
    op.drop_column("trades", "profile")
