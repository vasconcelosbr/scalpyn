"""Durable observation timestamps for the classic Pump Monitor.

Revision ID: 251_pump_monitor_timing
Revises: 250_mtf_target_presentation
"""
from alembic import op
import sqlalchemy as sa

revision = "251_pump_monitor_timing"
down_revision = "250_mtf_target_presentation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_monitor_timing_cycles (
            user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            pool_id uuid NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
            last_cycle_at timestamptz
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_monitor_asset_timing (
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            pool_id uuid NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
            symbol text NOT NULL,
            is_present boolean NOT NULL,
            tracking_started_at timestamptz NOT NULL,
            monitor_entered_at timestamptz,
            last_strong_long_at timestamptz,
            last_strong_long boolean,
            last_cycle_at timestamptz NOT NULL,
            PRIMARY KEY (user_id, pool_id, symbol)
        )
    """))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_monitor_asset_timing"))
    op.execute(sa.text("DROP TABLE IF EXISTS pump_monitor_timing_cycles"))
