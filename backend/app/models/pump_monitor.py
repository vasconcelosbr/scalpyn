"""Pump Monitor persistence: 1-minute flow buckets, cycle snapshots, alert log.

Observation only — nothing here is read by execution, L3 or bots.
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from ..database import Base


class FlowBucket1m(Base):
    """Taker flow of one symbol in one closed minute, built from exchange trades."""
    __tablename__ = "flow_buckets_1m"

    symbol = Column(String(40), primary_key=True)
    bucket_start = Column(DateTime(timezone=True), primary_key=True)
    bucket_seconds = Column(Integer, nullable=False, default=60)
    buy_base = Column(Numeric, nullable=True)
    sell_base = Column(Numeric, nullable=True)
    buy_quote = Column(Numeric, nullable=True)
    sell_quote = Column(Numeric, nullable=True)
    trade_count = Column(Integer, nullable=True)
    first_trade_id = Column(Text, nullable=True)
    last_trade_id = Column(Text, nullable=True)
    open_price = Column(Numeric, nullable=True)
    high_price = Column(Numeric, nullable=True)
    low_price = Column(Numeric, nullable=True)
    close_price = Column(Numeric, nullable=True)
    partial = Column(Boolean, nullable=False, default=False)
    gap_reason = Column(String(64), nullable=True)
    source = Column(String(40), nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_flow_buckets_1m_bucket_start", "bucket_start"),)


class PumpMonitorSnapshot(Base):
    __tablename__ = "pump_monitor_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cycle_at = Column(DateTime(timezone=True), nullable=False)
    pool_id = Column(UUID(as_uuid=True), nullable=True)
    symbol = Column(String(40), nullable=False)
    config_version = Column(Integer, nullable=False)
    config_hash = Column(String(64), nullable=False)
    score = Column(Numeric, nullable=True)
    row = Column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_pump_monitor_snapshots_cycle_at", "cycle_at"),
        Index("ix_pump_monitor_snapshots_symbol_cycle", "symbol", "cycle_at"),
    )


class PumpMonitorAlert(Base):
    __tablename__ = "pump_monitor_alerts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(40), nullable=False)
    alert_type = Column(String(40), nullable=False)
    triggered_at = Column(DateTime(timezone=True), nullable=False)
    inputs = Column(JSONB, nullable=False)
    config_version = Column(Integer, nullable=False)
    config_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_pump_monitor_alerts_symbol_time", "symbol", "triggered_at"),
        Index("ix_pump_monitor_alerts_type_time", "alert_type", "triggered_at"),
    )
