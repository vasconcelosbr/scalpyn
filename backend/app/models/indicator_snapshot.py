"""Indicator Snapshot Model — Store complete indicator metadata for audit trails."""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Numeric, Index
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone

from ..database import Base


class IndicatorSnapshot(Base):
    """Stores complete snapshot of indicators with all metadata.

    This table enables:
    - Full audit trail of indicator values and their confidence
    - Debugging of scoring decisions
    - Analysis of data quality over time
    - Rollback and replay capabilities
    """

    __tablename__ = "indicator_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True, default=lambda: datetime.now(timezone.utc))

    # Complete indicator data as JSONB
    # Structure: {indicator_name: {value, status, source, confidence, ...}}
    indicators_json = Column(JSONB, nullable=False)

    # Aggregated metrics for quick queries
    global_confidence = Column(Numeric(5, 4), nullable=False)  # average confidence across all indicators
    valid_indicators = Column(Integer, nullable=False)          # count of valid indicators
    total_indicators = Column(Integer, nullable=False)          # total indicators computed

    # Validation results
    validation_passed = Column(Boolean, nullable=False, default=False)
    validation_errors = Column(JSONB, nullable=True)  # List of validation errors

    # Score results (if computed)
    score = Column(Numeric(10, 2), nullable=True)
    score_confidence = Column(Numeric(5, 4), nullable=True)
    can_trade = Column(Boolean, nullable=False, default=False)

    # Indexes for common queries
    __table_args__ = (
        Index('idx_indicator_snapshots_symbol_timestamp', 'symbol', 'timestamp'),
        Index('idx_indicator_snapshots_can_trade', 'can_trade', 'timestamp'),
        Index('idx_indicator_snapshots_validation', 'validation_passed', 'timestamp'),
    )

    def __repr__(self):
        return (
            f"<IndicatorSnapshot(id={self.id}, symbol={self.symbol}, "
            f"timestamp={self.timestamp}, can_trade={self.can_trade}, "
            f"valid_indicators={self.valid_indicators}/{self.total_indicators})>"
        )
