"""Pump Monitor capital-flow regime history (Pump Score v1.4).

Additive only: one new table with the per-minute USDT taker flow of the
monitored universe and the classified tide state. Nothing existing changes.

Revision ID: 236_pump_capital_flow
Revises: 235_pump_label_resource_block
"""
from alembic import op
import sqlalchemy as sa

revision = "236_pump_capital_flow"
down_revision = "235_pump_label_resource_block"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_capital_flow_1m (
            user_id uuid NOT NULL,
            minute timestamptz NOT NULL,
            buy_usdt numeric,
            sell_usdt numeric,
            net_usdt numeric,
            symbols integer,
            complete_symbols integer,
            window_minutes integer,
            window_net_usdt numeric,
            window_ratio double precision,
            z double precision,
            state varchar(24),
            method varchar(24),
            config_version integer,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, minute)
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pump_capital_flow_1m_minute ON pump_capital_flow_1m (minute)"))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_capital_flow_1m"))
