"""Trade simulation model for ML dataset generation."""

from sqlalchemy import BigInteger, Column, String, DateTime, Integer, Boolean, Numeric, CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime, timezone
from ..database import Base


class TradeSimulation(Base):
    """Simulated trade outcomes for ML training dataset."""

    __tablename__ = 'trade_simulations'
    __table_args__ = (
        CheckConstraint("result IN ('WIN', 'LOSS', 'TIMEOUT')", name='check_result'),
        CheckConstraint("direction IN ('LONG', 'SHORT', 'SPOT')", name='check_direction'),
        CheckConstraint("decision_type IN ('ALLOW', 'BLOCK')", name='check_decision_type'),
        Index("idx_trade_simulations_symbol", "symbol"),
        Index("idx_trade_simulations_timestamp_entry", "timestamp_entry"),
        Index("idx_trade_simulations_result", "result"),
        Index("idx_trade_simulations_direction", "direction"),
        Index("idx_trade_simulations_decision_type", "decision_type"),
        Index("idx_trade_simulations_symbol_timestamp", "symbol", "timestamp_entry"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(20), nullable=False)
    timestamp_entry = Column(DateTime(timezone=True), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)

    tp_price = Column(Numeric(20, 8), nullable=False)
    sl_price = Column(Numeric(20, 8), nullable=False)

    exit_price = Column(Numeric(20, 8), nullable=True)
    exit_timestamp = Column(DateTime(timezone=True), nullable=True)

    result = Column(String(10), nullable=False)  # WIN | LOSS | TIMEOUT
    time_to_result = Column(Integer, nullable=True)  # seconds

    direction = Column(String(10), nullable=False)  # LONG | SHORT | SPOT

    is_simulated = Column(Boolean, default=True)
    source = Column(String(30), default='SIMULATION')

    decision_type = Column(String(10), nullable=False)  # ALLOW | BLOCK
    decision_id = Column(BigInteger, ForeignKey('decisions_log.id', ondelete='SET NULL'), nullable=True)

    features_snapshot = Column(JSONB, nullable=True)
    config_snapshot = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
