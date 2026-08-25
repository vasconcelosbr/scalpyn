from sqlalchemy import BigInteger, Column, String, Boolean, DateTime, Float, Integer, Text, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.types import TIMESTAMP
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
    direction = Column(String(10), nullable=True)
    event_type = Column(String(40), nullable=True)
    processed = Column(Boolean, nullable=False, default=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Populated by Trade Reconciliation (Module 2) using dedicated columns
    # rather than patching the immutable metrics JSONB.
    trade_executed = Column(Boolean, nullable=True)
    execution_type = Column(String(10), nullable=True)
    execution_entry_price = Column(Float, nullable=True)
    execution_entry_time = Column(DateTime(timezone=True), nullable=True)

    # Populated by Trade Monitor (Module 3) once the trade closes.
    outcome = Column(String(20), nullable=True)        # 'tp' | 'sl' | 'timeout'
    pnl_pct = Column(Float, nullable=True)
    holding_seconds = Column(Integer, nullable=True)

    # Profile attribution (migration 082) — NULL = legacy global decision
    profile_id      = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    profile_name    = Column(String(255),          nullable=True)
    profile_version = Column(TIMESTAMP(timezone=True), nullable=True)

    # ML Gate lineage/audit contract (migration 112).
    ranking_id = Column(UUID(as_uuid=True), ForeignKey("ml_opportunity_rankings.id", use_alter=True, name="fk_decisions_log_ranking_id"), nullable=True)
    model_id = Column(UUID(as_uuid=True), nullable=True)
    model_version = Column(String(50), nullable=True)
    model_lane = Column(String(50), nullable=True)
    probability = Column(Float, nullable=True)
    threshold_used = Column(Float, nullable=True)
    score_status = Column(String(40), nullable=True)
    gate_action = Column(String(20), nullable=True)
    reason_codes = Column(JSONB, nullable=True)
    orchestrator_payload = Column(JSONB, nullable=True)
    ml_gate_enabled = Column(Boolean, nullable=False, default=False)


class L3AuthorizationOutbox(Base):
    """Transactional hand-off from an immutable L3 decision to shadow capture."""

    __tablename__ = "l3_authorization_outbox"
    __table_args__ = (
        UniqueConstraint(
            "decision_id", "authorization_contract_hash",
            name="uq_l3_authorization_outbox_decision_contract",
        ),
        Index("ix_l3_authorization_outbox_status_created", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id = Column(
        BigInteger,
        ForeignKey("decisions_log.id", ondelete="CASCADE"),
        nullable=False,
    )
    authorization_contract_hash = Column(String(64), nullable=False)
    event_type = Column(String(50), nullable=False, default="CREATE_SHADOW_IF_ALLOWED")
    status = Column(String(20), nullable=False, default="PENDING")
    payload = Column(JSONB, nullable=False, default=dict)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    available_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)


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
