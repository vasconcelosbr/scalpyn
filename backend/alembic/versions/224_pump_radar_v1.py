"""Add the isolated Pump Radar v1 research domain.

Revision ID: 224_pump_radar_v1
Revises: 223_shadow_l3_historical_lineage
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "224_pump_radar_v1"
down_revision = "223_shadow_l3_historical_lineage"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _uuid_pk() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "pump_radar_ohlcv", _uuid_pk(),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("open", sa.Numeric(30, 12), nullable=False),
        sa.Column("high", sa.Numeric(30, 12), nullable=False),
        sa.Column("low", sa.Numeric(30, 12), nullable=False),
        sa.Column("close", sa.Numeric(30, 12), nullable=False),
        sa.Column("volume_base", sa.Numeric(38, 12), nullable=False),
        sa.Column("volume_quote", sa.Numeric(38, 12)),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(32), nullable=False, server_default="gate_spot"),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("quality_status", sa.String(32), nullable=False, server_default="VALID"),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("timeframe IN ('5m','15m','1h')", name="ck_pump_radar_ohlcv_timeframe"),
        sa.CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="ck_pump_radar_ohlcv_positive"),
        sa.CheckConstraint("high >= greatest(open, close, low) AND low <= least(open, close, high)", name="ck_pump_radar_ohlcv_valid_range"),
        sa.CheckConstraint("close_time > open_time", name="ck_pump_radar_ohlcv_time_order"),
        sa.UniqueConstraint("symbol", "timeframe", "open_time", name="uq_pump_radar_ohlcv_candle"),
    )
    op.create_index("ix_pump_radar_ohlcv_lookup", "pump_radar_ohlcv", ["symbol", "timeframe", "open_time"])
    op.create_index("ix_pump_radar_ohlcv_available", "pump_radar_ohlcv", ["available_at"])

    op.create_table(
        "pump_radar_runs", _uuid_pk(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("mode", sa.String(24), nullable=False, server_default="incremental"),
        sa.Column("date_from", sa.DateTime(timezone=True)),
        sa.Column("date_to", sa.DateTime(timezone=True)),
        sa.Column("config_snapshot", JSONB, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("universe_snapshot", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("timezone", sa.String(32), nullable=False, server_default="UTC"),
        sa.Column("total_assets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_assets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_assets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','COMPLETED','PARTIAL','FAILED','CANCELLED','CANCELLING')", name="ck_pump_radar_run_status"),
    )
    op.create_index("ix_pump_radar_runs_user_requested", "pump_radar_runs", ["user_id", "requested_at"])

    op.create_table(
        "pump_radar_run_assets", _uuid_pk(),
        sa.Column("run_id", UUID, sa.ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage", sa.Numeric(8, 6)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("source_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("run_id", "symbol", name="uq_pump_radar_run_asset"),
    )
    op.create_index("ix_pump_radar_assets_run_status", "pump_radar_run_assets", ["run_id", "status"])

    op.create_table(
        "pump_radar_events", _uuid_pk(),
        sa.Column("run_id", UUID, sa.ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_market_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True)),
        sa.Column("peak_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("start_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("peak_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("end_price", sa.Numeric(30, 12)),
        sa.Column("rise_pct", sa.Numeric(14, 6), nullable=False),
        sa.Column("retracement_pct", sa.Numeric(14, 6)),
        sa.Column("is_incomplete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reconstruction_status", sa.String(32), nullable=False, server_default="RECORDED"),
        sa.Column("quality_status", sa.String(32), nullable=False, server_default="VALID"),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("peak_at >= start_at", name="ck_pump_radar_event_peak_order"),
        sa.CheckConstraint("end_at IS NULL OR end_at >= peak_at", name="ck_pump_radar_event_end_order"),
        sa.UniqueConstraint("run_id", "symbol", "start_at", name="uq_pump_radar_event"),
    )
    op.create_index("ix_pump_radar_events_run_rank", "pump_radar_events", ["run_id", "rise_pct"])
    op.create_index("ix_pump_radar_events_symbol_time", "pump_radar_events", ["symbol", "start_at", "end_at"])

    op.create_table(
        "pump_radar_event_links", _uuid_pk(),
        sa.Column("event_id", UUID, sa.ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shadow_trade_id", UUID, sa.ForeignKey("shadow_trades.id", ondelete="SET NULL")),
        sa.Column("decision_id", sa.BigInteger(), sa.ForeignKey("decisions_log.id", ondelete="SET NULL")),
        sa.Column("link_kind", sa.String(32), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_at", sa.DateTime(timezone=True)),
        sa.Column("simulation_created_at", sa.DateTime(timezone=True)),
        sa.Column("entry_at", sa.DateTime(timezone=True)),
        sa.Column("exit_at", sa.DateTime(timezone=True)),
        sa.Column("delay_seconds", sa.Integer()),
        sa.Column("realized_pnl_pct", sa.Numeric(14, 6)),
        sa.Column("profile_id", UUID),
        sa.Column("profile_version_id", UUID),
        sa.Column("profile_config_hash", sa.String(128)),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("link_kind IN ('EXPLICIT_ENVELOPE','EXPLICIT_DECISION','EXPLICIT_SHADOW','TEMPORAL_CORRELATION')", name="ck_pump_radar_link_kind"),
        sa.UniqueConstraint("event_id", "shadow_trade_id", name="uq_pump_radar_event_shadow"),
    )
    op.create_index("ix_pump_radar_links_user_event", "pump_radar_event_links", ["user_id", "event_id"])
    op.create_index("uq_pump_radar_primary_shadow", "pump_radar_event_links", ["shadow_trade_id"], unique=True, postgresql_where=sa.text("is_primary IS TRUE AND shadow_trade_id IS NOT NULL"))

    op.create_table(
        "pump_radar_indicator_snapshots", _uuid_pk(),
        sa.Column("event_id", UUID, sa.ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_priority", sa.String(48), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("feature_engine_version", sa.String(128)),
        sa.Column("profile_id", UUID),
        sa.Column("profile_version_id", UUID),
        sa.Column("profile_config_hash", sa.String(128)),
        sa.Column("coverage", sa.Numeric(8, 6)),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("state IN ('PASSED','REJECTED','NOT_EVALUATED','UNAVAILABLE','RECONSTRUCTED')", name="ck_pump_radar_snapshot_state"),
        sa.UniqueConstraint("event_id", "snapshot_at", "source_priority", name="uq_pump_radar_snapshot"),
    )
    op.create_index("ix_pump_radar_snapshots_event_time", "pump_radar_indicator_snapshots", ["event_id", "snapshot_at"])

    op.create_table(
        "pump_radar_indicator_values", _uuid_pk(),
        sa.Column("snapshot_id", UUID, sa.ForeignKey("pump_radar_indicator_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("indicator_id", sa.String(120), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("numeric_value", sa.Numeric(30, 12)),
        sa.Column("text_value", sa.Text()),
        sa.Column("rule", JSONB),
        sa.Column("numerator", sa.Integer()),
        sa.Column("denominator", sa.Integer()),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("version", sa.String(128)),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("layer IN ('POOL','L1','L2','L3')", name="ck_pump_radar_indicator_layer"),
        sa.CheckConstraint("state IN ('PASSED','REJECTED','NOT_EVALUATED','UNAVAILABLE','RECONSTRUCTED')", name="ck_pump_radar_indicator_state"),
        sa.UniqueConstraint("snapshot_id", "indicator_id", "timeframe", name="uq_pump_radar_indicator_value"),
    )

    op.create_table(
        "pump_radar_controls", _uuid_pk(),
        sa.Column("run_id", UUID, sa.ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", UUID, sa.ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("followup_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("overlap_excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("match_rank", sa.Integer()),
        sa.Column("match_features", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("outcome", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("run_id", "event_id", "symbol", "anchor_at", name="uq_pump_radar_control"),
    )
    op.create_index("ix_pump_radar_controls_run_event", "pump_radar_controls", ["run_id", "event_id"])

    op.create_table(
        "pump_radar_range_results", _uuid_pk(),
        sa.Column("run_id", UUID, sa.ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("indicator_id", sa.String(120), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("range_key", sa.String(32), nullable=False),
        sa.Column("lower_bound", sa.Numeric(30, 12)),
        sa.Column("upper_bound", sa.Numeric(30, 12)),
        sa.Column("pump_numerator", sa.Integer(), nullable=False),
        sa.Column("pump_denominator", sa.Integer(), nullable=False),
        sa.Column("control_numerator", sa.Integer(), nullable=False),
        sa.Column("control_denominator", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.Numeric(8, 6)),
        sa.Column("difference", sa.Numeric(14, 6)),
        sa.Column("ratio", sa.Numeric(14, 6)),
        sa.Column("confidence_interval", JSONB),
        sa.Column("validation_status", sa.String(32), nullable=False, server_default="CANDIDATE"),
        sa.Column("discovery_boundary", sa.DateTime(timezone=True)),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("pump_denominator >= 0 AND control_denominator >= 0", name="ck_pump_radar_range_denominators"),
        sa.UniqueConstraint("run_id", "indicator_id", "timeframe", "range_key", name="uq_pump_radar_range"),
    )
    op.create_index("ix_pump_radar_ranges_run_status", "pump_radar_range_results", ["run_id", "validation_status"])

    op.create_table(
        "pump_radar_hypotheses", _uuid_pk(),
        sa.Column("run_id", UUID, sa.ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="CANDIDATE"),
        sa.Column("conditions", JSONB, nullable=False),
        sa.Column("profile_id", UUID),
        sa.Column("profile_version_id", UUID),
        sa.Column("profile_config_hash", sa.String(128)),
        sa.Column("replay_result", JSONB),
        sa.Column("export_payload", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provenance", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("status IN ('CANDIDATE','REPLAYED','EXPORTED','REJECTED')", name="ck_pump_radar_hypothesis_status"),
    )
    op.create_index("ix_pump_radar_hypotheses_user_run", "pump_radar_hypotheses", ["user_id", "run_id"])


def downgrade() -> None:
    for table in (
        "pump_radar_hypotheses", "pump_radar_range_results", "pump_radar_controls",
        "pump_radar_indicator_values", "pump_radar_indicator_snapshots",
        "pump_radar_event_links", "pump_radar_events", "pump_radar_run_assets",
        "pump_radar_runs", "pump_radar_ohlcv",
    ):
        op.drop_table(table)
