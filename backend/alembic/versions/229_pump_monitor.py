"""Pump Monitor: flow buckets, snapshots, alerts, receipt source type, rvol_strict.

Additive only: three new tables, one new column with a default on
``radar_feed_receipts`` and one new ``indicator_registry`` row. No existing
column, constraint or configuration is altered.

Revision ID: 229_pump_monitor
Revises: 228_radar_feed_audit
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "229_pump_monitor"
down_revision = "228_radar_feed_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "radar_feed_receipts",
        sa.Column("source_type", sa.String(32), nullable=False, server_default="radar"),
    )

    op.create_table(
        "flow_buckets_1m",
        sa.Column("symbol", sa.String(40), primary_key=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("buy_base", sa.Numeric()),
        sa.Column("sell_base", sa.Numeric()),
        sa.Column("buy_quote", sa.Numeric()),
        sa.Column("sell_quote", sa.Numeric()),
        sa.Column("trade_count", sa.Integer()),
        sa.Column("first_trade_id", sa.Text()),
        sa.Column("last_trade_id", sa.Text()),
        sa.Column("open_price", sa.Numeric()),
        sa.Column("high_price", sa.Numeric()),
        sa.Column("low_price", sa.Numeric()),
        sa.Column("close_price", sa.Numeric()),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("gap_reason", sa.String(64)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_flow_buckets_1m_bucket_start", "flow_buckets_1m", ["bucket_start"])

    op.create_table(
        "pump_monitor_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cycle_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True)),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("score", sa.Numeric()),
        sa.Column("row", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_pump_monitor_snapshots_cycle_at", "pump_monitor_snapshots", ["cycle_at"])
    op.create_index("ix_pump_monitor_snapshots_symbol_cycle", "pump_monitor_snapshots", ["symbol", "cycle_at"])

    op.create_table(
        "pump_monitor_alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("alert_type", sa.String(40), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_pump_monitor_alerts_symbol_time", "pump_monitor_alerts", ["symbol", "triggered_at"])
    op.create_index("ix_pump_monitor_alerts_type_time", "pump_monitor_alerts", ["alert_type", "triggered_at"])

    op.execute(sa.text("""
        INSERT INTO indicator_registry
            (indicator_id, alias_of, phenomenon, owning_layer, timeframe, producer,
             source_family, is_blocking, composed_inputs, contract_version)
        VALUES ('rvol_strict', NULL, 'LIQUIDITY_VOLUME', 'L3', '5m', 'feature_engine',
                'OHLCV', false, '[]'::jsonb, 'r6_indicator_registry_v1')
        ON CONFLICT (indicator_id) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("DELETE FROM indicator_registry WHERE indicator_id = 'rvol_strict'"))
    op.drop_table("pump_monitor_alerts")
    op.drop_table("pump_monitor_snapshots")
    op.drop_table("flow_buckets_1m")
    op.drop_column("radar_feed_receipts", "source_type")
