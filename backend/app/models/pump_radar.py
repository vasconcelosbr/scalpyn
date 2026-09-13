from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ..database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PumpRadarOHLCV(Base):
    __tablename__ = "pump_radar_ohlcv"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "open_time", name="uq_pump_radar_ohlcv_candle"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(40), nullable=False)
    timeframe = Column(String(8), nullable=False)
    open_time = Column(DateTime(timezone=True), nullable=False)
    close_time = Column(DateTime(timezone=True), nullable=False)
    available_at = Column(DateTime(timezone=True), nullable=True)
    ingested_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    open = Column(Numeric(30, 12), nullable=False)
    high = Column(Numeric(30, 12), nullable=False)
    low = Column(Numeric(30, 12), nullable=False)
    close = Column(Numeric(30, 12), nullable=False)
    volume_base = Column(Numeric(38, 12), nullable=False)
    volume_quote = Column(Numeric(38, 12), nullable=True)
    is_closed = Column(Boolean, nullable=False, default=True)
    source = Column(String(32), nullable=False, default="gate_spot")
    contract_version = Column(String(64), nullable=False)
    quality_status = Column(String(32), nullable=False, default="VALID")
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarRun(Base):
    __tablename__ = "pump_radar_runs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(32), nullable=False, default="QUEUED")
    mode = Column(String(24), nullable=False, default="incremental")
    date_from = Column(DateTime(timezone=True), nullable=True)
    date_to = Column(DateTime(timezone=True), nullable=True)
    config_snapshot = Column(JSONB, nullable=False)
    config_hash = Column(String(64), nullable=False)
    universe_snapshot = Column(JSONB, nullable=False, default=list)
    timezone = Column(String(32), nullable=False, default="UTC")
    total_assets = Column(Integer, nullable=False, default=0)
    processed_assets = Column(Integer, nullable=False, default=0)
    failed_assets = Column(Integer, nullable=False, default=0)
    requested_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarRunAsset(Base):
    __tablename__ = "pump_radar_run_assets"
    __table_args__ = (UniqueConstraint("run_id", "symbol", name="uq_pump_radar_run_asset"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(40), nullable=False)
    status = Column(String(32), nullable=False, default="QUEUED")
    event_count = Column(Integer, nullable=False, default=0)
    coverage = Column(Numeric(8, 6), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    source_metadata = Column(JSONB, nullable=False, default=dict)


class PumpRadarEvent(Base):
    __tablename__ = "pump_radar_events"
    __table_args__ = (UniqueConstraint("run_id", "symbol", "start_at", name="uq_pump_radar_event"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(40), nullable=False)
    start_at = Column(DateTime(timezone=True), nullable=False)
    confirmed_market_at = Column(DateTime(timezone=True), nullable=False)
    detected_at = Column(DateTime(timezone=True), nullable=True)
    peak_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=True)
    start_price = Column(Numeric(30, 12), nullable=False)
    peak_price = Column(Numeric(30, 12), nullable=False)
    end_price = Column(Numeric(30, 12), nullable=True)
    rise_pct = Column(Numeric(14, 6), nullable=False)
    retracement_pct = Column(Numeric(14, 6), nullable=True)
    is_incomplete = Column(Boolean, nullable=False, default=False)
    reconstruction_status = Column(String(32), nullable=False, default="RECORDED")
    quality_status = Column(String(32), nullable=False, default="VALID")
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarEventLink(Base):
    __tablename__ = "pump_radar_event_links"
    __table_args__ = (UniqueConstraint("event_id", "shadow_trade_id", name="uq_pump_radar_event_shadow"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shadow_trade_id = Column(UUID(as_uuid=True), ForeignKey("shadow_trades.id", ondelete="SET NULL"), nullable=True)
    decision_id = Column(BigInteger, ForeignKey("decisions_log.id", ondelete="SET NULL"), nullable=True)
    link_kind = Column(String(32), nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    approval_at = Column(DateTime(timezone=True), nullable=True)
    simulation_created_at = Column(DateTime(timezone=True), nullable=True)
    entry_at = Column(DateTime(timezone=True), nullable=True)
    exit_at = Column(DateTime(timezone=True), nullable=True)
    delay_seconds = Column(Integer, nullable=True)
    realized_pnl_pct = Column(Numeric(14, 6), nullable=True)
    profile_id = Column(UUID(as_uuid=True), nullable=True)
    profile_version_id = Column(UUID(as_uuid=True), nullable=True)
    profile_config_hash = Column(String(128), nullable=True)
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarIndicatorSnapshot(Base):
    __tablename__ = "pump_radar_indicator_snapshots"
    __table_args__ = (UniqueConstraint("event_id", "snapshot_at", "source_priority", name="uq_pump_radar_snapshot"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    snapshot_at = Column(DateTime(timezone=True), nullable=False)
    source_priority = Column(String(48), nullable=False)
    state = Column(String(32), nullable=False)
    schema_version = Column(String(64), nullable=False)
    feature_engine_version = Column(String(128), nullable=True)
    profile_id = Column(UUID(as_uuid=True), nullable=True)
    profile_version_id = Column(UUID(as_uuid=True), nullable=True)
    profile_config_hash = Column(String(128), nullable=True)
    coverage = Column(Numeric(8, 6), nullable=True)
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarIndicatorValue(Base):
    __tablename__ = "pump_radar_indicator_values"
    __table_args__ = (UniqueConstraint("snapshot_id", "indicator_id", "timeframe", name="uq_pump_radar_indicator_value"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_indicator_snapshots.id", ondelete="CASCADE"), nullable=False)
    indicator_id = Column(String(120), nullable=False)
    layer = Column(String(16), nullable=False)
    timeframe = Column(String(8), nullable=False)
    state = Column(String(32), nullable=False)
    numeric_value = Column(Numeric(30, 12), nullable=True)
    text_value = Column(Text, nullable=True)
    rule = Column(JSONB, nullable=True)
    numerator = Column(Integer, nullable=True)
    denominator = Column(Integer, nullable=True)
    source = Column(String(80), nullable=False)
    version = Column(String(128), nullable=True)
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarControl(Base):
    __tablename__ = "pump_radar_controls"
    __table_args__ = (UniqueConstraint("run_id", "event_id", "symbol", "anchor_at", name="uq_pump_radar_control"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(40), nullable=False)
    anchor_at = Column(DateTime(timezone=True), nullable=False)
    followup_complete = Column(Boolean, nullable=False, default=False)
    overlap_excluded = Column(Boolean, nullable=False, default=False)
    match_rank = Column(Integer, nullable=True)
    match_features = Column(JSONB, nullable=False, default=dict)
    outcome = Column(JSONB, nullable=False, default=dict)
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarRangeResult(Base):
    __tablename__ = "pump_radar_range_results"
    __table_args__ = (UniqueConstraint("run_id", "indicator_id", "timeframe", "range_key", name="uq_pump_radar_range"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False)
    indicator_id = Column(String(120), nullable=False)
    layer = Column(String(16), nullable=False)
    timeframe = Column(String(8), nullable=False)
    range_key = Column(String(32), nullable=False)
    lower_bound = Column(Numeric(30, 12), nullable=True)
    upper_bound = Column(Numeric(30, 12), nullable=True)
    pump_numerator = Column(Integer, nullable=False)
    pump_denominator = Column(Integer, nullable=False)
    control_numerator = Column(Integer, nullable=False)
    control_denominator = Column(Integer, nullable=False)
    coverage = Column(Numeric(8, 6), nullable=True)
    difference = Column(Numeric(14, 6), nullable=True)
    ratio = Column(Numeric(14, 6), nullable=True)
    confidence_interval = Column(JSONB, nullable=True)
    validation_status = Column(String(32), nullable=False, default="CANDIDATE")
    discovery_boundary = Column(DateTime(timezone=True), nullable=True)
    provenance = Column(JSONB, nullable=False, default=dict)


class PumpRadarReportRun(Base):
    __tablename__ = "pump_radar_report_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False)
    selection_mode = Column(String(16), nullable=False)
    filters = Column(JSONB, nullable=False, default=dict)
    selection_hash = Column(String(64), nullable=False)
    total_events = Column(Integer, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="READY")
    completeness = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class PumpRadarReportItem(Base):
    __tablename__ = "pump_radar_report_items"
    __table_args__ = (
        UniqueConstraint("report_run_id", "event_id", name="uq_pump_radar_report_item_event"),
        UniqueConstraint("report_run_id", "position", name="uq_pump_radar_report_item_position"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_report_runs.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_events.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class PumpRadarHypothesis(Base):
    __tablename__ = "pump_radar_hypotheses"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("pump_radar_runs.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(160), nullable=False)
    status = Column(String(24), nullable=False, default="CANDIDATE")
    conditions = Column(JSONB, nullable=False)
    profile_id = Column(UUID(as_uuid=True), nullable=True)
    profile_version_id = Column(UUID(as_uuid=True), nullable=True)
    profile_config_hash = Column(String(128), nullable=True)
    replay_result = Column(JSONB, nullable=True)
    export_payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    provenance = Column(JSONB, nullable=False, default=dict)
