"""Pump Monitor long-lived 5-minute flow and perpetual history (v1.16).

Additive only: two tables kept for months (``flow_history.retention_days``) so
order-flow and derivatives features can be learned later. ``flow_buckets_1m``
(2 days) and the Redis perp cache stay as they are.

Revision ID: 239_pump_flow_history
Revises: 238_pump_ml_live_predictions
"""
from alembic import op
import sqlalchemy as sa

revision = "239_pump_flow_history"
down_revision = "238_pump_ml_live_predictions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_flow_5m (
            symbol varchar(40) NOT NULL,
            bucket_start timestamptz NOT NULL,
            buy_quote numeric,
            sell_quote numeric,
            trade_count integer,
            minutes integer NOT NULL,
            partial_minutes integer NOT NULL,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, bucket_start)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pump_flow_5m_bucket ON pump_flow_5m (bucket_start)"))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_perp_stats_5m (
            symbol varchar(40) NOT NULL,
            stat_time timestamptz NOT NULL,
            stat_interval varchar(8) NOT NULL,
            long_taker_size double precision,
            short_taker_size double precision,
            open_interest_usd double precision,
            short_liq_usd double precision,
            long_liq_usd double precision,
            last_funding_rate double precision,
            lsr_taker double precision,
            mark_price double precision,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, stat_time, stat_interval)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pump_perp_stats_5m_time ON pump_perp_stats_5m (stat_time)"))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_perp_stats_5m"))
    op.execute(sa.text("DROP TABLE IF EXISTS pump_flow_5m"))
