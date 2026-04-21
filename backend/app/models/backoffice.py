from sqlalchemy import BigInteger, Column, String, Boolean, DateTime, Float, Integer, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base


class DecisionLog(Base):
    __tablename__ = 'decisions_log'
    __table_args__ = (
        Index("idx_decisions_symbol", "symbol"),
        Index("idx_decisions_created_at", "created_at"),
        Index("idx_decisions_score", "score"),
        Index("idx_decisions_decision", "decision"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    strategy = Column(String(50), nullable=False)
    timeframe = Column(String(10), nullable=True)
    score = Column(Float, nullable=True)
    decision = Column(String(10), nullable=False)
    l1_pass = Column(Boolean, nullable=True)
    l2_pass = Column(Boolean, nullable=True)
    l3_pass = Column(Boolean, nullable=True)
    reasons = Column(JSONB, nullable=True)
    metrics = Column(JSONB, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    signal = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    payload_json = Column(JSONB, nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AssetTrace(Base):
    __tablename__ = 'asset_traces'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(50), nullable=False, index=True)
    market_data_json = Column(JSONB, nullable=True)
    indicators_json = Column(JSONB, nullable=True)
    conditions_json = Column(JSONB, nullable=True)
    decision = Column(String(20), nullable=True)
    score = Column(Float, nullable=True)
    strategy = Column(String(20), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BackofficeAlert(Base):
    __tablename__ = 'backoffice_alerts'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_type = Column(String(20), nullable=False)
    category = Column(String(50), nullable=True)
    message = Column(Text, nullable=False)
    details_json = Column(JSONB, nullable=True)
    status = Column(String(20), default='active')
    acknowledged_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PipelineMetric(Base):
    __tablename__ = 'pipeline_metrics'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    discovered = Column(Integer, default=0)
    filtered = Column(Integer, default=0)
    scored = Column(Integer, default=0)
    signals_count = Column(Integer, default=0)
    executed = Column(Integer, default=0)
    approved = Column(Integer, default=0)
    rejected = Column(Integer, default=0)
    latency_ms = Column(Float, nullable=True)
    error_count = Column(Integer, default=0)
    strategy = Column(String(20), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
