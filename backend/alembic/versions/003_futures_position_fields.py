"""Add futures-specific fields to trades table

Revision ID: 003_futures_position_fields
Revises: 002_spot_engine_fields
Create Date: 2026-03-21

Adds:
  - leverage           NUMERIC(8,2)   leverage at entry (calculated, not chosen)
  - liq_price          NUMERIC(20,8)  liquidation price from Gate.io
  - tp2_price          NUMERIC(20,8)  second take-profit level
  - tp3_price          NUMERIC(20,8)  third take-profit / trailing level
  - tp1_hit            BOOLEAN        TP1 was triggered → SL moved to BE
  - tp2_hit            BOOLEAN        TP2 was triggered → trailing activated
  - sl_order_id        VARCHAR(100)   Gate price_order ID for the SL trigger
  - tp1_order_id       VARCHAR(100)   Gate price_order ID for TP1
  - tp2_order_id       VARCHAR(100)   Gate price_order ID for TP2
  - hwm_price          NUMERIC(20,8)  high/low water mark for ATR trailing
  - funding_cost_usdt  NUMERIC(20,8)  accumulated funding payments
  - risk_dollars       NUMERIC(20,2)  planned risk in USD at entry
"""

from alembic import op
import sqlalchemy as sa

revision = "003_futures_position_fields"
down_revision = "002_spot_engine_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("leverage",          sa.Numeric(8, 2),   nullable=True))
    op.add_column("trades", sa.Column("liq_price",         sa.Numeric(20, 8),  nullable=True))
    op.add_column("trades", sa.Column("tp2_price",         sa.Numeric(20, 8),  nullable=True))
    op.add_column("trades", sa.Column("tp3_price",         sa.Numeric(20, 8),  nullable=True))
    op.add_column("trades", sa.Column("tp1_hit",           sa.Boolean(),        nullable=True, server_default="false"))
    op.add_column("trades", sa.Column("tp2_hit",           sa.Boolean(),        nullable=True, server_default="false"))
    op.add_column("trades", sa.Column("sl_order_id",       sa.String(100),      nullable=True))
    op.add_column("trades", sa.Column("tp1_order_id",      sa.String(100),      nullable=True))
    op.add_column("trades", sa.Column("tp2_order_id",      sa.String(100),      nullable=True))
    op.add_column("trades", sa.Column("hwm_price",         sa.Numeric(20, 8),  nullable=True))
    op.add_column("trades", sa.Column("funding_cost_usdt", sa.Numeric(20, 8),  nullable=True, server_default="0"))
    op.add_column("trades", sa.Column("risk_dollars",      sa.Numeric(20, 2),  nullable=True))


def downgrade() -> None:
    for col in [
        "risk_dollars", "funding_cost_usdt", "hwm_price",
        "tp2_order_id", "tp1_order_id", "sl_order_id",
        "tp2_hit", "tp1_hit", "tp3_price", "tp2_price", "liq_price", "leverage",
    ]:
        op.drop_column("trades", col)
